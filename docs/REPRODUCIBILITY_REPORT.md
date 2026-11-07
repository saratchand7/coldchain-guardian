# Reproducibility report

Generator version: 1.3. Fixed seed: 20261022. Canonical files use sorted table/ID order, ISO 8601 timestamps, stable CSV/JSON serialization, and streaming SHA-256 checksums. The run manifest records dependency/runtime information, source commit, requested artifact formats, optional SQL hashes, and a completion status.

## Full scale

Executed twice after the evidence, coverage, custody, and audit corrections with:

```bash
python generate_data.py --scale full --seed 20261022 --json --sql
```

Each run produced 1,000 shipments, 300 containers, 600 sensors, 1,812,402 readings, 592 detected exceptions, 592 corrective actions, 4,700 custody events, and 2,741 lifecycle events. Both runs passed the expanded validation suite. The manifests and generation summaries matched byte-for-byte.

- source_manifest.json: `56ea1c1493beac37c96ca35eb892f0a44c61b57f9e5d6e74959987efa112aafa`
- sensorreading.csv: `383ee625ee3de93863a81c170d81146d3d92c214fe86fb0221b4aa215183c203`
- excursion.csv: `c42ffae03eb206411f5845c416793d63727804d9f7b51a321b19a10927eb9572`
- ground_truth.json: `adf356c910f3f8401e8f457266225d3ee24c3ed2eee50baefccb36a67c1fb40d`
- seed.sql: `5989e37511a57a7f46e8eb4a3750840370bc06cb95a390e5350c7c2a50dcc7a0`

The generated evaluation records event-detection and negative-decision results separately. TN remains unavailable because safe intervals are not exhaustively labelled.

## Starter scale

The current starter run contains 12 shipments, 6 containers, 12 sensors, 20,258 readings, 13 detected exceptions, 13 corrective actions, 54 custody events, and 31 lifecycle events. It passed the expanded validation set with CSV, JSON, and SQL artifacts enabled. The automated suite passes 23 tests, including coverage, evolving-incident, evidence-tampering, history, custody, unit, and calibration regressions.

Full hashes cover generated exports and independent ground-truth data; SQLite page layout and local runtime state are excluded.
