"""Canonical, source-linked excursion and sensor coverage detector."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from config import CALIBRATION_VALIDITY_DAYS, EXPECTED_INTERVAL_MINUTES
from database import connection_path, transaction

INTERVAL_MINUTES = EXPECTED_INTERVAL_MINUTES
PHYSICAL_RANGES = {'TEMP': (-80.0, 100.0), 'HUM': (0.0, 100.0)}
REQUIRED_MODALITIES = ('TEMP', 'HUM')


def parse_time(value):
    """Parse an ISO timestamp and normalize it to UTC."""
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PolicyResolver:
    def __init__(self, db):
        self.db=db;self.cache={}
    def at(self, product_id, at):
        instant=parse_time(at)
        if product_id not in self.cache:
            self.cache[product_id]=self.db.execute('SELECT * FROM ThresholdPolicy WHERE product_id=? ORDER BY effective_from',(product_id,)).fetchall()
        active=[row for row in self.cache[product_id] if parse_time(row['effective_from'])<=instant and
                (row['effective_to'] is None or instant<parse_time(row['effective_to']))]
        if len(active)!=1:raise ValueError('No unique effective threshold policy for '+product_id+' at '+str(at))
        return active[0]

def applicable_policy(db, product_id, at, resolver=None):
    return (resolver or PolicyResolver(db)).at(product_id,at)

class CalibrationResolver:
    """Cache calibration history per sensor while checking each reading instant."""
    def __init__(self,db):self.db=db;self.cache={}
    def valid(self,sensor_id,reading_at):
        if sensor_id not in self.cache:
            sensor=self.db.execute('SELECT calibrated_at,calibration_status FROM Sensor WHERE sensor_id=?',(sensor_id,)).fetchone()
            if not sensor:self.cache[sensor_id]=None
            else:
                history=self.db.execute('SELECT calibrated_at,status FROM SensorCalibrationHistory WHERE sensor_id=? ORDER BY calibrated_at',(sensor_id,)).fetchall()
                self.cache[sensor_id]=(sensor['calibrated_at'],sensor['calibration_status'],history)
        record=self.cache[sensor_id]
        if not record:return False
        calibrated_at,status,history=record;instant=parse_time(reading_at)
        for item in history:
            if parse_time(item['calibrated_at'])<=instant:calibrated_at,status=item['calibrated_at'],item['status']
            else:break
        age=instant-parse_time(calibrated_at)
        return status=='VALID' and timedelta(0)<=age<=timedelta(days=CALIBRATION_VALIDITY_DAYS)


def observation_window(db, shipment, as_of=None):
    """Return the authoritative monitoring window; planned arrival never truncates evidence."""
    start = parse_time(shipment['actual_departure'] or shipment['planned_departure'])
    if shipment['actual_arrival']:
        end = parse_time(shipment['actual_arrival'])
    elif as_of:
        end = parse_time(as_of)
    else:
        saved = db.execute('SELECT observed_through FROM MonitoringWindow WHERE shipment_id=?',
                           (shipment['shipment_id'],)).fetchone()
        latest = db.execute('SELECT MAX(recorded_at) FROM SensorReading WHERE shipment_id=?',
                            (shipment['shipment_id'],)).fetchone()[0]
        end = parse_time(saved['observed_through']) if saved else (parse_time(latest) if latest else start)
    return start, end


def sensor_assignment_intervals(db, shipment_id, sensor_id, start, end):
    rows = db.execute('SELECT a.started_at,a.ended_at FROM ContainerAssignment a '
                      'JOIN Sensor s USING(container_id) WHERE a.shipment_id=? AND s.sensor_id=? '
                      'AND a.started_at<=? AND (a.ended_at IS NULL OR a.ended_at>=?) '
                      'ORDER BY a.started_at',
                      (shipment_id, sensor_id, end.isoformat(), start.isoformat())).fetchall()
    return [(max(start, parse_time(r['started_at'])),
             min(end, parse_time(r['ended_at'])) if r['ended_at'] else end) for r in rows]


def reading_trust(db, reading, kind, window_start, calibration_resolver=None,
                  require_assignment=True):
    """One trust predicate shared by detection, gap analysis and assessment."""
    expected_unit = 'C' if kind == 'TEMP' else '%'
    at = parse_time(reading['recorded_at'])
    offset = (at-window_start).total_seconds()
    slot = round(offset/(INTERVAL_MINUTES*60))
    if abs(offset-slot*INTERVAL_MINUTES*60) > 1:
        return False, 'OFF_GRID_OBSERVATION'
    low, high = PHYSICAL_RANGES[kind]
    if not low <= float(reading['value']) <= high:
        return False, 'INVALID_PHYSICAL_RANGE'
    if reading['quality'] != 'VALID':
        return False, 'INVALID_OBSERVATION'
    if reading['unit'] != expected_unit:
        return False, 'UNTRUSTED_OBSERVATION'
    calibration_resolver = calibration_resolver or CalibrationResolver(db)
    if not calibration_resolver.valid(reading['sensor_id'], reading['recorded_at']):
        return False, 'UNTRUSTED_OBSERVATION'
    if require_assignment:
        assigned = db.execute('SELECT 1 FROM ContainerAssignment a JOIN Sensor s USING(container_id) '
                              'WHERE a.shipment_id=? AND s.sensor_id=? AND a.started_at<=? '
                              'AND (a.ended_at IS NULL OR a.ended_at>=?)',
                              (reading['shipment_id'], reading['sensor_id'], reading['recorded_at'],
                               reading['recorded_at'])).fetchone()
        if not assigned:
            return False, 'UNTRUSTED_OBSERVATION'
    return True, 'VALID'


def analysis_fingerprint(db, shipment_id, observation_end):
    """Hash every input that can change the detector's decision for a shipment."""
    digest = hashlib.sha256()
    digest.update(str(observation_end).encode())
    queries = [
        ('SELECT * FROM Shipment WHERE shipment_id=? ORDER BY shipment_id', (shipment_id,)),
        ('SELECT * FROM ContainerAssignment WHERE shipment_id=? ORDER BY assignment_id', (shipment_id,)),
        ('SELECT s.* FROM Sensor s JOIN ContainerAssignment a USING(container_id) '
         'WHERE a.shipment_id=? ORDER BY s.sensor_id', (shipment_id,)),
        ('SELECT h.* FROM SensorCalibrationHistory h JOIN Sensor s USING(sensor_id) '
         'JOIN ContainerAssignment a USING(container_id) WHERE a.shipment_id=? '
         'ORDER BY h.sensor_id,h.calibrated_at', (shipment_id,)),
        ('SELECT r.* FROM SensorReading r WHERE r.shipment_id=? AND r.recorded_at<=? '
         'ORDER BY r.recorded_at,r.sensor_id,r.reading_id', (shipment_id, str(observation_end))),
        ('SELECT p.* FROM ThresholdPolicy p JOIN Shipment s ON s.product_id=p.product_id '
         'WHERE s.shipment_id=? ORDER BY p.effective_from,p.policy_id', (shipment_id,)),
    ]
    for sql, params in queries:
        for row in db.execute(sql, params):
            digest.update(json.dumps(dict(row), sort_keys=True, separators=(',', ':')).encode())
    return digest.hexdigest()


