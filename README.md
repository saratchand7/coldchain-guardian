# ColdChain Guardian

**A local cold-chain monitoring demo for tracking shipment conditions, detecting excursions, and preserving operational evidence.**

ColdChain Guardian simulates temperature and humidity readings over shipment journeys. It applies effective-dated product policies, records source-linked exceptions, tracks route and custody history, and reports evidence completeness and evaluation metrics.

> **Demo only:** All records and policy values are synthetic. This project is not a validated quality system and must not be used to make product handling or safety decisions.

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Generate data](#generate-data)
- [Run tests and validation](#run-tests-and-validation)
- [Monitoring decisions](#monitoring-decisions)
- [Data and exports](#data-and-exports)
- [Application and API](#application-and-api)
- [Project structure](#project-structure)
- [Scope and limitations](#scope-and-limitations)

## Features

- Temperature and humidity monitoring with separate units and product policies.
- Effective-dated threshold policy checks, duration rules, calibration checks, and route delay detection.
- Leading, trailing, internal, invalid, and wholly absent expected sensor observations.
- Required TEMP and HUM modality coverage across time-bounded sensor replacements.
- Source/policy/calibration fingerprints that prevent stale or failed detection from certifying compliance.
- Stable excursion identities with append-only evidence revisions and status history.
- Corrective actions with preserved status and resolution history.
- Shipment lifecycle, route-stop, and custody records with delivery reconciliation.
- Dashboard, shipment and route views, excursion review, reports, and CSV/JSON exports.
- Deterministic synthetic data, separate ground-truth labels, validation, and reproducibility manifests.

## Quick start

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python generate_data.py --scale starter --seed 20261022
python app.py
```

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python generate_data.py --scale starter --seed 20261022
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). Keep the terminal open while using the app; stop it with **Ctrl+C**. The local demo does not require a user login.

## Generate data

The generator resets the configured demo database. Do not point it at operational data.

```bash
# Small local dataset
python generate_data.py --scale starter --seed 20261022

# Problem-statement volume: 1,000 shipments and 300 containers
python generate_data.py --scale full --seed 20261022

# Optional portable formats and active-trip observation horizon
python generate_data.py --scale starter --seed 20261022 --json --sql
python generate_data.py --scale full --seed 20261022 --as-of 2026-11-07T00:00:00+00:00
```

Readings are generated on a five-minute schedule over two-to-five-day planned trips. Completed trips use actual arrival, including delivery delays beyond the plan; active overdue trips continue to an explicit or recorded observation horizon. A run writes a unique run ID and `STARTED`/`RUNNING`/`FAILED`/`COMPLETE` state to `run_status.json`. Parameters are validated before reset, and `COMPLETE` is published only after successful validation. Retrying resets the configured synthetic database; a failed retry never leaves an older `COMPLETE` marker in place.

## Run tests and validation

```bash
python -m pytest -q
```

Generation writes `data/generated/validation_summary.json`, `validation_report.csv`, and `evaluation_summary.json`. Validation checks keys and joins, source assignments, unit and calibration meaning, effective policy coverage, expected sensor windows, route order, lifecycle and custody state, event evidence, action history, ground-truth references, and export hashes. Checks that were not run are listed separately; they are not counted as passing checks.

## Monitoring decisions

Compliance has four outcomes: `COMPLIANT`, `NON_COMPLIANT`, `INCOMPLETE_EVIDENCE`, and `NOT_YET_ASSESSED`.

- A completed shipment can be `COMPLIANT` only if required TEMP and HUM evidence is present and trusted across the actual journey, sensor replacement assignments and calibration are valid, exactly one product policy covers every expected sample, and a successful detector fingerprint matches the current source data and horizon.
- A detected temperature or humidity excursion above the policy duration allowance is `NON_COMPLIANT`.
- Missing, invalid, off-grid, stale, or untrusted observations, missing/overlapping policy, and invalid calibration keep the evidence incomplete.
- Active trips remain provisional and do not increase completed compliance.

The displayed compliance percentage uses **all shipments** as its denominator, including provisional and incomplete shipments. The dashboard therefore shows the four assessment counts and the number fully assessed beside the percentage.

An excursion begins at its first violating sample and ends at its first valid recovery sample. If recovery has not arrived, the duration is censored at the last observed violating sample plus one expected interval, bounded by the assessment window. A gap breaks the run; unobserved time is not counted as measured violation. Each event retains a stable ID and appends a revision containing its source reading IDs, policy bounds and allowance, duration, severity, recovery state, and source hash. Replaying identical input creates no revision.

The detector reads source database records only; it never reads ground truth. Evaluation requires the ground-truth artifact belonging to the same generated dataset; a custom database is not silently scored against the default file. Positive event detection scores and negative-decision checks are reported separately by event class. True negatives are unavailable because the dataset does not label every safe five-minute interval.

## Data and exports

Core entities are `Product`, `Shipment`, `Container`, `Sensor`, `SensorReading`, `RouteStop`, `ThresholdPolicy`, `Excursion`, `CustodyEvent`, and `CorrectiveAction`. Supporting tables include `ContainerAssignment`, `MonitoringWindow`, `DetectionAnalysis`, `SensorCalibrationHistory`, `ExcursionRevision`, `ExcursionStatusEvent`, `CorrectiveActionEvent`, `LifecycleEvent`, and `Notification`. `DetectionAnalysis` is operational state and its wall-clock completion timestamp is intentionally excluded from deterministic export checksums.

- Schema: [`database/schema.sql`](database/schema.sql)
- Field-level dictionary: [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md)
- Business rules: [`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md)
- Validation report: [`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md)
- Separate labels: `data/generated/ground_truth.csv` and `ground_truth.json`
- Generated source files and hashes: `data/generated/` and `data/source_manifest.json`

CSV is generated by default. `--json` adds per-table JSON; `--sql` adds a portable SQL seed. The manifest records the seed, scale, interval, Python version, source commit, formats, hashes, and optional SQL hash. Generated databases, exports, virtual environments, and local secrets are excluded from Git.

## Application and API

The application provides Overview, Shipments, Live Monitoring, Route Tracking, Excursions, Corrective Actions, Custody History, and Reports & Exports pages. There is no login because this is a local educational demo.

Key endpoints include `GET /api/dashboard`, `/api/shipments`, `/api/readings`, `/api/routes`, `/api/excursions`, `/api/actions`, `/api/metrics`, and `/api/validation`; `POST /api/detect`; lifecycle, custody, and corrective-action update endpoints; and streaming table exports at `/api/export/<table>.csv` and `.json`.

Shipment and reading lists accept `page` and `limit` and return `items`, `total`, `returned`, `pages`, and `next_page`. Readings also accept `shipment_id`, `sensor_id`, `kind`, `from`, and `to`. The monitoring chart states how many records it displays; filter to a shipment or request additional pages/time windows for a complete journey. Excursion detail includes source revisions and status history; action records retain their transition history.

## Project structure

```text
app.py                  Flask application and API routes
database/               SQLite schema
generator/              Deterministic data and SQL-seed generation
services/               Detection, assessment, metrics, lifecycle and validation
static/ and templates/   Dashboard assets and pages
tests/                  Automated regression and application tests
docs/                   Data dictionary, business rules, and reports
```

## Scope and limitations

This is an educational local demo. It has no authentication, role-based access, hardware ingestion, or production deployment. Product policies and sensor values are fabricated examples, not real handling guidance. Flask runs in development mode. Charts use an optional Chart.js CDN; the local backend and records continue to work without it.
