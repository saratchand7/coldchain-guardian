"""Deterministic synthetic starter/full dataset generator."""
import argparse,csv,hashlib,json,random,platform,subprocess,sys,uuid
from datetime import datetime,timedelta,timezone
from config import BASE_DIR,GENERATED,DATABASE,MANIFEST,EXPECTED_INTERVAL_MINUTES
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

def export_json_table(db,table):
 cur=db.execute('SELECT * FROM "'+table+'" ORDER BY 1');out=GENERATED/(table.lower()+'.json');rows=0
 with out.open('w',encoding='utf8',newline='\n') as f:
  f.write('[');first=True
  while batch:=cur.fetchmany(10000):
   for row in batch:
    if not first:f.write(',')
    f.write(json.dumps(dict(row),sort_keys=True,separators=(',',':'),default=str));first=False;rows+=1
  f.write(']\n')
 return {'file':out.name,'rows':rows,'sha256':sha256_file(out),'format':'json'}

RUN_STAGE='STARTUP'

def _write_run_status(status,run_id,stage,**extra):
 MANIFEST.parent.mkdir(parents=True,exist_ok=True)
 payload={'status':status,'run_id':run_id,'stage':stage,**extra}
 (MANIFEST.parent/'run_status.json').write_text(json.dumps(payload,indent=2,sort_keys=True),encoding='utf8',newline='\n')