def validate_policy_product(db, shipment_id, policy_id):
    row = db.execute('SELECT s.product_id shipment_product,p.product_id policy_product '
                     'FROM Shipment s JOIN ThresholdPolicy p ON p.policy_id=? '
                     'WHERE s.shipment_id=?', (policy_id, shipment_id)).fetchone()
    if not row:
        raise ValueError('Shipment or policy not found')
    if row['shipment_product'] != row['policy_product']:
        raise ValueError('POLICY_PRODUCT_MISMATCH')
    return True


def flag_policy_mismatch(shipment_id, policy_id):
    with transaction() as out:
        row = out.execute('SELECT s.product_id shipment_product,p.product_id policy_product,'
                          's.planned_departure FROM Shipment s JOIN ThresholdPolicy p ON p.policy_id=? '
                          'WHERE s.shipment_id=?', (policy_id, shipment_id)).fetchone()
        if not row:
            raise ValueError('Shipment or policy not found')
        if row['shipment_product'] == row['policy_product']:
            return None
        when = iso_now()
        return _exception(out, shipment_id, None, 'POLICY_PRODUCT_MISMATCH', when, None, 0,
                          'CRITICAL', {'policy_id': policy_id,
                          'shipment_product_id': row['shipment_product'],
                          'policy_product_id': row['policy_product']})


