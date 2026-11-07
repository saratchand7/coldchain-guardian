"""Shipment outcomes based on complete evidence and a current successful analysis."""
import json
from datetime import timedelta

from config import EXPECTED_INTERVAL_MINUTES
from services.engine import (CalibrationResolver, PolicyResolver, REQUIRED_MODALITIES,
                             analysis_fingerprint, observation_window, parse_time,
                             reading_trust)


def _assigned_sensors(db, shipment_id, start, end):
    return db.execute('SELECT s.sensor_id,s.kind,a.started_at,a.ended_at FROM ContainerAssignment a '
                      'JOIN Sensor s USING(container_id) WHERE a.shipment_id=? AND a.started_at<=? '
                      'AND (a.ended_at IS NULL OR a.ended_at>=?) ORDER BY s.kind,s.sensor_id',
                      (shipment_id, end.isoformat(), start.isoformat())).fetchall()


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
        sensors = _assigned_sensors(db, shipment_id, start, end)
        by_kind = {kind: [s for s in sensors if s['kind'] == kind] for kind in REQUIRED_MODALITIES}
        readings = db.execute('SELECT r.*,s.kind FROM SensorReading r JOIN Sensor s USING(sensor_id) '
                              'WHERE r.shipment_id=? AND r.recorded_at>=? AND r.recorded_at<=? '
                              'ORDER BY r.recorded_at,r.sensor_id',
                              (shipment_id, start.isoformat(), end.isoformat())).fetchall()
        at_sensor = {(r['recorded_at'], r['sensor_id']): r for r in readings}
        calibrations = CalibrationResolver(db)
        policies = PolicyResolver(db)
        cursor = start
        while cursor <= end:
            expected_total += len(REQUIRED_MODALITIES)
            try:
                policies.at(shipment['product_id'], cursor.isoformat())
            except ValueError:
                issues.append('AMBIGUOUS_OR_MISSING_POLICY')
            for kind in REQUIRED_MODALITIES:
                active = [s for s in by_kind[kind]
                          if parse_time(s['started_at']) <= cursor and
                          (s['ended_at'] is None or cursor <= parse_time(s['ended_at']))]
                if not active:
                    issues.append('MISSING_REQUIRED_MODALITY:'+kind)
                    continue
                covered = False
                for sensor in active:
                    reading = at_sensor.get((cursor.isoformat(), sensor['sensor_id']))
                    if reading and reading_trust(db, reading, kind, start, calibrations,
                                                 require_assignment=False)[0]:
                        covered = True
                        break
                if covered:
                    received_total += 1
                else:
                    issues.append('INCOMPLETE_MODALITY_COVERAGE:'+kind)
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
