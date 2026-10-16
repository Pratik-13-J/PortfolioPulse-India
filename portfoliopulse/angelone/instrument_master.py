"""Resolve configured NSE instruments against Angel One's current scrip master.

The config deliberately contains no tokens. A token is accepted only after an
exact trading-symbol and cash-market exchange match in the downloaded master.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.request import Request, urlopen


MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


class InstrumentMasterError(ValueError):
    """The instrument configuration or master cannot be trusted or resolved."""


@dataclass(frozen=True)
class ConfiguredInstrument:
    symbol: str
    trading_symbol: str
    exchange: str
    asset_class: str
    sector: str
    quantity: float
    target_weight: float
    is_benchmark: bool


@dataclass(frozen=True)
class ResolvedInstrument:
    symbol: str
    trading_symbol: str
    exchange: str
    token: str
    asset_class: str
    sector: str
    quantity: float
    target_weight: float
    is_benchmark: bool


@dataclass(frozen=True)
class MasterSnapshot:
    entries: tuple[dict[str, Any], ...]
    retrieved_at_utc: str
    from_cache: bool
    source_url: str = MASTER_URL


def _configured_entry(raw: Any, *, is_benchmark: bool) -> ConfiguredInstrument:
    if not isinstance(raw, dict):
        raise InstrumentMasterError("Every configured instrument must be an object.")
    if "token" in raw or "symboltoken" in raw:
        raise InstrumentMasterError("Do not put Angel One tokens in instrument config; resolve them from the master.")
    symbol = raw.get("symbol")
    trading_symbol = raw.get("trading_symbol")
    exchange = raw.get("exchange")
    asset_class = raw.get("asset_class")
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip():
        raise InstrumentMasterError("Each instrument needs a non-empty logical symbol.")
    if not isinstance(trading_symbol, str) or not trading_symbol or trading_symbol != trading_symbol.strip():
        raise InstrumentMasterError("Each instrument needs an exact non-empty trading_symbol.")
    if exchange != "NSE":
        raise InstrumentMasterError(f"{trading_symbol}: this portfolio requires exchange 'NSE'.")
    expected_class = "Benchmark" if is_benchmark else "Equity"
    if asset_class != expected_class:
        raise InstrumentMasterError(f"{trading_symbol}: asset_class must be '{expected_class}'.")
    sector = raw.get("sector", "Broad Market" if is_benchmark else "Unspecified")
    if not isinstance(sector, str) or not sector.strip():
        raise InstrumentMasterError(f"{trading_symbol}: sector must be a non-empty string.")
    if is_benchmark:
        quantity = 0.0
        target_weight = 0.0
    else:
        try:
            quantity = float(raw["quantity"])
            target_weight = float(raw["target_weight"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InstrumentMasterError(f"{trading_symbol}: quantity and target_weight must be numeric.") from exc
        if not math.isfinite(quantity) or quantity <= 0 or not math.isfinite(target_weight) or target_weight < 0:
            raise InstrumentMasterError(f"{trading_symbol}: invalid quantity or target_weight.")
    return ConfiguredInstrument(
        symbol=symbol,
        trading_symbol=trading_symbol,
        exchange=exchange,
        asset_class=asset_class,
        sector=sector.strip(),
        quantity=quantity,
        target_weight=target_weight,
        is_benchmark=is_benchmark,
    )


def load_config(path: Path) -> tuple[ConfiguredInstrument, ...]:
    """Load the benchmark and holdings; preserve exact Angel trading symbols."""
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstrumentMasterError(f"Cannot read instrument config: {path}.") from exc
    if not isinstance(config, dict) or config.get("interval") != "ONE_DAY" or config.get("base_currency") != "INR":
        raise InstrumentMasterError("Instrument config must specify ONE_DAY candles and INR currency.")
    holdings = config.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        raise InstrumentMasterError("Instrument config needs a non-empty holdings list.")
    entries = (_configured_entry(config.get("benchmark"), is_benchmark=True),) + tuple(
        _configured_entry(item, is_benchmark=False) for item in holdings
    )
    symbols = [item.symbol for item in entries]
    trading_symbols = [item.trading_symbol for item in entries]
    if len(set(symbols)) != len(symbols) or len(set(trading_symbols)) != len(trading_symbols):
        raise InstrumentMasterError("Configured symbols and trading symbols must be unique.")
    if not math.isclose(sum(item.target_weight for item in entries), 1.0, abs_tol=1e-9):
        raise InstrumentMasterError("Holding target weights must sum to 1.0.")
    return entries


def _download_master(url: str) -> bytes:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "PortfolioPulse-India/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def _parse_master(payload: bytes) -> tuple[dict[str, Any], ...]:
    try:
        entries = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise InstrumentMasterError("Angel One instrument master is not valid JSON.") from exc
    if not isinstance(entries, list) or not entries or any(not isinstance(item, dict) for item in entries):
        raise InstrumentMasterError("Angel One instrument master must be a non-empty list of objects.")
    return tuple(entries)


def _metadata_path(cache_path: Path) -> Path:
    return cache_path.with_name(cache_path.name + ".meta.json")


def _save_cache(cache_path: Path, payload: bytes, retrieved_at_utc: str) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "source_url": MASTER_URL,
        "retrieved_at_utc": retrieved_at_utc,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    payload_temp = cache_path.with_name(cache_path.name + ".tmp")
    meta_path = _metadata_path(cache_path)
    meta_temp = meta_path.with_name(meta_path.name + ".tmp")
    payload_temp.write_bytes(payload)
    meta_temp.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    os.replace(payload_temp, cache_path)
    os.replace(meta_temp, meta_path)


def _load_cache(cache_path: Path) -> MasterSnapshot:
    try:
        payload = cache_path.read_bytes()
        metadata = json.loads(_metadata_path(cache_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstrumentMasterError("No readable instrument-master cache and metadata are available.") from exc
    if not isinstance(metadata, dict) or metadata.get("source_url") != MASTER_URL:
        raise InstrumentMasterError("Cached instrument-master source is invalid.")
    if hashlib.sha256(payload).hexdigest() != metadata.get("sha256"):
        raise InstrumentMasterError("Cached instrument-master checksum does not match.")
    retrieved_at = metadata.get("retrieved_at_utc")
    if not isinstance(retrieved_at, str) or not retrieved_at:
        raise InstrumentMasterError("Cached instrument-master retrieval time is missing.")
    return MasterSnapshot(_parse_master(payload), retrieved_at, from_cache=True)


def fetch_master(cache_path: Path, getter: Callable[[str], bytes] | None = None) -> MasterSnapshot:
    """Fetch the official master, or disclose use of an intact local snapshot.

    `getter` takes the official URL and returns response bytes; inject it for
    tests. A cached snapshot may be stale, so callers must show `from_cache`
    and `retrieved_at_utc` rather than claiming live token verification.
    """
    cache_path = Path(cache_path)
    try:
        payload = (getter or _download_master)(MASTER_URL)
        entries = _parse_master(payload)
    except Exception as download_error:
        try:
            return _load_cache(cache_path)
        except InstrumentMasterError as cache_error:
            raise InstrumentMasterError(
                f"Instrument-master download failed and no valid local snapshot exists: {cache_error}"
            ) from download_error
    retrieved_at = datetime.now(timezone.utc).isoformat()
    _save_cache(cache_path, payload, retrieved_at)
    return MasterSnapshot(entries, retrieved_at, from_cache=False)


def load_cached_master(cache_path: Path) -> MasterSnapshot:
    """Read a verified local master snapshot without making a network request."""
    return _load_cache(Path(cache_path))


def resolve(
    config_entries: Sequence[ConfiguredInstrument],
    master_entries: Sequence[Mapping[str, Any]],
) -> tuple[ResolvedInstrument, ...]:
    """Require exactly one exact symbol/exchange match with a positive token."""
    resolved = []
    for configured in config_entries:
        matches = [
            row for row in master_entries
            if row.get("symbol") == configured.trading_symbol
            and str(row.get("exch_seg", "")).upper() in {"NSE", "NSE_CM"}
        ]
        if len(matches) != 1:
            raise InstrumentMasterError(
                f"{configured.trading_symbol}: expected one exact NSE master match; found {len(matches)}."
            )
        token = str(matches[0].get("token", ""))
        if not token.isdecimal() or int(token) <= 0:
            raise InstrumentMasterError(f"{configured.trading_symbol}: master token is invalid.")
        resolved.append(
            ResolvedInstrument(
                symbol=configured.symbol,
                trading_symbol=configured.trading_symbol,
                exchange=configured.exchange,
                token=token,
                asset_class=configured.asset_class,
                sector=configured.sector,
                quantity=configured.quantity,
                target_weight=configured.target_weight,
                is_benchmark=configured.is_benchmark,
            )
        )
    return tuple(resolved)
