"""Importers for user-downloaded reference files (corporate actions, constituents).

Every file needs a JSON sidecar (``<csv>.meta.json`` by default) that declares
where it came from and its SHA-256. The importer recomputes the digest and
refuses a mismatch. Imports are recorded in ``reference_datasets``; the
declared source is stored as declared and is *not* independently verified.
Nothing here downloads data, estimates weights or fills missing values.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import pandas as pd

from portfoliopulse.config import DB_PATH
from portfoliopulse.database import initialize, session
from portfoliopulse.migrations import CORPORATE_ACTION_TYPES, VERIFICATION_STATUSES

WEIGHT_SUM_TOLERANCE = 1e-4
WEIGHT_BASES = ("official", "estimated", "none")
CORPORATE_ACTION_COLUMNS = ("symbol", "ex_date", "action_type", "ratio_new", "ratio_old", "cash_amount_inr", "description")
CONSTITUENT_COLUMNS = ("symbol", "company_name", "industry", "weight")


class ReferenceDataError(ValueError):
    """A reference file or its sidecar cannot be imported faithfully."""


@dataclass(frozen=True)
class ReferenceImport:
    reference_dataset_id: int
    kind: str
    file_sha256: str
    row_count: int
    created: bool


def _iso_day(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ReferenceDataError(f"{label} must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ReferenceDataError(f"{label} must use YYYY-MM-DD.") from exc
    if parsed.isoformat() != value:
        raise ReferenceDataError(f"{label} must use YYYY-MM-DD.")
    return value


def _utc(value: Any) -> str:
    if not isinstance(value, str):
        raise ReferenceDataError("retrieved_at_utc must be a UTC ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReferenceDataError("retrieved_at_utc must be a UTC ISO timestamp.") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ReferenceDataError("retrieved_at_utc must be a UTC ISO timestamp.")
    return parsed.isoformat()


def read_sidecar(csv_path: Path, sidecar_path: Path | None = None) -> tuple[bytes, dict[str, Any]]:
    """Read a CSV and its sidecar, verifying the declared SHA-256."""
    csv_path = Path(csv_path)
    meta_path = Path(sidecar_path) if sidecar_path is not None else csv_path.with_suffix(".meta.json")
    try:
        payload = csv_path.read_bytes()
    except OSError as exc:
        raise ReferenceDataError(f"Cannot read reference CSV: {csv_path}.") from exc
    try:
        sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceDataError(f"A readable JSON sidecar is required: {meta_path}.") from exc
    if not isinstance(sidecar, dict):
        raise ReferenceDataError("The reference sidecar must be a JSON object.")
    for key in ("source_name", "source_url", "retrieved_at_utc", "provenance_label", "file_sha256"):
        if not isinstance(sidecar.get(key), str) or not sidecar[key].strip():
            raise ReferenceDataError(f"The reference sidecar needs {key}.")
    url = urlsplit(sidecar["source_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ReferenceDataError("source_url must be a credential-free HTTPS URL without query or fragment.")
    digest = hashlib.sha256(payload).hexdigest()
    if sidecar["file_sha256"].strip().lower() != digest:
        raise ReferenceDataError("The reference CSV does not match its declared SHA-256.")
    status = sidecar.get("verification_status", "user_declared_unverified")
    if status not in VERIFICATION_STATUSES or status == "verified_live":
        raise ReferenceDataError("Reference files cannot be self-declared verified_live; use a ladder status.")
    sidecar = {**sidecar, "file_sha256": digest, "verification_status": status,
               "retrieved_at_utc": _utc(sidecar["retrieved_at_utc"])}
    if "as_of_date" in sidecar:
        _iso_day(sidecar["as_of_date"], "as_of_date")
    return payload, sidecar


def _read_csv(payload: bytes, columns: tuple[str, ...], required: tuple[str, ...]) -> pd.DataFrame:
    try:
        frame = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise ReferenceDataError("The reference CSV cannot be parsed.") from exc
    missing = set(required) - set(frame.columns)
    if missing:
        raise ReferenceDataError(f"The reference CSV is missing columns: {', '.join(sorted(missing))}.")
    for column in columns:
        if column not in frame:
            frame[column] = ""
    frame = frame.loc[:, list(columns)]
    for column in columns:
        frame[column] = frame[column].astype(str).str.strip()
    if frame.empty:
        raise ReferenceDataError("The reference CSV has no rows.")
    return frame


def _optional_number(value: str, label: str, *, positive: bool) -> float | None:
    if value == "":
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise ReferenceDataError(f"{label} must be numeric when supplied.") from exc
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        raise ReferenceDataError(f"{label} must be {'positive' if positive else 'non-negative'} and finite.")
    return number


def _register(con, *, kind: str, sidecar: Mapping[str, Any], row_count: int) -> tuple[int, bool]:
    existing = con.execute(
        "SELECT reference_dataset_id, row_count FROM reference_datasets WHERE kind=? AND file_sha256=?",
        (kind, sidecar["file_sha256"]),
    ).fetchone()
    if existing is not None:
        if int(existing["row_count"]) != row_count:
            raise ReferenceDataError("An existing reference dataset with this digest has a different row count.")
        return int(existing["reference_dataset_id"]), False
    cursor = con.execute(
        """INSERT INTO reference_datasets(kind,source_name,source_url,provenance_label,verification_status,
               file_sha256,as_of_date,retrieved_at_utc,row_count,metadata_json)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (kind, sidecar["source_name"].strip(), sidecar["source_url"].strip(), sidecar["provenance_label"].strip(),
         sidecar["verification_status"], sidecar["file_sha256"], sidecar.get("as_of_date"),
         sidecar["retrieved_at_utc"], row_count, json.dumps(dict(sidecar), sort_keys=True, allow_nan=False)),
    )
    return int(cursor.lastrowid), True


