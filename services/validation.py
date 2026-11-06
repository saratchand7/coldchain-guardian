"""Independent database integrity checks and reproducible report exports."""
from database import connect
from pathlib import Path
import csv,json,hashlib
from datetime import datetime
from config import BASE_DIR,GENERATED,DATABASE

def validate(path=None,include_artifacts=False):
    db=connect(path) if path else connect()
    checks=[]
    fk=db.execute('PRAGMA foreign_key_check').fetchall()
    checks.append({'check':'foreign_keys','passed':not fk,'count':len(fk)})
    dup=db.execute('SELECT COUNT(*) FROM (SELECT reading_id FROM SensorReading GROUP BY reading_id HAVING COUNT(*)>1)').fetchone()[0]
    checks.append({'check':'reading_id_uniqueness','passed':dup==0,'count':dup})
    unassigned=db.execute("""SELECT COUNT(*) FROM SensorReading r JOIN Sensor s USING(sensor_id) WHERE NOT EXISTS (SELECT 1 FROM ContainerAssignment a WHERE a.shipment_id=r.shipment_id AND a.container_id=s.container_id AND a.started_at<=r.recorded_at AND (a.ended_at IS NULL OR a.ended_at>=r.recorded_at))""").fetchone()[0]
    checks.append({'check':'reading_sensor_shipment_assignment','passed':unassigned==0,'count':unassigned})
    outside=0
    for row in db.execute('SELECT r.recorded_at,s.actual_departure,s.actual_arrival,s.planned_departure,s.planned_arrival FROM SensorReading r JOIN Shipment s USING(shipment_id)'):
        start=row['actual_departure'] or row['planned_departure'];end=row['actual_arrival'] or row['planned_arrival']
        if row['recorded_at']<start or row['recorded_at']>end:outside+=1
    checks.append({'check':'reading_within_shipment_timeline','passed':outside==0,'count':outside})
    bad=db.execute("SELECT COUNT(*) FROM SensorReading WHERE quality='VALID' AND ((unit='C' AND (value < -80 OR value > 100)) OR (unit='%' AND (value < 0 OR value > 100)) OR (unit NOT IN ('C','%')))").fetchone()[0]
    checks.append({'check':'physical_ranges','passed':bad==0,'count':bad})
    invalid_unflagged=db.execute("SELECT COUNT(*) FROM SensorReading WHERE ((unit='C' AND (value < -80 OR value > 100)) OR (unit='%' AND (value < 0 OR value > 100))) AND quality!='INVALID_PHYSICAL_RANGE'").fetchone()[0]
    checks.append({'check':'invalid_readings_flagged','passed':invalid_unflagged==0,'count':invalid_unflagged})
    bad_quality=db.execute("SELECT COUNT(*) FROM SensorReading WHERE quality NOT IN ('VALID','INVALID_PHYSICAL_RANGE','INVALID_SENSOR','CALIBRATION_FAILURE')").fetchone()[0]
    checks.append({'check':'reading_quality_enum','passed':bad_quality==0,'count':bad_quality})
    invalid_units=db.execute("SELECT COUNT(*) FROM SensorReading WHERE unit NOT IN ('C','%')").fetchone()[0]
    checks.append({'check':'reading_unit_enum','passed':invalid_units==0,'count':invalid_units})
    dates=db.execute("SELECT COUNT(*) FROM Shipment WHERE planned_arrival<=planned_departure OR (actual_arrival IS NOT NULL AND actual_departure IS NOT NULL AND actual_arrival<actual_departure)").fetchone()[0]
    checks.append({'check':'shipment_timeline','passed':dates==0,'count':dates})
    stops=db.execute("SELECT COUNT(*) FROM (SELECT shipment_id,sequence_no,LAG(sequence_no) OVER(PARTITION BY shipment_id ORDER BY sequence_no) prior FROM RouteStop) WHERE prior IS NOT NULL AND sequence_no<=prior").fetchone()[0]
    checks.append({'check':'route_stop_sequence','passed':stops==0,'count':stops})
    policies=db.execute('SELECT COUNT(*) FROM ThresholdPolicy p JOIN Product x USING(product_id) WHERE p.temp_min>=p.temp_max OR p.humidity_min>=p.humidity_max').fetchone()[0]
    checks.append({'check':'threshold_policy_ranges','passed':policies==0,'count':policies})
    overlap=db.execute("""SELECT COUNT(*) FROM ThresholdPolicy a JOIN ThresholdPolicy b ON a.product_id=b.product_id AND a.policy_id<b.policy_id
      AND a.effective_from<COALESCE(b.effective_to,'9999-12-31T23:59:59+00:00')
      AND b.effective_from<COALESCE(a.effective_to,'9999-12-31T23:59:59+00:00')""").fetchone()[0]
    checks.append({'check':'threshold_effective_date_overlap','passed':overlap==0,'count':overlap})
    uncalibrated=db.execute("SELECT COUNT(*) FROM Sensor WHERE calibration_status!='VALID'").fetchone()[0]
    checks.append({'check':'sensor_calibration_warning','passed':True,'count':uncalibrated,'severity':'warning'})
    custody=db.execute("SELECT COUNT(*) FROM (SELECT shipment_id,occurred_at,LAG(occurred_at) OVER(PARTITION BY shipment_id ORDER BY occurred_at) prior FROM CustodyEvent) WHERE prior IS NOT NULL AND occurred_at<prior").fetchone()[0]
    checks.append({'check':'custody_chronology','passed':custody==0,'count':custody})
    life=db.execute('SELECT shipment_id,previous_status,new_status FROM LifecycleEvent ORDER BY shipment_id,occurred_at').fetchall()
    allowed={'CREATED':{'READY'},'READY':{'IN_TRANSIT'},'IN_TRANSIT':{'DELAYED','EXCEPTION','DELIVERED'},'DELAYED':{'IN_TRANSIT','EXCEPTION','DELIVERED'},'EXCEPTION':{'IN_TRANSIT','DELAYED','DELIVERED'},'DELIVERED':{'CLOSED'},'CLOSED':set()}
    bad_lifecycle=0;last={}
    for e in life:
        if e['new_status'] not in allowed.get(e['previous_status'],set()) or (e['shipment_id'] in last and last[e['shipment_id']]!=e['previous_status']):bad_lifecycle+=1
        last[e['shipment_id']]=e['new_status']
    checks.append({'check':'lifecycle_transition_history','passed':bad_lifecycle==0,'count':bad_lifecycle})
    undercount=0
    for e in db.execute("SELECT * FROM Excursion WHERE kind IN ('TEMP','HUM')"):
        try:
            evidence=json.loads(e['evidence_json']); samples=evidence.get('readings',[])
            if not samples: continue
            first=datetime.fromisoformat(samples[0]['at'])
            if evidence.get('recovery_reading_id'):
                rec=db.execute('SELECT recorded_at FROM SensorReading WHERE reading_id=?',(evidence['recovery_reading_id'],)).fetchone()
                calc=(datetime.fromisoformat(rec[0])-first).total_seconds()/60 if rec else 0
            else:calc=(datetime.fromisoformat(samples[-1]['at'])-first).total_seconds()/60+5
            if float(e['duration_minutes'])+0.01<calc:undercount+=1
        except (ValueError,KeyError,TypeError):undercount+=1
    checks.append({'check':'excursion_duration_recalculation','passed':undercount==0,'count':undercount})
    unknown_status=db.execute("SELECT COUNT(*) FROM Shipment WHERE status NOT IN ('CREATED','READY','IN_TRANSIT','DELAYED','EXCEPTION','DELIVERED','CLOSED')").fetchone()[0]
    checks.append({'check':'shipment_lifecycle_enum','passed':unknown_status==0,'count':unknown_status})
    overlap_assignment=db.execute("""SELECT COUNT(*) FROM ContainerAssignment a JOIN ContainerAssignment b
      ON a.container_id=b.container_id AND a.assignment_id<b.assignment_id
      AND a.started_at<COALESCE(b.ended_at,'9999-12-31T23:59:59+00:00')
      AND b.started_at<COALESCE(a.ended_at,'9999-12-31T23:59:59+00:00')""").fetchone()[0]
    checks.append({'check':'container_assignment_overlap','passed':overlap_assignment==0,'count':overlap_assignment})
    gap_missing=0
    from services.engine import detect_gaps
    pairs=db.execute('SELECT DISTINCT shipment_id,sensor_id FROM SensorReading').fetchall()
    for pair in pairs:
        gaps=detect_gaps(db,pair['shipment_id'],pair['sensor_id'])
        for gap in gaps:
            found=db.execute("SELECT 1 FROM Excursion WHERE shipment_id=? AND sensor_id=? AND kind='SENSOR_GAP' AND started_at=?",(pair['shipment_id'],pair['sensor_id'],gap['start'])).fetchone()
            if not found:gap_missing+=1
    checks.append({'check':'sensor_gap_exceptions_persisted','passed':gap_missing==0,'count':gap_missing})
    records_checked=sum(db.execute('SELECT COUNT(*) FROM \"'+table+'\"').fetchone()[0] for table in ('Product','Shipment','Container','ContainerAssignment','Sensor','SensorReading','RouteStop','ThresholdPolicy','Excursion','CustodyEvent','LifecycleEvent','CorrectiveAction','Notification'))
    anomalies=[dict(r) for r in db.execute('SELECT kind,COUNT(*) count FROM Excursion GROUP BY kind ORDER BY kind')]
    manifest_path=BASE_DIR/'data'/'source_manifest.json';csv_mismatches=0
    if not include_artifacts:
        checks.append({'check':'csv_database_consistency','passed':True,'count':0,'severity':'warning','detail':'Artifact checks are run when generating the validation report'})
    elif path is not None and Path(path).resolve()!=DATABASE.resolve():
        checks.append({'check':'csv_database_consistency','passed':True,'count':0,'severity':'warning','detail':'Skipped for isolated database'})
    elif manifest_path.exists():
        manifest=json.loads(manifest_path.read_text(encoding='utf8'))
        for item in manifest.get('files',[]):
            file=GENERATED/item['file']
            digest=hashlib.sha256()
            if file.exists():
                with file.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            else:csv_mismatches+=1;continue
            table_name={'sensorreading':'SensorReading','routestop':'RouteStop','thresholdpolicy':'ThresholdPolicy','custodyevent':'CustodyEvent','lifecycleevent':'LifecycleEvent'}.get(Path(item['file']).stem.lower(),Path(item['file']).stem.title())
            row_count=db.execute('SELECT COUNT(*) FROM \"'+table_name+'\"').fetchone()[0]
            if digest.hexdigest()!=item['sha256'] or row_count!=item['rows']:csv_mismatches+=1
        for item in manifest.get('ground_truth_files',[]):
            file=GENERATED/item['file']
            if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:csv_mismatches+=1
    else:
        checks.append({'check':'csv_database_consistency','passed':True,'count':0,'severity':'warning','detail':'No source manifest found'})
    if not any(c['check']=='csv_database_consistency' for c in checks):
        checks.append({'check':'csv_database_consistency','passed':csv_mismatches==0,'count':csv_mismatches})
    db.close()
    errors=[c for c in checks if not c['passed'] and c.get('severity')!='warning'];warnings=[c for c in checks if c.get('severity')=='warning' and c['count']]
    return {'status':'PASS' if not errors else 'FAIL','checks':checks,'total_checks':len(checks),'passed_checks':sum(c['passed'] for c in checks),'records_checked':records_checked,'errors':errors,'warnings':warnings,'anomaly_categories':anomalies}

