from datetime import date
import sqlite3, json, math
SCENARIOS={'downside':[50,60,120,30,10],'base':[100,100,192,60,20],'stretch':[150,140,250,85,27]}
BET_NAMES=['Attendance Assurance','Verified Referral Pods','Campus Cohort Commitments','High-Intent Recovery','Waitlist-to-Entry']
BUDGETS=[10000,10000,22000,8000,3500]; HOURS=[40,50,65,35,15]; PILOTS=[2000,2000,5000,2000,1000]; OWNERS=['Lifecycle lead','Campus lead B','Campus lead A','Lifecycle lead','Operations lead']; WEEKS=['8–12','4–12','2–10','5–11','10–12']; LIMITS=[100,100,115,134,175]; STOPS=[150,140,150,180,200]; SCALE=[10,78,12,100/3,42]; CHANGE=[5,109,8,20,30]; SIGNALS=['Reconfirmation uplift (pp)','Incremental registration cost (₹)','Incremental registrations / campus','Registration uplift (pp)','Offer acceptance (%)']; RISKS=['Opt-outs / proxy validity','Duplicate claims / organic cannibalization','Campus activation bandwidth','Consent / weak uplift','Safe available slots']
# All dates stored as local wall time (IST) per workbook guide.
def snapshot(db, day='2025-04-20', campaign='all'):
 date.fromisoformat(day); end=day+' 23:59:59'; q=lambda sql,args=():db.execute(sql,args).fetchone()[0]
 cfilter='' if campaign=='all' else ' AND first_campaign_id=?'; args=() if campaign=='all' else (campaign,)
 ids={r[0] for r in db.execute('SELECT person_id FROM people WHERE person_type="external" AND first_seen_at<=?'+cfilter,(end,*args))}
 reg={r[0] for r in db.execute('SELECT DISTINCT person_id FROM registrations WHERE registered_at<=?',(end,))}&ids
 history=db.execute('SELECT person_id,status FROM (SELECT h.*, ROW_NUMBER() OVER(PARTITION BY registration_id ORDER BY effective_at DESC,history_id DESC) rn FROM registration_history h WHERE effective_at<=?) WHERE rn=1',(end,)).fetchall()
 statuses={};
 for p,s in history:
  if p in reg: statuses.setdefault(p,set()).add(s)
 confirmed={p for p,s in statuses.items() if 'confirmed' in s}
 event=db.execute('SELECT event_starts_at,event_ends_at FROM event').fetchone()
 attended={r[0] for r in db.execute('SELECT DISTINCT person_id FROM verified_external_admissions WHERE occurred_at BETWEEN ? AND ?',(event[0],min(end,event[1])))}&ids
 m=db.execute('SELECT '+','.join('COALESCE(SUM('+x+'),0)' for x in ['impressions','reported_reach','clicks','landing_page_sessions','registration_starts','marketing_spend_inr'])+' FROM marketing WHERE activity_date<=?'+(' AND campaign_id=?' if args else ''),(end,*args)).fetchone()
 target=db.execute('SELECT * FROM targets WHERE effective_at<=? ORDER BY effective_at DESC LIMIT 1',(end,)).fetchone(); t=dict(target) if target else {}
 status_counts={s:sum(s in x and ('confirmed' not in x or s=='confirmed') for x in statuses.values()) for s in ['confirmed','cancelled','waitlisted','interested']}
 return dict(identified=len(ids),registered=len(reg),confirmed=len(confirmed),attendees=len(attended),registered_attendees=len(reg&attended),confirmed_attendees=len(confirmed&attended),walkins=len(attended-reg),impressions=m[0],reach=m[1],clicks=m[2],sessions=m[3],starts=m[4],spend=m[5],target=t,statuses=status_counts,lead_conversion=ratio(len(reg),len(ids)),showup=ratio(len(reg&attended),len(reg)),confirmed_showup=ratio(len(confirmed&attended),len(confirmed)),cpa=ratio(m[5],len(reg&attended)),cpr=ratio(m[5],len(reg)),blended_cpa=ratio(m[5],len(attended)),ctr=ratio(m[2],m[0]))
