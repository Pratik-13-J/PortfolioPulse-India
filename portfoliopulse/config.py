"""Shared paths and explicit assumptions for the local portfolio demo."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "portfoliopulse.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
PORTFOLIO_NAME = "My India Portfolio"
BENCHMARK_SYMBOL = "NIFTY50"
TRADING_DAYS_PER_YEAR = 252