def parse_corporate_actions(payload: bytes) -> pd.DataFrame:
    frame = _read_csv(payload, CORPORATE_ACTION_COLUMNS, ("symbol", "ex_date", "action_type"))
    if frame["symbol"].eq("").any():
        raise ReferenceDataError("Every corporate action needs a symbol.")
    for value in frame["ex_date"]:
        _iso_day(value, "ex_date")
    frame["action_type"] = frame["action_type"].str.lower()
    if not frame["action_type"].isin(CORPORATE_ACTION_TYPES).all():
        raise ReferenceDataError(f"action_type must be one of: {', '.join(CORPORATE_ACTION_TYPES)}.")
    if frame.duplicated(["symbol", "ex_date", "action_type"]).any():
        raise ReferenceDataError("Duplicate symbol/ex_date/action_type rows are not allowed.")
    frame["ratio_new"] = [_optional_number(v, "ratio_new", positive=True) for v in frame["ratio_new"]]
    frame["ratio_old"] = [_optional_number(v, "ratio_old", positive=True) for v in frame["ratio_old"]]
    frame["cash_amount_inr"] = [_optional_number(v, "cash_amount_inr", positive=False) for v in frame["cash_amount_inr"]]
    needs_ratio = frame["action_type"].isin(["split", "bonus"])
    if (needs_ratio & (frame["ratio_new"].isna() | frame["ratio_old"].isna())).any():
        raise ReferenceDataError("Split and bonus rows need ratio_new and ratio_old.")
    return frame.sort_values(["symbol", "ex_date"]).reset_index(drop=True)


def import_corporate_actions(
    csv_path: Path, *, sidecar_path: Path | None = None, db_path: Path = DB_PATH,
) -> ReferenceImport:
    """Validate and store a corporate-action file (e.g. an NSE download)."""
    payload, sidecar = read_sidecar(csv_path, sidecar_path)
    frame = parse_corporate_actions(payload)
    initialize(db_path)
    with session(db_path) as con:
        reference_id, created = _register(con, kind="corporate_actions", sidecar=sidecar, row_count=len(frame))
        if created:
            con.executemany(
                """INSERT INTO corporate_actions(reference_dataset_id,symbol,ex_date,action_type,
                       ratio_new,ratio_old,cash_amount_inr,description) VALUES(?,?,?,?,?,?,?,?)""",
                [(reference_id, r.symbol, r.ex_date, r.action_type,
                  None if pd.isna(r.ratio_new) else float(r.ratio_new),
                  None if pd.isna(r.ratio_old) else float(r.ratio_old),
                  None if pd.isna(r.cash_amount_inr) else float(r.cash_amount_inr), r.description)
                 for r in frame.itertuples(index=False)],
            )
    return ReferenceImport(reference_id, "corporate_actions", sidecar["file_sha256"], len(frame), created)


def load_corporate_actions(reference_dataset_id: int, *, db_path: Path = DB_PATH) -> pd.DataFrame:
    with session(db_path) as con:
        return pd.read_sql_query(
            """SELECT symbol,ex_date,action_type,ratio_new,ratio_old,cash_amount_inr,description
               FROM corporate_actions WHERE reference_dataset_id=? ORDER BY symbol,ex_date""",
            con, params=(reference_dataset_id,),
        )


def parse_constituents(payload: bytes, weight_basis: str) -> pd.DataFrame:
    """Parse constituent rows; weights must sum to 1 ± 1e-4 unless basis is 'none'."""
    if weight_basis not in WEIGHT_BASES:
        raise ReferenceDataError(f"weight_basis must be one of: {', '.join(WEIGHT_BASES)}.")
    frame = _read_csv(payload, CONSTITUENT_COLUMNS, ("symbol", "industry"))
    if frame["symbol"].eq("").any() or frame["industry"].eq("").any():
        raise ReferenceDataError("Every constituent needs a symbol and an industry.")
    if frame["symbol"].duplicated().any():
        raise ReferenceDataError("Constituent symbols must be unique.")
    if weight_basis == "none":
        if frame["weight"].ne("").any():
            raise ReferenceDataError("weight_basis 'none' cannot carry weights; declare 'official' or 'estimated'.")
        frame["weight"] = None
        return frame.reset_index(drop=True)
    if frame["weight"].eq("").any():
        raise ReferenceDataError("Every constituent needs a weight; missing weights are never filled.")
    weights = [_optional_number(v, "weight", positive=False) for v in frame["weight"]]
    if any(w is None or w > 1 for w in weights):
        raise ReferenceDataError("Weights must be decimal fractions in [0, 1].")
    total = math.fsum(weights)  # type: ignore[arg-type]
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ReferenceDataError(f"Constituent weights sum to {total:.6f}, not 1 ± {WEIGHT_SUM_TOLERANCE}.")
    frame["weight"] = weights
    return frame.reset_index(drop=True)


