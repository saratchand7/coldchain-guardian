"""Application configuration; paths can be isolated for safe tests and runs."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("COLDCHAIN_DATA_DIR", BASE_DIR / "data")).resolve()
DATABASE = Path(os.environ.get("COLDCHAIN_DB", DATA_DIR / "coldchain.db")).resolve()
GENERATED = Path(os.environ.get("COLDCHAIN_GENERATED_DIR", DATA_DIR / "generated")).resolve()
MANIFEST = Path(os.environ.get("COLDCHAIN_MANIFEST", DATA_DIR / "source_manifest.json")).resolve()
REPORTS_DIR = Path(os.environ.get("COLDCHAIN_REPORTS_DIR", BASE_DIR / "docs")).resolve()
SECRET_KEY = "local-development-only-change-before-deployment"
EXPECTED_INTERVAL_MINUTES = 5
CALIBRATION_VALIDITY_DAYS = 365