def write_reports(path=None):
    result=validate(path,include_artifacts=True);GENERATED.mkdir(parents=True,exist_ok=True)
    (GENERATED/'validation_summary.json').write_text(json.dumps(result,indent=2,sort_keys=True),encoding='utf8')
    with (GENERATED/'validation_report.csv').open('w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=['check','passed','count','severity']);w.writeheader()
        for row in result['checks']:w.writerow({**row,'severity':row.get('severity','error')})
    lines=['# Validation report','',f"Status: **{result['status']}**",'',f"Checks passed: {result['passed_checks']} / {result['total_checks']}",'','| Check | Result | Count |','|---|---:|---:|']
    lines += [f"| {x['check']} | {'PASS' if x['passed'] else 'FAIL'} | {x['count']} |" for x in result['checks']]
    lines += ['',f"Records checked: {result['records_checked']}",f"Errors: {len(result['errors'])}",f"Warnings: {len(result['warnings'])}",'', '## Detected anomaly categories','']
    lines += [f"- {x['kind']}: {x['count']}" for x in result['anomaly_categories']] or ['- None recorded']
    (BASE_DIR/'docs'/'VALIDATION_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    from services.metrics import calculate_metrics
    metrics=calculate_metrics(path,validation_result=result)
    (GENERATED/'evaluation_summary.json').write_text(json.dumps(metrics,indent=2,sort_keys=True),encoding='utf8')
    with (GENERATED/'evaluation_metrics.csv').open('w',newline='',encoding='utf8') as f:
        writer=csv.writer(f);writer.writerow(['metric','value'])
        for key,value in metrics.items():writer.writerow([key,json.dumps(value,sort_keys=True) if isinstance(value,(dict,list)) else value])
    return result
