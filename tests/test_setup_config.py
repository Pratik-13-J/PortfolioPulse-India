"""Checks that the starter Angel One configuration stays usable and safe."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SetupConfigTests(unittest.TestCase):
    def test_sample_instruments_are_indian_and_token_free(self) -> None:
        config = json.loads((ROOT / "config" / "instruments.json").read_text(encoding="utf-8"))
        self.assertEqual(config["base_currency"], "INR")
        self.assertEqual(config["interval"], "ONE_DAY")
        self.assertEqual(config["benchmark"]["symbol"], "NIFTY 50")
        self.assertEqual(config["benchmark"]["exchange"], "NSE")
        holdings = config["holdings"]
        self.assertEqual({item["symbol"] for item in holdings},
                         {"RELIANCE", "HDFCBANK", "INFY", "ICICIBANK", "ITC"})
        self.assertTrue(all(item["exchange"] == "NSE" for item in holdings))
        self.assertTrue(all(item["quantity"] > 0 for item in holdings))
        self.assertAlmostEqual(sum(item["target_weight"] for item in holdings), 1.0)
        self.assertTrue(all("token" not in item for item in [config["benchmark"], *holdings]))

    def test_example_credentials_are_blank_and_local_file_ignored(self) -> None:
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        entries = dict(line.split("=", 1) for line in example.splitlines()
                       if line and not line.startswith("#"))
        self.assertEqual(set(entries), {
            "ANGELONE_API_KEY", "ANGELONE_CLIENT_ID", "ANGELONE_PASSWORD", "ANGELONE_TOTP_SECRET"
        })
        self.assertTrue(all(not value for value in entries.values()))
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", gitignore)
        self.assertIn("!.env.example", gitignore)
        self.assertIn("data/raw/", gitignore)


if __name__ == "__main__":
    unittest.main()
