"""Separate, illustrative Indian listed-instrument universe for market downloads."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ListedInstrument:
    symbol: str
    name: str
    asset_class: str
    sector: str
    quantity: float
    target_weight: float


REAL_INSTRUMENTS = (
    ListedInstrument("RELIANCE.NS", "Reliance Industries", "Equity", "Energy and Conglomerate", 150, .18),
    ListedInstrument("HDFCBANK.NS", "HDFC Bank", "Equity", "Banking", 200, .18),
    ListedInstrument("INFY.NS", "Infosys", "Equity", "Information Technology", 100, .14),
    ListedInstrument("SUNPHARMA.NS", "Sun Pharmaceutical Industries", "Equity", "Healthcare", 55, .10),
    ListedInstrument("HINDUNILVR.NS", "Hindustan Unilever", "Equity", "Consumer Staples", 40, .10),
    ListedInstrument("MARUTI.NS", "Maruti Suzuki India", "Equity", "Automobiles", 7, .10),
    ListedInstrument("GOLDBEES.NS", "Nippon India ETF Gold BeES", "Gold", "Gold", 800, .10),
    ListedInstrument("LTGILTBEES.NS", "Nippon India ETF Long Term Gilt", "Debt", "Government Debt", 3300, .10),
    ListedInstrument("^NSEI", "Nifty 50 price index", "Benchmark", "Broad Market", 0, 0),
)

REAL_PORTFOLIO_NAME = "Indian Listed Sample Portfolio"
REAL_SYMBOLS = [item.symbol for item in REAL_INSTRUMENTS]
