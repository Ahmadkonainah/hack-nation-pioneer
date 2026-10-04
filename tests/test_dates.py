import unittest
from datetime import date

from dates import minus_years, parse_date, status_at


class ParseDate(unittest.TestCase):
    def test_full_month_and_year(self):
        self.assertEqual(parse_date("2026-10-01"), date(2026, 10, 1))
        self.assertEqual(parse_date("2026-10"), date(2026, 10, 1))   # first day of the period
        self.assertEqual(parse_date("2026"), date(2026, 1, 1))

    def test_rejects_what_is_not_a_date(self):
        for bad in ("", None, "tomorrow", "2026-13-01", "2026-02-31", "10/01/2026", "2026-1-1"):
            self.assertIsNone(parse_date(bad), bad)


class MinusYears(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(minus_years(date(2026, 10, 1), 15), date(2011, 10, 1))

    def test_leap_day_falls_back_to_28_february(self):
        self.assertEqual(minus_years(date(2028, 2, 29), 1), date(2027, 2, 28))


class StatusAt(unittest.TestCase):
    def test_failed_and_pending_never_depend_on_the_date(self):
        for eff in ("2020-01-01", "2030-01-01", ""):
            self.assertEqual(status_at("failed", eff, "2026-10-01"), "failed")
            self.assertEqual(status_at("pending", eff, "2026-10-01"), "pending")

    def test_enacted_switches_on_its_effective_date(self):
        self.assertEqual(status_at("enacted", "2027-01-01", "2026-12-31"), "not_yet_effective")
        self.assertEqual(status_at("enacted", "2027-01-01", "2027-01-01"), "in_force")

    def test_enacted_without_date_counts_as_in_force(self):
        self.assertEqual(status_at("enacted", "", "2026-10-01"), "in_force")
        self.assertEqual(status_at("enacted", None, "2026-10-01"), "in_force")


if __name__ == "__main__":
    unittest.main()
