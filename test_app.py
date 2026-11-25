import unittest, tempfile, shutil, os
from pathlib import Path
from fastapi.testclient import TestClient
import app as a
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.original=a.DB;a.DB=str(Path(self.tmp.name)/'test.db');shutil.copy(a.ROOT/'showup.db',a.DB);a.init();self.c=TestClient(a.app)
 def tearDown(self):a.DB=self.original;self.tmp.cleanup()
 def state(self):return self.c.get('/api/state').json()
 def post(self,path,data):
  r=self.c.post('/api/'+path,json=data);self.assertEqual(r.status_code,200,r.text);return r.json()
 def test_history(self):
  s=self.c.get('/api/history').json()['summary'];self.assertEqual((s['attendees'],s['registered'],s['confirmed'],s['spend']),(2050,2800,2500,274000));self.assertEqual(s['registered_attendees'],1925)
 def test_snapshot(self):
  s=self.c.get('/api/history?date=2025-04-11').json()['summary'];self.assertEqual((s['attendees'],s['spend'],s['identified']),(0,276500,3500));self.assertEqual(self.c.get('/api/history?date=2025-03-14').json()['summary']['target']['marketing_budget_inr'],240000)
 def test_forecasts(self):
  s=self.state()['forecasts'];self.assertEqual([s[k]['forecast'] for k in ['downside','base','stretch']],[2190,2420,2600]);self.assertEqual(s['base']['planned_budget'],300000)
 def test_changed_campaign(self):
  self.post('campaigns',{'id':'B3','signal':8,'sample':5});s=self.state();self.assertEqual(s['forecasts']['base']['forecast'],2356);self.assertEqual(next(c for c in s['campaigns'] if c['id']=='B3')['decision'],'STOP')
 def test_task_and_outcome(self):
  self.post('tasks',{'id':'T1','status':'Done'});self.assertEqual(self.state()['simulated_actual'],0);self.assertEqual(self.state()['tasks'][0]['status'],'Done');
  for _ in range(2):self.post('outcomes',{'person_key':'SIM1','campaign_id':'B3','verified':True})
  self.assertEqual(self.state()['simulated_actual'],1)
 def test_capacity(self):
  self.post('settings',{'peak_share':.8});self.assertEqual(self.state()['forecasts']['stretch']['forecast'],2375)
 def test_budget_alert(self):
  self.post('settings',{'budget':225000});self.assertFalse(self.state()['forecasts']['base']['feasible'])
 def test_create_campaign(self):
  id=self.post('campaigns',{'name':'Pilot X','budget':100,'expected':2,'hours':1})['id'];self.assertTrue(any(c['id']==id for c in self.state()['campaigns']))
 def test_invalid(self):self.assertEqual(self.c.post('/api/campaigns',json={'id':'B1','budget':-1}).status_code,400)
 def test_experiment(self):
  self.post('experiments',{'campaign_id':'B3','signal':8,'sample':5,'status':'Running'}); s=self.state(); self.assertEqual(s['forecasts']['base']['forecast'],2356); self.assertEqual(next(x for x in s['experiments'] if x['campaign_id']=='B3')['status'],'Running'); self.assertEqual(next(x for x in s['campaigns'] if x['id']=='B3')['signal'],8)
 def test_scenario(self):
  x={'settings':{'peak_share':.8},'name':'Peak stress'}; id=self.post('scenarios',x)['id']; self.assertEqual(self.c.get('/api/scenarios/'+id).json()['forecasts']['stretch']['forecast'],2375); self.assertEqual(self.state()['forecasts']['stretch']['forecast'],2600); self.assertEqual(self.state()['scenarios'][0]['name'],'Peak stress')
 def test_preview(self):
  p=self.post('preview',{'settings':{'showup_factor':.8,'conversion_factor':.9},'campaign_id':'B3','campaign_budget':11000}); self.assertLess(p['forecasts']['base']['forecast'],2420);self.assertEqual(self.state()['forecasts']['base']['forecast'],2420)
 def test_task_insight(self):
  id=self.post('tasks',{'campaign_id':'B3','title':'Review','owner':'Campus lead A','priority':'High','deadline':'2027-02-01','status':'Open','impact':'64 attendees','criteria':'Review records','insight':'Low yield'})['id'];self.post('tasks',{'id':id,'status':'Done'});self.assertEqual(next(t for t in self.state()['tasks'] if t['id']==id)['insight'],'Low yield');self.assertEqual(self.state()['simulated_actual'],0)
 def test_filter(self):
  s=self.c.get('/api/history').json(); c=s['campaigns'][0]; f=self.c.get('/api/history',params={'campaign':c['campaign_id']}).json()['summary'];self.assertEqual(f['attendees'],c['attendees'])
 def test_health(self):self.assertEqual(self.c.get('/health').json(),{'status':'ok'})
 def test_apply_plan(self):
  p=self.post('plan',{'settings':{'peak_share':.8},'campaign_id':'B3','campaign_budget':22000});self.assertEqual(p['forecasts']['stretch']['forecast'],2375);self.assertEqual(self.state()['forecasts']['stretch']['forecast'],2375);self.assertEqual(self.c.get('/api/history').json()['summary']['attendees'],2050)
 def test_auth(self):
  old=os.environ.get('SHOWUP_PASSWORD');os.environ['SHOWUP_PASSWORD']='demo-test-secret'
  try:
   self.assertEqual(self.c.get('/api/state').status_code,401);self.assertEqual(self.c.get('/health').status_code,200);self.assertEqual(self.c.get('/api/state',auth=('reviewer','demo-test-secret')).status_code,200)
  finally:
   if old is None:os.environ.pop('SHOWUP_PASSWORD',None)
   else:os.environ['SHOWUP_PASSWORD']=old
 def test_rate_validation(self):
  self.assertEqual(self.c.post('/api/preview',json={'settings':{'showup_rate':1.1}}).status_code,400);self.assertLess(self.post('preview',{'settings':{'showup_rate':.5}})['forecasts']['base']['forecast'],2420)
 def test_diagnostics(self):
  h=self.c.get('/api/history').json();self.assertEqual(h['diagnostics']['cohorts'][0]['attendees'],1215);self.assertEqual(h['diagnostics']['bottleneck']['count'],700)
if __name__=='__main__':unittest.main(verbosity=2)
