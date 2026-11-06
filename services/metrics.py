"""Metrics calculated from stored facts and independent positive event labels."""
import json
from config import GENERATED
from database import connect
from services.validation import validate

def calculate_metrics(path=None,validate_data=True,validation_result=None):
    db=connect(path) if path else connect()
    scalar=lambda q: db.execute(q).fetchone()[0]
    total=scalar('SELECT COUNT(*) FROM Shipment')
    compliant=scalar("""SELECT COUNT(*) FROM Shipment s WHERE NOT EXISTS
      (SELECT 1 FROM Excursion e WHERE e.shipment_id=s.shipment_id AND e.severity IN ('MEDIUM','HIGH','CRITICAL'))""")
    exc_count=scalar('SELECT COUNT(*) FROM Excursion')
    delivered=scalar("SELECT COUNT(*) FROM Shipment WHERE status IN ('DELIVERED','CLOSED')")
    completed=scalar("SELECT COUNT(*) FROM CorrectiveAction WHERE status='COMPLETED'")
    action_count=scalar("SELECT COUNT(*) FROM CorrectiveAction WHERE status!='CANCELLED'")
    average=scalar("SELECT COALESCE(AVG(duration_minutes),0) FROM Excursion WHERE kind IN ('TEMP','HUM')")
    critical=scalar("SELECT COUNT(*) FROM Excursion WHERE severity='CRITICAL'")
    shipment_exc=scalar('SELECT COUNT(DISTINCT shipment_id) FROM Excursion')
    # Expected versus valid readings across assigned container sensor timelines.
    expected=0
    for row in db.execute("""SELECT sh.actual_departure,sh.actual_arrival,sh.planned_departure,sh.planned_arrival,sh.status,
       (SELECT MAX(r.recorded_at) FROM SensorReading r WHERE r.shipment_id=sh.shipment_id) latest_reading,
       COUNT(DISTINCT se.sensor_id) sensors
       FROM Shipment sh JOIN ContainerAssignment ca USING(shipment_id)
       JOIN Sensor se USING(container_id) WHERE COALESCE(sh.actual_departure,sh.planned_departure) IS NOT NULL
       GROUP BY sh.shipment_id"""):
        start_at=row['actual_departure'] or row['planned_departure']
        end_at=row['actual_arrival'] or (row['latest_reading'] if row['status'] not in ('DELIVERED','CLOSED') else row['planned_arrival']) or row['planned_arrival']
        minutes=max(0,(__import__('datetime').datetime.fromisoformat(end_at)-__import__('datetime').datetime.fromisoformat(start_at)).total_seconds()/60)
        expected+=int(minutes//5+1)*row['sensors']
    received=scalar("SELECT COUNT(*) FROM SensorReading WHERE quality='VALID'")
    validation=validation_result or (validate(path) if validate_data else {'passed_checks':0,'total_checks':0})
    detected=[dict(r) for r in db.execute('SELECT shipment_id,sensor_id,kind,started_at FROM Excursion')]
    db.close()
    truth_path=GENERATED/'ground_truth.json'
    truth=json.loads(truth_path.read_text(encoding='utf8')) if truth_path.exists() else []
    labelled=[x for x in truth if x.get('expected')=='detected' and x.get('expected_kind')]
    detected_keys={(x['shipment_id'],x['sensor_id'] or '',x['kind'],x['started_at']) for x in detected}
    truth_keys={(x['shipment_id'],x.get('sensor_id') or '',x['expected_kind'],x['recorded_at']) for x in labelled}
    tp=len(detected_keys & truth_keys);fn=len(truth_keys-detected_keys)
    fp=len(detected_keys-truth_keys)
    precision=tp/(tp+fp) if tp+fp else None;recall=tp/(tp+fn) if tp+fn else None
    return {
      'shipment_compliance_percentage':round(100*compliant/total,2) if total else 0,
      'excursion_rate_percentage':round(100*shipment_exc/total,2) if total else 0,
      'sensor_availability_percentage':round(100*min(received,expected)/expected,2) if expected else 100,
      'detection_precision':precision,'detection_recall':recall,'detection_confusion':{'TP':tp,'FP':fp,'TN':None,'FN':fn,'note':'TN is unavailable because negative ground-truth coverage is event-level, not every safe five-minute interval.'},
      'corrective_action_completion_percentage':round(100*completed/action_count,2) if action_count else 100,
      'data_integrity_percentage':round(100*validation['passed_checks']/validation['total_checks'],2) if validation['total_checks'] else None,
      'total_detected_anomalies':exc_count,'average_excursion_duration_minutes':round(average,2),
      'critical_exceptions':critical,'ground_truth_positive_events':len(labelled)
    }
