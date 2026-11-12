"""Sanitized Angel One HTTP 403 diagnostic. Run live checks locally only.

Offline (default, no network, no credentials):
    python -m scripts.diagnose_angelone

Live (authenticates with your local .env; prints no secret, token or IP):
    python -m scripts.diagnose_angelone --live --idle-seconds 65 --attempts 2 --spacing 60

The JSON report contains booleans, status codes, hosts, route paths, header
names, Angel error codes and fixed labels only. Share it, not your .env.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from portfoliopulse.angelone.diagnostics import (
    RequestRecorder, environment_report, live_checks, master_report, sdk_report,
)
from portfoliopulse.angelone.instrument_master import (
    InstrumentMasterError, load_cached_master, load_config, resolve,
)
from portfoliopulse.config import PROJECT_ROOT

MASTER_PATH = PROJECT_ROOT / "data" / "raw" / "instrument_master.json"


def offline_report(*, skip_master: bool = False) -> tuple[dict, list]:
    report: dict = {
        "mode": "offline",
        "dotenv_file_present": (PROJECT_ROOT / ".env").exists(),
        "credential_names_in_process_environment": environment_report(),
        "sdk": sdk_report(),
    }
    resolved: list = []
    if skip_master:
        report["instrument_master"] = {"skipped": True}
        return report, resolved
    meta_path = MASTER_PATH.with_name(MASTER_PATH.name + ".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    error = None
    try:
        snapshot = load_cached_master(MASTER_PATH)
        resolved = list(resolve(load_config(PROJECT_ROOT / "config" / "instruments.json"), snapshot.entries))
    except InstrumentMasterError as exc:
        error = type(exc).__name__
    report["instrument_master"] = master_report(meta, resolved, error)
    return report, resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="Authenticate and run the live checks (local use only).")
    parser.add_argument("--idle-seconds", type=float, default=65.0, help="Idle wait before the first candle call.")
    parser.add_argument("--attempts", type=int, default=1, help="Number of historical candle calls.")
    parser.add_argument("--spacing", type=float, default=60.0, help="Seconds between candle calls.")
    parser.add_argument("--window-days", type=int, default=10, help="Candle window length, 1-15 days.")
    parser.add_argument("--symbol", default="RELIANCE", help="Configured equity symbol for LTP/candle checks.")
    parser.add_argument("--skip-master", action="store_true", help="Do not read the cached instrument master.")
    parser.add_argument("--output", type=Path, help="Optional path for the JSON report.")
    args = parser.parse_args(argv)

    report, resolved = offline_report(skip_master=args.skip_master and not args.live)
    if args.live:
        from portfoliopulse.angelone.client import AngelOneDataError, authenticate_from_environment

        report["mode"] = "live"
        matches = [item for item in resolved if item.symbol == args.symbol and not item.is_benchmark]
        if not matches:
            report["live"] = {"error": "configured_symbol_not_resolved_from_cached_master"}
        else:
            recorder = RequestRecorder()
            with recorder.install():
                try:
                    client = authenticate_from_environment()
                except AngelOneDataError:
                    client = None
                login_at = time.monotonic()
                login_http = [item.as_dict() for item in recorder.observations]
                if client is None:
                    report["live"] = {"login": "failed", "http": login_http}
                else:
                    report["live"] = {
                        "login": "succeeded", "login_http": login_http,
                        **live_checks(client, recorder, instrument=matches[0], login_monotonic=login_at,
                                      idle_seconds=args.idle_seconds, candle_attempts=args.attempts,
                                      spacing_seconds=args.spacing, window_days=args.window_days),
                    }
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