def ratio(a,b):return a/b if b else None
def forecast(rows,settings,scenario='base'):
 # The scenario contribution is the locked planning estimate at full funding and time.
 # Edits to forecast impact, budget, and time are explicit sensitivities, not observed effects.
 ceiling=settings['budget']; retained=settings['retained']; paid_cut=settings['paid_cut']; days=settings['weeks']; team=settings['hours']; occupancy=settings['occupancy']; peak=settings['peak_share']; dwell=settings['dwell']; daily=settings['daily']; throughput=settings['throughput']; share=settings['arrival_share']; window=settings['arrival_window']
 cap=math.floor(min(daily,occupancy*.85*10/(dwell*1.5),throughput*window/share)/peak)
 loss=round(paid_cut/(152000/515)) if scenario!='downside' else round(paid_cut*130/30000)
 details=[]
 for r in rows:
  i=r['bet_index']; default=SCENARIOS[scenario][i] if i is not None else 0
  impact=r['expected'] if scenario=='base' or i is None else (default*r['expected']/SCENARIOS['base'][i]);
  if i is not None and r['signal'] is not None and r['sample'] >= [400,100,5,150,20][i]:
   v=r['signal']; implied=[2000*max(0,v)/100*.5, (r['budget']/v*.781 if v>0 else r['expected']),20*max(0,v)*.8,300*max(0,v)/100*.6,60*max(0,v)/100*.8][i]; impact=min(impact,implied)
  funding=min(1,r['budget']/BUDGETS[i]) if i is not None else 1
  time=min(1,days/12); hours_factor=min(1,r['hours']/HOURS[i]) if i is not None else 1
  conversion=settings.get('conversion_factor',1)*settings.get('conversion_rate',.8)/.8 if i in (1,2,3,None) else 1; showup=settings.get('showup_factor',1)*settings.get('showup_rate',.6875)/.6875
  value=round(impact*funding*time*hours_factor*conversion*showup) if r['status']!='Stopped' else 0
  details.append({'id':r['id'],'name':r['name'],'contribution':value,'projected_cpa':ratio(r['budget'],value)})
 total=sum(x['contribution'] for x in details); planned=retained+sum(max(r['spend'],r['budget'] if r['status']!='Stopped' else 0) for r in rows); hours=515+sum(r['hours'] for r in rows if r['status']!='Stopped'); raw=max(0,2050-loss+total)
 return dict(scenario=scenario,raw=raw,forecast=min(raw,cap),capacity=cap,paid_loss=loss,contribution=total,planned_budget=planned,budget_gap=ceiling-planned,planned_hours=hours,hours_gap=team-hours,feasible=planned<=ceiling and hours<=team and raw<=cap,details=details,blended_cpa=ratio(planned,min(raw,cap)))
def decision(r, projected):
 if r['status']=='Stopped':return 'STOP'
 if r['bet_index'] is None:return 'TEST'
 i=r['bet_index']; v=r['signal']; cpa=projected; n=r['sample']; minimum=[400,100,5,150,20][i]
 if not r['guardrail']:return 'STOP'
 if n<minimum or v is None:return 'TEST'
 if cpa is None or cpa>STOPS[i] or (v>CHANGE[i] if i==1 else v<CHANGE[i]):return 'STOP'
 if cpa<=LIMITS[i] and (v<=SCALE[i] if i==1 else v>=SCALE[i]):return 'SCALE'
 return 'CHANGE'

# Grouping is a planning classification; these are descriptive historical cohorts.
def historical_diagnostics(summary, breakdown, day):
 groups={'Community-led':['Campus Ambassadors','College Partnerships','Student Referrals','Community Groups'],'Intent-led':['Organic Search'],'Paid discovery':['Paid Social','Creator Partners']}
 cohorts=[]
 for name,names in groups.items():
  cs=[c for c in breakdown if c['campaign_name'] in names]; r=sum(c['registered'] for c in cs); a=sum(c['registered_attendees'] for c in cs); spend=sum(c['spend'] for c in cs)
  cohorts.append(dict(name=name,registered=r,attendees=a,spend=spend,cpa=ratio(spend,a),showup=ratio(a,r)))
 leakage=[dict(name='Identified but unregistered',count=summary['identified']-summary['registered']-summary['walkins']),dict(name='Registered not confirmed',count=summary['registered']-summary['confirmed'])]
 if day>='2025-04-13':leakage.append(dict(name='Confirmed without attendance',count=summary['confirmed']-summary['confirmed_attendees']))
 return dict(cohorts=cohorts,leakage=leakage,bottleneck=max(leakage,key=lambda x:x['count']),confirmation_rate=ratio(summary['confirmed'],summary['registered']))
