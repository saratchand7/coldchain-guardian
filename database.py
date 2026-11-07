"""SQLite helpers and transactional lifecycle operations."""
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from config import BASE_DIR, DATABASE

def connect(path=DATABASE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db

def connection_path(db):
    """Return the file backing an open SQLite connection."""
    row = db.execute('PRAGMA database_list').fetchone()
    return Path(row[2]) if row and row[2] else DATABASE

def initialize(path=DATABASE):
    with connect(path) as db:
        db.executescript((BASE_DIR / "database" / "schema.sql").read_text(encoding="utf-8"))

@contextmanager
def transaction(path=DATABASE):
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
