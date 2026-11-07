# Architecture

The browser loads Flask-rendered pages and calls JSON endpoints. Flask validates inputs, delegates domain operations to services, and reads/writes the local SQLite database. Every SQLite connection enables foreign-key enforcement. Related exception, notification and action records are committed in transactions.

Synthetic generation proceeds in parent-first order: products and effective policies; containers and sensors; shipments and container assignments; route stops, readings, lifecycle history, custody history; then exception detection and validation. Reading inserts use bounded batches. CSV and SQL writers stream rows to avoid holding full exports in memory. Detection does not read ground_truth.csv or ground_truth.json. Evaluation reads those labels only after detections have been stored.

Detection, gap coverage, evidence verification, and assessment share the same actual/observed journey window and source-trust rules. Each successful run stores a DetectionAnalysis source fingerprint; assessment fails closed if that record is absent, failed, stale, or covers a different horizon. TEMP and HUM are required across assignment intervals, so correctly timed replacement sensors can jointly provide complete evidence. Operational run IDs and completion timestamps are kept outside deterministic business-data checksums.

## Relational relationships

- Product 1:N Shipment and ThresholdPolicy.
- Shipment 1:N ContainerAssignment, SensorReading, RouteStop, Excursion, CustodyEvent and LifecycleEvent.
- Container 1:N Sensor and ContainerAssignment.
- Sensor 1:N SensorReading.
- Excursion 1:N CorrectiveAction and Notification.
- ContainerAssignment records time-bounded allocation history and implements container/shipment assignment relationships.

Historical readings, custody and lifecycle events are retained. Container assignments are bounded by start/end timestamps. A shipment's product selects its effective-dated policy. Detector evidence references source reading IDs and the policy/range or operational event that caused the exception.

## Modules

- app.py: dashboard, shipment, monitoring, route, custody, exception, action, validation and export endpoints.
- database.py and database/schema.sql: schema creation, foreign keys and transaction helper.
- generator/: master records, shipment timelines, readings, anomaly injection and optional SQL output.
- services/engine.py: policy evaluation and exception generation.
- services/lifecycle.py: lifecycle transition and custody chronology enforcement.
- services/corrective_actions.py: action state machine.
- services/metrics.py: fact and ground-truth evaluation metrics.
- services/validation.py: integrity checks and reproducible reports.
- templates/ and static/: responsive application pages, styles and charts.

No cloud service, paid API, hardware or Docker is required. Chart.js is the only online optional asset.
