"""Uniform provider protocol over the existing data-source modules."""

from portfoliopulse.providers.base import (  # noqa: F401
    AngelOneProvider, DailyCandleProvider, ProvenanceRecord, ProviderResult,
    SourcedCsvProvider, YahooResearchProvider, require_explicit_provider,
)
