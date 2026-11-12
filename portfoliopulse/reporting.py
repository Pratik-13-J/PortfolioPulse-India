"""CSV and self-contained HTML reports built only from persisted results.

Every report starts with a provenance header (dataset, source, verification
status, price basis, engine runs, generation time) and the standing labels:
price return vs NIFTY 50 price index (not TRI); in-sample / hypothetical /
illustrative; not investment advice. Values are HTML-escaped. Plotly's
JavaScript is embedded once so the file opens offline.
"""

from __future__ import annotations

import html
import io
import zipfile
from datetime import datetime, timezone
from typing import Any, Mapping

import pandas as pd
import plotly.graph_objects as go

LABELS = (
    "Price return vs NIFTY 50 price index (not TRI); dividends and corporate actions are not independently adjusted.",
    "VaR/ES and optimization outputs are in-sample statistics; stress scenarios are hypothetical revaluations.",
    "Optimized weights are illustrative and never replace the stored holdings. Not investment advice.",
)


def provenance_rows(dataset: Mapping[str, Any], runs: Mapping[str, Any]) -> list[tuple[str, str]]:
    rows = [
        ("Dataset", f"{dataset.get('dataset_id')} · {dataset.get('portfolio_name', '')}"),
        ("Source", str(dataset.get("source_name", ""))),
        ("Verification status", str(dataset.get("verification_status", ""))),
        ("Provenance status", str(dataset.get("provenance_status", ""))),
        ("Price basis", str(dataset.get("price_basis", ""))),
        ("Window", f"{dataset.get('window_start')} to {dataset.get('window_end_exclusive')} (exclusive)"),
    ]
    for kind, run in runs.items():
        rows.append((f"{kind} run", "not stored" if run is None else
                     f"analysis {run.analysis_id} · {run.engine_version} · {run.created_at_utc} UTC"))
    rows.append(("Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))
    return rows


def csv_bundle(tables: Mapping[str, pd.DataFrame], header: list[tuple[str, str]]) -> bytes:
    """A zip of CSVs plus a provenance.csv; deterministic member order."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("provenance.csv", pd.DataFrame(
            header + [("Label", label) for label in LABELS], columns=["field", "value"]).to_csv(index=False))
        for name in sorted(tables):
            frame = tables[name]
            if frame is not None and not frame.empty:
                archive.writestr(f"{name}.csv", frame.to_csv(index=False))
    return buffer.getvalue()


def html_report(title: str, header: list[tuple[str, str]], sections: list[tuple[str, Any]]) -> str:
    """Sections are (heading, DataFrame | plotly Figure | str | list[str])."""
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:24px;color:#0b0b0b;background:#fcfcfb}"
        "table{border-collapse:collapse;font-size:13px;margin:8px 0}"
        "th,td{border:1px solid #e3e2de;padding:4px 8px;text-align:right}th{background:#f0efec}"
        "td:first-child,th:first-child{text-align:left}.prov td{text-align:left}"
        ".label{color:#52514e;font-size:13px}</style></head><body>",
        f"<h1>{html.escape(title)}</h1>", "<table class='prov'>",
    ]
    parts += [f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in header]
    parts.append("</table>")
    parts += [f"<p class='label'>{html.escape(label)}</p>" for label in LABELS]
    plotly_loaded = False
    for heading, content in sections:
        parts.append(f"<h2>{html.escape(heading)}</h2>")
        if isinstance(content, go.Figure):
            parts.append(content.to_html(full_html=False, include_plotlyjs=not plotly_loaded))
            plotly_loaded = True
        elif isinstance(content, pd.DataFrame):
            parts.append(content.to_html(index=False, escape=True, float_format=lambda v: f"{v:.6g}",
                                         na_rep="n/a"))
        elif isinstance(content, (list, tuple)):
            parts.append("<ul>" + "".join(f"<li>{html.escape(str(i))}</li>" for i in content) + "</ul>")
        else:
            parts.append(f"<p>{html.escape(str(content))}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)
