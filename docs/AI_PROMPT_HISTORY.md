# AI prompt history

- Prompt source: user-provided attachment, “MASTER PROJECT DEVELOPMENT PROMPT – YOTTAFLEX AI,” requesting a full ColdChain Guardian project, Windows instructions, generator, rules, dashboard, evaluation, validation, tests and documentation.
- Tool: Codex in this workspace assisted with code, implementation structure, documentation and test authoring.
- Components assisted: SQLite schema and helpers, synthetic data generator, policy-aware detection, lifecycle/custody/action services, Flask APIs, dashboard pages, evaluation and validation outputs.
- Design decisions reviewed during implementation: use a local SQLite database; keep the detector rule-based; use fixed-seed synthetic data; preserve source evidence and historical events; use interval-based duration because exact crossings are unknown; keep ground truth out of detection; report true-negative as unavailable without safe-interval labels.
- Verification performed: 15 automated pytest tests; full-scale generation repeated twice with matching checksums; 20/20 full-data validation; 588 event-level true positives with zero false positives or negatives; starter generation; ground-truth input checks; SQL seed round-trip with zero foreign-key errors; and prior Flask page/API/export smoke tests. See docs/REPRODUCIBILITY_REPORT.md for actual run counts and hashes.
- No personal manual work or review is claimed. The project owner should review each policy and rule, run the Windows commands, make any desired changes, and add their own review notes before submission.
- Known scope: demonstration only. No user authentication, production hosting, real sensor integration, clinical safety interpretation or formal quality-system validation.