def _source_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _exception(db, shipment_id, sensor_id, kind, start, end, duration, severity, evidence):
    """Upsert the current event projection and append a revision only for new evidence."""
    evidence = dict(evidence)
    evidence['condition_active'] = True
    evidence.pop('condition_resolution_reason', None)
    evidence.pop('condition_resolved_at', None)
    canonical = {'shipment_id': shipment_id, 'sensor_id': sensor_id, 'kind': kind,
                 'started_at': start, 'ended_at': end,
                 'duration_minutes': round(float(duration), 4), 'severity': severity,
                 'policy_id': evidence.get('policy_id'), 'evidence': evidence}
    digest = _source_hash(canonical)
    existing = db.execute('SELECT * FROM Excursion WHERE shipment_id=? AND sensor_id IS ? '
                          'AND kind=? AND started_at=?',
                          (shipment_id, sensor_id, kind, start)).fetchone()
    created = existing is None
    if created:
        sequence = db.execute("SELECT COALESCE(MAX(CAST(SUBSTR(excursion_id,10) AS INTEGER)),0)+1 "
                              "FROM Excursion WHERE excursion_id LIKE 'EXC-2026-%'").fetchone()[0]
        eid = f'EXC-2026-{sequence:06d}'
        db.execute('INSERT INTO Excursion VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (eid, shipment_id, sensor_id, kind, start, end, duration, severity, 'OPEN',
                    json.dumps(evidence, sort_keys=True)))
        db.execute('INSERT INTO ExcursionStatusEvent VALUES(?,?,?,?,?,?,?)',
                   ('ESE-'+eid+'-001', eid, None, 'OPEN', start, 'detector', 'Initial detection'))
        db.execute('INSERT OR IGNORE INTO Notification VALUES(?,?,?,?,0)',
                   (f'NTF-{eid}', eid, start, f'{kind} detected for shipment {shipment_id}'))
        action_id = f'ACT-{eid}'
        db.execute("INSERT OR IGNORE INTO CorrectiveAction VALUES(?,?,?,?,?,?,NULL,'OPEN','')",
                   (action_id, eid, 'Cold chain team', 'INVESTIGATE',
                    f'Investigate {kind} for shipment {shipment_id}', start))
        db.execute('INSERT OR IGNORE INTO CorrectiveActionEvent VALUES(?,?,?,?,?,?,?,?)',
                   ('CAE-'+action_id+'-001', action_id, None, 'OPEN', 'Cold chain team',
                    start, 'Detector created action', ''))
    else:
        eid = existing['excursion_id']
        if existing['evidence_json'] != json.dumps(evidence, sort_keys=True) or \
           existing['ended_at'] != end or abs(float(existing['duration_minutes']) - float(duration)) > 0.001 or \
           existing['severity'] != severity:
            db.execute('UPDATE Excursion SET ended_at=?,duration_minutes=?,severity=?,evidence_json=? '
                       'WHERE excursion_id=?', (end, duration, severity,
                       json.dumps(evidence, sort_keys=True), eid))
            if severity != existing['severity'] and severity in {'HIGH', 'CRITICAL'}:
                db.execute('INSERT OR IGNORE INTO Notification VALUES(?,?,?,?,0)',
                           (f'NTF-{eid}-{digest[:10]}', eid, iso_now(),
                            f'{kind} severity escalated to {severity} for shipment {shipment_id}'))
    has_revision = db.execute('SELECT 1 FROM ExcursionRevision WHERE excursion_id=? AND source_hash=?',
                              (eid, digest)).fetchone()
    if not has_revision:
        rev = db.execute('SELECT COALESCE(MAX(revision_no),0)+1 FROM ExcursionRevision WHERE excursion_id=?',
                         (eid,)).fetchone()[0]
        db.execute('INSERT INTO ExcursionRevision VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (f'EXR-{eid}-{rev:06d}', eid, rev,
                    evidence.get('assessment_as_of') or evidence.get('window_end') or start, start, end, duration,
                    severity, evidence.get('policy_id'), digest, json.dumps(evidence, sort_keys=True)))
    return eid


def _sensor_calibration_valid(db, sensor_id, reading_at):
    return CalibrationResolver(db).valid(sensor_id,reading_at)


def detect_gaps(db, shipment_id, sensor_id, interval_minutes=INTERVAL_MINUTES,
                window_start=None, window_end=None):
    """Return missing expected slots, including leading, trailing and total loss."""
    assignment = db.execute('SELECT a.started_at,a.ended_at,s.actual_departure,s.actual_arrival,'
                            's.planned_departure,s.planned_arrival,s.status FROM ContainerAssignment a '
                            'JOIN Shipment s USING(shipment_id) WHERE a.shipment_id=? '
                            'AND EXISTS(SELECT 1 FROM Sensor x WHERE x.sensor_id=? AND x.container_id=a.container_id) '
                            'ORDER BY a.started_at LIMIT 1', (shipment_id, sensor_id)).fetchone()
    if not assignment:
        return []
    requested_start = parse_time(window_start or assignment['actual_departure'] or assignment['started_at'] or assignment['planned_departure'])
    start = max(requested_start, parse_time(assignment['started_at']))
    if window_end:
        end = parse_time(window_end)
    elif assignment['actual_arrival']:
        end = parse_time(assignment['actual_arrival'])
    else:
        saved=db.execute('SELECT observed_through FROM MonitoringWindow WHERE shipment_id=?',(shipment_id,)).fetchone()
        latest = db.execute('SELECT MAX(recorded_at) FROM SensorReading WHERE shipment_id=? AND sensor_id=?',
                            (shipment_id, sensor_id)).fetchone()[0]
        end = parse_time(saved['observed_through']) if saved else (parse_time(latest) if latest else start)
    if assignment['ended_at']:
        end = min(end, parse_time(assignment['ended_at']))
    if end < start:
        return []
    sensor_kind=db.execute('SELECT kind FROM Sensor WHERE sensor_id=?',(sensor_id,)).fetchone()
    if not sensor_kind:return []
    expected_unit='C' if sensor_kind['kind']=='TEMP' else '%'
    rows = db.execute('SELECT * FROM SensorReading WHERE shipment_id=? AND sensor_id=? '
                      'AND recorded_at>=? AND recorded_at<=? ORDER BY recorded_at',
                      (shipment_id, sensor_id, start.isoformat(), end.isoformat())).fetchall()
    status_by_slot = {};off_grid=[];calibrations=CalibrationResolver(db)
    for row in rows:
        at = parse_time(row['recorded_at'])
        offset = (at-start).total_seconds()
        slot_no = round(offset/(interval_minutes*60))
        if abs(offset-slot_no*interval_minutes*60) > 1:
            off_grid.append(row)
            continue
        slot = start + timedelta(minutes=slot_no*interval_minutes)
        trusted, reason = reading_trust(db, row, sensor_kind['kind'], start, calibrations,
                                        require_assignment=False)
        status_by_slot[slot] = 'VALID' if trusted else reason
    expected = []
    current = start
    step = timedelta(minutes=interval_minutes)
    while current <= end:
        expected.append(current)
        current += step
    latest_valid=max((slot for slot,state in status_by_slot.items() if state=='VALID'),default=None)
    missing = [slot for slot in expected if status_by_slot.get(slot) != 'VALID']
    spans = []
    for slot in missing:
        classification = status_by_slot.get(slot)
        if classification is None:
            classification='STALE_OBSERVATION' if latest_valid is not None and slot>latest_valid else 'MISSING_EXPECTED_READINGS'
        if spans and slot == spans[-1]['_end'] + step and classification == spans[-1]['classification']:
            spans[-1]['_end'] = slot
            spans[-1]['missing_count'] += 1
        else:
            spans.append({'_start': slot, '_end': slot, 'missing_count': 1,
                          'classification': classification})
    result = []
    for span in spans:
        result.append({'start': span['_start'].isoformat(), 'end': span['_end'].isoformat(),
                       'missing_count': span['missing_count'],
                       'duration_minutes': span['missing_count'] * interval_minutes,
                       'classification': span['classification'],
                       'window_start': start.isoformat(), 'window_end': end.isoformat()})
    for row in off_grid:
        at=parse_time(row['recorded_at'])
        result.append({'start':at.isoformat(),'end':at.isoformat(),'missing_count':0,'duration_minutes':0,
                       'classification':'OFF_GRID_OBSERVATION','off_grid_reading_id':row['reading_id'],
                       'window_start':start.isoformat(),'window_end':end.isoformat()})
    return result


