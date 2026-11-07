"""Shipment condition outcomes; compliance requires complete, trusted evidence."""
from datetime import timedelta, datetime, timezone

from config import EXPECTED_INTERVAL_MINUTES
from services.engine import parse_time, CalibrationResolver


def assess_shipment(db, shipment_id, as_of=None):
    shipment = db.execute('SELECT * FROM Shipment WHERE shipment_id=?', (shipment_id,)).fetchone()
    if not shipment:
        raise ValueError('Shipment not found')
    start = parse_time(shipment['actual_departure'] or shipment['planned_departure'])
    completed = shipment['status'] in {'DELIVERED', 'CLOSED'} and shipment['actual_arrival'] is not None
    if completed:
        end = parse_time(shipment['actual_arrival'])
    elif as_of:
        end = min(parse_time(as_of), parse_time(shipment['planned_arrival']))
    else:
        saved=db.execute('SELECT observed_through FROM MonitoringWindow WHERE shipment_id=?',(shipment_id,)).fetchone()
        end=parse_time(saved['observed_through']) if saved else min(datetime.now(timezone.utc),parse_time(shipment['planned_arrival']))
    sensors = db.execute('SELECT DISTINCT x.sensor_id,x.kind FROM ContainerAssignment a '
                         'JOIN Sensor x USING(container_id) WHERE a.shipment_id=? AND a.started_at<=? '
                         'AND (a.ended_at IS NULL OR a.ended_at>=?)',
                         (shipment_id, end.isoformat(), start.isoformat())).fetchall()
    issues = [];calibrations=CalibrationResolver(db)
    policies = db.execute('SELECT effective_from,effective_to FROM ThresholdPolicy WHERE product_id=?',
                          (shipment['product_id'],)).fetchall()
    expected_total = received_total = 0
    if not sensors:
        issues.append('NO_VALID_SENSOR_ASSIGNMENT')
    if end < start:
        issues.append('INVALID_OBSERVATION_WINDOW')
    else:
        expected_per_sensor = int((end-start).total_seconds() // (EXPECTED_INTERVAL_MINUTES*60)) + 1
        expected_total = expected_per_sensor * len(sensors)
        for sensor in sensors:
            unit = 'C' if sensor['kind'] == 'TEMP' else '%'
            readings = db.execute('SELECT recorded_at,value,unit,quality FROM SensorReading '
                                  'WHERE shipment_id=? AND sensor_id=? AND recorded_at>=? AND recorded_at<=?',
                                  (shipment_id, sensor['sensor_id'], start.isoformat(), end.isoformat())).fetchall()
            valid_slots = set()
            for r in readings:
                at = parse_time(r['recorded_at'])
                offset = (at-start).total_seconds()
                slot = round(offset/(EXPECTED_INTERVAL_MINUTES*60))
                aligned = abs(offset-slot*EXPECTED_INTERVAL_MINUTES*60) <= 1
                physical = (-80 <= r['value'] <= 100) if sensor['kind'] == 'TEMP' else (0 <= r['value'] <= 100)
                trusted = (aligned and r['quality'] == 'VALID' and r['unit'] == unit and physical and
                           calibrations.valid(sensor['sensor_id'], r['recorded_at']))
                if trusted:
                    valid_slots.add(slot)
            received_total += len(valid_slots)
            if len(valid_slots) != expected_per_sensor:
                issues.append('INCOMPLETE_SENSOR_COVERAGE:'+sensor['sensor_id'])
            if not calibrations.valid(sensor['sensor_id'], start.isoformat()):
                issues.append('INVALID_CALIBRATION:'+sensor['sensor_id'])
            # Every expected instant must have exactly one effective policy.
            if not policies:
                issues.append('MISSING_POLICY')
                continue
            cursor = start
            while cursor <= end:
                active = sum(1 for p in policies if parse_time(p['effective_from']) <= cursor and
                             (p['effective_to'] is None or cursor < parse_time(p['effective_to'])))
                if active != 1:
                    issues.append('AMBIGUOUS_OR_MISSING_POLICY')
                    break
                cursor += timedelta(minutes=EXPECTED_INTERVAL_MINUTES)
    if not completed:
        outcome = 'NOT_YET_ASSESSED'
    elif issues:
        outcome = 'INCOMPLETE_EVIDENCE'
    else:
        severe = db.execute("SELECT 1 FROM Excursion WHERE shipment_id=? AND kind IN ('TEMP','HUM') "
                            "AND severity IN ('MEDIUM','HIGH','CRITICAL') LIMIT 1", (shipment_id,)).fetchone()
        outcome = 'NON_COMPLIANT' if severe else 'COMPLIANT'
    return {'shipment_id': shipment_id, 'outcome': outcome, 'completed': completed,
            'observation_start': start.isoformat(), 'observation_end': end.isoformat(),
            'expected_readings': expected_total, 'trusted_readings': received_total,
            'issues': sorted(set(issues)), 'provisional_excursion_count': db.execute(
                "SELECT COUNT(*) FROM Excursion WHERE shipment_id=? AND status!='CLOSED'", (shipment_id,)
            ).fetchone()[0]}


def assess_all(db, as_of=None):
    outcomes = [assess_shipment(db, row['shipment_id'], as_of) for row in
                db.execute('SELECT shipment_id FROM Shipment ORDER BY shipment_id')]
    counts = {key: sum(item['outcome'] == key for item in outcomes)
              for key in ('COMPLIANT', 'NON_COMPLIANT', 'INCOMPLETE_EVIDENCE', 'NOT_YET_ASSESSED')}
    return {'outcomes': outcomes, 'counts': counts,
            'eligible_shipments': len(outcomes),
            'assessed_shipments': counts['COMPLIANT']+counts['NON_COMPLIANT'],
            'compliance_percentage': round(100*counts['COMPLIANT']/len(outcomes), 2) if outcomes else 0}
