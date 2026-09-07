"""Minimal config. Pilot storage defaults to a file under this repo's own
data/ directory (never inside AIanger, never shared with it)."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = str(REPO_ROOT / "data" / "pilot.db")


def database_path() -> str:
    return os.environ.get("PILOT_DB_PATH", DEFAULT_DB_PATH)
