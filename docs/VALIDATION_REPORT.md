# Validation report

Status: **PASS**

Checks passed: 31 / 31

| Check | Result | Count |
|---|---:|---:|
| foreign_keys | PASS | 0 |
| reading_id_uniqueness | PASS | 0 |
| reading_sensor_shipment_assignment | PASS | 0 |
| reading_within_shipment_timeline | PASS | 0 |
| reading_kind_unit_and_physical_range | PASS | 0 |
| reading_quality_enum | PASS | 0 |
| invalid_reading_quality_matches_value | PASS | 0 |
| sensor_kind_unit_semantics | PASS | 0 |
| shipment_timeline | PASS | 0 |
| route_stop_sequence_and_chronology | PASS | 0 |
| threshold_policy_ranges | PASS | 0 |
| threshold_effective_date_overlap | PASS | 0 |
| policy_coverage_for_readings | PASS | 0 |
| valid_calibration_is_current_at_readings | PASS | 0 |
| untrusted_readings_traceable | PASS | 0 |
| container_status_assignment_reconciliation | PASS | 0 |
| excursion_duration_recalculation_from_sources | PASS | 0 |
| open_excursion_is_current | PASS | 0 |
| excursion_revision_snapshot_hash_integrity | PASS | 0 |
| shipment_lifecycle_enum | PASS | 0 |
| container_assignment_overlap | PASS | 0 |
| expected_sensor_coverage_spans_persisted | PASS | 0 |
| missing_or_invalid_expected_slots_visible | PASS | 0 |
| current_successful_detection_analysis | PASS | 0 |
| required_temp_and_humidity_evidence | PASS | 0 |
| custody_chain_and_delivery_reconciliation | PASS | 0 |
| lifecycle_history_matches_current_state | PASS | 0 |
| status_history_matches_current_state | PASS | 0 |
| ground_truth_source_references | PASS | 0 |
| ground_truth_expected_events_persisted | PASS | 0 |
| export_manifest_hash_and_row_consistency | PASS | 0 |

Records checked: 1832916
Errors: 0
Warnings: 0

## Skipped checks

- None

## Detected anomaly categories

- CALIBRATION_FAILURE: 4
- HUM: 195
- INVALID_PHYSICAL_RANGE: 1
- ROUTE_DELAY: 41
- SENSOR_GAP: 157
- TEMP: 194

## Independent recheck of `f0f73ae`

The 8 November 2026 recheck reported 30 passing tests. Its independent fixtures
confirmed that a continuous temperature excursion survives sensor replacement
with its duration and contributing sensor IDs intact. Reanalysis after source
correction passed validation, including the archived revision integrity check.
The SQL restore integration test rebuilt analysis for all 12 starter shipments
and reproduced the original assessment counts.

The independent full run used `python generate_data.py --scale full --seed 20261022`
and produced 1,000 shipments, 1,812,402 readings, and 592 excursions. It passed
31 of 31 validation checks and all three negative-decision cases. Labelled event
matching found 592 true positives, zero false positives, and zero false negatives.
All 18 export hashes and both ground-truth hashes matched the manifest. The
assessment counts were 608 compliant, 35 non-compliant, 57 with incomplete
evidence, and 300 not yet assessed.

### Open issue with concurrent containers

The full-run generator assigns one container per shipment at a time. A separate
20-minute fixture assigned two containers concurrently and recorded complete,
calibrated readings every five minutes. One container stayed at 9 °C and the
other at 5 °C against a 2–8 °C policy with a 10-minute excursion allowance.
The engine pooled the temperature readings, emitted no excursion, and reported
`COMPLIANT`. Validation passed.

Detection and assessment need to evaluate each monitored container and combine
their results at shipment level. Regression coverage should include concurrent
safe and violating containers, two safe containers, disagreement between
redundant sensors on the same container, validated sensor replacement, and a
missing interval. Evidence should identify the affected container, sensor,
readings, and policy. The independent recheck did not exercise the Windows
launcher.

### Concurrent-container correction

Detection now tracks condition runs per monitored unit, and shipment assessment
requires temperature and humidity coverage for each active unit. In regression
tests, the concurrent 9 °C container produced a traceable 20-minute excursion and
a `NON_COMPLIANT` shipment. Two safe containers produced `COMPLIANT`. Conflicting
observations at a handoff produced `INCOMPLETE_EVIDENCE`. The existing handoff and
missing-slot regression tests also passed.