def _is_expected_unit(db, sensor_id, reading):
    row = db.execute('SELECT kind FROM Sensor WHERE sensor_id=?', (sensor_id,)).fetchone()
    return bool(row and reading['unit'] == ('C' if row['kind'] == 'TEMP' else '%'))


def _duration_from_evidence(db, row, evidence):
    shipment=db.execute('SELECT * FROM Shipment WHERE shipment_id=?',(row['shipment_id'],)).fetchone()
    journey_start,journey_end=observation_window(db,shipment,evidence.get('assessment_as_of'))
    sources = evidence.get('readings') or []
    ids = [item.get('reading_id') for item in sources]
    if not ids or any(not rid for rid in ids) or len(ids) != len(set(ids)):
        raise ValueError('Excursion evidence has missing or duplicate reading IDs')
    marks = ','.join('?' for _ in ids)
    linked = db.execute(f'SELECT r.*,s.kind FROM SensorReading r JOIN Sensor s USING(sensor_id) '
                        f'WHERE r.reading_id IN ({marks})', ids).fetchall()
    by_id = {r['reading_id']: r for r in linked}
    if len(by_id) != len(ids):
        raise ValueError('Excursion source reading is missing')
    readings = [by_id[rid] for rid in ids]
    allowed_sensors=set(evidence.get('sensor_ids') or ([row['sensor_id']] if row['sensor_id'] else []))
    for source_item,r in zip(sources,readings):
        source_at=parse_time(r['recorded_at'])
        if source_at<journey_start or source_at>journey_end:
            raise ValueError('Excursion source reading is outside the shipment window')
        if r['shipment_id'] != row['shipment_id'] or r['sensor_id'] not in allowed_sensors or r['kind'] != row['kind']:
            raise ValueError('Excursion evidence reading belongs to another shipment, sensor, or modality')
        physical = PHYSICAL_RANGES[r['kind']][0] <= float(r['value']) <= PHYSICAL_RANGES[r['kind']][1]
        if r['quality'] != 'VALID' or not _is_expected_unit(db, r['sensor_id'], r) or not physical:
            raise ValueError('Excursion evidence contains an untrusted reading')
        if source_item.get('at') and parse_time(source_item['at'])!=parse_time(r['recorded_at']):
            raise ValueError('Serialized evidence timestamp differs from its authoritative source')
        if source_item.get('value') is not None and abs(float(source_item['value'])-float(r['value']))>1e-9:
            raise ValueError('Serialized evidence value differs from its authoritative source')
        if not _sensor_calibration_valid(db, r['sensor_id'], r['recorded_at']):
            raise ValueError('Excursion evidence calibration is invalid at reading time')
        assignment = db.execute('SELECT 1 FROM ContainerAssignment a JOIN Sensor s USING(container_id) '
                                'WHERE a.shipment_id=? AND s.sensor_id=? AND a.started_at<=? '
                                'AND (a.ended_at IS NULL OR a.ended_at>=?)',
                                (row['shipment_id'], r['sensor_id'], r['recorded_at'], r['recorded_at'])).fetchone()
        if not assignment:
            raise ValueError('Excursion source reading is outside its assignment')
        policy = applicable_policy(db, db.execute('SELECT product_id FROM Shipment WHERE shipment_id=?',
                                                  (row['shipment_id'],)).fetchone()['product_id'],
                                   r['recorded_at'])
        if evidence.get('policy_id') and policy['policy_id'] != evidence['policy_id']:
            raise ValueError('Excursion evidence policy does not match its source reading')
        bound_low, bound_high = ((policy['temp_min'], policy['temp_max']) if r['kind']=='TEMP'
                                 else (policy['humidity_min'], policy['humidity_max']))
        if bound_low <= r['value'] <= bound_high:
            raise ValueError('Excursion evidence includes a non-violating source reading')
    if parse_time(readings[0]['recorded_at']) != parse_time(row['started_at']):
        raise ValueError('Excursion start does not match its first source reading')
    instants = [parse_time(r['recorded_at']) for r in readings]
    if instants != sorted(instants):
        raise ValueError('Excursion source readings are reordered')
    for a, b in zip(instants, instants[1:]):
        if b-a > timedelta(minutes=INTERVAL_MINUTES):
            raise ValueError('Excursion source crosses an unobserved gap')
    recovery_id = evidence.get('recovery_reading_id')
    if recovery_id:
        recovery = db.execute('SELECT r.*,s.kind FROM SensorReading r JOIN Sensor s USING(sensor_id) '
                              'WHERE reading_id=?', (recovery_id,)).fetchone()
        if not recovery or recovery['shipment_id'] != row['shipment_id'] or \
           recovery['sensor_id'] not in allowed_sensors or recovery['kind'] != row['kind']:
            raise ValueError('Recovery reading belongs to another shipment, sensor, or modality')
        recovery_kind = recovery['kind']
        recovery_physical = PHYSICAL_RANGES[recovery_kind][0] <= float(recovery['value']) <= PHYSICAL_RANGES[recovery_kind][1]
        if recovery['quality'] != 'VALID' or not _is_expected_unit(db, recovery['sensor_id'], recovery) or not recovery_physical:
            raise ValueError('Recovery reading is not trusted')
        if not _sensor_calibration_valid(db,recovery['sensor_id'],recovery['recorded_at']):
            raise ValueError('Recovery calibration is invalid at reading time')
        recovery_assignment=db.execute('SELECT 1 FROM ContainerAssignment a JOIN Sensor s USING(container_id) '
              'WHERE a.shipment_id=? AND s.sensor_id=? AND a.started_at<=? AND (a.ended_at IS NULL OR a.ended_at>=?)',
              (row['shipment_id'],recovery['sensor_id'],recovery['recorded_at'],recovery['recorded_at'])).fetchone()
        if not recovery_assignment:raise ValueError('Recovery reading is outside its sensor assignment')
        recovery_at = parse_time(recovery['recorded_at'])
        if recovery_at <= instants[-1] or recovery_at-instants[-1] > timedelta(minutes=INTERVAL_MINUTES):
            raise ValueError('Recovery does not immediately follow the excursion evidence')
        shipment_product=db.execute('SELECT product_id FROM Shipment WHERE shipment_id=?',(row['shipment_id'],)).fetchone()[0]
        policy=applicable_policy(db,shipment_product,recovery['recorded_at'])
        if recovery_at<journey_start or recovery_at>journey_end:raise ValueError('Recovery reading is outside the shipment window')
        recovery_low,recovery_high=((policy['temp_min'],policy['temp_max']) if row['kind']=='TEMP' else (policy['humidity_min'],policy['humidity_max']))
        if not recovery_low<=recovery['value']<=recovery_high:
            raise ValueError('Recovery source reading is still outside the product policy')
        calculated = (recovery_at - instants[0]).total_seconds()/60
    else:
        end=instants[-1]+timedelta(minutes=INTERVAL_MINUTES)
        if evidence.get('assessment_as_of'):
            end=min(end,parse_time(evidence['assessment_as_of']))
        calculated = max(0,(end-instants[0]).total_seconds()/60)
    return calculated, readings


