# Reproducibility report

Generator version: 1.3. Fixed seed: 20261022. Canonical files use sorted table/ID order, ISO 8601 timestamps, stable CSV/JSON serialization, and streaming SHA-256 checksums. The run manifest records dependency/runtime information, source commit, requested artifact formats, optional SQL hashes, and a completion status.

## Full scale

The previous version was executed twice at full scale. Those older hashes and event figures are retained here as historical baseline evidence only; they are not claims for generator version 1.3. Re-run the following after any detector or generator change to establish current full-scale evidence:

```bash
python generate_data.py --scale full --seed 20261022 --json --sql
```

The completed `data/source_manifest.json` provides the current hashes and run provenance. Validation now includes additional source evidence, expected coverage, history, policy, calibration, and artifact checks.

- source_manifest.json: 66F740A04B73D7C2B21EDD5D27AB695E73537D61E017A0A48AA7C90D59FB045D
- generation_summary.json: 3CE9A91C5FA29DD72B9843B8671F0D9E55FA3C7C71645C47177DD261D0D2161F
- ground_truth.json: 4DB78B87EFCDD6B19712AC57F77DD4D63B1D23FE790AE11CE9550A2049251609
- excursion.csv: A6E8CC0D761F679B4F00EA0B4E4C8DCE5321788B9B6CED091B427670EFABCF5F
- sensorreading.csv: 383EE625EE3DE93863A81C170D81146D3D92C214FE86FB0221B4AA215183C203

Full-scale event evaluation: TP 588, FP 0, FN 0; precision 1.0, recall 1.0. TN is unavailable because safe intervals are not exhaustively labelled.

## Starter scale

The current starter run contains 12 shipments, 6 containers, 12 sensors, 20,258 readings, 13 detected exceptions, 13 corrective actions, 54 custody events, and 31 lifecycle events. It passed the expanded validation set with CSV, JSON, and SQL artifacts enabled. The automated suite passes 19 tests, including coverage and evolving-incident regressions.

Full hashes cover generated exports and independent ground-truth data; SQLite page layout and local runtime state are excluded.
