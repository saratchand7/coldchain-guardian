"""Shipment outcomes based on complete evidence and a current successful analysis."""
import json
from datetime import timedelta

from config import EXPECTED_INTERVAL_MINUTES
from services.engine import (CalibrationResolver, PolicyResolver, REQUIRED_MODALITIES,
                             analysis_fingerprint, observation_window, parse_time,
                             reading_trust, monitoring_assignments, policy_bounds, condition_state)


def assess_shipment(db, shipment_id, as_of=None):
    shipment = db.execute('SELECT * FROM Shipment WHERE shipment_id=?', (shipment_id,)).fetchone()
    if not shipment:
        raise ValueError('Shipment not found')
    start, end = observation_window(db, shipment, as_of)
    completed = shipment['status'] in {'DELIVERED', 'CLOSED'} and shipment['actual_arrival'] is not None
    issues = []
    expected_total = received_total = 0
    if end < start:
        issues.append('INVALID_OBSERVATION_WINDOW')
    else:
        assignments = monitoring_assignments(db, shipment_id, start, end)
        sensors = {}
        for sensor in db.execute(
                'SELECT DISTINCT s.* FROM Sensor s JOIN ContainerAssignment a USING(container_id) '
                'WHERE a.shipment_id=?', (shipment_id,)):
            sensors.setdefault((sensor['container_id'], sensor['kind']), []).append(sensor['sensor_id'])
        windows = [(row, parse_time(row['started_at']),
                    parse_time(row['ended_at']) if row['ended_at'] else end)
                   for row in assignments]
        readings = db.execute('SELECT r.*,s.kind FROM SensorReading r JOIN Sensor s USING(sensor_id) '
                              'WHERE r.shipment_id=? AND r.recorded_at>=? AND r.recorded_at<=? '
                              'ORDER BY r.recorded_at,r.sensor_id',
                              (shipment_id, start.isoformat(), end.isoformat())).fetchall()
        at_sensor = {(r['recorded_at'], r['sensor_id']): r for r in readings}
        calibrations = CalibrationResolver(db)
        policies = PolicyResolver(db)
        cursor = start
        while cursor <= end:
            active = {}
            for assignment, assigned_from, assigned_until in windows:
                if assigned_from <= cursor <= assigned_until:
                    active.setdefault(assignment['scope'], set()).add(assignment['container_id'])
            if not active:
                active[None] = set()
            expected_total += len(active) * len(REQUIRED_MODALITIES)
            try:
                policy = policies.at(shipment['product_id'], cursor.isoformat())
            except ValueError:
                policy = None
                issues.append('AMBIGUOUS_OR_MISSING_POLICY')
            for containers in active.values():
                for kind in REQUIRED_MODALITIES:
                    sensor_ids = {sensor_id for container in containers
                                  for sensor_id in sensors.get((container, kind), [])}
                    if not sensor_ids:
                        issues.append('MISSING_REQUIRED_MODALITY:'+kind)
                        continue
                    trusted = []
                    for sensor_id in sensor_ids:
                        reading = at_sensor.get((cursor.isoformat(), sensor_id))
                        if reading and reading_trust(db, reading, kind, start, calibrations,
                                                     require_assignment=False)[0]:
                            trusted.append(reading)
                    if not trusted:
                        issues.append('INCOMPLETE_MODALITY_COVERAGE:'+kind)
                    elif policy and len({condition_state(r['value'], *policy_bounds(policy, kind))
                                         for r in trusted}) > 1:
                        issues.append('CONFLICTING_MODALITY_OBSERVATIONS:'+kind)
                    else:
                        received_total += 1
            cursor += timedelta(minutes=EXPECTED_INTERVAL_MINUTES)

    if completed:
        analysis = db.execute('SELECT * FROM DetectionAnalysis WHERE shipment_id=?',
                              (shipment_id,)).fetchone()
        current_hash = analysis_fingerprint(db, shipment_id, end.isoformat())
        if not analysis:
            issues.append('ANALYSIS_NOT_RUN')
        elif analysis['status'] != 'SUCCESS':
            issues.append('ANALYSIS_'+analysis['status'])
        elif analysis['observation_end'] != end.isoformat() or analysis['source_hash'] != current_hash:
            issues.append('ANALYSIS_STALE')

    if not completed:
        outcome = 'NOT_YET_ASSESSED'
    elif issues:
        outcome = 'INCOMPLETE_EVIDENCE'
    else:
        severe = False
        for row in db.execute("SELECT severity,evidence_json FROM Excursion WHERE shipment_id=? "
                              "AND kind IN ('TEMP','HUM') AND severity IN ('MEDIUM','HIGH','CRITICAL')",
                              (shipment_id,)):
            evidence = json.loads(row['evidence_json'] or '{}')
            if evidence.get('condition_active', True):
                severe = True
                break
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
            'compliance_percentage': round(100*counts['COMPLIANT']/len(outcomes), 2) if outcomes else 0,
            'compliance_denominator': 'all shipments, including provisional and incomplete evidence'}
