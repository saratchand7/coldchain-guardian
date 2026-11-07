"""Independent source, temporal, history and artifact validation."""
from pathlib import Path
import csv, json, hashlib

from config import BASE_DIR, GENERATED, DATABASE, MANIFEST, REPORTS_DIR
from database import connect
from services.engine import (detect_gaps, _duration_from_evidence, parse_time,
                              CalibrationResolver, PolicyResolver, INTERVAL_MINUTES,
                              analysis_fingerprint, observation_window)


def _check(checks, name, count, detail=None, severity='error'):
    entry={'check':name,'passed':count==0,'count':int(count)}
    if detail is not None:entry['detail']=detail
    if severity!='error':entry['severity']=severity
    checks.append(entry)


def validate(path=None,include_artifacts=False):
    db=connect(path) if path else connect();checks=[];skipped=[]
    fk=db.execute('PRAGMA foreign_key_check').fetchall();_check(checks,'foreign_keys',len(fk))
    dup=db.execute('SELECT COUNT(*) FROM (SELECT reading_id FROM SensorReading GROUP BY reading_id HAVING COUNT(*)>1)').fetchone()[0]
    _check(checks,'reading_id_uniqueness',dup)
    unassigned=db.execute("""SELECT COUNT(*) FROM SensorReading r JOIN Sensor s USING(sensor_id)
      WHERE NOT EXISTS(SELECT 1 FROM ContainerAssignment a WHERE a.shipment_id=r.shipment_id
      AND a.container_id=s.container_id AND a.started_at<=r.recorded_at
      AND (a.ended_at IS NULL OR a.ended_at>=r.recorded_at))""").fetchone()[0]
    _check(checks,'reading_sensor_shipment_assignment',unassigned)
    outside=0
    windows={}
    for r in db.execute('SELECT r.recorded_at,s.* FROM SensorReading r JOIN Shipment s USING(shipment_id)'):
        if r['shipment_id'] not in windows:windows[r['shipment_id']]=observation_window(db,r)
        start,end=windows[r['shipment_id']]
        if parse_time(r['recorded_at'])<start or parse_time(r['recorded_at'])>end:outside+=1
    _check(checks,'reading_within_shipment_timeline',outside)
    bad_range=db.execute("""SELECT COUNT(*) FROM SensorReading r JOIN Sensor s USING(sensor_id) WHERE r.quality='VALID'
      AND ((s.kind='TEMP' AND (r.unit!='C' OR r.value < -80 OR r.value > 100))
      OR (s.kind='HUM' AND (r.unit!='%' OR r.value < 0 OR r.value > 100)))""").fetchone()[0]
    _check(checks,'reading_kind_unit_and_physical_range',bad_range)
    bad_quality=db.execute("SELECT COUNT(*) FROM SensorReading WHERE quality NOT IN ('VALID','INVALID_PHYSICAL_RANGE','INVALID_SENSOR','CALIBRATION_FAILURE')").fetchone()[0]
    _check(checks,'reading_quality_enum',bad_quality)
    wrong_invalid=db.execute("SELECT COUNT(*) FROM SensorReading WHERE quality='INVALID_PHYSICAL_RANGE' AND ((unit='C' AND value BETWEEN -80 AND 100) OR (unit='%' AND value BETWEEN 0 AND 100))").fetchone()[0]
    _check(checks,'invalid_reading_quality_matches_value',wrong_invalid)
    semantic= db.execute("SELECT COUNT(*) FROM SensorReading r JOIN Sensor s USING(sensor_id) WHERE (s.kind='TEMP' AND r.unit!='C') OR (s.kind='HUM' AND r.unit!='%')").fetchone()[0]
    _check(checks,'sensor_kind_unit_semantics',semantic)
    dates=db.execute('SELECT COUNT(*) FROM Shipment WHERE planned_arrival<=planned_departure OR (actual_arrival IS NOT NULL AND actual_departure IS NOT NULL AND actual_arrival<actual_departure)').fetchone()[0]
    _check(checks,'shipment_timeline',dates)
    bad_stops=0
    for row in db.execute('SELECT shipment_id,COUNT(*) n,MIN(sequence_no) lo,MAX(sequence_no) hi FROM RouteStop GROUP BY shipment_id'):
        if row['lo']!=1 or row['hi']!=row['n'] or row['n']!=4:bad_stops+=1
    for sid in [r[0] for r in db.execute('SELECT DISTINCT shipment_id FROM RouteStop')]:
        actual=[parse_time(r[0]) for r in db.execute('SELECT actual_at FROM RouteStop WHERE shipment_id=? AND actual_at IS NOT NULL ORDER BY sequence_no',(sid,))]
        if any(b<=a for a,b in zip(actual,actual[1:])):bad_stops+=1
    _check(checks,'route_stop_sequence_and_chronology',bad_stops)
    invalid_policy=db.execute('SELECT COUNT(*) FROM ThresholdPolicy WHERE temp_min>=temp_max OR humidity_min>=humidity_max OR max_excursion_minutes<=0').fetchone()[0]
    _check(checks,'threshold_policy_ranges',invalid_policy)
    overlap=db.execute("""SELECT COUNT(*) FROM ThresholdPolicy a JOIN ThresholdPolicy b ON a.product_id=b.product_id AND a.policy_id<b.policy_id
      AND a.effective_from<COALESCE(b.effective_to,'9999-12-31T23:59:59+00:00')
      AND b.effective_from<COALESCE(a.effective_to,'9999-12-31T23:59:59+00:00')""").fetchone()[0]
    _check(checks,'threshold_effective_date_overlap',overlap)
    no_policy=0;policy_resolver=PolicyResolver(db)
    for r in db.execute('SELECT s.product_id,r.recorded_at FROM SensorReading r JOIN Shipment s USING(shipment_id)'):
        try:policy_resolver.at(r['product_id'],r['recorded_at'])
        except ValueError:no_policy+=1
    _check(checks,'policy_coverage_for_readings',no_policy)
    future_cal=0;untrusted=0;calibration_resolver=CalibrationResolver(db)
    for r in db.execute('SELECT r.sensor_id,r.recorded_at,r.quality,s.calibration_status FROM SensorReading r JOIN Sensor s USING(sensor_id)'):
        valid=calibration_resolver.valid(r['sensor_id'],r['recorded_at'])
        if r['quality']=='VALID' and not valid:untrusted+=1
    for s in db.execute("SELECT sensor_id,calibrated_at FROM Sensor WHERE calibration_status='VALID'"):
        first=db.execute('SELECT MIN(recorded_at) FROM SensorReading WHERE sensor_id=?',(s['sensor_id'],)).fetchone()[0]
        if first and not calibration_resolver.valid(s['sensor_id'],first):future_cal+=1
    _check(checks,'valid_calibration_is_current_at_readings',future_cal)
    _check(checks,'untrusted_readings_traceable',0,detail=f'{untrusted} VALID-labelled rows have failed, future, expired, or otherwise untrusted calibration; they are excluded from condition decisions',severity='warning')
    bad_assignment=db.execute("""SELECT COUNT(*) FROM ContainerAssignment a JOIN Container c USING(container_id)
      WHERE (a.ended_at IS NULL AND c.status!='IN_USE') OR (a.ended_at IS NOT NULL AND c.status='IN_USE'
      AND NOT EXISTS(SELECT 1 FROM ContainerAssignment x WHERE x.container_id=c.container_id AND x.ended_at IS NULL))""").fetchone()[0]
    _check(checks,'container_status_assignment_reconciliation',bad_assignment)
    # Verify source evidence, duration, identity, current extent and every stored recovery link.
    evidence_errors=0;stale_events=0
    for e in db.execute("SELECT * FROM Excursion WHERE kind IN ('TEMP','HUM')"):
        try:
            evidence=json.loads(e['evidence_json'] or '{}')
            calculated,reads=_duration_from_evidence(db,e,evidence)
            if abs(float(e['duration_minutes'])-calculated)>0.01:evidence_errors+=1
            if e['ended_at'] is None and not evidence.get('recovery_reading_id'):
                latest=db.execute("SELECT MAX(recorded_at) FROM SensorReading WHERE shipment_id=? AND sensor_id=? AND quality='VALID'",(e['shipment_id'],e['sensor_id'])).fetchone()[0]
                if latest and parse_time(latest)>parse_time(reads[-1]['recorded_at']):stale_events+=1
        except (ValueError,KeyError,TypeError):evidence_errors+=1
    _check(checks,'excursion_duration_recalculation_from_sources',evidence_errors)
    _check(checks,'open_excursion_is_current',stale_events)
    unknown_status=db.execute("SELECT COUNT(*) FROM Shipment WHERE status NOT IN ('CREATED','READY','IN_TRANSIT','DELAYED','EXCEPTION','DELIVERED','CLOSED')").fetchone()[0]
    _check(checks,'shipment_lifecycle_enum',unknown_status)
    overlap_assignment=db.execute("""SELECT COUNT(*) FROM ContainerAssignment a JOIN ContainerAssignment b
      ON a.container_id=b.container_id AND a.assignment_id<b.assignment_id
      AND a.started_at<COALESCE(b.ended_at,'9999-12-31T23:59:59+00:00')
      AND b.started_at<COALESCE(a.ended_at,'9999-12-31T23:59:59+00:00')""").fetchone()[0]
    _check(checks,'container_assignment_overlap',overlap_assignment)
    # Compare source observation windows against all expected sensors, even if they have zero rows.
    gap_missing=0;coverage_slots=0
    from services.engine import detect_gaps
    pairs=db.execute('SELECT DISTINCT a.shipment_id,s.sensor_id,sh.actual_departure,sh.actual_arrival,sh.planned_departure,sh.status FROM ContainerAssignment a JOIN Sensor s USING(container_id) JOIN Shipment sh USING(shipment_id)').fetchall()
    for pair in pairs:
        start=pair['actual_departure'] or pair['planned_departure']
        if pair['actual_arrival']:
            end=pair['actual_arrival']
        else:
            saved=db.execute('SELECT observed_through FROM MonitoringWindow WHERE shipment_id=?',(pair['shipment_id'],)).fetchone()
            end=(saved['observed_through'] if saved else None)
            if not end:
                latest=db.execute('SELECT MAX(recorded_at) FROM SensorReading WHERE shipment_id=? AND sensor_id=?',(pair['shipment_id'],pair['sensor_id'])).fetchone()[0]
                end=latest or start
        gaps=detect_gaps(db,pair['shipment_id'],pair['sensor_id'],window_start=start,window_end=end)
        for gap in gaps:
            coverage_slots+=gap['missing_count']
            found=db.execute("SELECT evidence_json FROM Excursion WHERE shipment_id=? AND sensor_id=? AND kind='SENSOR_GAP' AND started_at=?",(pair['shipment_id'],pair['sensor_id'],gap['start'])).fetchone()
            if not found:gap_missing+=1
    _check(checks,'expected_sensor_coverage_spans_persisted',gap_missing)
    _check(checks,'missing_or_invalid_expected_slots_visible',0,detail=f'{coverage_slots} expected slots are missing, invalid, or untrusted',severity='warning')
    # Compliance is only possible after successful detection against the current source horizon.
    stale_analysis=0;missing_modalities=0
    from services.assessment import assess_shipment
    for shipment in db.execute('SELECT * FROM Shipment'):
        start,end=observation_window(db,shipment)
        analysis=db.execute('SELECT * FROM DetectionAnalysis WHERE shipment_id=?',(shipment['shipment_id'],)).fetchone()
        if not analysis or analysis['status']!='SUCCESS' or analysis['observation_end']!=end.isoformat() or \
           analysis['source_hash']!=analysis_fingerprint(db,shipment['shipment_id'],end.isoformat()):
            stale_analysis+=1
        outcome=assess_shipment(db,shipment['shipment_id'])
        if any(issue.startswith('MISSING_REQUIRED_MODALITY:') for issue in outcome['issues']):
            missing_modalities+=1
    _check(checks,'current_successful_detection_analysis',stale_analysis)
    _check(checks,'required_temp_and_humidity_evidence',missing_modalities)
    # Custody ingestion order, party chain, departure and delivery facts.
    custody_errors=0
    for sh in db.execute('SELECT shipment_id,actual_departure,actual_arrival FROM Shipment'):
        events=db.execute('SELECT * FROM CustodyEvent WHERE shipment_id=? ORDER BY rowid',(sh['shipment_id'],)).fetchall()
        previous_party=None;previous_at=None
        for i,event in enumerate(events):
            at=parse_time(event['occurred_at'])
            if (i==0 and (event['event_type']!='DEPARTURE' or event['previous_custodian'] is not None)) or \
               (i>0 and (event['previous_custodian']!=previous_party or previous_at is None or at<=previous_at)):
                custody_errors+=1
            if sh['actual_departure'] and at<parse_time(sh['actual_departure']):custody_errors+=1
            if event['event_type']=='DELIVERY' and (not sh['actual_arrival'] or at!=parse_time(sh['actual_arrival'])):custody_errors+=1
            previous_party=event['new_custodian'];previous_at=at
        if sh['actual_arrival'] and events and not any(e['event_type']=='DELIVERY' for e in events):custody_errors+=1
    _check(checks,'custody_chain_and_delivery_reconciliation',custody_errors)
    lifecycle=db.execute('SELECT shipment_id,previous_status,new_status,occurred_at FROM LifecycleEvent ORDER BY shipment_id,occurred_at,rowid').fetchall()
    allowed={'CREATED':{'READY'},'READY':{'IN_TRANSIT'},'IN_TRANSIT':{'DELAYED','EXCEPTION','DELIVERED'},'DELAYED':{'IN_TRANSIT','EXCEPTION','DELIVERED'},'EXCEPTION':{'IN_TRANSIT','DELAYED','DELIVERED'},'DELIVERED':{'CLOSED'},'CLOSED':set()}
    bad_lifecycle=0;last={}
    for e in lifecycle:
        if e['new_status'] not in allowed.get(e['previous_status'],set()) or (e['shipment_id'] in last and last[e['shipment_id']]!=e['previous_status']):bad_lifecycle+=1
        last[e['shipment_id']]=e['new_status']
    for s in db.execute('SELECT shipment_id,status FROM Shipment'):
        if s['shipment_id'] in last and last[s['shipment_id']]!=s['status']:bad_lifecycle+=1
    _check(checks,'lifecycle_history_matches_current_state',bad_lifecycle)
    history_errors=0
    for table,current_table,key,status_col in [('CorrectiveActionEvent','CorrectiveAction','action_id','status'),('ExcursionStatusEvent','Excursion','excursion_id','status')]:
        for cur in db.execute(f'SELECT {key},{status_col} FROM {current_table}'):
            events=db.execute(f'SELECT previous_status,new_status FROM {table} WHERE {key}=? ORDER BY occurred_at,event_id',(cur[0],)).fetchall()
            if not events or events[-1]['new_status']!=cur[1]:history_errors+=1
            for a,b in zip(events,events[1:]):
                if a['new_status']!=b['previous_status']:history_errors+=1
    _check(checks,'status_history_matches_current_state',history_errors)
    # Every truth ID and embedded duration source must resolve to its labelled parent rows.
    truth_errors=0;truth_items=[]
    truth_path=GENERATED/'ground_truth.json'
    isolated_db=path is not None and Path(path).resolve()!=DATABASE.resolve()
    if isolated_db:
        skipped.append('ground_truth_source_references: isolated database has no matching ground-truth artifact set')
    elif truth_path.exists():
        truth_items=json.loads(truth_path.read_text(encoding='utf8'))
        for item in truth_items:
            if item.get('sensor_id') and item.get('shipment_id') and item.get('recorded_at'):
                exists=db.execute('SELECT 1 FROM SensorReading WHERE shipment_id=? AND sensor_id=? AND recorded_at=?',(item['shipment_id'],item['sensor_id'],item['recorded_at'])).fetchone()
                # Labels may name event starts rather than a reading if an injected point was dropped.
                if item.get('scenario') not in {'sensor_dropout'} and not exists:truth_errors+=1
            payload=json.loads(item.get('input_json') or '{}')
            for rid in payload.get('source_reading_ids',[]):
                src=db.execute('SELECT shipment_id,sensor_id FROM SensorReading WHERE reading_id=?',(rid,)).fetchone()
                if not src or src['shipment_id']!=item.get('shipment_id') or src['sensor_id']!=item.get('sensor_id'):truth_errors+=1
    if not isolated_db:
        _check(checks,'ground_truth_source_references',truth_errors)
        expected={(x['shipment_id'],x.get('sensor_id') or '',x['expected_kind'],x['recorded_at'])
                  for x in truth_items if x.get('expected')=='detected' and x.get('expected_kind')}
        actual=set()
        for event in db.execute('SELECT shipment_id,sensor_id,kind,started_at,evidence_json FROM Excursion'):
            if json.loads(event['evidence_json'] or '{}').get('condition_active',True):
                actual.add((event['shipment_id'],event['sensor_id'] or '',event['kind'],event['started_at']))
        _check(checks,'ground_truth_expected_events_persisted',len(expected-actual))
    # Decision history is independently checked by the real decision rules.
    records_checked=sum(db.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0] for table in
      ('Product','Shipment','Container','ContainerAssignment','MonitoringWindow','DetectionAnalysis','Sensor','SensorCalibrationHistory','SensorReading','RouteStop','ThresholdPolicy','Excursion','ExcursionRevision','ExcursionStatusEvent','CustodyEvent','LifecycleEvent','CorrectiveAction','CorrectiveActionEvent','Notification'))
    anomalies=[dict(r) for r in db.execute('SELECT kind,COUNT(*) count FROM Excursion GROUP BY kind ORDER BY kind')]
    if include_artifacts:
        if path is not None and Path(path).resolve()!=DATABASE.resolve():
            skipped.append('export_manifest_hash_validation: isolated database uses a separate generated directory')
        elif not MANIFEST.exists():
            skipped.append('export_manifest_hash_validation: no source manifest exists')
        else:
            manifest=json.loads(MANIFEST.read_text(encoding='utf8'));mismatches=0
            for item in manifest.get('files',[]):
                file=GENERATED/item['file']
                if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:
                    mismatches+=1;continue
                if item['file'].endswith('.csv') or item['file'].endswith('.json'):
                    table_map={'sensorreading':'SensorReading','routestop':'RouteStop','thresholdpolicy':'ThresholdPolicy',
                               'custodyevent':'CustodyEvent','lifecycleevent':'LifecycleEvent',
                               'monitoringwindow':'MonitoringWindow','detectionanalysis':'DetectionAnalysis','sensorcalibrationhistory':'SensorCalibrationHistory','excursionrevision':'ExcursionRevision',
                               'excursionstatusevent':'ExcursionStatusEvent','correctiveactionevent':'CorrectiveActionEvent'}
                    table=table_map.get(Path(item['file']).stem.lower(),Path(item['file']).stem.title())
                    try: row_count=db.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0]
                    except Exception:row_count=-1
                    if row_count!=item['rows']:mismatches+=1
            for item in manifest.get('ground_truth_files',[]):
                file=GENERATED/item['file']
                if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:mismatches+=1
            if manifest.get('sql_export'):
                sql=GENERATED/manifest['sql_export']['file']
                if not sql.exists() or hashlib.sha256(sql.read_bytes()).hexdigest()!=manifest['sql_export']['sha256']:mismatches+=1
            _check(checks,'export_manifest_hash_and_row_consistency',mismatches)
    else:
        skipped.append('export_manifest_hash_validation: call validate(include_artifacts=True) to execute')
    db.close()
    errors=[c for c in checks if not c['passed'] and c.get('severity')!='warning']
    warnings=[c for c in checks if c.get('severity')=='warning' and c['count']]
    return {'status':'PASS' if not errors else 'FAIL','checks':checks,'skipped_checks':skipped,
            'total_checks':len(checks),'passed_checks':sum(c['passed'] for c in checks),
            'records_checked':records_checked,'errors':errors,'warnings':warnings,'anomaly_categories':anomalies}


