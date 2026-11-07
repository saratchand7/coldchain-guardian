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

A product excursion starts at the first violating sample. It ends at the first valid recovery sample. Duration is the elapsed interval between those samples. If no recovery exists, the detector uses the last violating sample plus the expected five-minute interval and leaves ended_at empty. This avoids pretending the exact threshold-crossing instant is known. A sample gap breaks an excursion group; unrelated excursions are not merged.

A missing interval is detected by comparing adjacent readings for each shipment/sensor. Missing intervals create SENSOR_GAP exceptions with affected interval and count, and get a notification and corrective action. Missing values are never treated as safe. A failed or expired sensor calibration creates a CRITICAL CALIBRATION_FAILURE. A route stop that is marked delayed or has actual_at after planned_at creates a route-delay exception.

Severity classes: LOW means duration is within the policy allowance. MEDIUM means above allowance through twice the allowance. HIGH means more than twice the allowance but below 60 minutes. CRITICAL means at least 60 minutes or a severe physical/calibration failure. SENSOR_GAP is HIGH. Route delay is MEDIUM under one hour and HIGH at or above one hour. These categories communicate operational response only; they do not infer clinical product safety.

## Lifecycle and custody

Allowed shipment transitions are CREATED→READY→IN_TRANSIT; IN_TRANSIT can become DELAYED, EXCEPTION, or DELIVERED; DELAYED and EXCEPTION can return to transit or move to the other exception/delay state or delivery; DELIVERED→CLOSED. No other state change is accepted. Delivery without departure is rejected as INVALID_SHIPMENT_TIMELINE. Each accepted transition appends LifecycleEvent with previous and new status, event time, location, and notes.

Custody events append previous custodian, new custodian, event type, location, timezone-aware timestamp, and notes. Input timestamps are normalized to UTC and must be strictly later than the previous shipment event. Existing custody events are not overwritten.

## Corrective actions and negative scenarios

Each detected exception creates an OPEN corrective action and notification in one transaction. Actions move OPEN→IN_PROGRESS or CANCELLED; IN_PROGRESS→COMPLETED or CANCELLED. Completion records completed_at and resolution notes. Closed actions cannot be reopened.

The generator stages valid baseline readings first, then applies deterministic labelled edge cases and calibration/dropout mutations. The separate ground-truth files include the known injected anomaly events plus five independent negative cases: physical-range values are invalid rather than temperature excursions; gaps are exceptions rather than safe intervals; delivery before departure is rejected; a wrong-product policy is rejected; and a shortened reported duration is compared with source readings and flagged as DURATION_UNDERCOUNT. The detector never opens or consumes the ground-truth files. Evaluation reads them after detection.