def _run(run_id):
 global RUN_STAGE
 p=argparse.ArgumentParser();p.add_argument('--scale',choices=['starter','full'],default='starter');p.add_argument('--seed',type=int,default=20261022);p.add_argument('--as-of',help='explicit UTC observation horizon for active journeys (ISO 8601)');p.add_argument('--no-detect',action='store_true',help='skip the normal post-generation detector pass');p.add_argument('--sql',action='store_true',help='also create a portable SQL seed file');p.add_argument('--json',action='store_true',help='also export each generated table as JSON');a=p.parse_args();a.detect=not a.no_detect;rng=random.Random(a.seed);RUN_STAGE='RESETTING_DATABASE';initialize();GENERATED.mkdir(parents=True,exist_ok=True);MANIFEST.parent.mkdir(parents=True,exist_ok=True)
 count=12 if a.scale=='starter' else 1000; nc=6 if a.scale=='starter' else 300
 with transaction() as db:
  for table in ('CorrectiveActionEvent','CorrectiveAction','ExcursionStatusEvent','ExcursionRevision','Notification','Excursion','DetectionAnalysis','LifecycleEvent','CustodyEvent','SensorReading','SensorCalibrationHistory','RouteStop','MonitoringWindow','ContainerAssignment','Sensor','Container','Shipment','ThresholdPolicy','Product'):db.execute(f'DELETE FROM {table}')
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
   db.execute('INSERT INTO MonitoringWindow VALUES(?,?,?,?,?)',(sid,start.isoformat(),observed_end.isoformat(),observed_end.isoformat(),'fixed-seed generated observation horizon'))
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
    if event_no==4: when=actual_end
    db.execute('INSERT INTO CustodyEvent VALUES(?,?,?,?,?,?,?,?)',(f'CST-{i:06d}-{event_no+1:03d}',sid,previous,custodian,event_type,custodian,when.isoformat(),'Synthetic custody record'));previous=custodian
   if i%7==0:
    for k in ('TEMP','HUM'):
     if not (container_idx==1 and k=='TEMP'):truth.append({'case_id':f'GT-{i:06d}-{k}','scenario':'brief_excursion','shipment_id':sid,'sensor_id':f'SNS-{c}-{k}','expected':'detected','expected_kind':k,'recorded_at':(start+timedelta(minutes=150)).isoformat(),'input_json':''})
   if i%19==0 or i==12:
    for k in ('TEMP','HUM'):
     if not (container_idx==1 and k=='TEMP'):truth.append({'case_id':f'GT-{i:06d}-PROLONGED-{k}','scenario':'prolonged_excursion','shipment_id':sid,'sensor_id':f'SNS-{c}-{k}','expected':'detected','expected_kind':k,'recorded_at':(start+timedelta(minutes=300)).isoformat(),'input_json':''})
   if i%13==0 or (a.scale=='starter' and i==count):
    for k in ('TEMP','HUM'):truth.append({'case_id':f'GT-{i:06d}-GAP-{k}','scenario':'sensor_dropout','shipment_id':sid,'sensor_id':f'SNS-{c}-{k}','expected':'detected','expected_kind':'SENSOR_GAP','recorded_at':(start+timedelta(minutes=405)).isoformat()})
   if i==1:
    truth.append({'case_id':'GT-NEG-1','scenario':'physical_range','shipment_id':sid,'sensor_id':f'SNS-{c}-TEMP','expected':'detected','expected_kind':'INVALID_PHYSICAL_RANGE','recorded_at':(start+timedelta(minutes=225)).isoformat()})
    truth.append({'case_id':'GT-NEG-1-GAP','scenario':'invalid_observation_gap','shipment_id':sid,'sensor_id':f'SNS-{c}-TEMP','expected':'detected','expected_kind':'SENSOR_GAP','recorded_at':(start+timedelta(minutes=225)).isoformat()})
   if container_idx==1:
    truth.append({'case_id':f'GT-{i:06d}-CAL','scenario':'calibration_failure','shipment_id':sid,'sensor_id':f'SNS-{c}-TEMP','expected':'detected','expected_kind':'CALIBRATION_FAILURE','recorded_at':start.isoformat()})
    truth.append({'case_id':f'GT-{i:06d}-CAL-GAP','scenario':'untrusted_calibration_coverage','shipment_id':sid,'sensor_id':f'SNS-{c}-TEMP','expected':'detected','expected_kind':'SENSOR_GAP','recorded_at':start.isoformat()})
   if delayed and not active:truth.append({'case_id':f'GT-{i:06d}-DELAY','scenario':'route_delay','shipment_id':sid,'sensor_id':'','expected':'detected','expected_kind':'ROUTE_DELAY','recorded_at':(start+(end-start)*4/5).isoformat()})
  db.execute('CREATE UNIQUE INDEX idx_staging_reading_id ON StagingSensorReading(reading_id)')
  db.executemany('UPDATE StagingSensorReading SET value=?,quality=? WHERE reading_id=?',anomaly_updates)
  db.executemany('DELETE FROM StagingSensorReading WHERE reading_id=?',gap_deletes)
  db.executemany("UPDATE Sensor SET calibration_status='FAILED' WHERE sensor_id=?",failed_sensors)
  for (sensor_id,) in set(failed_sensors):
   first_assignment=db.execute('SELECT MIN(a.started_at) FROM ContainerAssignment a JOIN Sensor s USING(container_id) WHERE s.sensor_id=?',(sensor_id,)).fetchone()[0]
   if first_assignment:
    db.execute('INSERT INTO SensorCalibrationHistory VALUES(?,?,?,?,?,?)',
               (f'CAL-{sensor_id}-002',sensor_id,first_assignment,'FAILED',first_assignment,'Synthetic calibration failure'))
  db.execute('INSERT INTO SensorReading SELECT * FROM StagingSensorReading ORDER BY shipment_id,recorded_at,sensor_id')
  db.execute('DROP TABLE StagingSensorReading')
  # Open assignments represent containers currently in use; completed journeys release them.
  db.execute("UPDATE Container SET status='AVAILABLE'")
  db.execute("UPDATE Container SET status='IN_USE' WHERE container_id IN (SELECT container_id FROM ContainerAssignment WHERE ended_at IS NULL)")
  duration_shipment=shipment_id(7)
  duration_container=f'CNT-{((7-1)%nc)+1:06d}'
  sensor7=f'SNS-{duration_container}-TEMP'
  duration_start=shipment_starts[duration_shipment]+timedelta(minutes=150)
  duration_readings=[r[0] for r in db.execute('SELECT reading_id FROM SensorReading WHERE shipment_id=? AND sensor_id=? AND recorded_at>=? AND recorded_at<=? ORDER BY recorded_at LIMIT 3',
                       (duration_shipment,sensor7,duration_start.isoformat(),(duration_start+timedelta(minutes=10)).isoformat()))]
  duration_times=[r[0] for r in db.execute('SELECT recorded_at FROM SensorReading WHERE reading_id IN (?,?,?) ORDER BY recorded_at',duration_readings)] if len(duration_readings)==3 else []
  calculated_duration=(datetime.fromisoformat(duration_times[-1])-datetime.fromisoformat(duration_times[0])).total_seconds()/60 if duration_times else 0
  truth.extend([
   {'case_id':'NEG-TIMELINE','scenario':'delivered_before_departure','shipment_id':shipment_id(1),'sensor_id':'','expected':'rejected','expected_kind':'INVALID_SHIPMENT_TIMELINE','recorded_at':'','input_json':json.dumps({'current_status':'CREATED','attempted_status':'DELIVERED','actual_departure':None})},
   {'case_id':'NEG-POLICY','scenario':'wrong_product_threshold','shipment_id':shipment_id(1),'sensor_id':'','expected':'rejected','expected_kind':'POLICY_PRODUCT_MISMATCH','recorded_at':'','input_json':json.dumps({'shipment_product_id':'PRD-001','policy_id':'POL-PRD-002','policy_product_id':'PRD-002'})},
   {'case_id':'NEG-DURATION','scenario':'excursion_duration_undercount','shipment_id':duration_shipment,'sensor_id':sensor7,'expected':'caught','expected_kind':'DURATION_UNDERCOUNT','recorded_at':duration_start.isoformat(),'input_json':json.dumps({'source_reading_ids':duration_readings[:2],'recovery_reading_id':duration_readings[2] if len(duration_readings)>2 else None,'reported_duration_minutes':5,'calculated_duration_minutes':calculated_duration})}
  ])
  with (GENERATED/'ground_truth.csv').open('w',newline='',encoding='utf8') as f:
   for row in truth:row.setdefault('input_json','')
   fields=['case_id','scenario','shipment_id','sensor_id','expected','expected_kind','recorded_at','input_json'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(truth)
  (GENERATED/'ground_truth.json').write_text(json.dumps(truth,indent=2,sort_keys=True),encoding='utf8')
  files=[]
  exported_tables=('Product','Shipment','Container','Sensor','SensorReading','ContainerAssignment','RouteStop','ThresholdPolicy','Excursion','CustodyEvent','LifecycleEvent','CorrectiveAction','Notification','SensorCalibrationHistory','ExcursionRevision','ExcursionStatusEvent','CorrectiveActionEvent')
  for table in exported_tables:
   if table in ('Excursion','CorrectiveAction','Notification','ExcursionRevision','ExcursionStatusEvent','CorrectiveActionEvent'): continue
   files.append(export_csv_table(db,table))
   if a.json: files.append(export_json_table(db,table))
 if a.scale=='starter':
  compact=connect();compact.execute('VACUUM');compact.close()
 RUN_STAGE='DETECTION';detected=0
 if a.detect:
  from services.engine import detect_excursions
  db=connect();ids=[r[0] for r in db.execute('SELECT shipment_id FROM Shipment ORDER BY shipment_id')]
  for sid in ids:
   horizon=a.as_of or db.execute('SELECT observed_through FROM MonitoringWindow WHERE shipment_id=?',(sid,)).fetchone()[0]
   detected+=len(detect_excursions(db,sid,horizon))
  db.close()
 db=connect()
 # DetectionAnalysis contains operational completion timestamps and is intentionally
 # excluded from deterministic dataset artifacts/checksums.
 for table in ('MonitoringWindow','Excursion','CorrectiveAction','Notification','ExcursionRevision','ExcursionStatusEvent','CorrectiveActionEvent'):
  files.append(export_csv_table(db,table))
  if a.json: files.append(export_json_table(db,table))
 db.close()
 files.sort(key=lambda item:item['file'])
 truth_manifest=[{'file':name,'rows':len(truth),'sha256':sha256_file(GENERATED/name)} for name in ('ground_truth.csv','ground_truth.json')]
 sql_export=None
 if a.sql:
  from generator.sql_seed import export_sql_seed
  sql_export=export_sql_seed()
  if sql_export:
   sql_export['sha256']=sha256_file(GENERATED/sql_export['file'])
 else:
  (GENERATED/'seed.sql').unlink(missing_ok=True)
 try: source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=BASE_DIR,text=True,stderr=subprocess.DEVNULL).strip()
 except Exception: source_commit=None
 manifest={'seed':a.seed,'scale':a.scale,'products':len(PRODUCTS),'shipments':count,'containers':nc,
           'generator_version':'1.4','python_version':platform.python_version(),
           'interval_minutes':EXPECTED_INTERVAL_MINUTES,'synthetic_only':True,
           'timestamp_policy':'wall-clock generation time omitted for reproducibility','as_of':a.as_of,
           'source_commit':source_commit,'files':files,
           'ground_truth_files':truth_manifest,'sql_export':sql_export}
 MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding='utf8',newline='\n')
 RUN_STAGE='VALIDATION';_write_run_status('RUNNING',run_id,RUN_STAGE,seed=a.seed,scale=a.scale)
 from services.validation import write_reports
 validation=write_reports()
 db=connect()
 counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('Product','Shipment','Container','Sensor','SensorReading','Excursion','CorrectiveAction','CustodyEvent','LifecycleEvent')}
 db.close()
 summary={'scale':a.scale,'seed':a.seed,'generator_version':'1.4','counts':counts,'validation_status':validation['status'],'sql_export':sql_export}
 (GENERATED/'generation_summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True),encoding='utf8',newline='\n')
 if validation['status']!='PASS':
  _write_run_status('FAILED',run_id,'VALIDATION',seed=a.seed,scale=a.scale,reason='validation failed',validation_errors=validation['errors'])
  print(json.dumps({**summary,'excursions_detected':detected},indent=2));raise SystemExit(2)
 manifest['status']='COMPLETE'
 MANIFEST.write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding='utf8',newline='\n')
 RUN_STAGE='COMPLETE';_write_run_status('COMPLETE',run_id,RUN_STAGE,seed=a.seed,scale=a.scale,manifest=MANIFEST.name)
 print(json.dumps({**summary,'excursions_detected':detected},indent=2))

def main():
 global RUN_STAGE
 run_id=uuid.uuid4().hex
 try:
  pre=argparse.ArgumentParser(add_help=False);pre.add_argument('--as-of');known,_=pre.parse_known_args()
  if known.as_of:datetime.fromisoformat(known.as_of.replace('Z','+00:00'))
 except (ValueError,IndexError) as exc:
  _write_run_status('FAILED',run_id,'ARGUMENT_VALIDATION',reason=str(exc));raise SystemExit(2)
 _write_run_status('STARTED',run_id,'STARTUP')
 try:_run(run_id)
 except BaseException as exc:
  existing={}
  try:existing=json.loads((MANIFEST.parent/'run_status.json').read_text(encoding='utf8'))
  except Exception:pass
  if RUN_STAGE!='COMPLETE' and not (existing.get('run_id')==run_id and existing.get('status')=='FAILED'):
   _write_run_status('FAILED',run_id,RUN_STAGE,reason=str(exc) or type(exc).__name__)
  raise

if __name__=='__main__':main()
