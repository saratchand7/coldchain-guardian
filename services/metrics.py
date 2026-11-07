"""Evidence-aware compliance, availability, event and negative-decision metrics."""
import json
from pathlib import Path
from config import GENERATED, DATABASE
from database import connect
from services.assessment import assess_all
from services.lifecycle import validate_transition
from services.engine import (validate_policy_product, verify_duration_report, parse_time,
                             CalibrationResolver, applicable_policy)


def _evaluate_negative_cases(db, truth):
    cases = []
    for item in truth:
        expected = item.get('expected')
        if expected not in {'rejected', 'caught'}:
            continue
        payload = json.loads(item.get('input_json') or '{}')
        actual = 'accepted'
        evidence = {}
        if item.get('scenario') == 'delivered_before_departure':
            try:
                validate_transition(payload.get('current_status', ''), payload.get('attempted_status', ''),
                                    payload.get('actual_departure'))
            except ValueError as exc:
                actual = 'rejected'
                evidence = {'reason': str(exc)}
        elif item.get('scenario') == 'wrong_product_threshold':
            try:
                validate_policy_product(db, item['shipment_id'], payload.get('policy_id', ''))
            except ValueError as exc:
                actual = 'rejected'
                evidence = {'reason': str(exc)}
        elif item.get('scenario') == 'excursion_duration_undercount':
            ids = payload.get('source_reading_ids', [])
            rows = []
            if ids:
                marks = ','.join('?' for _ in ids)
                rows = db.execute(f'SELECT * FROM SensorReading WHERE reading_id IN ({marks})', ids).fetchall()
            by_id={r['reading_id']:r for r in rows}
            ordered=[by_id[rid] for rid in ids if rid in by_id]
            valid = len(ordered) == len(ids) and all(r['shipment_id'] == item['shipment_id'] and
                    r['sensor_id'] == item.get('sensor_id') and r['quality'] == 'VALID' for r in ordered)
            if valid and [parse_time(r['recorded_at']) for r in ordered] != sorted(parse_time(r['recorded_at']) for r in ordered):valid=False
            calibration=CalibrationResolver(db)
            if valid:
                for row in ordered:
                    sensor=db.execute('SELECT kind FROM Sensor WHERE sensor_id=?',(row['sensor_id'],)).fetchone()
                    assignment=db.execute('SELECT 1 FROM ContainerAssignment WHERE shipment_id=? AND container_id=(SELECT container_id FROM Sensor WHERE sensor_id=?) AND started_at<=? AND (ended_at IS NULL OR ended_at>=?)',(item['shipment_id'],item['sensor_id'],row['recorded_at'],row['recorded_at'])).fetchone()
                    if not sensor or row['unit']!=('C' if sensor['kind']=='TEMP' else '%') or not calibration.valid(row['sensor_id'],row['recorded_at']) or not assignment:valid=False;break
                    product=db.execute('SELECT product_id FROM Shipment WHERE shipment_id=?',(item['shipment_id'],)).fetchone()[0]
                    policy=applicable_policy(db,product,row['recorded_at'])
                    low,high=((policy['temp_min'],policy['temp_max']) if sensor['kind']=='TEMP' else (policy['humidity_min'],policy['humidity_max']))
                    if low<=row['value']<=high:valid=False;break
            calculated = 0
            if ordered:
                from services.engine import INTERVAL_MINUTES
                recovery_id=payload.get('recovery_reading_id')
                if recovery_id:
                    recovery=db.execute('SELECT * FROM SensorReading WHERE reading_id=?',(recovery_id,)).fetchone()
                    if recovery and recovery['shipment_id']==item['shipment_id'] and recovery['sensor_id']==item.get('sensor_id') and recovery['quality']=='VALID':
                        sensor=db.execute('SELECT kind FROM Sensor WHERE sensor_id=?',(recovery['sensor_id'],)).fetchone()
                        assignment=db.execute('SELECT 1 FROM ContainerAssignment WHERE shipment_id=? AND container_id=(SELECT container_id FROM Sensor WHERE sensor_id=?) AND started_at<=? AND (ended_at IS NULL OR ended_at>=?)',(item['shipment_id'],recovery['sensor_id'],recovery['recorded_at'],recovery['recorded_at'])).fetchone()
                        if not sensor or recovery['unit']!=('C' if sensor['kind']=='TEMP' else '%') or not calibration.valid(recovery['sensor_id'],recovery['recorded_at']) or not assignment:valid=False
                        if sensor:
                            product=db.execute('SELECT product_id FROM Shipment WHERE shipment_id=?',(item['shipment_id'],)).fetchone()[0]
                            policy=applicable_policy(db,product,recovery['recorded_at'])
                            low,high=((policy['temp_min'],policy['temp_max']) if sensor['kind']=='TEMP' else (policy['humidity_min'],policy['humidity_max']))
                            if not low<=recovery['value']<=high:valid=False
                        calculated=(parse_time(recovery['recorded_at'])-parse_time(ordered[0]['recorded_at'])).total_seconds()/60
                        rows.append(recovery)
                    else:valid=False
                else:
                    calculated=(parse_time(ordered[-1]['recorded_at'])-parse_time(ordered[0]['recorded_at'])).total_seconds()/60+INTERVAL_MINUTES
            caught = valid and verify_duration_report(calculated, payload.get('reported_duration_minutes', 0))
            actual = 'caught' if caught else 'accepted'
            evidence = {'source_rows': len(rows), 'calculated_duration_minutes': calculated,
                        'reported_duration_minutes': payload.get('reported_duration_minutes')}
        cases.append({'case_id': item.get('case_id'), 'expected': expected, 'actual': actual,
                      'passed': expected == actual, 'evidence': evidence})
    return {'cases': cases, 'passed': sum(x['passed'] for x in cases), 'total': len(cases),
            'accuracy_percentage': round(100*sum(x['passed'] for x in cases)/len(cases), 2) if cases else None}


