import sqlite3, datetime, json, sys, re
from pathlib import Path
import openpyxl
TABLES={'Event':'event','Targets':'targets','Campaigns':'historical_campaigns','Daily Marketing':'marketing','People':'people','Interest and Engagement':'engagement','Registrations':'registrations','Registration History':'registration_history','Check-ins':'checkins','Read me':'source_readme','Field guide':'field_guide'}
KEYS={'historical_campaigns':'campaign_id','marketing':'activity_id','people':'person_id','engagement':'interaction_id','registrations':'registration_id','registration_history':'history_id','checkins':'event_id'}
def run(src, dest):
 w=openpyxl.load_workbook(src,read_only=True,data_only=True); db=sqlite3.connect(dest); audit={}
 for sheet,table in TABLES.items():
  rows=iter(w[sheet].values); cols=next(rows); cols=[str(x) for x in cols if x is not None]; n=len(cols); data=[]
  for row in rows:
   if not any(x is not None for x in row):continue
   data.append(tuple(x.isoformat(sep=' ') if isinstance(x,datetime.datetime) else x for x in row[:n]))
  key=KEYS.get(table); seen={}; clean=[]
  for r in data:
   if key:
    k=r[cols.index(key)]
    if k in seen:
     if seen[k]!=r:raise ValueError(f'Conflicting ID {table}:{k}')
     continue
    seen[k]=r
   clean.append(r)
  types=['REAL' if all(r[i] is None or isinstance(r[i],(int,float)) for r in data) else 'TEXT' for i in range(n)]
  schema=', '.join('"'+c+'" '+t+(' PRIMARY KEY' if c==key else '') for c,t in zip(cols,types))
  for name,rs,sch in [('raw_'+table,data,', '.join('"'+c+'" '+t for c,t in zip(cols,types))),(table,clean,schema)]:
   db.execute(f'CREATE TABLE IF NOT EXISTS {name} ({sch})');db.execute(f'DELETE FROM {name}');db.executemany(f'INSERT INTO {name} VALUES ({",".join("?" for _ in cols)})',rs)
  audit[table]={'raw':len(data),'clean':len(clean),'duplicates':len(data)-len(clean)}
 db.commit();db.close(); print(json.dumps(audit,indent=2));return audit
if __name__=='__main__':run(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else 'showup.db')
