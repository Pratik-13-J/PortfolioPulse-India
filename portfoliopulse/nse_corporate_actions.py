"""Convert an NSE "Corporate Actions" CSV download into the project's format.

The NSE page (Companies Listing -> Corporate Filings -> Corporate Actions)
exports one row per announced action with free-text ``PURPOSE`` such as
``Bonus 1:1``, ``Face Value Split (Sub-Division) - From Rs 10/- Per Share To
Re 1/- Per Share``, ``Interim Dividend - Rs 8 Per Share`` or ``Demerger``.
Column names and date formats are detected tolerantly, but every recognised
column is validated and nothing is guessed:

* bonus ``a:b`` (a new shares for every b held) -> ratio_new = a + b,
  ratio_old = b (shares after : before; price factor = ratio_old / ratio_new);
* face-value split from F_old to F_new -> ratio_new = F_old, ratio_old = F_new;
* dividends -> cash_amount_inr per share; several dividends on one ex-date
  are summed into one row (the table key is symbol / ex_date / action_type);
* demerger / rights / merger -> stored without a ratio (their price effect
  needs a separate sourced value);
* meeting notices (AGM/EGM, book closure) are ignored as non-price events;
* anything else, or a purpose that cannot be parsed confidently, goes to the
  *review* table and is never imported automatically.

The output CSV keeps the raw NSE file's SHA-256 in its sidecar so the import
stays traceable to the downloaded file.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from portfoliopulse.reference_data import CORPORATE_ACTION_COLUMNS, ReferenceDataError

_COLUMN_ALIASES = {
    "symbol": ("symbol",),
    "series": ("series",),
    "purpose": ("purpose", "subject"),
    "ex_date": ("exdate", "ex-date", "exdt"),
    "record_date": ("recorddate", "recdate"),
}
_NUMBER = r"(\d+(?:\.\d+)?)"
_BONUS = re.compile(rf"\bbonus\b[^0-9]*{_NUMBER}\s*:\s*{_NUMBER}", re.I)
_SPLIT = re.compile(rf"(?:split|sub-?division).*?from\s*(?:rs\.?|re\.?|inr)?\s*{_NUMBER}.*?to\s*(?:rs\.?|re\.?|inr)?\s*{_NUMBER}", re.I)
_DIVIDEND = re.compile(rf"dividend[^0-9]*?(?:rs\.?|re\.?|inr|₹)\s*{_NUMBER}", re.I)
_IGNORED = re.compile(r"^(annual general meeting|agm|egm|extra ?ordinary general meeting|book closure|"
                      r"general meeting)\b", re.I)


@dataclass(frozen=True)
class NseConversion:
    actions: pd.DataFrame  # CORPORATE_ACTION_COLUMNS, ready for import_corporate_actions
    review: pd.DataFrame  # rows that need a human decision; never imported automatically
    ignored: pd.DataFrame  # non-price notices (AGM/EGM, book closure)
    raw_sha256: str
    raw_rows: int


def _normalise(name: str) -> str:
    return re.sub(r"[\s_]", "", str(name).strip().lower())


def _find_columns(frame: pd.DataFrame) -> dict[str, str]:
    found: dict[str, str] = {}
    normalised = {_normalise(c): c for c in frame.columns}
    for target, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if _normalise(alias) in normalised:
                found[target] = normalised[_normalise(alias)]
                break
    missing = {"symbol", "purpose", "ex_date"} - set(found)
    if missing:
        raise ReferenceDataError(f"NSE corporate-action CSV is missing columns: {', '.join(sorted(missing))}.")
    return found


def _iso_date(value: Any) -> str | None:
    text = str(value).strip()
    if text in ("", "-", "nan", "NaT", "None"):
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ReferenceDataError(f"Unrecognised NSE date: {text!r}.")


def parse_purpose(purpose: str) -> list[dict[str, Any]] | None:
    """Parse one PURPOSE text into action dicts; None means 'needs review'.

    Compound purposes separated by '/' are parsed part by part; any part that is
    neither recognised nor an ignorable meeting notice sends the whole row to review.
    """
    text = " ".join(str(purpose).split())
    if not text:
        return None
    # Split compound purposes on '/', but not inside amounts such as "Rs 10/- Per Share".
    parts = [p.strip() for p in re.split(r"\s*/\s*(?!-\s*per\b)", text, flags=re.I) if p.strip()]
    actions: list[dict[str, Any]] = []
    for part in parts:
        lower = part.lower()
        if _IGNORED.search(part):
            continue
        if "bonus" in lower:
            m = _BONUS.search(part)
            if not m:
                return None
            new, old = float(m.group(1)), float(m.group(2))
            actions.append({"action_type": "bonus", "ratio_new": new + old, "ratio_old": old, "description": part})
        elif "split" in lower or "sub-division" in lower or "subdivision" in lower:
            m = _SPLIT.search(part)
            if not m or float(m.group(2)) >= float(m.group(1)):
                return None
            actions.append({"action_type": "split", "ratio_new": float(m.group(1)), "ratio_old": float(m.group(2)),
                            "description": part})
        elif "dividend" in lower:
            m = _DIVIDEND.search(part)
            if not m:
                return None
            actions.append({"action_type": "dividend", "cash_amount_inr": float(m.group(1)), "description": part})
        elif "demerger" in lower or "scheme of arrangement" in lower:
            actions.append({"action_type": "demerger", "description": part})
        elif "rights" in lower:
            actions.append({"action_type": "rights", "description": part})
        elif "amalgamation" in lower or "merger" in lower:
            actions.append({"action_type": "merger", "description": part})
        else:
            return None
    return actions


def convert_nse_csv(payload: bytes, *, symbols: list[str] | None = None,
                    window: tuple[str, str] | None = None) -> NseConversion:
    """Parse raw NSE CSV bytes; optionally keep only ``symbols`` and an ex-date window [start, end)."""
    try:
        frame = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise ReferenceDataError("The NSE corporate-action CSV cannot be parsed.") from exc
    columns = _find_columns(frame)
    raw_rows = len(frame)
    rows, review, ignored = [], [], []
    for _, raw in frame.iterrows():
        symbol = str(raw[columns["symbol"]]).strip().upper()
        series = str(raw[columns["series"]]).strip().upper() if "series" in columns else "EQ"
        purpose = str(raw[columns["purpose"]]).strip()
        if parse_purpose(purpose) == []:  # meeting notices carry no price effect, dated or not
            if symbols is None or symbol in symbols:
                ignored.append({"symbol": symbol, "ex_date": str(raw[columns["ex_date"]]).strip(),
                                "purpose": purpose, "reason": "meeting notice"})
            continue
        try:
            ex_date = _iso_date(raw[columns["ex_date"]])
        except ReferenceDataError as exc:
            review.append({"symbol": symbol, "ex_date": str(raw[columns["ex_date"]]), "purpose": purpose,
                           "reason": str(exc)})
            continue
        if symbols is not None and symbol not in symbols:
            continue
        if series not in ("EQ", ""):
            ignored.append({"symbol": symbol, "ex_date": ex_date, "purpose": purpose, "reason": f"series {series}"})
            continue
        if ex_date is None:
            review.append({"symbol": symbol, "ex_date": "", "purpose": purpose, "reason": "missing ex-date"})
            continue
        if window is not None and not (window[0] <= ex_date < window[1]):
            continue
        parsed = parse_purpose(purpose)
        if parsed is None:
            review.append({"symbol": symbol, "ex_date": ex_date, "purpose": purpose,
                           "reason": "purpose not recognised confidently"})
            continue
        if not parsed:
            ignored.append({"symbol": symbol, "ex_date": ex_date, "purpose": purpose, "reason": "meeting notice"})
            continue
        for action in parsed:
            rows.append({"symbol": symbol, "ex_date": ex_date, **action})
    actions = pd.DataFrame(rows, columns=["symbol", "ex_date", "action_type", "ratio_new", "ratio_old",
                                          "cash_amount_inr", "description"])
    if not actions.empty:
        merged = []
        for (symbol, ex_date, kind), group in actions.groupby(["symbol", "ex_date", "action_type"], sort=True):
            if len(group) > 1 and kind != "dividend":
                for item in group.itertuples(index=False):
                    review.append({"symbol": symbol, "ex_date": ex_date, "purpose": item.description,
                                   "reason": f"several {kind} rows on one ex-date"})
                continue
            first = group.iloc[0].to_dict()
            if kind == "dividend":
                first["cash_amount_inr"] = float(group["cash_amount_inr"].sum())
                first["description"] = " + ".join(group["description"])
            merged.append(first)
        actions = pd.DataFrame(merged, columns=list(CORPORATE_ACTION_COLUMNS))
    return NseConversion(
        actions=actions.reindex(columns=list(CORPORATE_ACTION_COLUMNS)),
        review=pd.DataFrame(review, columns=["symbol", "ex_date", "purpose", "reason"]),
        ignored=pd.DataFrame(ignored, columns=["symbol", "ex_date", "purpose", "reason"]),
        raw_sha256=hashlib.sha256(payload).hexdigest(), raw_rows=raw_rows,
    )


def write_project_files(
    conversion: NseConversion, output_csv: Path, *, source_url: str, retrieved_at_utc: str,
    raw_file_name: str, as_of_date: str | None = None,
) -> tuple[Path, Path]:
    """Write the project-format CSV and its sidecar (declared NSE source, both SHA-256s).

    Refuses to write while any row still needs review, so nothing ambiguous
    reaches the importer.
    """
    if not conversion.review.empty:
        raise ReferenceDataError(f"{len(conversion.review)} NSE row(s) need review before import.")
    if conversion.actions.empty:
        raise ReferenceDataError("No corporate actions to write.")
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    text = conversion.actions.to_csv(index=False, lineterminator="\n", float_format="%.10g")
    output_csv.write_bytes(text.encode("utf-8"))
    sidecar: dict[str, Any] = {
        "source_name": "NSE India corporate actions (user download)",
        "source_url": source_url,
        "retrieved_at_utc": retrieved_at_utc,
        "provenance_label": "nse_corporate_actions_user_download",
        "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "raw_file_name": raw_file_name,
        "raw_file_sha256": conversion.raw_sha256,
        "raw_rows": conversion.raw_rows,
        "converted_at_utc": datetime.now(timezone.utc).isoformat(),
        "conversion": "portfoliopulse.nse_corporate_actions.convert_nse_csv",
        "ignored_rows": conversion.ignored.to_dict(orient="records"),
    }
    if as_of_date:
        sidecar["as_of_date"] = as_of_date
    meta = output_csv.with_suffix(".meta.json")
    meta.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_csv, meta