def check_excursion_duration(db, excursion_id):
    """Verify stored excursion timing using authoritative, linked reading rows."""
    row = db.execute('SELECT * FROM Excursion WHERE excursion_id=?', (excursion_id,)).fetchone()
    if not row:
        raise ValueError('Excursion not found')
    evidence = json.loads(row['evidence_json'] or '{}')
    calculated, readings = _duration_from_evidence(db, row, evidence)
    latest = db.execute('SELECT MAX(r.recorded_at) FROM SensorReading r JOIN Sensor s USING(sensor_id) '
                        'WHERE r.shipment_id=? AND s.kind=? AND r.quality=\'VALID\'',
                        (row['shipment_id'], row['kind'])).fetchone()[0]
    evidence_end = evidence.get('recovery_at') or readings[-1]['recorded_at']
    stale = row['ended_at'] is None and not evidence.get('recovery_reading_id') and latest is not None and parse_time(latest) > parse_time(evidence_end)
    undercount = verify_duration_report(calculated, row['duration_minutes'])
    if undercount or stale:
        payload = {'source_excursion_id': excursion_id,
                   'reported_duration_minutes': row['duration_minutes'],
                   'recalculated_duration_minutes': calculated, 'stale_evidence': stale,
                   'source_reading_ids': [r['reading_id'] for r in readings]}
        with transaction(connection_path(db)) as out:
            _exception(out, row['shipment_id'], row['sensor_id'], 'DURATION_UNDERCOUNT',
                       row['started_at'], row['ended_at'], calculated, 'MEDIUM', payload)
    return {'excursion_id': excursion_id, 'reported_duration_minutes': row['duration_minutes'],
            'recalculated_duration_minutes': calculated, 'stale_evidence': stale,
            'flagged': undercount or stale}


