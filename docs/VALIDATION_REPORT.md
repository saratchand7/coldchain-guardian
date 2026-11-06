# Validation report

Status: **PASS**

Checks passed: 20 / 20

| Check | Result | Count |
|---|---:|---:|
| foreign_keys | PASS | 0 |
| reading_id_uniqueness | PASS | 0 |
| reading_sensor_shipment_assignment | PASS | 0 |
| reading_within_shipment_timeline | PASS | 0 |
| physical_ranges | PASS | 0 |
| invalid_readings_flagged | PASS | 0 |
| reading_quality_enum | PASS | 0 |
| reading_unit_enum | PASS | 0 |
| shipment_timeline | PASS | 0 |
| route_stop_sequence | PASS | 0 |
| threshold_policy_ranges | PASS | 0 |
| threshold_effective_date_overlap | PASS | 0 |
| sensor_calibration_warning | PASS | 1 |
| custody_chronology | PASS | 0 |
| lifecycle_transition_history | PASS | 0 |
| excursion_duration_recalculation | PASS | 0 |
| shipment_lifecycle_enum | PASS | 0 |
| container_assignment_overlap | PASS | 0 |
| sensor_gap_exceptions_persisted | PASS | 0 |
| csv_database_consistency | PASS | 0 |

Records checked: 20483
Errors: 0
Warnings: 1

## Detected anomaly categories

- CALIBRATION_FAILURE: 2
- HUM: 2
- INVALID_PHYSICAL_RANGE: 1
- ROUTE_DELAY: 1
- SENSOR_GAP: 2
- TEMP: 2
