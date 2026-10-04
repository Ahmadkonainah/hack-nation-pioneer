#!/usr/bin/env python3
"""
Step A3 - turn each rule's coverage text into conditions the address lookup can test.

Module A wrote coverage as free text plus a rough condition list. Some rules have no usable test
(San Francisco's rent-increase rule says "rent-controlled units" and another record gives the cut-off date),
and many conditions are filed under "other". One short model call reads all consolidated rules together
(so it can use what a sibling record in the same city says) and writes clean, testable conditions:

    first_occupancy_date  construction / certificate-of-occupancy date test
    units                 number of dwelling units
    owner_occupied        owner lives in the building
    use_type              kind of building (hotel, dormitory, care facility ...)
    status_not_in_data    special status the parcel data cannot show (deed-restricted, subsidised ...)
    tenancy               test on one tenancy, not the building (tenant lived 12 months ...)
    rule_coverage         "covered by / not subject to" another rule of ours (value = that rule's id)

Rules whose answer is invalid are kept as they were (the lookup falls back to the Module A conditions).
The same call also writes plain_en, a one-sentence summary for tenants. In-force rules that come back
"undefined" or untestable get a second, narrower look (see main).

Run from the repo root:   python src/enrich.py        (one API call, about $0.20, cached)
Reads  outputs/rules_consolidated.json   Writes outputs/rules_enriched.json, outputs/rules.json,
outputs/enrichment_log.json
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DEFAULT_MODEL, OUT, ROOT, use_utf8_console  # noqa: E402
from dates import QUERY_DATE  # noqa: E402
from llm import THINKING, cached_structured_call, strictify  # noqa: E402
import consolidate as C  # noqa: E402

CACHE_DIR = ROOT / "cache" / "enrich"
# FACTS, OPS and SCOPES double as the enums in the JSON schema below, so the model cannot return a fact, operator
# or scope the engine does not know. Which operator goes with which fact is checked in clean_cond.
FACTS = ["first_occupancy_date", "units", "owner_occupied", "use_type", "status_not_in_data", "tenancy", "rule_coverage"]
OPS = ["on_or_before", "before", "after", "on_or_after", "<=", "<", ">=", ">", "==", "!=", "is", "is_not"]
SCOPES = ["universal", "conditional", "undefined"]

# Do not edit SYSTEM, not even whitespace. It is part of the cache key, so any change throws away the saved
# answer and costs a new call. The second-look prompt in main() is added to the user message, not to SYSTEM.
SYSTEM = """You convert the coverage text of rental-housing rules into conditions a computer can test against one building. You get one JSON record per rule (many cities and states). Use ONLY what the records say. Never use outside legal knowledge. Do not guess.

For each rule return:
scope
 - "universal": the record says the rule covers rentals in its jurisdiction with no building-based test (no construction date, no unit count, no owner test). Exemptions may still exist.
 - "conditional": the rule covers only buildings that pass tests the records state (a construction-date cut-off, a unit count, and so on).
 - "undefined": the records do not say which buildings are covered (for example "the page does not define which units qualify") and no sibling record supplies it.
conditions: tests that must ALL be true for the rule to cover a building (empty for universal).
exemptions: each exemption is one label plus conditions that must ALL be true for the exemption to remove coverage. If any one exemption is fully true, the rule does not cover the building.

Condition fields: fact, op, value, years_before_as_of, text (a short quote or paraphrase from the record).
Facts (use exactly these):
 - first_occupancy_date: when the building was first built, first occupied or got its certificate of occupancy. op is on_or_before | before | after | on_or_after. For a fixed cut-off put an ISO date in value (YYYY-MM-DD) and years_before_as_of = 0. For a rolling test such as "certificate of occupancy within the previous 15 years" leave value "" and set years_before_as_of = 15. "Built after X" = after. "Built on or before X" = on_or_before. "Before 1980" = before 1980-01-01.
 - units: the number of dwelling units in the building or on the parcel. op is <= | < | >= | > | == | !=. value is an integer written as text.
 - owner_occupied: an owner lives in the building. op is "is". value is "true".
 - use_type: the kind of property (hotel, motel, dormitory, hospital, care facility, mobile home, condominium unit, single-family home). op is "is". value is a short label.
 - status_not_in_data: a special status that tax-assessor parcel data cannot show: deed-restricted or subsidised affordable housing, Section 8 or public housing, government-owned, "owner filed an exemption notice", "tenant is a lifetime offender", "unit demolished for new construction". op is "is". value is a short label.
 - tenancy: a test on one tenancy rather than on the building (tenant lived there 12 months, lease longer than 30 days, notice periods). op is "is". value is a short label.
 - rule_coverage: the rule covers (op "is") or does not cover (op "is_not") buildings that another rule in this list covers. value = that other rule's id (for example "r-0021"). Use it for text like "units not subject to the Rent Stabilization Ordinance" or "units covered by the Rent Ordinance".
