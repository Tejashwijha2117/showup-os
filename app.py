from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
import uvicorn
from contextvars import ContextVar
ctx=ContextVar("db")
def jsonify(**kw): return kw
from pathlib import Path
import sqlite3,json,os,uuid,re,base64,secrets,shutil
from contextlib import closing
from engine import *
ROOT=Path(__file__).parent; app=FastAPI(); DB=os.environ.get('SHOWUP_DB',str(ROOT/'showup.db'))
DEFAULTS=dict(budget=300000,retained=246500,paid_cut=30000,weeks=12,hours=720,occupancy=1500,peak_share=.7,dwell=4,daily=1900,throughput=300,arrival_share=.4,arrival_window=3,conversion_factor=1,showup_factor=1,conversion_rate=.8,showup_rate=.6875)
def connect():
 db=sqlite3.connect(DB,check_same_thread=False);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db
def init():
 if not Path(DB).exists() and Path(DB).resolve()!=(ROOT/'showup.db').resolve():
  Path(DB).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/'showup.db',DB)
 with closing(connect()) as db, db:
  db.executescript((ROOT/'schema.sql').read_text())
  if 'insight' not in [r[1] for r in db.execute('PRAGMA table_info(tasks)')]: db.execute("ALTER TABLE tasks ADD COLUMN insight TEXT DEFAULT ''")
  if not db.execute('SELECT 1 FROM settings').fetchone():
   db.execute('INSERT INTO settings VALUES (1,?)',(json.dumps(DEFAULTS),))
   for i,n in enumerate(BET_NAMES):
    db.execute('INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(f'B{i+1}',n,OWNERS[i],['Confirmed retained','New peer groups','New campus networks','Retained unregistered','Retained waitlist'][i],['Improve show-up','Acquire incremental peers','Activate 20 colleges','Recover registrations','Release safe slots'][i],'Planned',BUDGETS[i],0,SCENARIOS['base'][i],HOURS[i],i,None,0,1,WEEKS[i]))
   db.execute('INSERT INTO tasks(id,campaign_id,title,owner,priority,deadline,status,impact,criteria) VALUES (?,?,?,?,?,?,?,?,?)',('T1','B3','Validate five campus pilot partners','Campus lead A','High','2027-02-01','Open','Unlock ₹17K conditional funding','Confirm five partners and record pilot baseline'))
  for c in rows(db,'campaigns'):
   i=c['bet_index']; db.execute('INSERT OR IGNORE INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?)',(c['id'],c['id'],c['objective'] or 'Custom hypothesis', ['Randomized holdout','Group holdout','Staggered campus rollout','Randomized holdout','Capacity-verified pilot'][i] if i is not None else 'Custom pilot',SIGNALS[i] if i is not None else 'Custom signal',[400,100,5,150,20][i] if i is not None else 1,c['signal'],c['sample'],c['guardrail'],'Planned'))
@app.middleware('http')
async def database(request, call_next):
 password=os.environ.get('SHOWUP_PASSWORD')
 if password and request.url.path!='/health':
  auth=request.headers.get('authorization',''); valid=False
  if auth.startswith('Basic '):
   try: valid=secrets.compare_digest(base64.b64decode(auth[6:],validate=True).decode(), 'reviewer:'+password)
   except (ValueError,UnicodeError): pass
  if not valid:return JSONResponse({'error':'Authentication required'},status_code=401,headers={'WWW-Authenticate':'Basic realm="SHOWUP OS"'})
 if request.method=='POST' and request.headers.get('origin') and request.headers['origin'].split('://')[-1]!=request.headers.get('host'):return JSONResponse({'error':'Cross-origin writes denied'},status_code=403)
 db=connect(); token=ctx.set(db)
 try: return await call_next(request)
 finally: db.close();ctx.reset(token)
def getdb():return ctx.get()
def rows(db,t):return [dict(x) for x in db.execute('SELECT * FROM '+t)]
def audit(db,action,entity,data):db.execute('INSERT INTO audit(action,entity,payload) VALUES (?,?,?)',(action,entity,json.dumps(data)))
@app.exception_handler(ValueError)
@app.exception_handler(sqlite3.IntegrityError)
async def bad(request,e):return JSONResponse({"error":str(e)},status_code=400)
@app.get('/')
def index():return FileResponse(ROOT/'index.html')
@app.get('/api/state')
def state():
 db=getdb(); cs=rows(db,'campaigns'); settings={**DEFAULTS,**json.loads(db.execute('SELECT value FROM settings').fetchone()[0])}; fs={s:forecast(cs,settings,s) for s in SCENARIOS}; by={x['id']:x for x in fs['base']['details']};
 for c in cs:c.update(projected=by[c['id']],decision=('CHANGE' if decision(c,by[c['id']]['projected_cpa'])=='SCALE' and not fs['base']['feasible'] else decision(c,by[c['id']]['projected_cpa'])),actual=db.execute('SELECT COUNT(DISTINCT person_key) FROM outcomes WHERE campaign_id=? AND verified=1',(c['id'],)).fetchone()[0])
 return jsonify(experiments=rows(db,'experiments'),scenarios=[{k:v for k,v in r.items() if k not in ('settings_json','campaigns_json')} for r in rows(db,'scenarios')],campaigns=cs,tasks=rows(db,'tasks'),settings=settings,forecasts=fs,simulated_actual=db.execute('SELECT COUNT(DISTINCT person_key) FROM outcomes WHERE verified=1').fetchone()[0],audit=rows(db,'audit')[-20:])
@app.get('/api/history')
def history(date:str='2025-04-20',campaign:str='all'):
 db=getdb();day=date; h=snapshot(db,day,campaign); campaigns=[dict(r) for r in db.execute('SELECT campaign_id,campaign_name FROM historical_campaigns')]; breakdown=[dict(c,**snapshot(db,day,c['campaign_id'])) for c in campaigns]; dates=['2025-01-31','2025-02-28','2025-03-14','2025-03-15','2025-03-31','2025-04-11','2025-04-13','2025-04-20']; return jsonify(summary=h,diagnostics=historical_diagnostics(h,breakdown,day),campaigns=breakdown,trend=[dict(date=d,**snapshot(db,d,campaign)) for d in dates if d<=day])
@app.post('/api/campaigns')
def campaign(x:dict):
 db=getdb(); id=x.get('id') or uuid.uuid4().hex[:10]; old=db.execute('SELECT * FROM campaigns WHERE id=?',(id,)).fetchone(); defaults=dict(name='',owner='',segment='',objective='',status='Planned',budget=0,spend=0,expected=0,hours=0,bet_index=None,signal=None,sample=0,guardrail=1,weeks=''); data={**defaults,**(dict(old) if old else {}),**{k:v for k,v in x.items() if k in defaults and k!='bet_index'}}
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',str(id)):raise ValueError('Invalid campaign ID')
 if not str(data['name']).strip():raise ValueError('Campaign name required')
 for k in ['budget','spend','expected','hours','sample']:
  data[k]=float(data[k]);
  if not math.isfinite(data[k]) or data[k]<0:raise ValueError('Nonnegative finite values required')
 data['guardrail']=int(data['guardrail']);
 if data['guardrail'] not in (0,1) or not data['sample'].is_integer():raise ValueError('Invalid sample or guardrail')
 if data['status'] not in ['Planned','Testing','Active','Paused','Stopped','Completed']:raise ValueError('Invalid campaign status')
 if data['signal'] not in (None,''):data['signal']=float(data['signal']);
 else:data['signal']=None
 if data['signal'] is not None and not math.isfinite(data['signal']):raise ValueError('Finite signal required')
 with db:
  cols=list(defaults);db.execute('INSERT INTO campaigns(id,'+','.join(cols)+') VALUES ('+','.join('?' for _ in range(len(cols)+1))+') ON CONFLICT(id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in cols),(id,*[data[k] for k in cols]));audit(db,'campaign.save',id,data); db.execute('INSERT OR IGNORE INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?)',(id,id,data['objective'] or 'Custom hypothesis','Custom pilot','Custom signal',1,None,0,1,'Planned')); db.execute('UPDATE experiments SET signal=?,sample=?,guardrail=? WHERE campaign_id=?',(data['signal'],data['sample'],data['guardrail'],id))
 return jsonify(id=id)
@app.post('/api/tasks')
def task(x:dict):
 db=getdb();id=x.get('id') or uuid.uuid4().hex[:10];old=db.execute('SELECT * FROM tasks WHERE id=?',(id,)).fetchone(); cols=['campaign_id','title','owner','priority','deadline','status','impact','criteria','insight'];d={**dict.fromkeys(cols,''),**(dict(old) if old else {}),**x};
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',str(id)):raise ValueError('Invalid task ID')
 if not d['title'].strip():raise ValueError('Task title required')
 if d['status'] not in ['Open','In progress','Done']:raise ValueError('Invalid task status')
 date.fromisoformat(d['deadline']);
 with db:db.execute('INSERT INTO tasks(id,campaign_id,title,owner,priority,deadline,status,impact,criteria,insight) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in cols),(id,*[d[k] for k in cols]));audit(db,'task.save',id,d)
 return jsonify(id=id)
@app.post('/api/settings')
def settings(x:dict):
 db=getdb(); d={**DEFAULTS,**json.loads(db.execute('SELECT value FROM settings').fetchone()[0]),**{k:v for k,v in x.items() if k in DEFAULTS}};
 for k in DEFAULTS:
  d[k]=float(d[k]);
  if not math.isfinite(d[k]) or d[k]<0:raise ValueError('Invalid setting')
 if not 0<=d['conversion_rate']<=1 or not 0<=d['showup_rate']<=1:raise ValueError('Rates must be between 0 and 1')
 if d['conversion_factor']>2 or d['showup_factor']>2: raise ValueError('Sensitivity factors must be between 0 and 2')
 if not 0<d['peak_share']<=1 or not 0<d['arrival_share']<=1 or d['dwell']<=0 or d['arrival_window']<=0:raise ValueError('Invalid capacity inputs')
 with db:db.execute('UPDATE settings SET value=?',(json.dumps(d),));audit(db,'settings.save','1',d)
 return jsonify(ok=True)
@app.post('/api/outcomes')
def outcome(x:dict):
 db=getdb(); p=str(x.get('person_key','')).strip();
 if not p:raise ValueError('Synthetic person ID required')
 if type(x.get('verified')) is not bool:raise ValueError('Verified must be boolean')
 with db:db.execute('INSERT INTO outcomes VALUES (?,?,?) ON CONFLICT(person_key) DO UPDATE SET campaign_id=excluded.campaign_id,verified=excluded.verified',(p,x['campaign_id'],int(bool(x['verified']))));audit(db,'outcome.save',p,x)
 return jsonify(ok=True)
@app.get('/health')
def health():return {'status':'ok'}
@app.post('/api/experiments')
def experiment(x:dict):
 db=getdb(); cid=x.get('campaign_id'); old=db.execute('SELECT * FROM experiments WHERE campaign_id=?',(cid,)).fetchone()
 if not old:raise ValueError('Unknown campaign')
 d={**dict(old),**{k:v for k,v in x.items() if k in ['hypothesis','method','status','signal','sample','guardrail']}}
 d['sample']=int(d['sample']); d['guardrail']=int(d['guardrail']); d['signal']=None if d['signal'] in ('',None) else float(d['signal'])
 if d['sample']<0 or d['guardrail'] not in (0,1) or (d['signal'] is not None and not math.isfinite(d['signal'])) or d['status'] not in ['Planned','Running','Complete']:raise ValueError('Invalid experiment')
 with db:
  db.execute('UPDATE experiments SET hypothesis=?,method=?,status=?,signal=?,sample=?,guardrail=? WHERE campaign_id=?',tuple(d[k] for k in ['hypothesis','method','status','signal','sample','guardrail'])+(cid,));db.execute('UPDATE campaigns SET signal=?,sample=?,guardrail=? WHERE id=?',(d['signal'],d['sample'],d['guardrail'],cid));audit(db,'experiment.save',cid,d)
 return {'ok':True}
@app.post('/api/preview')
def preview(x:dict):
 db=getdb(); settings={**DEFAULTS,**json.loads(db.execute('SELECT value FROM settings').fetchone()[0])}; settings.update({k:float(v) for k,v in x.get('settings',{}).items() if k in DEFAULTS}); cs=rows(db,'campaigns')
 for c in cs:
  if c['id']==x.get('campaign_id'):c['budget']=float(x.get('campaign_budget',c['budget']))
 if any(not math.isfinite(v) or v<0 for v in settings.values()) or not 0<settings['peak_share']<=1 or not 0<settings['arrival_share']<=1 or settings['dwell']<=0 or settings['arrival_window']<=0 or max(settings['conversion_rate'],settings['showup_rate'])>1 or max(settings['conversion_factor'],settings['showup_factor'])>2 or any(not math.isfinite(c['budget']) or c['budget']<0 for c in cs):raise ValueError('Invalid scenario inputs')
 return {'forecasts':{k:forecast(cs,settings,k) for k in SCENARIOS},'settings':settings,'campaigns':cs}
@app.post('/api/plan')
def apply_plan(x:dict):
 db=getdb(); p=preview(x); cid=x.get('campaign_id')
 with db:
  db.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(p['settings']),))
  if cid:
   c=next((c for c in p['campaigns'] if c['id']==cid),None)
   if not c:raise ValueError('Unknown campaign')
   db.execute('UPDATE campaigns SET budget=? WHERE id=?',(c['budget'],cid))
  audit(db,'plan.apply','1',{'settings':p['settings'],'campaign_id':cid,'campaign_budget':x.get('campaign_budget')})
 return {'ok':True,'forecasts':p['forecasts']}
@app.post('/api/scenarios')
def scenario_save(x:dict):
 db=getdb(); name=str(x.get('name','')).strip()
 if not name:raise ValueError('Scenario name required')
 p=preview(x); id=uuid.uuid4().hex[:10]
 with db:db.execute('INSERT INTO scenarios(id,name,settings_json,campaigns_json) VALUES (?,?,?,?)',(id,name,json.dumps(p['settings']),json.dumps(p['campaigns'])));audit(db,'scenario.save',id,{'name':name})
 return {'id':id}
@app.get('/api/scenarios/{id}')
def scenario_read(id:str):
 r=getdb().execute('SELECT * FROM scenarios WHERE id=?',(id,)).fetchone()
 if not r:raise ValueError('Unknown scenario')
 s=json.loads(r['settings_json']);c=json.loads(r['campaigns_json']);return {'name':r['name'],'forecasts':{k:forecast(c,s,k) for k in SCENARIOS},'settings':s,'campaigns':c}
init()
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('PORT',8000)))
