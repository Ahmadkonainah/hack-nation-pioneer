#!/usr/bin/env python3
"""
Step A4 - write the plain-language Spanish view of every rule.

Millions of US renters and small landlords read Spanish first. The English summaries written by enrich.py
(plain_en) are translated into plain Spanish, together with each rule's title and key value. One model call
translates all rules at once; the answer is saved in cache/translate/, so a re-run is free.

What is translated, and what is not
  * Translated:  title, key value, the one-sentence plain-language summary.
  * Not translated: the law itself. Citations, section numbers and the quoted text stay in English, because
    the English text is the legal text. The app says so on every Spanish screen.

How the translation is kept honest (the model is not trusted to be careful, so code checks it)
  * Every rule id must come back exactly once.
  * Every number must survive unchanged: the digits in the Spanish text must be the same digits, the same
    number of times, as in the English. A model that turns "$30" into "$35" is caught here.
  * A summary that comes back empty, or word for word English, is rejected.
  A rule that fails the checks gets one more, narrower attempt. If it fails again it is left out and the app
  shows that rule in English. The Spanish text is labelled in the app as AI-translated and not reviewed.

Run from the repo root:   python src/translate.py        (one API call, about $0.10, cached)
Reads  outputs/rules_enriched.json     Writes outputs/rules_es.json, outputs/translation_log.json
build_web.py adds the translations to the app; without rules_es.json the app simply has no Spanish button.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DEFAULT_MODEL, OUT, ROOT, read_json, use_utf8_console, utc_now_iso, write_json  # noqa: E402
from llm import THINKING, cached_structured_call, strictify  # noqa: E402

CACHE_DIR = ROOT / "cache" / "translate"
FIELDS = ("title", "key_value", "plain_en")        # what is translated, in the order the app shows it

# Do not edit SYSTEM, not even whitespace. It is part of the cache key, so any change throws away the saved
# answer and costs a new call.
SYSTEM = """You translate short summaries of US rental-housing laws from English into plain Spanish for tenants and small landlords in the United States.

You get one JSON record per rule with title, key_value and plain_en. Return for each: title_es, key_value_es, plain_es.

