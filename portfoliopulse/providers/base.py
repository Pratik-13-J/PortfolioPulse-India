"""Provider protocol: one interface over the existing daily-candle sources.

Adapters are thin wrappers around the already-tested modules
(``angelone.client``, ``sourced_csv``, ``market_data``). They add no new
validation or transformation; they only attach a uniform
:class:`ProvenanceRecord` so callers can never mix sources silently.

Hierarchy (roadmap §3): Angel One SmartAPI is primary; a fallback is used only
when the caller explicitly selects it. There is no automatic substitution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

import pandas as pd

from portfoliopulse.angelone.instrument_master import ConfiguredInstrument, ResolvedInstrument
from portfoliopulse.migrations import PRICE_BASES, VERIFICATION_STATUSES


@dataclass(frozen=True)
class ProvenanceRecord:
    provider: str
    provenance_kind: str
    price_basis: str
    verification_status: str
    source_name: str
    retrieved_at_utc: str
    source_url: str | None = None
    tier: str = "primary"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.price_basis not in PRICE_BASES:
            raise ValueError("Unsupported price basis.")
        if self.verification_status not in VERIFICATION_STATUSES:
            raise ValueError("Unsupported verification status.")
        if self.tier not in ("primary", "supplementary", "fallback", "isolated"):
            raise ValueError("Unsupported provider tier.")
        if self.verification_status == "verified_live" and self.provenance_kind != "angel_one_smartapi":
            raise ValueError("Only a live Angel One retrieval may be verified_live.")


@dataclass(frozen=True)
class ProviderResult:
    candles: pd.DataFrame
    provenance: ProvenanceRecord
    raw: Any = None


@runtime_checkable
class DailyCandleProvider(Protocol):
    name: str
    tier: str

    def fetch(self, start: str, end_exclusive: str) -> ProviderResult: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AngelOneProvider:
    """Primary provider; wraps :func:`angelone.client.fetch_daily_candles`.

    The authenticated client is injected so tests stay offline. A result is
    ``verified_live`` because the candles came from a live broker response.
    """

    name = "angel_one_smartapi"
    tier = "primary"

    def __init__(self, client: Any, instruments: Sequence[ResolvedInstrument], **fetch_kwargs: Any) -> None:
        self._client = client
        self._instruments = tuple(instruments)
        self._kwargs = fetch_kwargs

    def fetch(self, start: str, end_exclusive: str) -> ProviderResult:
        from portfoliopulse.angelone import client as angel

        batches: list[dict[str, Any]] = []
        candles = angel.fetch_daily_candles(
            self._client, self._instruments, start, end_exclusive, raw_batches=batches, **self._kwargs,
        )
        record = ProvenanceRecord(
            provider=self.name, provenance_kind=angel.SOURCE, price_basis=angel.PRICE_BASIS,
            verification_status="verified_live", source_name=angel.SOURCE,
            retrieved_at_utc=str(candles["retrieved_at_utc"].iloc[0]), tier=self.tier,
        )
        return ProviderResult(candles, record, raw=batches)


class SourcedCsvProvider:
    """User CSV fallback; wraps :func:`sourced_csv.import_sourced_csv`.

    ``tier`` defaults to ``fallback``. The sidecar's declaration is recorded
    as ``user_declared_unverified`` (or ``fallback_unverified`` when the caller
    labels the file as a secondary-provider export such as Yahoo).
    """

    name = "sourced_csv"

    def __init__(self, csv_path: Path, config_entries: Sequence[ConfiguredInstrument], *,
                 metadata_path: Path | None = None, output_dir: Path | None = None,
                 verification_status: str = "user_declared_unverified", tier: str = "fallback") -> None:
        if verification_status not in ("user_declared_unverified", "fallback_unverified"):
            raise ValueError("A CSV import can only be user_declared_unverified or fallback_unverified.")
        self._csv = Path(csv_path)
        self._config = tuple(config_entries)
        self._meta = metadata_path
        self._out = output_dir
        self._status = verification_status
        self.tier = tier

    def fetch(self, start: str, end_exclusive: str) -> ProviderResult:
        from portfoliopulse.sourced_csv import PROVENANCE_KIND, import_sourced_csv

        result = import_sourced_csv(self._csv, self._config, start, end_exclusive,
                                    metadata_path=self._meta, output_dir=self._out)
        meta = result.metadata
        record = ProvenanceRecord(
            provider=self.name, provenance_kind=PROVENANCE_KIND, price_basis=meta["price_basis"],
            verification_status=self._status, source_name=meta["source_name"],
            retrieved_at_utc=meta["retrieved_at_utc"], source_url=meta.get("source_url"), tier=self.tier,
        )
        return ProviderResult(result.candles, record, raw=result)


class YahooResearchProvider:
    """Secondary research path; wraps :func:`market_data.download_adjusted_observations`.

    Returns adjusted *close observations* (not OHLCV candles) labelled
    ``provider_adjusted_ohlcv`` / ``fallback_unverified``. It is for
    independent comparison and must not be merged into another provider's
    return series.
    """

    name = "yahoo_finance_research"
    tier = "fallback"

    def __init__(self, symbols: Sequence[str], *, downloader: Callable[..., Any] | None = None) -> None:
        self._symbols = list(symbols)
        self._downloader = downloader

    def fetch(self, start: str, end_exclusive: str) -> ProviderResult:
        from portfoliopulse.market_data import download_adjusted_observations

        observations = download_adjusted_observations(self._symbols, start, end_exclusive, downloader=self._downloader)
        record = ProvenanceRecord(
            provider=self.name, provenance_kind="yahoo_finance_research", price_basis="provider_adjusted_ohlcv",
            verification_status="fallback_unverified", source_name="Yahoo Finance via yfinance",
            retrieved_at_utc=_now(), tier=self.tier,
            notes=("Close observations only; auto_adjust=True; research comparison, not a merge source.",),
        )
        return ProviderResult(observations, record)


def require_explicit_provider(selected: str, available: Sequence[DailyCandleProvider]) -> DailyCandleProvider:
    """Return exactly the named provider; never fall back automatically."""
    matches = [item for item in available if item.name == selected]
    if len(matches) != 1:
        raise ValueError(f"Provider {selected!r} is not uniquely available; no substitution is performed.")
    return matches[0]
