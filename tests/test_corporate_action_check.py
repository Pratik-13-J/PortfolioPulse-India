"""Warn-only corporate-action jump check on hand-calculable close grids."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from portfoliopulse.data_quality import (
    DataQualityError, corporate_action_jump_warnings, unexplained_action_dates,
)

DAYS = pd.to_datetime(["2025-08-22", "2025-08-25", "2025-08-26", "2025-08-27", "2025-08-28"])


def grid() -> pd.DataFrame:
    # BONUS halves on 2025-08-26 (unadjusted 1:1 bonus): 200 -> 100 is -50%.
    # QUIET has a 1:1 bonus ex-date on the same day but no jump (adjusted).
    # GAP is missing on 2025-08-25; its move is measured across observed closes.
    return pd.DataFrame({
        "BONUS": [200.0, 202.0, 101.0, 100.0, 102.0],
        "QUIET": [100.0, 101.0, 100.0, 102.0, 103.0],
        "GAP": [100.0, np.nan, 140.0, 141.0, 140.0],
    }, index=DAYS)


ACTIONS = pd.DataFrame({
    "symbol": ["BONUS", "QUIET"], "ex_date": ["2025-08-26", "2025-08-26"], "action_type": ["bonus", "bonus"],
})


class JumpCheckTests(unittest.TestCase):
    def test_flags_large_jumps_and_matches_nearby_ex_dates(self) -> None:
        source = grid()
        before = source.copy()
        flags = corporate_action_jump_warnings(source, corporate_actions=ACTIONS)
        pd.testing.assert_frame_equal(source, before)  # data unchanged
        self.assertEqual(flags["symbol"].tolist(), ["BONUS", "GAP"])
        bonus = flags.iloc[0]
        self.assertAlmostEqual(bonus["close_return"], 101.0 / 202.0 - 1.0)
        self.assertTrue(bonus["near_action"])
        self.assertEqual((bonus["action_ex_date"], bonus["action_type"]), ("2025-08-26", "bonus"))
        gap = flags.iloc[1]
        self.assertAlmostEqual(gap["close_return"], 0.40)
        self.assertEqual(gap["previous_date"], "2025-08-22")
        self.assertFalse(gap["near_action"])

    def test_threshold_is_strict_and_configurable(self) -> None:
        self.assertEqual(corporate_action_jump_warnings(grid(), threshold=0.40)["symbol"].tolist(), ["BONUS"])
        self.assertEqual(len(corporate_action_jump_warnings(grid(), threshold=0.60)), 0)

    def test_quiet_split_or_bonus_dates_are_reported_as_evidence_of_adjustment(self) -> None:
        quiet = unexplained_action_dates(grid(), ACTIONS)
        self.assertEqual(quiet.to_dict(orient="records"),
                         [{"symbol": "QUIET", "ex_date": "2025-08-26", "action_type": "bonus"}])

    def test_window_excludes_far_actions(self) -> None:
        far = ACTIONS.assign(ex_date=["2025-08-15", "2025-08-15"])
        flags = corporate_action_jump_warnings(grid(), corporate_actions=far, window_days=3)
        self.assertFalse(flags["near_action"].any())

    def test_invalid_inputs_are_rejected(self) -> None:
        with self.assertRaises(DataQualityError):
            corporate_action_jump_warnings(pd.DataFrame())
        with self.assertRaises(DataQualityError):
            corporate_action_jump_warnings(grid(), threshold=0)
        with self.assertRaises(DataQualityError):
            corporate_action_jump_warnings(grid(), corporate_actions=ACTIONS.drop(columns="ex_date"))


if __name__ == "__main__":
    unittest.main()