Rules
- Use ONLY what the English says. Add nothing, remove nothing, explain nothing extra. Do not use outside legal knowledge.
- Keep every number exactly as written: dollar amounts, percentages, days, months, years, section numbers, dates. Do not convert, round or reformat them ("$1,000" stays "$1,000", "5%" stays "5%").
- Keep names of laws, cities, states, agencies and statutes in their usual form: "California", "Los Angeles", "Tenant Protection Act", "Rent Board". Put a short Spanish explanation after an English law name only if the English does so.
- Plain language, about an 8th-grade reading level, short sentences, standard Spanish understood across the US. Use "usted" forms or neutral forms; no slang, no regional words.
- Use these terms: rent cap = "límite de aumento de renta"; rent increase = "aumento de renta"; just cause = "causa justificada"; no-fault eviction = "desalojo sin culpa del inquilino"; eviction = "desalojo"; security deposit = "depósito de seguridad"; landlord = "propietario" or "arrendador"; tenant = "inquilino"; screening fee = "cuota de evaluación del solicitante"; applicant = "solicitante"; rent stabilization = "estabilización de rentas"; relocation payment = "pago de reubicación"; tenancy = "arrendamiento".
- A bill that is not law says so ("Un proyecto de ley propuesto que ..."); a measure that failed says so ("Una medida que fue anulada y nunca se convirtió en ley").
- plain_es must be one or two sentences, never longer than the English by more than a third.
Return one JSON object that follows the schema, and nothing else. Include every rule id you were given exactly once."""

SCHEMA = strictify({
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "team_rule_id": {"type": "string"},
                    "title_es": {"type": "string"},
                    "key_value_es": {"type": "string"},
                    "plain_es": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
})

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str | None) -> Counter:
    """Every number in the text, counted. Thousands separators are ignored so "1,000" equals "1000"."""
    return Counter(n.replace(",", "") for n in _NUMBER.findall(text or ""))


def source_records(rules: list[dict]) -> list[dict]:
    """The only fields the model sees: id plus the three texts to translate."""
    return [{"team_rule_id": r["team_rule_id"], "title": r.get("title") or "", "key_value": r.get("key_value") or "",
             "plain_en": r.get("plain_en") or ""} for r in rules]


def check_one(src: dict, tr: dict) -> list[str]:
    """Problems with one translated record (empty list = fine)."""
    problems = []
    for en_key, es_key in (("title", "title_es"), ("key_value", "key_value_es"), ("plain_en", "plain_es")):
        en, es = (src.get(en_key) or "").strip(), (tr.get(es_key) or "").strip()
        if not en:
            continue                                   # nothing to translate (a rule without a key value)
        if not es:
            problems.append(f"{es_key} is empty")
            continue
        if numbers_in(en) != numbers_in(es):
            problems.append(f"{es_key}: numbers differ (English {sorted(numbers_in(en).elements())}, Spanish {sorted(numbers_in(es).elements())})")
        # An untouched copy is a failed translation, except for very short labels that are the same in both languages.
        if es.lower() == en.lower() and len(en.split()) > 3:
            problems.append(f"{es_key} was left in English")
    return problems


def validate(sources: list[dict], answer: dict) -> tuple[dict, dict]:
    """Split the model's answer into accepted translations and rejected ones.

    Returns ({rule_id: {title_es, key_value_es, plain_es}}, {rule_id: [problems]}). A rule id that came back twice,
    that was never asked for, or that is missing counts as rejected (missing ones are reported under their own id).
    """
    by_id = {s["team_rule_id"]: s for s in sources}
    seen = Counter(t.get("team_rule_id") for t in answer.get("translations", []))
    ok, bad = {}, {}
    for t in answer.get("translations", []):
        rid = t.get("team_rule_id")
        if rid not in by_id:
            bad[f"(unknown id {rid})"] = ["id was not in the list"]
            continue
        if seen[rid] > 1:
            bad[rid] = ["returned more than once"]
            continue
        probs = check_one(by_id[rid], t)
        if probs:
            bad[rid] = probs
        else:
            ok[rid] = {k: (t.get(k) or "").strip() for k in ("title_es", "key_value_es", "plain_es")}
    for rid in by_id:
        if rid not in ok and rid not in bad:
            bad[rid] = ["missing from the answer"]
    return ok, bad


def ask(model: str, records: list[dict], refresh: bool, label: str, extra: str = "") -> dict:
    """One translation call over `records`, saved under cache/translate/. `extra` is appended to the user message
    (used by the retry to say what went wrong); it is part of the cache key, so a retry is saved too."""
    user = ("Rules (one JSON object per line):\n" + "\n".join(json.dumps(r, ensure_ascii=False) for r in records) +
            f"\n\nThere are {len(records)} rules. Return every id exactly once.{extra}")
    return cached_structured_call(cache_dir=CACHE_DIR, system=SYSTEM, schema=SCHEMA, user=user, model=model,
                                  label=label, list_key="translations", refresh=refresh)["result"]


def translate_rules(rules: list[dict], model: str, refresh: bool = False) -> tuple[dict, dict, str]:
    """Translate all rules, retry the rejected ones once, and return (accepted, still_rejected, model_notes)."""
    sources = source_records(rules)
    answer = ask(model, sources, refresh, f"translate {len(sources)} rules into plain Spanish")
    ok, bad = validate(sources, answer)
    if bad:
        print(f"{len(bad)} rule(s) failed the checks; one more attempt for those.")
        again = [s for s in sources if s["team_rule_id"] in bad]
        why = "\n".join(f"- {rid}: {'; '.join(p)}" for rid, p in bad.items())
        extra = f"\n\nYour earlier translation of these rules failed these checks. Fix exactly this, keep every number unchanged:\n{why}"
        ok2, bad2 = validate(again, ask(model, again, refresh, f"retranslate {len(again)} rule(s)", extra))
        ok.update(ok2)
        bad = bad2
    return ok, bad, answer.get("notes", "")


def main(argv=None) -> int:
    """CLI: translate, check, write outputs/rules_es.json and outputs/translation_log.json. Returns 0, or 1 if over a quarter failed."""
    use_utf8_console()
    ap = argparse.ArgumentParser(description="Plain-language Spanish view of every rule")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--thinking", choices=["off", "adaptive"], default="off")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    THINKING["mode"] = args.thinking

    src = OUT / "rules_enriched.json"
    if not src.exists():
        sys.exit("outputs/rules_enriched.json not found. Run  python src\\enrich.py  first.")
    rules = read_json(src)["rules"]
    missing_en = [r["team_rule_id"] for r in rules if not (r.get("plain_en") or "").strip()]
    if missing_en:
        sys.exit(f"{len(missing_en)} rule(s) have no English summary yet ({missing_en[:3]} ...). Run  python src\\enrich.py  first.")

    ok, bad, notes = translate_rules(rules, args.model, args.refresh)
    write_json(OUT / "rules_es.json", {
        "model": args.model, "generated_at": utc_now_iso(),
        "label": "Traducción automática (IA). No revisada por un abogado. El texto legal oficial está en inglés.",
        "translations": ok,
    })
    write_json(OUT / "translation_log.json", {"model_notes": notes, "translated": len(ok), "rejected": bad})
    print(f"\nTranslated {len(ok)} of {len(rules)} rules; {len(bad)} left in English.")
    for rid, p in bad.items():
        print(f"  {rid}: {'; '.join(p)}")
    print("Wrote outputs/rules_es.json. Next: python src\\build_web.py  (or  python src\\run_all.py --from build_web)")
    return 1 if len(bad) > len(rules) / 4 else 0


if __name__ == "__main__":
    sys.exit(main())
