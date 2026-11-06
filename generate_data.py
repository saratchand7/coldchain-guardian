"""Deterministic synthetic starter/full dataset generator."""
import argparse,csv,hashlib,json,random
from datetime import datetime,timedelta,timezone
from config import BASE_DIR,GENERATED,DATABASE
from database import initialize,transaction,connect
from generator.master_data import PRODUCTS,insert_master_data
from generator.shipment_generator import shipment_id,trip_end,route_time,final_status
from generator.reading_generator import should_drop,normal_value,reading_id
from generator.anomaly_injector import injected_value,quality

def sha256_file(path):
 digest=hashlib.sha256()
 with path.open('rb') as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
 return digest.hexdigest()

def export_csv_table(db,table):
 cur=db.execute('SELECT * FROM "'+table+'" ORDER BY 1');out=GENERATED/(table.lower()+'.csv');fields=[x[0] for x in cur.description];rows=0
 with out.open('w',newline='',encoding='utf8') as f:
  writer=csv.writer(f);writer.writerow(fields)
  while batch:=cur.fetchmany(10000):writer.writerows(batch);rows+=len(batch)
 return {'file':out.name,'rows':rows,'sha256':sha256_file(out)}

def main():
 p=argparse.ArgumentParser();p.add_argument('--scale',choices=['starter','full'],default='starter');p.add_argument('--seed',type=int,default=20261022);p.add_argument('--no-detect',action='store_true',help='skip the normal post-generation detector pass');p.add_argument('--sql',action='store_true',help='also create a portable SQL seed file');a=p.parse_args();a.detect=not a.no_detect;rng=random.Random(a.seed);initialize();GENERATED.mkdir(parents=True,exist_ok=True)
 count=12 if a.scale=='starter' else 1000; nc=6 if a.scale=='starter' else 300
 with transaction() as db:
  for table in ('CorrectiveAction','Notification','Excursion','LifecycleEvent','CustodyEvent','SensorReading','RouteStop','ContainerAssignment','Sensor','Container','Shipment','ThresholdPolicy','Product'):db.execute(f'DELETE FROM {table}')
  insert_master_data(db,nc)
  db.execute('CREATE TEMP TABLE StagingSensorReading AS SELECT * FROM SensorReading WHERE 0')
  anomaly_updates=[];gap_deletes=[];failed_sensors=[]
  base=datetime(2026,10,1,tzinfo=timezone.utc);truth=[];available=[base for _ in range(nc)];shipment_starts={}
  for i in range(1,count+1):
   sid=shipment_id(i);prod=PRODUCTS[(i-1)%len(PRODUCTS)];container_idx=(i-1)%nc;c=f'CNT-{container_idx+1:06d}';start=available[container_idx];duration=rng.randint(48,120);end=trip_end(start,duration)
   active=i>count-nc;delayed=(i%17==0 or (a.scale=='starter' and i==count//2)) and not active;actual_end=None if active else (end+timedelta(hours=2) if delayed else end);available[container_idx]=(actual_end or end)+timedelta(hours=1);shipment_starts[sid]=start
   status=final_status(active,delayed)
   db.execute('INSERT INTO Shipment VALUES(?,?,?,?,?,?,?,?,?)',(sid,prod[0],'Origin Hub','Destination Hub',start.isoformat(),end.isoformat(),start.isoformat(),actual_end.isoformat() if actual_end else None,status));db.execute('INSERT INTO ContainerAssignment VALUES(?,?,?,?,?)',(f'ASN-{i:06d}',c,sid,start.isoformat(),actual_end.isoformat() if actual_end else None))
   lifecycle=[('READY',start-timedelta(minutes=10)),('IN_TRANSIT',start)]
   if delayed:lifecycle.append(('DELAYED',end+timedelta(hours=1)))
   if not active:lifecycle.append(('DELIVERED',actual_end))
   prior='CREATED'
   for event_no,(new_status,when) in enumerate(lifecycle,1):
    event_id=f'LCY-{sid}-{event_no:03d}'
    db.execute('INSERT INTO LifecycleEvent VALUES(?,?,?,?,?,?,?)',(event_id,sid,prior,new_status,when.isoformat(),'Destination Hub' if new_status=='DELIVERED' else 'Origin Hub','Synthetic generated lifecycle event'));prior=new_status
   observed_end=actual_end if actual_end else start+timedelta(seconds=int((end-start).total_seconds()//300*0.65)*300)
   for n in range(1,5):
    t=route_time(start,end,n);observed=t<=observed_end;actual_stop=t+timedelta(hours=2) if delayed and n==4 else (t if observed else None)
    stop_status='PLANNED' if not observed else ('DELAYED' if delayed and n==4 else 'COMPLETED')
    db.execute('INSERT INTO RouteStop VALUES(?,?,?,?,?,?,?)',(f'STP-{i:06d}-{n}',sid,n,f'Hub {n}',t.isoformat(),actual_stop.isoformat() if actual_stop else None,stop_status))
   for k in ('TEMP','HUM'):
    sensor=f'SNS-{c}-{k}';lo,hi=(prod[2],prod[3]) if k=='TEMP' else (prod[4],prod[5]);t=start;idx=0;reading_batch=[]
    while t<=observed_end:
     baseline=normal_value(rng,lo,hi);rid=reading_id(sensor,t)
     reading_batch.append((rid,sensor,sid,t.isoformat(),round(baseline,2),'C' if k=='TEMP' else '%','VALID'))
     if (i%7==0 and 30<=idx<=31) or ((i%19==0 or i==12) and 60<=idx<=73) or (i==1 and k=='TEMP' and idx==45):
      value=injected_value(baseline,i,k,idx,hi);anomaly_updates.append((round(value,2),quality(value,k),rid))
     if should_drop(i,idx) or (a.scale=='starter' and i==count and 80<idx<84):gap_deletes.append((rid,))
     if i==1 and k=='TEMP' and idx==45:pass
     if len(reading_batch)>=1000:db.executemany('INSERT INTO StagingSensorReading VALUES(?,?,?,?,?,?,?)',reading_batch);reading_batch.clear()
     t+=timedelta(minutes=5);idx+=1
    if reading_batch:db.executemany('INSERT INTO StagingSensorReading VALUES(?,?,?,?,?,?,?)',reading_batch)
   if container_idx==1:failed_sensors.append((f'SNS-{c}-TEMP',))
   previous=None
   last_custody=3 if active else 4
   for event_no in range(0,last_custody+1):
    when=start if event_no==0 else start+(end-start)*event_no/5
    custodian='Origin Hub' if event_no==0 else (('Destination Hub' if event_no==4 else f'Carrier {event_no}'))
    event_type='DEPARTURE' if event_no==0 else ('DELIVERY' if event_no==4 else 'CUSTODY_TRANSFER')
    db.execute('INSERT INTO CustodyEvent VALUES(?,?,?,?,?,?,?,?)',(f'CST-{i:06d}-{event_no+1:03d}',sid,previous,custodian,event_type,custodian,when.isoformat(),'Synthetic custody record'));previous=custodian
   if i%7==0:
    for k in ('TEMP','HUM'):truth.append({'case_id':f'GT-{i:06d}-{k}','scenario':'brief_excursion','shipment_id':sid,'sensor_id':f'SNS-{c}-{k}','expected':'detected','expected_kind':k,'recorded_at':(start+timedelta(minutes=150)).isoformat(),'input_json':''})
   if i%19==0 or i==12:
    for k in ('TEMP','HUM'):truth.append({'case_id':f'GT-{i:06d}-PROLONGED-{k}','scenario':'prolonged_excursion','shipment_id':sid,'sensor_id':f'SNS-{c}-{k}','expected':'detected','expected_kind':k,'recorded_at':(start+timedelta(minutes=300)).isoformat(),'input_json':''})
   if i%13==0 or (a.scale=='starter' and i==count):
    for k in ('TEMP','HUM'):truth.append({'case_id':f'GT-{i:06d}-GAP-{k}','scenario':'sensor_dropout','shipment_id':sid,'sensor_id':f'SNS-{c}-{k}','expected':'detected','expected_kind':'SENSOR_GAP','recorded_at':(start+timedelta(minutes=405)).isoformat()})
   if i==1:truth.append({'case_id':'GT-NEG-1','scenario':'physical_range','shipment_id':sid,'sensor_id':f'SNS-{c}-TEMP','expected':'detected','expected_kind':'INVALID_PHYSICAL_RANGE','recorded_at':(start+timedelta(minutes=225)).isoformat()})
   if container_idx==1:truth.append({'case_id':f'GT-{i:06d}-CAL','scenario':'calibration_failure','shipment_id':sid,'sensor_id':f'SNS-{c}-TEMP','expected':'detected','expected_kind':'CALIBRATION_FAILURE','recorded_at':start.isoformat()})
   if delayed and not active:truth.append({'case_id':f'GT-{i:06d}-DELAY','scenario':'route_delay','shipment_id':sid,'sensor_id':'','expected':'detected','expected_kind':'ROUTE_DELAY','recorded_at':(start+(end-start)*4/5).isoformat()})
  db.execute('CREATE UNIQUE INDEX idx_staging_reading_id ON StagingSensorReading(reading_id)')
  db.executemany('UPDATE StagingSensorReading SET value=?,quality=? WHERE reading_id=?',anomaly_updates)
  db.executemany('DELETE FROM StagingSensorReading WHERE reading_id=?',gap_deletes)
  db.executemany("UPDATE Sensor SET calibration_status='FAILED' WHERE sensor_id=?",failed_sensors)
  db.execute('INSERT INTO SensorReading SELECT * FROM StagingSensorReading ORDER BY shipment_id,recorded_at,sensor_id')
  db.execute('DROP TABLE StagingSensorReading')
  duration_start=shipment_starts[shipment_id(7)]+timedelta(minutes=150)
  sensor7='SNS-CNT-000001-TEMP';duration_readings=[reading_id(sensor7,duration_start+timedelta(minutes=n)) for n in (0,5,10)]
  truth.extend([
   {'case_id':'NEG-TIMELINE','scenario':'delivered_before_departure','shipment_id':shipment_id(1),'sensor_id':'','expected':'rejected','expected_kind':'INVALID_SHIPMENT_TIMELINE','recorded_at':'','input_json':json.dumps({'current_status':'CREATED','attempted_status':'DELIVERED','actual_departure':None})},
   {'case_id':'NEG-POLICY','scenario':'wrong_product_threshold','shipment_id':shipment_id(1),'sensor_id':'','expected':'rejected','expected_kind':'POLICY_PRODUCT_MISMATCH','recorded_at':'','input_json':json.dumps({'shipment_product_id':'PRD-001','policy_id':'POL-PRD-002','policy_product_id':'PRD-002'})},
   {'case_id':'NEG-DURATION','scenario':'excursion_duration_undercount','shipment_id':shipment_id(7),'sensor_id':sensor7,'expected':'caught','expected_kind':'DURATION_UNDERCOUNT','recorded_at':duration_start.isoformat(),'input_json':json.dumps({'source_reading_ids':duration_readings,'reported_duration_minutes':5,'calculated_duration_minutes':10})}
  ])
  with (GENERATED/'ground_truth.csv').open('w',newline='',encoding='utf8') as f:
   for row in truth:row.setdefault('input_json','')
   fields=['case_id','scenario','shipment_id','sensor_id','expected','expected_kind','recorded_at','input_json'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(truth)
  (GENERATED/'ground_truth.json').write_text(json.dumps(truth,indent=2,sort_keys=True),encoding='utf8')
  files=[]
  for table in ('Product','Shipment','Container','ContainerAssignment','Sensor','SensorReading','RouteStop','ThresholdPolicy','CustodyEvent','LifecycleEvent'):
   files.append(export_csv_table(db,table))
 if a.scale=='starter':
  compact=connect();compact.execute('VACUUM');compact.close()
 detected=0
 if a.detect:
  from services.engine import detect_excursions
  db=connect();ids=[r[0] for r in db.execute('SELECT shipment_id FROM Shipment ORDER BY shipment_id')]
  for sid in ids: detected+=len(detect_excursions(db,sid))
  db.close()
 db=connect()
 for table in ('Excursion','CorrectiveAction','Notification'):files.append(export_csv_table(db,table))
 db.close()
 files.sort(key=lambda item:item['file'])
 truth_manifest=[{'file':name,'rows':len(truth),'sha256':sha256_file(GENERATED/name)} for name in ('ground_truth.csv','ground_truth.json')]
 manifest={'seed':a.seed,'scale':a.scale,'products':len(PRODUCTS),'shipments':count,'containers':nc,'generator_version':'1.2','files':files,'ground_truth_files':truth_manifest}
 (BASE_DIR/'data'/'source_manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding='utf8')
 from services.validation import write_reports
 sql_export=None
 if a.sql:
  from generator.sql_seed import export_sql_seed
  sql_export=export_sql_seed()
 else:
  (GENERATED/'seed.sql').unlink(missing_ok=True)
 validation=write_reports()
 db=connect()
 counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('Product','Shipment','Container','Sensor','SensorReading','Excursion','CorrectiveAction','CustodyEvent','LifecycleEvent')}
 db.close()
 summary={'scale':a.scale,'seed':a.seed,'generator_version':'1.2','counts':counts,'validation_status':validation['status'],'sql_export':sql_export}
 (GENERATED/'generation_summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True),encoding='utf8')
 print(json.dumps({**summary,'excursions_detected':detected},indent=2))
if __name__=='__main__':main()