years_before_as_of is 0 unless the test is rolling.

Do NOT turn deference into an exemption. If a record says a rule gives way to, or does not apply where, another law or local ordinance governs ("housing under a stricter local rent control", "unless a local just-cause ordinance applies"), leave that out of the conditions and exemptions: the system handles yielding between rules separately.
Use sibling records. If a rule says "rent-controlled units" or "units covered by the rent ordinance" and another record of the same jurisdiction says that units first receiving a certificate of occupancy after a date are exempt from that rent ordinance, then the ordinance covers units first occupied on or before that date: apply first_occupancy_date on_or_before that date to the rule, and name the record in support. Otherwise do not copy conditions from one rule to another.
"undefined" is the last resort. Use it only when the records say nothing about who or what is covered. If a record says who it covers in words the parcel data cannot test (affordable housing only, tenants with a criminal record, units under a given status), use "conditional" with a status_not_in_data or tenancy condition. If a record says the rule covers the units of an ordinance and also the units that are exempt from that ordinance's rent limits, the rule is "universal".
An exemption from some OTHER law is not an exemption from this rule (for example a state rule that says new buildings are exempt from LOCAL rent control exempts nothing from the state rule itself): leave it out.
rule_coverage must use an id that appears in the list you were given. If the text points to a law that is not in the list, leave that test out.
A jurisdiction-wide ban or duty (a state or city law with no building test) is "universal". Pending bills and failed measures: scope "undefined", no conditions.
A rule that records what the state does NOT do (for example "the state has no rent control and municipalities may adopt it") covers every rental in that state: "universal", no exemptions, even if the record also mentions buildings that local ordinances exempt (that is an exemption from the local law, not from this rule).
If the existing "coverage" field is already right, keep its conditions but put each one under the fact names above (replace "other" by use_type, status_not_in_data, tenancy or rule_coverage).
support = the record ids and text you relied on (for example "own text" or "r-0027 coverage text"). reasoning = one sentence.
plain_en = one sentence of at most 28 words in plain English for a tenant or a small landlord (about an 8th-grade reading level). Say what the rule requires, limits or bans, and who has to follow it. No section numbers, no Latin, no "pursuant to". Use only facts in the record (the requirement and key_value fields). For a bill that is not law say so ("A proposed bill that would ban ..."); for a measure that failed say so ("A ballot question that was struck down and never became law").
Return one JSON object that follows the schema, and nothing else. Include every rule id you were given exactly once."""

# One test on a building. Every value is text (a unit count or a date is written as a string) so the schema
# needs only one type; clean_cond checks that the text really is a whole number or an ISO date.
_COND = {
    "type": "object",
    "properties": {
        "fact": {"type": "string", "enum": FACTS},
        "op": {"type": "string", "enum": OPS},
        "value": {"type": "string"},
        "years_before_as_of": {"type": "integer"},
        "text": {"type": "string"},
    },
}
SCHEMA = strictify({
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "scope": {"type": "string", "enum": SCOPES},
                    "conditions": {"type": "array", "items": _COND},
                    "exemptions": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"label": {"type": "string"}, "conditions": {"type": "array", "items": _COND}}},
                    },
                    "support": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "plain_en": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
})

# year, year-month or full date: the same shapes effective_date uses elsewhere in the pipeline
ISO = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def compact(rules: list[dict]) -> str:
    """One JSON object per line for the prompt, text fields clipped.
    Module A's own coverage is sent only when it holds a test or an exemption, so the model is not shown empty shells."""
    lines = []
    for r in rules:
        cov = r.get("coverage") or {}
        lines.append(json.dumps({
            "id": r["team_rule_id"], "jurisdiction": r["jurisdiction"], "category": r["category"], "title": C.clip(r["title"], 100),
            "legal_status": r["legal_state"], "effective_date": r["effective_date"],
            "requirement": C.clip(r.get("requirement"), 450), "key_value": C.clip(r.get("key_value"), 120),
            "coverage_text": C.clip(r.get("coverage_conditions"), 500), "exemptions_text": C.clip(r.get("exemptions"), 600),
            "interaction": C.clip(r.get("interaction"), 300),
            "coverage": cov if (cov.get("all_of") or cov.get("exemptions")) else None,
        }, ensure_ascii=False))
    return "\n".join(lines)


