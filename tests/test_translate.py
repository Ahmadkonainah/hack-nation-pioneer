"""The Spanish step: the model is replaced by a fake; what is tested is the code that checks the model."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import llm
import translate as T
from tests.test_llm_cache import fake_response

RULES = [
    {"team_rule_id": "r-1", "title": "California rent cap", "key_value": "5% + CPI, max 10%",
     "plain_en": "Rent increases are capped at 5% plus local inflation, up to 10%, for housing older than 15 years."},
    {"team_rule_id": "r-2", "title": "Application fee limit", "key_value": "$30 per applicant",
     "plain_en": "Landlords can charge only actual screening costs, never over $30, and must give a receipt."},
]


def answer(**overrides):
    base = {
        "r-1": {"team_rule_id": "r-1", "title_es": "Límite de aumento de renta en California", "key_value_es": "5% + IPC, máximo 10%",
                "plain_es": "Los aumentos de renta tienen un límite de 5% más la inflación local, hasta 10%, en viviendas de más de 15 años."},
        "r-2": {"team_rule_id": "r-2", "title_es": "Límite de la cuota de solicitud", "key_value_es": "$30 por solicitante",
                "plain_es": "Los propietarios solo pueden cobrar los costos reales de evaluación, nunca más de $30, y deben dar un recibo."},
    }
    for rid, fields in overrides.items():
        base[rid] = {**base[rid], **fields}
    return json.dumps({"translations": list(base.values()), "notes": ""})


class Numbers(unittest.TestCase):
    def test_counts_every_number_and_ignores_thousands_commas(self):
        self.assertEqual(T.numbers_in("$1,000 and 5% in 12 months"), T.numbers_in("$1000, 5%, 12"))
        self.assertNotEqual(T.numbers_in("$30"), T.numbers_in("$35"))
        self.assertNotEqual(T.numbers_in("12 months, 12 days"), T.numbers_in("12 months"))


class Validate(unittest.TestCase):
    def test_good_answer_is_accepted(self):
        ok, bad = T.validate(T.source_records(RULES), json.loads(answer()))
        self.assertEqual(sorted(ok), ["r-1", "r-2"])
        self.assertEqual(bad, {})

    def test_a_changed_amount_is_rejected(self):
        ok, bad = T.validate(T.source_records(RULES), json.loads(answer(**{"r-2": {"plain_es": "Nunca más de $35."}})))
        self.assertIn("r-1", ok)
        self.assertIn("r-2", bad)
        self.assertIn("numbers differ", bad["r-2"][0])

    def test_untranslated_empty_missing_and_duplicate_are_rejected(self):
        src = T.source_records(RULES)
        left_english = json.loads(answer(**{"r-1": {"plain_es": RULES[0]["plain_en"], "title_es": RULES[0]["title"]}}))
        self.assertIn("r-1", T.validate(src, left_english)[1])
        empty = json.loads(answer(**{"r-2": {"plain_es": ""}}))
        self.assertIn("r-2", T.validate(src, empty)[1])
        missing = {"translations": [json.loads(answer())["translations"][0]]}
        self.assertEqual(T.validate(src, missing)[1], {"r-2": ["missing from the answer"]})
        dup = json.loads(answer())
        dup["translations"].append(dup["translations"][0])
        self.assertIn("r-1", T.validate(src, dup)[1])

    def test_an_unknown_id_is_reported_not_trusted(self):
        a = json.loads(answer())
        a["translations"].append({"team_rule_id": "r-99", "title_es": "x", "key_value_es": "y", "plain_es": "z"})
        self.assertIn("(unknown id r-99)", T.validate(T.source_records(RULES), a)[1])


class Retry(unittest.TestCase):
    def run_with(self, responses):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(T, "CACHE_DIR", Path(d)), \
             mock.patch.object(llm, "get_client", return_value=object()), \
             mock.patch.object(llm, "call_structured", side_effect=[fake_response(r) for r in responses]) as cs:
            return T.translate_rules(RULES, "claude-sonnet-5-5"), cs.call_count

    def test_one_clean_call_when_everything_is_right(self):
        (ok, bad, _), calls = self.run_with([answer()])
        self.assertEqual((len(ok), bad, calls), (2, {}, 1))

    def test_a_rejected_rule_gets_one_more_attempt_that_can_fix_it(self):
        broken = answer(**{"r-2": {"plain_es": "Nunca más de $35."}})
        retry = json.dumps({"translations": [json.loads(answer())["translations"][1]], "notes": ""})
        (ok, bad, _), calls = self.run_with([broken, retry])
        self.assertEqual((sorted(ok), bad, calls), (["r-1", "r-2"], {}, 2))

    def test_a_rule_that_fails_twice_is_left_out_not_shipped_wrong(self):
        broken = answer(**{"r-2": {"plain_es": "Nunca más de $35."}})
        (ok, bad, _), calls = self.run_with([broken, broken])
        self.assertEqual(sorted(ok), ["r-1"])
        self.assertIn("r-2", bad)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
