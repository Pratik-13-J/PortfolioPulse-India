PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    asset_class TEXT NOT NULL CHECK(asset_class IN ('Equity', 'Debt', 'Gold', 'Benchmark')),
    sector TEXT NOT NULL DEFAULT 'Unspecified',
    currency TEXT NOT NULL DEFAULT 'INR',
    data_source TEXT NOT NULL DEFAULT 'synthetic_india_demo_v1'
);

CREATE TABLE IF NOT EXISTS portfolios (
    portfolio_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    base_currency TEXT NOT NULL DEFAULT 'INR',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS holdings (
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    quantity REAL NOT NULL CHECK(quantity >= 0),
    target_weight REAL NOT NULL CHECK(target_weight >= 0),
    PRIMARY KEY (portfolio_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS market_observations (
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    observation_date TEXT NOT NULL,
    close_value REAL NOT NULL CHECK(close_value > 0),
    source TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, observation_date)
);

CREATE INDEX IF NOT EXISTS idx_observations_date ON market_observations(observation_date);

CREATE TABLE IF NOT EXISTS risk_runs (
    run_id INTEGER PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    annual_risk_free_rate REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_metrics (
    run_id INTEGER NOT NULL REFERENCES risk_runs(run_id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    PRIMARY KEY (run_id, metric_name)
);

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id INTEGER PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    name TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    impact_inr REAL NOT NULL,
    impact_pct REAL NOT NULL,
    shocks_json TEXT NOT NULL,
    contributions_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
