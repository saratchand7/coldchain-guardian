"""Application configuration."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "data" / "coldchain.db"
GENERATED = BASE_DIR / "data" / "generated"
SECRET_KEY = "local-development-only-change-before-deployment"
EXPECTED_INTERVAL_MINUTES = 5
