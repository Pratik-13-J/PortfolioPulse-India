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

-- The original instruments, market_observations, and analytics tables above
-- remain available for the fictional dashboard. Real daily candles live in
-- explicitly selected datasets, so two providers can never overwrite each
-- other's prices for the same symbol and date.
CREATE TABLE IF NOT EXISTS instrument_listings (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(instrument_id) ON DELETE CASCADE,
    trading_symbol TEXT NOT NULL CHECK(length(trim(trading_symbol)) > 0),
    exchange TEXT NOT NULL CHECK(exchange = 'NSE'),
    UNIQUE (exchange, trading_symbol)
);

CREATE TABLE IF NOT EXISTS portfolio_benchmarks (
    portfolio_id INTEGER PRIMARY KEY REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id)
);

CREATE TABLE IF NOT EXISTS candle_datasets (
    dataset_id INTEGER PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    dataset_key TEXT NOT NULL UNIQUE,
    candles_sha256 TEXT NOT NULL,
    source_name TEXT NOT NULL CHECK(length(trim(source_name)) > 0),
    source_url TEXT,
    provenance_kind TEXT NOT NULL CHECK(length(trim(provenance_kind)) > 0),
    provenance_status TEXT NOT NULL CHECK(length(trim(provenance_status)) > 0),
    retrieved_at_utc TEXT NOT NULL,
    price_basis TEXT NOT NULL CHECK(price_basis IN ('unadjusted_ohlcv', 'provider_adjusted_ohlcv')),
    interval TEXT NOT NULL CHECK(interval = 'ONE_DAY'),
    window_start TEXT NOT NULL,
    window_end_exclusive TEXT NOT NULL CHECK(window_end_exclusive > window_start),
    input_csv_sha256 TEXT,
    candle_count INTEGER NOT NULL CHECK(candle_count > 0),
    aligned_price_dates INTEGER CHECK(aligned_price_dates >= 0),
    valid_daily_return_dates INTEGER CHECK(valid_daily_return_dates >= 0),
    incomplete_union_dates INTEGER CHECK(incomplete_union_dates >= 0),
    metadata_json TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_candle_datasets_portfolio
    ON candle_datasets(portfolio_id, imported_at_utc);

-- Preserve the exact buy-and-hold inputs used with each dataset. The current
-- portfolio configuration may be edited later without rewriting old analyses.
CREATE TABLE IF NOT EXISTS dataset_holdings (
    dataset_id INTEGER NOT NULL REFERENCES candle_datasets(dataset_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    trading_symbol TEXT NOT NULL,
    exchange TEXT NOT NULL CHECK(exchange = 'NSE'),
    asset_class TEXT NOT NULL CHECK(asset_class IN ('Equity', 'Benchmark')),
    sector TEXT NOT NULL,
    quantity REAL NOT NULL CHECK(quantity >= 0),
    target_weight REAL NOT NULL CHECK(target_weight >= 0 AND target_weight <= 1),
    is_benchmark INTEGER NOT NULL CHECK(is_benchmark IN (0, 1)),
    PRIMARY KEY (dataset_id, symbol)
);

CREATE TABLE IF NOT EXISTS daily_candles (
    dataset_id INTEGER NOT NULL REFERENCES candle_datasets(dataset_id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    observation_date TEXT NOT NULL,
    open_price REAL NOT NULL CHECK(open_price > 0),
    high_price REAL NOT NULL CHECK(high_price > 0),
    low_price REAL NOT NULL CHECK(low_price > 0),
    close_price REAL NOT NULL CHECK(close_price > 0),
    volume REAL NOT NULL CHECK(volume >= 0),
    broker_token TEXT,
    timestamp_ist TEXT,
    retrieved_at_utc TEXT NOT NULL,
    CHECK(low_price <= open_price AND low_price <= close_price),
    CHECK(high_price >= open_price AND high_price >= close_price),
    CHECK(high_price >= low_price),
    PRIMARY KEY (dataset_id, instrument_id, observation_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_candles_dataset_date
    ON daily_candles(dataset_id, observation_date);

CREATE TABLE IF NOT EXISTS analysis_metadata (
    analysis_id INTEGER PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES candle_datasets(dataset_id),
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL CHECK(window_end >= window_start),
    settings_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset_id, portfolio_id, window_start, window_end, settings_json, quality_json)
);

CREATE TABLE IF NOT EXISTS analysis_results (
    analysis_id INTEGER NOT NULL REFERENCES analysis_metadata(analysis_id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL CHECK(length(trim(metric_name)) > 0),
    value REAL NOT NULL,
    unit TEXT NOT NULL CHECK(length(trim(unit)) > 0),
    PRIMARY KEY(analysis_id, metric_name)
);
