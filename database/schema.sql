PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS Product (
 product_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
 temp_min REAL NOT NULL, temp_max REAL NOT NULL,
 humidity_min REAL NOT NULL, humidity_max REAL NOT NULL,
 max_excursion_minutes INTEGER NOT NULL CHECK(max_excursion_minutes > 0),
 CHECK(temp_min < temp_max), CHECK(humidity_min < humidity_max)
);
CREATE TABLE IF NOT EXISTS Shipment (
 shipment_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES Product(product_id),
 origin TEXT NOT NULL, destination TEXT NOT NULL, planned_departure TEXT NOT NULL,
 planned_arrival TEXT NOT NULL, actual_departure TEXT, actual_arrival TEXT,
 status TEXT NOT NULL CHECK(status IN ('CREATED','READY','IN_TRANSIT','DELAYED','EXCEPTION','DELIVERED','CLOSED')),
 CHECK(planned_arrival > planned_departure),
 CHECK(actual_arrival IS NULL OR actual_departure IS NULL OR actual_arrival >= actual_departure)
);
CREATE INDEX IF NOT EXISTS idx_shipment_status ON Shipment(status);
CREATE TABLE IF NOT EXISTS Container (
 container_id TEXT PRIMARY KEY, container_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK(status IN ('AVAILABLE','IN_USE','MAINTENANCE','RETIRED'))
);
CREATE TABLE IF NOT EXISTS ContainerAssignment (
 assignment_id TEXT PRIMARY KEY, container_id TEXT NOT NULL REFERENCES Container(container_id),
 shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id), started_at TEXT NOT NULL, ended_at TEXT,
 UNIQUE(container_id, shipment_id), CHECK(ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX IF NOT EXISTS idx_assignment_shipment ON ContainerAssignment(shipment_id);
CREATE TABLE IF NOT EXISTS MonitoringWindow (
 shipment_id TEXT PRIMARY KEY REFERENCES Shipment(shipment_id),
 observation_start TEXT NOT NULL, observed_through TEXT NOT NULL,
 updated_at TEXT NOT NULL, source TEXT NOT NULL,
 CHECK(observed_through >= observation_start)
);
CREATE TABLE IF NOT EXISTS Sensor (
 sensor_id TEXT PRIMARY KEY, container_id TEXT NOT NULL REFERENCES Container(container_id),
 kind TEXT NOT NULL CHECK(kind IN ('TEMP','HUM')), calibrated_at TEXT NOT NULL,
 calibration_status TEXT NOT NULL CHECK(calibration_status IN ('VALID','EXPIRED','FAILED')),
 UNIQUE(container_id, kind)
);
CREATE TABLE IF NOT EXISTS SensorCalibrationHistory (
 calibration_id TEXT PRIMARY KEY, sensor_id TEXT NOT NULL REFERENCES Sensor(sensor_id),
 calibrated_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('VALID','EXPIRED','FAILED')),
 recorded_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS SensorReading (
 reading_id TEXT PRIMARY KEY, sensor_id TEXT NOT NULL REFERENCES Sensor(sensor_id),
 shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id), recorded_at TEXT NOT NULL,
 value REAL NOT NULL, unit TEXT NOT NULL CHECK(unit IN ('C','%')), quality TEXT NOT NULL DEFAULT 'VALID' CHECK(quality IN ('VALID','INVALID_PHYSICAL_RANGE','INVALID_SENSOR','CALIBRATION_FAILURE')),
 UNIQUE(sensor_id, recorded_at)
);
CREATE INDEX IF NOT EXISTS idx_reading_shipment_time ON SensorReading(shipment_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_reading_shipment_sensor_time ON SensorReading(shipment_id, sensor_id, recorded_at);
CREATE TABLE IF NOT EXISTS RouteStop (
 stop_id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
 sequence_no INTEGER NOT NULL CHECK(sequence_no > 0), location TEXT NOT NULL,
 planned_at TEXT NOT NULL, actual_at TEXT, status TEXT NOT NULL DEFAULT 'PLANNED' CHECK(status IN ('PLANNED','COMPLETED','DELAYED','EXCEPTION')),
 UNIQUE(shipment_id, sequence_no)
);
CREATE TABLE IF NOT EXISTS ThresholdPolicy (
 policy_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES Product(product_id),
 effective_from TEXT NOT NULL, effective_to TEXT, temp_min REAL NOT NULL, temp_max REAL NOT NULL,
 humidity_min REAL NOT NULL, humidity_max REAL NOT NULL, max_excursion_minutes INTEGER NOT NULL CHECK(max_excursion_minutes>0),
 CHECK(temp_min < temp_max), CHECK(humidity_min < humidity_max),
 CHECK(effective_to IS NULL OR effective_to > effective_from)
);
CREATE TABLE IF NOT EXISTS Excursion (
 excursion_id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
 sensor_id TEXT REFERENCES Sensor(sensor_id),
 kind TEXT NOT NULL CHECK(kind IN ('TEMP','HUM','SENSOR_GAP','INVALID_PHYSICAL_RANGE','CALIBRATION_FAILURE','ROUTE_DELAY','POLICY_PRODUCT_MISMATCH','DURATION_UNDERCOUNT','INVALID_SHIPMENT_TIMELINE')),
 started_at TEXT NOT NULL, ended_at TEXT, duration_minutes REAL NOT NULL DEFAULT 0 CHECK(duration_minutes>=0),
 severity TEXT NOT NULL CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
 status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','ACKNOWLEDGED','CLOSED')), evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_excursion_status ON Excursion(status, severity);
CREATE TABLE IF NOT EXISTS ExcursionRevision (
 revision_id TEXT PRIMARY KEY, excursion_id TEXT NOT NULL REFERENCES Excursion(excursion_id),
 revision_no INTEGER NOT NULL, assessed_at TEXT NOT NULL, started_at TEXT NOT NULL,
 ended_at TEXT, duration_minutes REAL NOT NULL CHECK(duration_minutes>=0),
 severity TEXT NOT NULL CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
 policy_id TEXT, source_hash TEXT NOT NULL, evidence_json TEXT NOT NULL,
 UNIQUE(excursion_id,revision_no), UNIQUE(excursion_id,source_hash)
);
CREATE TABLE IF NOT EXISTS ExcursionStatusEvent (
 event_id TEXT PRIMARY KEY, excursion_id TEXT NOT NULL REFERENCES Excursion(excursion_id),
 previous_status TEXT, new_status TEXT NOT NULL CHECK(new_status IN ('OPEN','ACKNOWLEDGED','CLOSED')),
 occurred_at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'demo-user', reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS CustodyEvent (
 event_id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
 previous_custodian TEXT, new_custodian TEXT NOT NULL, event_type TEXT NOT NULL,
 location TEXT NOT NULL, occurred_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_custody_shipment_time ON CustodyEvent(shipment_id, occurred_at);
CREATE TABLE IF NOT EXISTS LifecycleEvent (
 event_id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
 previous_status TEXT NOT NULL CHECK(previous_status IN ('CREATED','READY','IN_TRANSIT','DELAYED','EXCEPTION','DELIVERED','CLOSED')),
 new_status TEXT NOT NULL CHECK(new_status IN ('CREATED','READY','IN_TRANSIT','DELAYED','EXCEPTION','DELIVERED','CLOSED')), occurred_at TEXT NOT NULL,
 location TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_shipment_time ON LifecycleEvent(shipment_id, occurred_at);
CREATE TABLE IF NOT EXISTS CorrectiveAction (
 action_id TEXT PRIMARY KEY, excursion_id TEXT NOT NULL REFERENCES Excursion(excursion_id),
 assigned_to TEXT NOT NULL, action_type TEXT NOT NULL, description TEXT NOT NULL,
 created_at TEXT NOT NULL, completed_at TEXT,
 status TEXT NOT NULL CHECK(status IN ('OPEN','IN_PROGRESS','COMPLETED','CANCELLED')),
 resolution_notes TEXT NOT NULL DEFAULT '',
 CHECK(status!='COMPLETED' OR completed_at IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS CorrectiveActionEvent (
 event_id TEXT PRIMARY KEY, action_id TEXT NOT NULL REFERENCES CorrectiveAction(action_id),
 previous_status TEXT, new_status TEXT NOT NULL CHECK(new_status IN ('OPEN','IN_PROGRESS','COMPLETED','CANCELLED')),
 assigned_to TEXT NOT NULL, occurred_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 resolution_notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS Notification (
 notification_id TEXT PRIMARY KEY, excursion_id TEXT NOT NULL REFERENCES Excursion(excursion_id),
 created_at TEXT NOT NULL, message TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN (0,1))
);