def verify_duration_report(calculated, reported):
    """Shared decision rule for rejecting a duration that understates source evidence."""
    return float(calculated)-float(reported) > 0.01


def _severity(duration, limit):
    if duration <= limit:
        return 'LOW'
    if duration >= 60:
        return 'CRITICAL'
    if duration <= 2*limit:
        return 'MEDIUM'
    return 'HIGH'


def _reconcile_disappeared_conditions(db, shipment_id, current_keys, window_start, window_end):
    """Keep audit events but mark detector conditions removed by late/current evidence."""
    managed = ('TEMP', 'HUM', 'SENSOR_GAP', 'INVALID_PHYSICAL_RANGE', 'CALIBRATION_FAILURE')
    marks = ','.join('?' for _ in managed)
    rows = db.execute(f'SELECT * FROM Excursion WHERE shipment_id=? AND kind IN ({marks}) '
                      'AND started_at>=? AND started_at<=?',
                      (shipment_id, *managed, window_start, window_end)).fetchall()
    for row in rows:
        key = (row['sensor_id'], row['kind'], row['started_at'])
        evidence = json.loads(row['evidence_json'] or '{}')
        if key in current_keys or evidence.get('condition_active') is False:
            continue
        evidence['condition_active'] = False
        evidence['condition_resolution_reason'] = 'LATE_OR_CURRENT_EVIDENCE_REMOVED_CONDITION'
        evidence['condition_resolved_at'] = window_end
        evidence['operational_review_status'] = row['status']
        evidence['historical_source_contract'] = 'EXCURSION_REVISION_SNAPSHOT'
        digest = _source_hash({'excursion_id': row['excursion_id'], 'resolution': evidence})
        revision = db.execute('SELECT COALESCE(MAX(revision_no),0)+1 FROM ExcursionRevision '
                              'WHERE excursion_id=?', (row['excursion_id'],)).fetchone()[0]
        db.execute('UPDATE Excursion SET evidence_json=? WHERE excursion_id=?',
                   (json.dumps(evidence, sort_keys=True), row['excursion_id']))
        db.execute('INSERT OR IGNORE INTO ExcursionRevision VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (f"EXR-{row['excursion_id']}-{revision:06d}", row['excursion_id'], revision,
                    window_end, row['started_at'], row['ended_at'], row['duration_minutes'],
                    row['severity'], evidence.get('policy_id'), digest,
                    json.dumps(evidence, sort_keys=True)))


def _condition_groups(condition_slots, start, end):
    """Build modality-level runs; a trusted sensor handoff cannot reset duration."""
    groups=[];step=timedelta(minutes=INTERVAL_MINUTES)
    for kind in REQUIRED_MODALITIES:
        active=None;cursor=start
        while cursor<=end:
            candidates=condition_slots[kind].get(cursor.isoformat(),[])
            policies={item['policy_id'] for item in candidates}
            known=bool(candidates) and len(policies)==1
            violations=[item for item in candidates if item['violation']]
            all_violating=known and len(violations)==len(candidates)
            all_safe=known and not violations
            policy_id=next(iter(policies)) if known else None
            if active and (not known or policy_id!=active['policy_id']):
                groups.append(_finish_segment(active,None,None,cursor,
                              'POLICY_CHANGE' if known else 'GAP_OR_INVALID_OBSERVATION'))
                active=None
            if all_violating:
                first=sorted(violations,key=lambda x:(x['sensor_id'],x['reading_id']))[0]
                if active is None:
                    active={'sensor_id':first['sensor_id'],'sensor_ids':set(),'kind':kind,
                            'start':cursor.isoformat(),'last':cursor.isoformat(),
                            'limit':first['limit'],'policy_id':policy_id,
                            'low':first['low'],'high':first['high'],'readings':[]}
                active['last']=cursor.isoformat()
                for item in sorted(violations,key=lambda x:(x['sensor_id'],x['reading_id'])):
                    active['sensor_ids'].add(item['sensor_id'])
                    active['readings'].append({'reading_id':item['reading_id'],'sensor_id':item['sensor_id'],
                                               'value':item['value'],'at':cursor.isoformat()})
            elif active and all_safe:
                recovery=sorted(candidates,key=lambda x:(x['sensor_id'],x['reading_id']))[0]
                active['sensor_ids'].add(recovery['sensor_id'])
                groups.append(_finish_segment(active,cursor.isoformat(),recovery['reading_id'],end))
                active=None
            elif active:
                groups.append(_finish_segment(active,None,None,cursor,'GAP_OR_INVALID_OBSERVATION'))
                active=None
            cursor+=step
        if active:groups.append(_finish_segment(active,None,None,end))
    return groups


