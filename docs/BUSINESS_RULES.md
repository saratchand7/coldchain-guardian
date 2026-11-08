# Business rules

## Illustrative product policy

The sample policy values are fabricated demonstration data, not real storage or transport recommendations.

| Product | Temperature | Humidity | Allowed excursion interval |
|---|---|---|---:|
| Vaccine A | 2–8°C | 30–60% | 15 minutes |
| Medicine B | 15–25°C | 30–65% | 30 minutes |
| Frozen Product C | -25 to -15°C | 20–50% | 10 minutes |

The shipment product identifies its applicable ThresholdPolicy. A policy is selected only when the reading timestamp is within effective_from and effective_to; an absent policy fails closed. The explicit policy validation endpoint rejects a policy whose product differs from the shipment product.

## Reading and event rules

Temperature readings outside -80 to 100°C and humidity outside 0–100% are physical-range-invalid. They create INVALID_PHYSICAL_RANGE evidence and are never evaluated as a product threshold excursion. These broad limits are sensor data checks, not product limits.

A product excursion starts at the first violating sample. It ends at the first valid recovery sample. Duration is the elapsed interval between those samples. The detector evaluates each shipment and modality on the shared five-minute grid. Consecutive trusted readings under the same effective policy remain one run across a validated sensor/container handoff, and evidence records all contributing sensor and reading IDs. If no recovery exists, the detector uses the last violating sample plus the expected interval and leaves ended_at empty. A missing or conflicting modality slot censors the run; unrelated shipments, policies, modalities, and runs are never merged.

A missing interval is detected against the expected five-minute schedule from shipment departure through actual arrival, even when delivery is later than planned. Active overdue journeys continue through their explicit or recorded observation horizon. TEMP and HUM are both required; replacement sensors may jointly satisfy coverage when their assignment intervals cover every expected slot. Leading, trailing, internal, off-grid, invalid, and zero-row sensor timelines are represented. Missing values are never treated as safe. A gap censors an open product excursion at the previous valid sample plus one expected interval; the unobserved interval is unknown. One shared trust rule checks schedule alignment, assignment, unit, physical range, quality, and effective calibration. A failed, future-dated, or older-than-365-day calibration makes a reading untrusted and creates a CRITICAL CALIBRATION_FAILURE. Failed sensor readings remain in the database for traceability but do not support product threshold conclusions. A route stop marked delayed or with actual_at after planned_at creates a route-delay exception.

## Evidence outcomes

Every shipment is assigned one of four assessment outcomes: `COMPLIANT`, `NON_COMPLIANT`, `INCOMPLETE_EVIDENCE`, or `NOT_YET_ASSESSED`. A completed shipment is `COMPLIANT` only when both required modalities have trusted evidence at every expected slot, the calibration and interval assignment are valid, one effective product policy covers every expected sample, and `DetectionAnalysis` proves a successful analysis whose source hash and horizon match the current data. Missing, failed, or stale analysis fails closed. A temperature or humidity excursion exceeding its policy duration allowance is `NON_COMPLIANT`. Missing or untrusted readings, invalid calibration, incomplete policy coverage, and unresolved data gaps produce `INCOMPLETE_EVIDENCE`. Active shipments are `NOT_YET_ASSESSED` and may still display provisional alerts. Absence of an exception is not proof of compliance.

Excursion IDs are stable for shipment, first contributing sensor, kind, and start time. Each changed assessment appends an `ExcursionRevision` containing a serialized source snapshot and content hash, policy, bounds, allowance, contributing sensors/readings, recovery, duration, and severity. An identical source assessment is idempotent. Current active evidence is checked against authoritative source rows; retired history is checked independently through its immutable revision snapshot/hash so an approved source correction cannot erase or silently invalidate the earlier decision record. `ExcursionStatusEvent` records acknowledgement and closure separately from detector revisions. Closing an exception requires a reason and all related corrective actions to be completed or cancelled.

When late evidence fills a gap or removes a previously observed condition, the original event and evidence remain for audit. A new revision sets `condition_active=false` with a late/current-evidence resolution reason. This resolves the detector's current condition projection; an open human corrective-action task still requires explicit operational review and user-entered completion evidence.

Severity classes: LOW means duration is within the policy allowance. MEDIUM means above allowance through twice the allowance. HIGH means more than twice the allowance but below 60 minutes. CRITICAL means at least 60 minutes or a severe physical/calibration failure. SENSOR_GAP is HIGH. Route delay is MEDIUM under one hour and HIGH at or above one hour. These categories communicate operational response only; they do not infer clinical product safety.

## Lifecycle and custody

Allowed shipment transitions are CREATED→READY→IN_TRANSIT; IN_TRANSIT can become DELAYED, EXCEPTION, or DELIVERED; DELAYED and EXCEPTION can return to transit or move to the other exception/delay state or delivery; DELIVERED→CLOSED. No other state change is accepted. Delivery without departure is rejected as INVALID_SHIPMENT_TIMELINE. Each accepted transition appends LifecycleEvent with previous and new status, event time, location, and notes.

Custody events append previous custodian, new custodian, event type, location, timezone-aware timestamp, and notes. Input timestamps are normalized to UTC and must be strictly later than the previous shipment event. The first event is DEPARTURE, each event names the prior event's new custodian, and DELIVERY matches actual arrival. Existing custody events are not overwritten.

## Corrective actions and negative scenarios

Each detected exception creates an OPEN corrective action and notification in one transaction. Actions move OPEN→IN_PROGRESS or CANCELLED; IN_PROGRESS→COMPLETED or CANCELLED. Completion requires non-empty resolution notes and records completed_at. Every action transition is appended to `CorrectiveActionEvent`. A completed action cannot be replayed with replacement notes or reassigned; such edits are rejected to preserve the original evidence.

The generator stages valid baseline readings first, then applies deterministic labelled edge cases and calibration/dropout mutations. The separate ground-truth files include the known injected anomaly events plus five independent negative cases: physical-range values are invalid rather than temperature excursions; gaps are exceptions rather than safe intervals; delivery before departure is rejected; a wrong-product policy is rejected; and a shortened reported duration is compared with source readings and flagged as DURATION_UNDERCOUNT. The detector never opens or consumes the ground-truth files. Evaluation reads them after detection.