def calculate_metrics(path=None, validate_data=True, validation_result=None, as_of=None, truth_path=None):
    db = connect(path) if path else connect()
    total = db.execute('SELECT COUNT(*) FROM Shipment').fetchone()[0]
    exc_count = db.execute('SELECT COUNT(*) FROM Excursion').fetchone()[0]
    completed = db.execute("SELECT COUNT(*) FROM CorrectiveAction WHERE status='COMPLETED'").fetchone()[0]
    action_count = db.execute("SELECT COUNT(*) FROM CorrectiveAction WHERE status!='CANCELLED'").fetchone()[0]
    average = db.execute("SELECT COALESCE(AVG(duration_minutes),0) FROM Excursion WHERE kind IN ('TEMP','HUM')").fetchone()[0]
    critical = db.execute("SELECT COUNT(*) FROM Excursion WHERE severity='CRITICAL'").fetchone()[0]
    shipment_exc = db.execute('SELECT COUNT(DISTINCT shipment_id) FROM Excursion').fetchone()[0]
    assessment = assess_all(db, as_of)
    positive_detected = []
    for row in db.execute('SELECT shipment_id,sensor_id,kind,started_at,evidence_json FROM Excursion'):
        if json.loads(row['evidence_json'] or '{}').get('condition_active', True):
            positive_detected.append(dict(row))
    validation = validation_result or ({'passed_checks':0,'total_checks':0} if not validate_data else None)
    if truth_path is None and (path is None or Path(path).resolve() == DATABASE.resolve()):
        truth_path = GENERATED/'ground_truth.json'
    truth_path = Path(truth_path) if truth_path else None
    truth = json.loads(truth_path.read_text(encoding='utf8')) if truth_path and truth_path.exists() else []
    labelled = [x for x in truth if x.get('expected')=='detected' and x.get('expected_kind')]
    detected_keys = {(x['shipment_id'],x['sensor_id'] or '',x['kind'],x['started_at']) for x in positive_detected}
    truth_keys = {(x['shipment_id'],x.get('sensor_id') or '',x['expected_kind'],x['recorded_at']) for x in labelled}
    tp, fn, fp = len(detected_keys & truth_keys), len(truth_keys-detected_keys), len(detected_keys-truth_keys)
    precision=tp/(tp+fp) if tp+fp else None
    recall=tp/(tp+fn) if tp+fn else None
    rejected = _evaluate_negative_cases(db, truth)
    trusted, expected = sum(x['trusted_readings'] for x in assessment['outcomes']), sum(x['expected_readings'] for x in assessment['outcomes'])
    db.close()
    if validation is None:
        from services.validation import validate
        validation = validate(path)
    counts=assessment['counts']
    return {
      'shipment_compliance_percentage':assessment['compliance_percentage'],
      'shipment_compliance_denominator':assessment['compliance_denominator'],
      'shipment_assessment_counts':counts,
      'eligible_shipments':assessment['eligible_shipments'],
      'assessed_shipments':assessment['assessed_shipments'],
      'sensor_availability_percentage':round(100*min(trusted,expected)/expected,2) if expected else 0,
      'sensor_readings_trusted':trusted,'sensor_readings_expected':expected,
      'excursion_rate_percentage':round(100*shipment_exc/total,2) if total else 0,
      'detection_precision':precision,'detection_recall':recall,
      'detection_confusion':{'TP':tp,'FP':fp,'TN':None,'FN':fn,
          'note':'TN is unavailable because negative ground-truth coverage is event-level, not every safe five-minute interval.'},
      'negative_decision_evaluation':rejected,
      'corrective_action_completion_percentage':round(100*completed/action_count,2) if action_count else 100,
      'data_integrity_percentage':round(100*validation['passed_checks']/validation['total_checks'],2) if validation['total_checks'] else None,
      'total_detected_anomalies':exc_count,'average_excursion_duration_minutes':round(average,2),
      'critical_exceptions':critical,'ground_truth_positive_events':len(labelled)
      ,'evaluation_scope':'independently labelled positive events by class; TN unavailable'
      ,'ground_truth_artifact':str(truth_path) if truth_path else None
    }