def _detect_excursions_impl(db, shipment_id, as_of=None):
    shipment = db.execute('SELECT * FROM Shipment WHERE shipment_id=?', (shipment_id,)).fetchone()
    if not shipment:
        raise ValueError('Shipment not found')
    start, end = observation_window(db, shipment, as_of)
    sensors = db.execute('SELECT DISTINCT x.sensor_id,x.kind,x.calibration_status,x.calibrated_at,a.started_at,a.ended_at '
                         'FROM ContainerAssignment a JOIN Sensor x USING(container_id) '
                         'WHERE a.shipment_id=? AND a.started_at<=? AND (a.ended_at IS NULL OR a.ended_at>=?)',
                         (shipment_id, end.isoformat(), start.isoformat())).fetchall()
    groups = [];condition_slots={kind:{} for kind in REQUIRED_MODALITIES}
    calibration_resolver=CalibrationResolver(db);policy_resolver=PolicyResolver(db)
    for sensor_row in sensors:
        sensor = sensor_row['sensor_id']
        kind = sensor_row['kind']
        unit = 'C' if kind == 'TEMP' else '%'
        sensor_start = max(start, parse_time(sensor_row['started_at']))
        sensor_end = min(end, parse_time(sensor_row['ended_at'])) if sensor_row['ended_at'] else end
        if sensor_end < sensor_start:
            continue
        rows = db.execute('SELECT * FROM SensorReading WHERE shipment_id=? AND sensor_id=? '
                          'AND recorded_at>=? AND recorded_at<=? ORDER BY recorded_at,reading_id',
                          (shipment_id, sensor, sensor_start.isoformat(), sensor_end.isoformat())).fetchall()
        # A calibration fault is a quality exception, while readings remain in the source table.
        if not calibration_resolver.valid(sensor, sensor_start.isoformat()):
            groups.append({'sensor_id': sensor, 'kind': 'CALIBRATION_FAILURE', 'start': sensor_start.isoformat(),
                           'end': None, 'duration': 0, 'severity': 'CRITICAL',
                           'evidence': {'calibrated_at': sensor_row['calibrated_at'],
                                        'calibration_status': sensor_row['calibration_status']}})
        for reading in rows:
            at = parse_time(reading['recorded_at'])
            trusted, trust_reason = reading_trust(db, reading, kind, start,
                                                  calibration_resolver, require_assignment=False)
            if not trusted:
                if trust_reason == 'INVALID_PHYSICAL_RANGE':
                    groups.append({'sensor_id': sensor, 'kind': 'INVALID_PHYSICAL_RANGE',
                                   'start': reading['recorded_at'], 'end': reading['recorded_at'],
                                   'duration': 0, 'severity': 'CRITICAL',
                                   'evidence': {'reading_id': reading['reading_id'], 'value': reading['value'],
                                                'unit': reading['unit'], 'quality': reading['quality']}})
                continue
            try:
                policy = applicable_policy(db, shipment['product_id'], reading['recorded_at'],policy_resolver)
            except ValueError as exc:
                raise ValueError(str(exc))
            low, high = ((policy['temp_min'], policy['temp_max']) if kind == 'TEMP'
                         else (policy['humidity_min'], policy['humidity_max']))
            violation = reading['value'] < low or reading['value'] > high
            condition_slots[kind].setdefault(at.isoformat(),[]).append({
                'sensor_id':sensor,'reading_id':reading['reading_id'],'value':reading['value'],
                'policy_id':policy['policy_id'],'limit':policy['max_excursion_minutes'],
                'low':low,'high':high,'violation':violation})
        for gap in detect_gaps(db, shipment_id, sensor, window_start=sensor_start.isoformat(), window_end=sensor_end.isoformat()):
            groups.append({'sensor_id': sensor, 'kind': 'SENSOR_GAP', 'start': gap['start'], 'end': gap['end'],
                           'duration': gap['duration_minutes'], 'severity': 'HIGH', 'evidence': gap})
    groups.extend(_condition_groups(condition_slots,start,end))
    for stop in db.execute("SELECT * FROM RouteStop WHERE shipment_id=? AND (status='DELAYED' OR (actual_at IS NOT NULL AND actual_at>planned_at))",
                           (shipment_id,)):
        delay = max(0, (parse_time(stop['actual_at'])-parse_time(stop['planned_at'])).total_seconds()/60) if stop['actual_at'] else 0
        groups.append({'sensor_id': None, 'kind': 'ROUTE_DELAY', 'start': stop['planned_at'],
                       'end': stop['actual_at'], 'duration': delay,
                       'severity': 'HIGH' if delay >= 60 else 'MEDIUM',
                       'evidence': {'stop_id': stop['stop_id'], 'sequence_no': stop['sequence_no'],
                                    'location': stop['location'], 'planned_at': stop['planned_at'],
                                    'actual_at': stop['actual_at']}})
    ids = []
    with transaction(connection_path(db)) as out:
        if not shipment['actual_arrival']:
            out.execute('INSERT INTO MonitoringWindow VALUES(?,?,?,?,?) ON CONFLICT(shipment_id) DO UPDATE SET observed_through=excluded.observed_through,updated_at=excluded.updated_at,source=excluded.source',
                        (shipment_id,start.isoformat(),end.isoformat(),end.isoformat(),'detector assessment horizon'))
        current_keys = set()
        for group in groups:
            evidence = group.get('evidence')
            if evidence is None:
                duration = max(0, (parse_time(group['recovery']) - parse_time(group['start'])).total_seconds()/60) \
                    if group.get('recovery') else group['duration']
                evidence = {'basis': 'sampled source readings; unknown gap time is censored',
                            'readings': group.get('readings', []),
                            'sensor_ids': group.get('sensor_ids') or [group.get('sensor_id')],
                            'recovery_reading_id': group.get('recovery_reading_id'),
                            'recovery_at': group.get('recovery'), 'assessment_as_of': end.isoformat(),
                            'censored_at':group.get('end') if not group.get('recovery') else None,
                            'censor_reason':group.get('censor_reason'),
                            'policy_id': group.get('policy_id'), 'policy_low': group.get('low'),
                            'policy_high': group.get('high'), 'allowed_duration_minutes': group.get('limit')}
                severity = _severity(duration, group['limit'])
            else:
                duration = group['duration']
                severity = group['severity']
            current_keys.add((group['sensor_id'], group['kind'], group['start']))
            ids.append(_exception(out, shipment_id, group['sensor_id'], group['kind'], group['start'],
                                  group.get('recovery') or (group.get('end') if group.get('censor_reason') not in (None,'END_OF_OBSERVATION_WINDOW') else None),
                                  duration, severity, evidence))
        _reconcile_disappeared_conditions(out, shipment_id, current_keys,
                                          start.isoformat(), end.isoformat())
    return sorted(set(ids))


