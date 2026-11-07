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
| Sensor | sensor_id | TEXT | Yes | PK; SNS-CNT-######-TEMP/HUM |
| Sensor | container_id | TEXT | Yes | FK Container |
| Sensor | kind | TEXT | Yes; TEMP, HUM | Unique per container and kind |
| Sensor | calibrated_at | TEXT | Yes | ISO 8601 UTC |
| Sensor | calibration_status | TEXT | Yes; VALID, EXPIRED, FAILED | CHECK constraint; non-valid status raises exception |
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
| Notification | notification_id | TEXT | Yes | PK |
| Notification | excursion_id | TEXT | Yes | FK Excursion |
| Notification | created_at, message | TEXT | Yes | Required event notice |
| Notification | acknowledged | INTEGER | Yes; 0 or 1 | CHECK constraint |

ContainerAssignment is the bridge/history table for shipment and container allocation. LifecycleEvent and CustodyEvent are append-only historical records.