def clean_cond(c: dict, ids: set[str], problems: list[str], where: str):
    """Check one condition the model wrote. Returns a clean dict, or None if it cannot be used.
    `ids` = every valid rule id (for rule_coverage); `problems` gets one plain-English note per rejection;
    `where` = the rule id shown in those notes. A bad test is rejected here so it never reaches the engine."""
    fact, op, val = c.get("fact"), c.get("op"), (c.get("value") or "").strip()
    yrs = c.get("years_before_as_of") or 0
    if fact not in FACTS or op not in OPS:
        problems.append(f"{where}: bad fact/op {fact!r}/{op!r}")
        return None
    # The schema allows any operator with any fact (for example "<=" on a date), so the valid pairs are checked here.
    if fact == "first_occupancy_date":
        if op not in ("on_or_before", "before", "after", "on_or_after"):
            problems.append(f"{where}: bad op {op!r} for a date")
            return None
        if yrs <= 0 and not ISO.match(val):
            problems.append(f"{where}: date {val!r} is not ISO and no rolling years given")
            return None
        if yrs > 0:
            val = ""                       # a rolling test is stored by years alone, so a stray fixed date cannot contradict it
    elif fact == "units":
        if op not in ("<=", "<", ">=", ">", "==", "!=") or not re.fullmatch(r"\d+", val):
            problems.append(f"{where}: bad units test {op!r} {val!r}")
            return None
    elif fact == "rule_coverage":
        if val not in ids or op not in ("is", "is_not"):
            problems.append(f"{where}: rule_coverage points at unknown rule {val!r}")
            return None
    else:
        # owner_occupied, use_type, status_not_in_data, tenancy: only "is" / "is_not" make sense, so a stray operator is fixed, not rejected
        if op not in ("is", "is_not"):
            op = "is"
    # years only matter for the date test; every other fact stores 0
    return {"fact": fact, "op": op, "value": val, "years_before_as_of": int(yrs) if fact == "first_occupancy_date" else 0,
            "text": (c.get("text") or "").strip()}


def validate(answer: dict, rules: list[dict]):
    """Check the model's answer. Returns (coverage_v2 by rule id, problems, plain_en by rule id).
    A rule with any bad piece is left out whole, so the lookup uses its Module A conditions instead of a half-converted test.
    plain_en is judged on its own (15 to 400 characters), so a bad coverage does not lose the summary."""
    ids = {r["team_rule_id"] for r in rules}
    out, problems, seen = {}, [], set()
    plains = {}
    for a in answer.get("rules", []):
        rid = a.get("id")
        if rid not in ids or rid in seen:
            problems.append(f"unknown or repeated rule id {rid!r}")
            continue
        seen.add(rid)
        # kept even if this rule's conditions are dropped later; the length bounds reject empty or runaway text
        pl = re.sub(r"\s+", " ", str(a.get("plain_en") or "")).strip()
        if 15 <= len(pl) <= 400:
            plains[rid] = pl
        bad = len(problems)
        def _keep(c):
            """a rule_coverage test that points at a rule we do not have is dropped on its own, with a note"""
            if c.get("fact") == "rule_coverage" and str(c.get("value") or "") not in ids:
                notes.append(f"{rid}: test on unknown rule {str(c.get('value'))[:60]!r} left out")
                return False
            return True
        # notes are logged but do not count as a defect, so dropping one dangling rule_coverage test does not discard the rule
        notes = []
        conds = [clean_cond(c, ids, problems, rid) for c in a.get("conditions", []) if _keep(c)]
        exs = []
        for ex in a.get("exemptions", []):
            ec = [clean_cond(c, ids, problems, rid) for c in ex.get("conditions", []) if _keep(c)]
            if ex.get("conditions") and not ec:
                # An exemption whose only test was unreadable is not usable. Kept with no tests it would read as
                # "exemption test not stated" in the engine and turn answers into unknown.
                continue
            exs.append({"label": (ex.get("label") or "").strip(), "conditions": ec})
        if a.get("scope") not in SCOPES:
            problems.append(f"{rid}: bad scope")
        if len(problems) > bad:          # any invalid piece: keep the whole rule as Module A wrote it
            problems.append(f"{rid}: enrichment dropped, Module A conditions kept")
            continue
        problems.extend(notes)
        # "universal" means no building test. If tests came with it, trust the tests: that is the cautious reading.
        if a["scope"] == "universal" and conds:
            problems.append(f"{rid}: universal scope with conditions; kept as conditional")
            a["scope"] = "conditional"
        # a rule "covered by itself" would depend on its own answer
        if rid in {c["value"] for c in conds if c["fact"] == "rule_coverage"}:
            problems.append(f"{rid}: points at itself; enrichment dropped")
            continue
        out[rid] = {"scope": a["scope"], "conditions": conds, "exemptions": exs,
                    "support": (a.get("support") or "").strip(), "reasoning": (a.get("reasoning") or "").strip()}
    return out, problems, plains


