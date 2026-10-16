"""Shared paths and explicit assumptions for the local portfolio demo."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "portfoliopulse.db"
DEMO_CSV_PATH = PROJECT_ROOT / "data" / "demo" / "prices.csv"
MARKET_CSV_PATH = PROJECT_ROOT / "data" / "market" / "adjusted_prices.csv"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
PORTFOLIO_NAME = "India Demo Portfolio"
BENCHMARK_SYMBOL = "DEMO_NIFTY50"
TRADING_DAYS_PER_YEAR = 252