def _record_analysis(db, shipment_id, status, start, end, source_hash=None, error=''):
    db.execute('INSERT INTO DetectionAnalysis VALUES(?,?,?,?,?,?,?) '
               'ON CONFLICT(shipment_id) DO UPDATE SET status=excluded.status,'
               'observation_start=excluded.observation_start,observation_end=excluded.observation_end,'
               'source_hash=excluded.source_hash,completed_at=excluded.completed_at,error_message=excluded.error_message',
               (shipment_id, status, start.isoformat(), end.isoformat(), source_hash,
                iso_now() if status != 'RUNNING' else None, error[:1000]))


def detect_excursions(db, shipment_id, as_of=None):
    """Run detection and persist freshness/failure state for compliance assessment."""
    shipment = db.execute('SELECT * FROM Shipment WHERE shipment_id=?', (shipment_id,)).fetchone()
    if not shipment:
        raise ValueError('Shipment not found')
    start, end = observation_window(db, shipment, as_of)
    with transaction(connection_path(db)) as out:
        _record_analysis(out, shipment_id, 'RUNNING', start, end)
    try:
        ids = _detect_excursions_impl(db, shipment_id, as_of)
        source_hash = analysis_fingerprint(db, shipment_id, end.isoformat())
        with transaction(connection_path(db)) as out:
            _record_analysis(out, shipment_id, 'SUCCESS', start, end, source_hash)
        return ids
    except Exception as exc:
        with transaction(connection_path(db)) as out:
            _record_analysis(out, shipment_id, 'FAILED', start, end, error=str(exc))
        raise


def _finish_segment(active, recovery_at, recovery_id, window_end, censor_reason=None):
    first = parse_time(active['start'])
    last = parse_time(active['last'])
    if recovery_at:
        duration = (parse_time(recovery_at)-first).total_seconds()/60
        ending = recovery_at
    else:
        ending = min(last+timedelta(minutes=INTERVAL_MINUTES), window_end)
        duration = max(0, (ending-first).total_seconds()/60)
    if isinstance(ending,datetime):ending=ending.isoformat()
    return {'sensor_id': active['sensor_id'], 'kind': active['kind'], 'start': active['start'],
            'sensor_ids':sorted(active.get('sensor_ids') or {active['sensor_id']}),
            'last': active['last'], 'recovery': recovery_at, 'recovery_reading_id': recovery_id,
            'duration': duration, 'limit': active['limit'], 'policy_id': active['policy_id'],
            'low': active['low'], 'high': active['high'], 'readings': active['readings'],
            'evidence': None, 'end': ending,
            'censor_reason':None if recovery_at else (censor_reason or 'END_OF_OBSERVATION_WINDOW')}