def import_benchmark_constituents(
    csv_path: Path, *, sidecar_path: Path | None = None, db_path: Path = DB_PATH,
) -> ReferenceImport:
    """Store a dated constituent/industry file for one benchmark.

    The sidecar additionally needs ``benchmark_symbol``, ``as_of_date`` and
    ``weight_basis`` (``official`` / ``estimated`` / ``none``). The free NSE
    Indices list has names and industries only, so ``none`` is the honest
    basis for it; attribution stays gated for such a dataset.
    """
    payload, sidecar = read_sidecar(csv_path, sidecar_path)
    for key in ("benchmark_symbol", "as_of_date", "weight_basis"):
        if not isinstance(sidecar.get(key), str) or not sidecar[key].strip():
            raise ReferenceDataError(f"The constituent sidecar needs {key}.")
    frame = parse_constituents(payload, sidecar["weight_basis"])
    initialize(db_path)
    with session(db_path) as con:
        reference_id, created = _register(con, kind="index_constituents", sidecar=sidecar, row_count=len(frame))
        if created:
            con.executemany(
                """INSERT INTO benchmark_constituents(reference_dataset_id,benchmark_symbol,as_of_date,symbol,
                       company_name,industry,weight,weight_basis) VALUES(?,?,?,?,?,?,?,?)""",
                [(reference_id, sidecar["benchmark_symbol"], sidecar["as_of_date"], r.symbol, r.company_name,
                  r.industry, None if r.weight is None or pd.isna(r.weight) else float(r.weight),
                  sidecar["weight_basis"]) for r in frame.itertuples(index=False)],
            )
    return ReferenceImport(reference_id, "index_constituents", sidecar["file_sha256"], len(frame), created)


def load_benchmark_constituents(reference_dataset_id: int, *, db_path: Path = DB_PATH) -> pd.DataFrame:
    with session(db_path) as con:
        return pd.read_sql_query(
            """SELECT benchmark_symbol,as_of_date,symbol,company_name,industry,weight,weight_basis
               FROM benchmark_constituents WHERE reference_dataset_id=? ORDER BY symbol""",
            con, params=(reference_dataset_id,),
        )


def save_security_classifications(
    mapping: Mapping[str, tuple[str, str]], *, taxonomy: str, effective_date: str, source_label: str,
    reference_dataset_id: int | None = None, db_path: Path = DB_PATH,
) -> int:
    """Record symbol -> (classification, common_sector) for one taxonomy.

    ``common_sector`` is the shared bucket used to compare NSE industries with
    the project's own sector labels. Rows are keyed by (symbol, taxonomy,
    effective_date) so a later mapping never overwrites history.
    """
    if taxonomy not in ("nse_industry", "project_sector"):
        raise ReferenceDataError("taxonomy must be 'nse_industry' or 'project_sector'.")
    _iso_day(effective_date, "effective_date")
    if not isinstance(source_label, str) or not source_label.strip():
        raise ReferenceDataError("source_label must be non-empty.")
    rows = []
    for symbol, pair in mapping.items():
        if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not all(isinstance(v, str) and v.strip() for v in pair):
            raise ReferenceDataError("Each classification needs (classification, common_sector) text.")
        rows.append((symbol, taxonomy, pair[0].strip(), pair[1].strip(), effective_date, reference_dataset_id,
                     source_label.strip()))
    initialize(db_path)
    with session(db_path) as con:
        con.executemany(
            """INSERT INTO security_classifications(symbol,taxonomy,classification,common_sector,effective_date,
                   reference_dataset_id,source_label) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(symbol,taxonomy,effective_date) DO NOTHING""", rows,
        )
    return len(rows)


def load_security_classifications(
    taxonomy: str, *, as_of_date: str, db_path: Path = DB_PATH,
) -> dict[str, tuple[str, str]]:
    """Latest classification per symbol effective on or before ``as_of_date``."""
    _iso_day(as_of_date, "as_of_date")
    with session(db_path) as con:
        rows = con.execute(
            """SELECT symbol,classification,common_sector,effective_date FROM security_classifications
               WHERE taxonomy=? AND effective_date<=? ORDER BY symbol,effective_date""",
            (taxonomy, as_of_date),
        ).fetchall()
    result: dict[str, tuple[str, str]] = {}
    for row in rows:
        result[row["symbol"]] = (row["classification"], row["common_sector"])
    return result
