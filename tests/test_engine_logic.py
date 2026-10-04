"""The three-valued logic at the heart of the engine: a fact is true, false or unknown (None). Unknown is never guessed."""
import unittest
from datetime import date

import engine as G


class TruthTables(unittest.TestCase):
    def test_and(self):
        self.assertIs(G.t_and([True, True]), True)
        self.assertIs(G.t_and([True, False, None]), False)    # one False settles it
        self.assertIsNone(G.t_and([True, None]))
        self.assertIs(G.t_and([]), True)

    def test_or(self):
        self.assertIs(G.t_or([False, False]), False)
        self.assertIs(G.t_or([False, True, None]), True)      # one True settles it
        self.assertIsNone(G.t_or([False, None]))
        self.assertIs(G.t_or([]), False)

    def test_not_keeps_unknown_unknown(self):
        self.assertIs(G.t_not(True), False)
        self.assertIs(G.t_not(False), True)
        self.assertIsNone(G.t_not(None))


class YearBuiltVersusCutoff(unittest.TestCase):
    """Only the year is known, so the building may date from any day of it."""

    def test_whole_year_on_one_side_is_decided(self):
        cutoff = date(1979, 10, 1)
        self.assertIs(G.cmp_date_year("before", 1975, cutoff), True)
        self.assertIs(G.cmp_date_year("before", 1985, cutoff), False)
        self.assertIs(G.cmp_date_year("on_or_after", 1985, cutoff), True)

    def test_year_that_straddles_the_cutoff_is_unknown(self):
        self.assertIsNone(G.cmp_date_year("before", 1979, date(1979, 10, 1)))
        self.assertIsNone(G.cmp_date_year("after", 1979, date(1979, 10, 1)))

    def test_boundaries(self):
        self.assertIs(G.cmp_date_year("on_or_before", 1979, date(1979, 12, 31)), True)
        self.assertIs(G.cmp_date_year("before", 1979, date(1979, 12, 31)), None)
        self.assertIs(G.cmp_date_year("after", 1979, date(1979, 1, 1)), None)


class UnitRangeVersusThreshold(unittest.TestCase):
    def test_exact_count(self):
        self.assertIs(G.cmp_units(">=", 4, 4, 3), True)
        self.assertIs(G.cmp_units(">=", 2, 2, 3), False)
        self.assertIs(G.cmp_units("==", 2, 2, 2), True)

    def test_open_ended_range(self):
        # "5 or more units": passes >= 5, but cannot answer <= 10
        self.assertIs(G.cmp_units(">=", 5, None, 5), True)
        self.assertIsNone(G.cmp_units("<=", 5, None, 10))
        self.assertIs(G.cmp_units("<=", 12, None, 10), False)

    def test_missing_lower_bound_counts_as_one(self):
        self.assertIs(G.cmp_units(">=", None, None, 1), True)

    def test_range_spanning_the_threshold_is_unknown(self):
        self.assertIsNone(G.cmp_units(">=", 2, 6, 4))


class MissingYearBuilt(unittest.TestCase):
    COND = {"fact": "first_occupancy_date", "op": "before", "value": "1979-10-01", "years_before_as_of": 0}

    def test_missing_year_is_unknown_not_old_and_not_new(self):
        facts = {"year_built": None, "units_lo": 1, "units_hi": 1}
        value, kind, phrase = G.eval_cond(self.COND, facts, "2026-10-01", False, None)
        self.assertIsNone(value)
        self.assertEqual(kind, "fact")
        self.assertIn("missing", phrase)

    def test_rolling_age_test_moves_with_the_as_of_date(self):
        # "first occupied within the last 15 years": a 2012 building is covered in 2026 but not in 2028
        cond = {"fact": "first_occupancy_date", "op": "after", "value": "", "years_before_as_of": 15}
        facts = {"year_built": 2012, "units_lo": 1, "units_hi": 1}
        self.assertIs(G.eval_cond(cond, facts, "2026-10-01", False, None)[0], True)
        self.assertIs(G.eval_cond(cond, facts, "2028-10-01", False, None)[0], False)


class WhatIf(unittest.TestCase):
    BASE = {"year_built": None, "units_lo": 2, "units_hi": None, "units_basis": "parcel", "owner_occupied": None}

    def test_typed_facts_override_and_are_marked(self):
        f = G.apply_what_if(self.BASE, {"year_built": 1975, "units": 3, "owner_occupied": True})
        self.assertEqual(f["year_built"], 1975)
        self.assertEqual((f["units_lo"], f["units_hi"]), (3, 3))
        self.assertEqual(f["units_basis"], "entered by you")
        self.assertIs(f["owner_occupied"], True)

    def test_parcel_facts_are_never_changed(self):
        before = dict(self.BASE)
        G.apply_what_if(self.BASE, {"year_built": 1975})
        self.assertEqual(self.BASE, before)

    def test_typos_and_junk_are_ignored(self):
        f = G.apply_what_if(self.BASE, {"year_built": 19750, "units": 0, "owner_occupied": "maybe"})
        self.assertEqual(f, self.BASE)

    def test_nothing_typed_changes_nothing(self):
        self.assertEqual(G.apply_what_if(self.BASE, None), self.BASE)
        self.assertEqual(G.apply_what_if(self.BASE, {}), self.BASE)


if __name__ == "__main__":
    unittest.main()