def ask(args, user: str, label: str) -> dict:
    """One enrichment call, saved under cache/enrich/ so the same question is never paid for twice.
    `args` supplies --model and --refresh; `label` is only the progress text."""
    return cached_structured_call(cache_dir=CACHE_DIR, system=SYSTEM, schema=SCHEMA, user=user, model=args.model,
                                  label=label, list_key="rules", refresh=args.refresh)


def main(argv=None) -> int:
    """CLI: one call for all rules, a second look at the hard in-force ones, then validate and write the enriched files. Returns 0."""
    use_utf8_console()
    ap = argparse.ArgumentParser(description="Convert coverage text into testable conditions")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--thinking", choices=["off", "adaptive"], default="off")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    THINKING["mode"] = args.thinking

    src = OUT / "rules_consolidated.json"
    if not src.exists():
        sys.exit("outputs/rules_consolidated.json not found. Run  python src\\consolidate.py  first.")
    data = json.loads(src.read_text(encoding="utf-8"))
    rules = data["rules"]
    user = ("Rules (one JSON object per line):\n" + compact(rules) +
            f"\n\nThere are {len(rules)} rules. Return every id exactly once.")
    payload = ask(args, user, f"convert the coverage of {len(rules)} rules")

    # Second look. A rule that is in force is read again, with the sibling records of its jurisdiction in front
    # of the model, when the first answer left it "undefined" or gave it only tests the parcel data cannot show
    # (a status or a tenancy): a sibling often states the cut-off date, or says the rule covers the whole ordinance.
    # Why a second call: the first sees every jurisdiction at once and can miss that link; this one is narrow.
    # Pending bills and failed measures are skipped on purpose: "undefined" is the right answer for them.
    first = {a.get("id"): a for a in payload["result"].get("rules", [])}

    def _untestable(a):
        """True when the answer is "conditional" but every test is a status or tenancy the parcel data cannot show,
        so the engine could never answer it."""
        cs = a.get("conditions") or []
        return a.get("scope") == "conditional" and bool(cs) and all(c.get("fact") in ("status_not_in_data", "tenancy") for c in cs)

    again = [r for r in rules if r.get("legal_state") == "enacted"
             and ((first.get(r["team_rule_id"]) or {}).get("scope") == "undefined" or _untestable(first.get(r["team_rule_id"]) or {}))]
    if again:
        ids2 = [x["team_rule_id"] for x in again]
        sib = [r for r in rules if any(r["jurisdiction"] == x["jurisdiction"] for x in again)]
        prev = "\n".join(json.dumps({"id": i, "scope": first[i].get("scope"), "conditions": first[i].get("conditions"),
                                     "exemptions": first[i].get("exemptions")}, ensure_ascii=False) for i in ids2 if i in first)
        # The prompt allows three reasons to change an answer (a, b, c) and says to repeat everything else,
        # so the second call cannot churn results that were already stable.
        user2 = ("Records of the jurisdictions below (one JSON object per line):\n" + compact(sib) +
                 "\n\nSECOND LOOK. These rules are in force: " + ", ".join(ids2) + ". Your first answers for them were:\n" + prev +
                 "\n\nRead the records of the same jurisdiction again (coverage_text, exemptions_text, interaction of the sibling records). Change an answer ONLY when a record supplies a basis the first answer missed:\n"
                 "(a) a record states a construction or certificate-of-occupancy cut-off for the ordinance (for example units first occupied after a date are exempt from its rent limits): "
                 "the rent-increase rule covers units first occupied on or before that date; use first_occupancy_date and name that record in support;\n"
                 "(b) a record says the rule also covers units that are exempt from the ordinance's rent limits, or covers rentals in general: the rule is \"universal\";\n"
                 "(c) the rule covers exactly the units that another rule in the list covers: use rule_coverage with that rule's id.\n"
                 "Otherwise repeat your first answer unchanged (a rule that covers only affordable housing, only demolished units, only people with a criminal record, or only one kind of tenancy stays as it was). "
                 "Answer \"undefined\" only if no record says who is covered. Return ONLY these ids, each exactly once.")
        p2 = ask(args, user2, f"take a second look at {len(again)} rule(s): " + ", ".join(ids2))
        second = {a.get("id"): a for a in p2["result"].get("rules", [])}
        # Accept a second answer only if it is no longer "undefined", passes validate on its own and really differs
        # from the first. Otherwise the first answer stays, so this step can only improve a rule, never break one.
        # plain_en stays from the first answer: the second look is about coverage only.
        merged, swapped = [], []
        for a in payload["result"].get("rules", []):
            b = second.get(a.get("id"))
            if b and a.get("id") in ids2 and b.get("scope") != "undefined":
                ok2, _p, _pl = validate({"rules": [dict(b)]}, rules)
                changed = (b.get("scope"), b.get("conditions")) != (a.get("scope"), a.get("conditions"))
                if a["id"] in ok2 and changed:
                    b = dict(b, plain_en=a.get("plain_en") or b.get("plain_en", ""))
                    merged.append(b)
                    swapped.append(a["id"])
                    continue
            merged.append(a)
        payload = {**payload, "result": {**payload["result"], "rules": merged}}
        print(f"Second look changed {len(swapped)} of {len(again)}: {', '.join(swapped) or 'none'}")

    # Final check of the merged answers: anything invalid is dropped here, whichever call it came from.
    cov2, problems, plains = validate(payload["result"], rules)
    # --- write the results. plain_en and coverage_v2 are added only when valid; a rule without coverage_v2 is
    # tested with its Module A conditions (engine.coverage_of). ---
    enriched = []
    for r in rules:
        r2 = copy.deepcopy(r)
        if r["team_rule_id"] in plains:
            r2["plain_en"] = plains[r["team_rule_id"]]
        if r["team_rule_id"] in cov2:
            r2["coverage_v2"] = cov2[r["team_rule_id"]]
        enriched.append(r2)
    (OUT / "rules_enriched.json").write_text(json.dumps({
        "query_date": QUERY_DATE, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "outputs/rules_consolidated.json", "rules": enriched}, ensure_ascii=False, indent=1), encoding="utf-8")
    # rules.json is rewritten so it always follows rules_enriched.json (its submission fields are the same as after consolidate)
    (OUT / "rules.json").write_text(json.dumps({"rules": [C.submission_view(r) for r in enriched]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "enrichment_log.json").write_text(json.dumps({
        "model_notes": payload["result"].get("notes", ""), "problems": problems,
        "per_rule": {rid: {"scope": v["scope"], "support": v["support"], "reasoning": v["reasoning"]} for rid, v in cov2.items()}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # --- report: what the model decided for each rule, so a person can eyeball the date cut-offs (they decide who is covered) ---
    n = len(rules)
    print(f"\nPlain-language sentences written for {len(plains)}/{n} rules.")
    sc = {s: sum(1 for v in cov2.values() if v["scope"] == s) for s in SCOPES}
    print(f"\n{len(cov2)}/{n} rules converted.  universal: {sc['universal']}   conditional: {sc['conditional']}   undefined: {sc['undefined']}")
    if problems:
        print(f"Problems ({len(problems)}), see outputs/enrichment_log.json:")
        for p in problems[:12]:
            print("  -", p)
    print("\nRule | scope | conditions | exemptions (the model's view; check the date cut-offs):")
    for r in enriched:
        v = r.get("coverage_v2")
        if not v:
            print(f"  {r['team_rule_id']}  {r['jurisdiction'][:16]:16s}  (kept Module A conditions)")
            continue
        cut = [f"{c['fact']} {c['op']} {c['value'] or str(c['years_before_as_of']) + 'y'}" for c in v["conditions"]]
        print(f"  {r['team_rule_id']}  {r['jurisdiction'][:16]:16s}  {v['scope']:11s} {len(v['conditions'])}c {len(v['exemptions'])}e  {'; '.join(cut)[:90]}")
    print("\nWrote outputs/rules_enriched.json, outputs/rules.json (updated), outputs/enrichment_log.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