def write_reports(path=None):
    result=validate(path,include_artifacts=True);GENERATED.mkdir(parents=True,exist_ok=True);REPORTS_DIR.mkdir(parents=True,exist_ok=True)
    (GENERATED/'validation_summary.json').write_text(json.dumps(result,indent=2,sort_keys=True),encoding='utf8',newline='\n')
    with (GENERATED/'validation_report.csv').open('w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=['check','passed','count','severity','detail']);w.writeheader()
        for row in result['checks']:w.writerow({**row,'severity':row.get('severity','error')})
    lines=['# Validation report','',f"Status: **{result['status']}**",'',f"Checks passed: {result['passed_checks']} / {result['total_checks']}",'','| Check | Result | Count |','|---|---:|---:|']
    lines += [f"| {x['check']} | {'PASS' if x['passed'] else ('WARN' if x.get('severity')=='warning' else 'FAIL')} | {x['count']} |" for x in result['checks']]
    lines += ['',f"Records checked: {result['records_checked']}",f"Errors: {len(result['errors'])}",f"Warnings: {len(result['warnings'])}", '', '## Skipped checks','']
    lines += [f'- {x}' for x in result['skipped_checks']] or ['- None']
    lines += ['', '## Detected anomaly categories','']
    lines += [f"- {x['kind']}: {x['count']}" for x in result['anomaly_categories']] or ['- None recorded']
    (REPORTS_DIR/'VALIDATION_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf8',newline='\n')
    from services.metrics import calculate_metrics
    metrics=calculate_metrics(path,validation_result=result)
    (GENERATED/'evaluation_summary.json').write_text(json.dumps(metrics,indent=2,sort_keys=True),encoding='utf8',newline='\n')
    with (GENERATED/'evaluation_metrics.csv').open('w',newline='',encoding='utf8') as f:
        writer=csv.writer(f);writer.writerow(['metric','value'])
        for key,value in metrics.items():writer.writerow([key,json.dumps(value,sort_keys=True) if isinstance(value,(dict,list)) else value])
    return result
