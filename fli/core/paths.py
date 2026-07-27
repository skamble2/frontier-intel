"""Filesystem layout. Computed from this file so the package is relocatable."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCHEMA_PATH = ROOT / "storage" / "schema.sql"
DEFAULT_DB = ROOT / "data" / "fli.db"
CONFIG_DIR = ROOT / "config"
FIXTURES_DIR = ROOT / "fixtures"
OVERRIDES_PATH = CONFIG_DIR / "register_overrides.yml"
