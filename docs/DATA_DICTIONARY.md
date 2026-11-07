# Data dictionary

All timestamps are ISO 8601 UTC text. PK is primary key; FK is foreign key. Required fields are NOT NULL unless marked optional. Reading IDs derive from sensor ID and UTC timestamp. Numeric units are Celsius, percent, or minutes as indicated.

| Entity | Field | Type | Required / allowed values | Uniqueness and validation |
|---|---|---|---|---|
| Product | product_id | TEXT | Yes | PK; immutable demo ID |
| Product | name | TEXT | Yes | UNIQUE |
| Product | temp_min, temp_max | REAL | Yes, °C | Minimum must be below maximum |
| Product | humidity_min, humidity_max | REAL | Yes, % | Minimum must be below maximum |
| Product | max_excursion_minutes | INTEGER | Yes, minutes | Positive |
| Shipment | shipment_id | TEXT | Yes | PK, SHP-YYYY-###### |
| Shipment | product_id | TEXT | Yes | FK Product |
| Shipment | origin, destination | TEXT | Yes | Non-empty location names |
| Shipment | planned_departure, planned_arrival | TEXT | Yes | Arrival after departure |
| Shipment | actual_departure, actual_arrival | TEXT | Optional | If both set, arrival cannot precede departure |
| Shipment | status | TEXT | Yes; CREATED, READY, IN_TRANSIT, DELAYED, EXCEPTION, DELIVERED, CLOSED | Transition service validates order |
| Container | container_id | TEXT | Yes | PK, CNT-###### |
| Container | container_type | TEXT | Yes | Non-empty |
| Container | status | TEXT | Yes; AVAILABLE, IN_USE, MAINTENANCE, RETIRED | CHECK constraint |
| ContainerAssignment | assignment_id | TEXT | Yes | PK |
| ContainerAssignment | container_id, shipment_id | TEXT | Yes | FK Container and Shipment; pair UNIQUE |
| ContainerAssignment | started_at, ended_at | TEXT | Start required, end optional | End must not precede start; validation detects overlapping assignments |
| MonitoringWindow | shipment_id | TEXT | Yes | PK/FK Shipment; one explicit assessment horizon per shipment |
| MonitoringWindow | observation_start, observed_through | TEXT | Yes | UTC window used for expected sensor coverage; end cannot precede start |
| MonitoringWindow | updated_at, source | TEXT | Yes | UTC update and documented horizon source |
| DetectionAnalysis | shipment_id | TEXT | Yes | PK/FK Shipment; current detector state for one shipment |
| DetectionAnalysis | status | TEXT | Yes | RUNNING, SUCCESS, or FAILED; compliance requires SUCCESS |
| DetectionAnalysis | observation_start, observation_end | TEXT | Yes | Exact source-analysis horizon; actual arrival is authoritative for delivered trips |
| DetectionAnalysis | source_hash | TEXT | No | Hash of current shipment, assignments, sensors, calibration, readings, policies, and horizon |
| DetectionAnalysis | completed_at, error_message | TEXT | No/Yes | Operational completion time and bounded failure reason; excluded from deterministic checksums |
| Sensor | sensor_id | TEXT | Yes | PK; SNS-CNT-######-TEMP/HUM |
| Sensor | container_id | TEXT | Yes | FK Container |
| Sensor | kind | TEXT | Yes; TEMP, HUM | Unique per container and kind |
| Sensor | calibrated_at | TEXT | Yes | ISO 8601 UTC |
| Sensor | calibration_status | TEXT | Yes; VALID, EXPIRED, FAILED | CHECK constraint; non-valid status raises exception |
| SensorCalibrationHistory | calibration_id | TEXT | Yes | PK |
| SensorCalibrationHistory | sensor_id, calibrated_at, status | TEXT | Yes | FK Sensor; calibration is evaluated as of each reading |
| SensorCalibrationHistory | recorded_at, notes | TEXT | Yes | Audit timestamp and retained calibration evidence |
| SensorReading | reading_id | TEXT | Yes | PK; RDG-sensor-timestamp |
| SensorReading | sensor_id, shipment_id | TEXT | Yes | FK Sensor and Shipment |
| SensorReading | recorded_at | TEXT | Yes | UNIQUE with sensor_id |
| SensorReading | value | REAL | Yes | Physical bounds checked by validator/detector |
| SensorReading | unit | TEXT | Yes; C, % | CHECK constraint |
| SensorReading | quality | TEXT | Yes; VALID, INVALID_PHYSICAL_RANGE, INVALID_SENSOR, CALIBRATION_FAILURE | CHECK constraint; invalid readings excluded from product excursion logic |
| RouteStop | stop_id | TEXT | Yes | PK |
| RouteStop | shipment_id | TEXT | Yes | FK Shipment |
| RouteStop | sequence_no | INTEGER | Yes | Positive and unique within shipment |
| RouteStop | location | TEXT | Yes | Non-empty |
| RouteStop | planned_at, actual_at | TEXT | Planned required, actual optional | ISO 8601; later actual time is route delay evidence |
| RouteStop | status | TEXT | Yes; PLANNED, COMPLETED, DELAYED, EXCEPTION | CHECK constraint |
| ThresholdPolicy | policy_id | TEXT | Yes | PK |
| ThresholdPolicy | product_id | TEXT | Yes | FK Product; detector matches shipment product |
| ThresholdPolicy | effective_from, effective_to | TEXT | Start required, end optional | End must be after start; validation checks overlaps |
| ThresholdPolicy | temp_min, temp_max | REAL | Yes, °C | Minimum must be below maximum |
| ThresholdPolicy | humidity_min, humidity_max | REAL | Yes, % | Minimum must be below maximum |
| ThresholdPolicy | max_excursion_minutes | INTEGER | Yes | Positive |
| Excursion | excursion_id | TEXT | Yes | PK, deterministic EXC ID |
| Excursion | shipment_id | TEXT | Yes | FK Shipment |
| Excursion | sensor_id | TEXT | Optional | FK Sensor; operational events such as route delay may have none |
| Excursion | kind | TEXT | Yes | TEMP, HUM, SENSOR_GAP, INVALID_PHYSICAL_RANGE, CALIBRATION_FAILURE, ROUTE_DELAY, POLICY_PRODUCT_MISMATCH, DURATION_UNDERCOUNT, INVALID_SHIPMENT_TIMELINE |
| Excursion | started_at, ended_at | TEXT | Start required, end optional | ISO 8601; open incidents have no end |
| Excursion | duration_minutes | REAL | Yes | Non-negative; checked against source evidence |
| Excursion | severity | TEXT | Yes; LOW, MEDIUM, HIGH, CRITICAL | CHECK constraint |
| Excursion | status | TEXT | Yes; OPEN, ACKNOWLEDGED, CLOSED | Operational state |
| Excursion | evidence_json | TEXT | Yes; defaults to empty object | Serialized source IDs, readings, policy/range or route evidence |
| ExcursionRevision | revision_id | TEXT | Yes | PK; one immutable revision per changed source hash |
| ExcursionRevision | excursion_id, revision_no | TEXT, INTEGER | Yes | FK Excursion; revision number unique per event |
| ExcursionRevision | assessed_at, started_at, ended_at, duration_minutes, severity | TEXT/REAL | Yes except end | Source-derived current-event projection history |
| ExcursionRevision | policy_id, source_hash, evidence_json | TEXT | Policy optional; hash/evidence required | Captures policy lineage and canonical source evidence |
| ExcursionStatusEvent | event_id, excursion_id | TEXT | Yes | PK and FK Excursion |
| ExcursionStatusEvent | previous_status, new_status, occurred_at, actor, reason | TEXT | New status/time/actor required | Append-only acknowledgement and closure history |
| CustodyEvent | event_id | TEXT | Yes | PK, deterministic |
| CustodyEvent | shipment_id | TEXT | Yes | FK Shipment |
| CustodyEvent | previous_custodian | TEXT | Optional | Prior party, absent for first event |
| CustodyEvent | new_custodian, event_type, location | TEXT | Yes | Required handoff details |
| CustodyEvent | occurred_at | TEXT | Yes | Timezone-aware; strictly chronological per shipment |
| CustodyEvent | notes | TEXT | Yes | Defaults to empty text |
| LifecycleEvent | event_id | TEXT | Yes | PK |
| LifecycleEvent | shipment_id | TEXT | Yes | FK Shipment |
| LifecycleEvent | previous_status, new_status | TEXT | Yes; shipment status values | CHECK constraints and transition validation |
| LifecycleEvent | occurred_at, location | TEXT | Yes | ISO 8601 UTC and event location |
| LifecycleEvent | notes | TEXT | Yes | Defaults to empty text |
| CorrectiveAction | action_id | TEXT | Yes | PK, deterministic ACT ID |
| CorrectiveAction | excursion_id | TEXT | Yes | FK Excursion |
| CorrectiveAction | assigned_to, action_type, description | TEXT | Yes | Required assignment and work details |
| CorrectiveAction | created_at | TEXT | Yes | ISO 8601 UTC |
| CorrectiveAction | completed_at | TEXT | Optional | Set on transition to COMPLETED |
| CorrectiveAction | status | TEXT | Yes; OPEN, IN_PROGRESS, COMPLETED, CANCELLED | CHECK and transition rules |
| CorrectiveAction | resolution_notes | TEXT | Yes | Defaults to empty text |
| CorrectiveActionEvent | event_id, action_id | TEXT | Yes | PK and FK CorrectiveAction |
| CorrectiveActionEvent | previous_status, new_status, assigned_to, occurred_at | TEXT | Required except previous status | Append-only state and assignment history |
| CorrectiveActionEvent | reason, resolution_notes | TEXT | Yes | Retains completion evidence and transition reason |
| Notification | notification_id | TEXT | Yes | PK |
| Notification | excursion_id | TEXT | Yes | FK Excursion |
| Notification | created_at, message | TEXT | Yes | Required event notice |
| Notification | acknowledged | INTEGER | Yes; 0 or 1 | CHECK constraint |

ContainerAssignment is the bridge/history table for shipment and container allocation. MonitoringWindow records the explicit observation boundary for active journeys. DetectionAnalysis prevents missing, failed, or stale detector output from certifying compliance. LifecycleEvent, CustodyEvent, ExcursionStatusEvent, ExcursionRevision, and CorrectiveActionEvent are append-only historical records.
