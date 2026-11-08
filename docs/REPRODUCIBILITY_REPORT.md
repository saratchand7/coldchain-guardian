# Reproducibility report

Generator version: 1.4. Fixed seed: 20261022. Current verified source commit: `1f8e231b2a3adb3bc65266d8ea635269c709d2b0`. Canonical files use sorted table/ID order, ISO 8601 timestamps, stable CSV/JSON serialization, and streaming SHA-256 checksums. Operational run IDs and completion timestamps are excluded from deterministic business-data checksums.

## Current handoff, history, and restore verification

The sensor-handoff duration, archived-revision integrity, and SQL restore-to-ready corrections were verified with 30 automated tests. The suite includes equivalent one/two-sensor conditions, cross-sensor recovery, an unknown handoff slot, source correction followed by validation, two deterministic starter generations, and a restored seed whose 12 shipments are reanalyzed before validation. All 30 tests passed in 31.41 seconds in the local verification environment.

One full run at the current commit produced 1,000 shipments, 300 containers, 600 sensors, 1,812,402 readings, 592 detected exceptions, 592 corrective actions, 4,700 custody events, and 2,741 lifecycle events. Validation passed 31/31 checks, including `excursion_revision_snapshot_hash_integrity`. Evaluation remained TP 592, FP 0, FN 0, with 608 COMPLIANT, 35 NON_COMPLIANT, 57 INCOMPLETE_EVIDENCE, 300 NOT_YET_ASSESSED, 60.8% all-shipment compliance, and 99.78% trusted-sensor availability.

- sensorreading.csv: `383ee625ee3de93863a81c170d81146d3d92c214fe86fb0221b4aa215183c203`
- excursion.csv: `8d3a53a452290d7127793eb3e066c065e2ffba9d4ec67d8646f72d858a370dad`
- ground_truth.json: `6cc412be367789d894c6d1307225a6aaec81799ecc7be92150b3caffe54006aa`

## Earlier two-run full-scale baseline

At source commit `003fb96137531aa3e955b713d458bfeff4366ff6`, the earlier analysis-freshness, delayed-window, required-modality, trust, ground-truth, late-evidence, and run-status corrections were executed twice with:

```bash
python generate_data.py --scale full --seed 20261022
```

Run 1 completed in 560.625 seconds and isolated run 2 in 481.893 seconds. Each produced 1,000 shipments, 300 containers, 600 sensors, 1,812,402 readings, 592 detected exceptions, 592 corrective actions, 4,700 custody events, and 2,741 lifecycle events. Both passed all 30 validation checks. The complete parsed manifests, 18 export row-count/hash entries, two ground-truth entries, and generation-summary bytes matched with zero mismatches.

- source_manifest.json: `b436b4f759693b7338921744ebb1594bd2cc6c9d7e3aa08df55ec45ec6a9c5e8`
- sensorreading.csv: `383ee625ee3de93863a81c170d81146d3d92c214fe86fb0221b4aa215183c203`
- excursion.csv: `9d58588b2813b0f295fc77d737b0ee9f254c77b4e18c91295df09fbe688ad898`
- ground_truth.json: `6cc412be367789d894c6d1307225a6aaec81799ecc7be92150b3caffe54006aa`

The independent event labels reconcile with actual decisions: TP 592, FP 0, FN 0, and TN unavailable because safe intervals are not exhaustively labelled. Assessment counts are COMPLIANT 608, NON_COMPLIANT 35, INCOMPLETE_EVIDENCE 57, and NOT_YET_ASSESSED 300. The displayed 60.8% compliance is 608/1,000 total shipments, not a percentage limited to fully assessed shipments. Trusted-sensor availability is 99.78%.

## Starter scale

The current starter verification uses `python generate_data.py --scale starter --seed 20261022 --sql`. It produced 12 shipments, 6 containers, 12 sensors, 20,258 readings, 13 detected exceptions, 13 corrective actions, 54 custody events, and 31 lifecycle events. The SQL seed SHA-256 is `2fa9d1cd476aaf8409094886fce4410a20b6f068432cbe4f533da564748d0446`. The restore integration applies the schema and seed, confirms zero foreign-key violations, rebuilds all 12 `DetectionAnalysis` rows, passes validation, and reproduces the source assessment counts. The isolated automated suite passes 30 tests, including the focused handoff, history, and restore regressions.

Full hashes cover generated exports and independent ground-truth data; SQLite page layout and local runtime state are excluded.
