"""The engine on the real saved rules and the 500 sample addresses (no API, no network)."""
import unittest

import engine as G
from tests import fixtures as fx

RESULTS = {"applies", "superseded", "not_yet_effective", "pending", "unknown"}


class RealAnswers(unittest.TestCase):
    def test_every_address_is_answered_with_a_known_result(self):
        for aid in fx.addresses():
            out = fx.lookup(aid)
            for e in out["entries"]:
                self.assertIn(e["result"], RESULTS, aid)
                self.assertTrue(e["explanation"].strip(), f"{aid} {e['team_rule_id']} has no explanation")

    def test_state_cap_is_superseded_by_the_stricter_city_ordinance(self):
        # A0001 is in Los Angeles: California's rent cap (r-0001) gives way to the city's stricter rule (r-0021).
        res = fx.results_by_rule(fx.lookup("A0001"))
        self.assertEqual(res["r-0001"], "superseded")
        self.assertEqual(res["r-0021"], "applies")

    def test_missing_year_built_gives_unknown_and_what_if_settles_it(self):
        # A0346 has no year built, so the age test cannot be decided. Typing a year must settle it.
        self.assertFalse(fx.addresses()["A0346"]["year_built"])
        before = fx.results_by_rule(fx.lookup("A0346"))
        after = fx.results_by_rule(fx.lookup("A0346", what_if={"year_built": 1975}))
        self.assertEqual(before["r-0001"], "unknown")
        self.assertNotEqual(after["r-0001"], "unknown")

    def test_typing_a_missing_year_never_creates_unknowns(self):
        # Where the parcel has no year built, adding one is new information: unknown answers can only go down.
        missing = [aid for aid, a in fx.addresses().items() if not a["year_built"]]
        self.assertTrue(missing)
        for aid in missing:
            plain = sum(e["result"] == "unknown" for e in fx.lookup(aid)["entries"])
            typed = sum(e["result"] == "unknown" for e in fx.lookup(aid, what_if={"year_built": 1975})["entries"])
            self.assertLessEqual(typed, plain, aid)

    def test_a_rule_is_not_in_force_before_its_effective_date(self):
        dated = [r for r in fx.rules() if r["status"] == "not_yet_effective" or
                 (r["legal_state"] == "enacted" and (r["effective_date"] or "") > "2026-10-01")]
        if not dated:
            self.skipTest("no rule in the saved data starts after the query date")
        rid = dated[0]["team_rule_id"]
        for aid in fx.addresses():
            for e in fx.lookup(aid)["entries"]:
                if e["team_rule_id"] == rid:
                    self.assertIn(e["result"], {"not_yet_effective", "unknown", "superseded"}, aid)
                    return

    def test_failed_rules_never_show_up(self):
        failed = {r["team_rule_id"] for r in fx.rules() if r["legal_state"] == "failed"}
        for aid in list(fx.addresses())[:100]:
            self.assertFalse(failed & set(fx.results_by_rule(fx.lookup(aid))), aid)

    def test_python_default_date_matches_project_date(self):
        self.assertEqual(G.QUERY_DATE, "2026-10-01")


if __name__ == "__main__":
    unittest.main()
