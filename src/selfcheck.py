#!/usr/bin/env python3
"""
Self-check. The organisers do not share their scorer, so we check our own submission files the way a careful reviewer would:
  1. rules.json   required fields, allowed values, quoted text really is in the source document
  2. lookups.json all 500 addresses, allowed results, every rule id exists, explanations present
  3. changes.json T1-T5 behave as the test definitions say
  4. gaps         jurisdictions and categories with no rule, share of 'unknown' answers

    python src\\selfcheck.py            prints PASS / WARN / FAIL lines, exit code 1 if anything FAILs
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract as E  # noqa: E402
import changes as C  # noqa: E402

OUT = E.OUT
RESULTS = {"applies", "unknown", "superseded", "not_yet_effective", "pending"}
STATUSES = {"in_force", "not_yet_effective", "pending", "failed"}
REQUIRED = ["team_rule_id", "jurisdiction", "level", "category", "status", "title", "requirement", "citation", "source_url", "quoted_span"]
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
fails, warns = [], []
LOG, SECTION = [], [""]


def section(title):
    SECTION[0] = title
    print("\n== " + title + " ==")


def ok(msg):
    print("PASS  " + msg)
    LOG.append({"status": "PASS", "section": SECTION[0], "check": msg})


def warn(msg):
    print("WARN  " + msg)
    warns.append(msg)
    LOG.append({"status": "WARN", "section": SECTION[0], "check": msg})


def fail(msg):
    print("FAIL  " + msg)
    fails.append(msg)
    LOG.append({"status": "FAIL", "section": SECTION[0], "check": msg})


def check(cond, msg_ok, msg_bad, soft=False):
    if cond:
        ok(msg_ok)
    else:
        (warn if soft else fail)(msg_bad)
    return cond


def norm(s: str) -> str:
    s = (s or "").translate(E._TRANS) if hasattr(E, "_TRANS") else (s or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def load(name):
    p = OUT / name
    if not p.exists():
        fail(f"outputs/{name} is missing")
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    rules_doc, lookups_doc, changes_doc = load("rules.json"), load("lookups.json"), load("changes.json")
    det = load("changes_detailed.json")
    rows = list(csv.DictReader(open(E.ROOT / "data" / "sample_addresses.csv", newline="", encoding="utf-8-sig")))
    resolved = {r["address_id"]: r for r in (load("resolved_addresses.json") or {"addresses": []})["addresses"]}
    if not (rules_doc and lookups_doc and changes_doc):
        print("\nRun the earlier steps first (extract, consolidate, enrich, resolve, lookup, changes).")
        return 1
    rules = rules_doc["rules"]
    by_id = {r["team_rule_id"]: r for r in rules}

    section("1. rules.json")
    required = REQUIRED
    sp = E.ROOT / "schema" / "rule_record.schema.json"
    if sp.exists():
        sch = json.loads(sp.read_text(encoding="utf-8"))
        required = sch.get("required", REQUIRED)
        enums = {k: v["enum"] for k, v in sch.get("properties", {}).items() if isinstance(v, dict) and "enum" in v}
    else:
        enums = {"level": ["state", "city"], "category": E.CATEGORIES, "status": sorted(STATUSES)}
    check(len(by_id) == len(rules), f"{len(rules)} rules, ids unique", "duplicate team_rule_id values")
    miss = [(r["team_rule_id"], k) for r in rules for k in required if r.get(k) in (None, "")]
    check(not miss, "every rule has all required fields", f"missing required fields: {miss[:5]}")
    badenum = [(r["team_rule_id"], k, r.get(k)) for r in rules for k, vals in enums.items() if r.get(k) not in vals]
    check(not badenum, "allowed values for level, category and status", f"bad enum values: {badenum[:5]}")
    check(all(len(r["quoted_span"]) >= 20 for r in rules), "quoted_span has at least 20 characters", "a quoted_span is shorter than 20 characters")
    check(all(not r.get("effective_date") or DATE_RE.match(r["effective_date"]) for r in rules), "effective_date format", "an effective_date is not ISO")
    check(all(r["jurisdiction"] in E.JURISDICTIONS for r in rules), "jurisdictions are in scope", "a jurisdiction is out of scope")
    texts, unfound = {}, []
    for r in rules:
        for src, span in [(r["source_doc_id"], r["quoted_span"])] + [(s["doc_id"], s["quoted_span"]) for s in r.get("supporting_sources") or []]:
            if src not in texts:
                tf = E.CORPUS / "text" / f"{src}.txt"
                texts[src] = norm(tf.read_text(encoding="utf-8", errors="replace")) if tf.exists() else None
            if texts[src] is None or norm(span) not in texts[src]:
                unfound.append((r["team_rule_id"], src))
    check(not unfound, "every quoted span is found in its source document", f"quoted span not found in the source text for {unfound[:6]}")
    dangling = [(r["team_rule_id"], o) for r in rules for o in r.get("overrides") or [] if o not in by_id]
    check(not dangling, "every overrides id points at a rule", f"overrides point at unknown rules: {dangling[:5]}")
    sc = Counter(r["status"] for r in rules)
    print(f"      rules by status: {dict(sc)}")
    ballot = [r for r in rules if r["status"] == "failed" and r["jurisdiction"] in ("MA", "Boston, MA", "Cambridge, MA") and re.search(r"ballot|25-21|initiative", " ".join([r["title"], r["requirement"], r["citation"]]), re.I)]
    check(bool(ballot), "the failed Massachusetts ballot question is recorded as failed (T5)", "no failed rule mentions the Massachusetts ballot question (IP 25-21); its source text (D059) may be missing", soft=True)
    check(sc.get("pending", 0) >= 2, "pending bills recorded (T4)", "fewer than two pending rules (T4 needs S.2983 and H.5222)", soft=True)

    section("2. lookups.json")
    lk = lookups_doc["lookups"]
    ids = [r["address_id"] for r in rows]
    check(set(lk) == set(ids), f"all {len(ids)} addresses answered", f"{len(set(ids) - set(lk))} addresses missing, {len(set(lk) - set(ids))} unknown ids")
    badres = [(a, e["team_rule_id"], e["result"]) for a, es in lk.items() for e in es if e["result"] not in RESULTS]
    check(not badres, "result values are allowed", f"bad result values: {badres[:5]}")
    badid = [(a, e["team_rule_id"]) for a, es in lk.items() for e in es if e["team_rule_id"] not in by_id]
    check(not badid, "every lookup points at a rule in rules.json", f"unknown rule ids in lookups: {badid[:5]}")
    check(all(isinstance(e["conflict_flag"], bool) and len(e["explanation"]) > 15 for es in lk.values() for e in es), "every entry has an explanation and a boolean conflict_flag", "entry without explanation or conflict_flag")
    dup = [a for a, es in lk.items() if len({e["team_rule_id"] for e in es}) != len(es)]
    check(not dup, "no rule listed twice for one address", f"duplicate rule entries for {dup[:5]}")
    empty = [a for a, es in lk.items() if not es]
    check(not empty, "every address has at least one answer", f"{len(empty)} addresses have no entry: {empty[:5]}", soft=True)
    wrongjur = []
    for row in rows:
        city = (resolved.get(row["address_id"]) or {}).get("legal_city") or (resolved.get(row["address_id"]) or {}).get("fallback_city")
        for e in lk.get(row["address_id"], []):
            j = by_id[e["team_rule_id"]]["jurisdiction"]
            if j not in (row["state"], city):
                wrongjur.append((row["address_id"], e["team_rule_id"]))
    check(not wrongjur, "no address gets a rule from another state or city", f"rules from the wrong place: {wrongjur[:5]}")
    tot = Counter(e["result"] for es in lk.values() for e in es)
    n = sum(tot.values())
    print(f"      answers: {dict(tot)}   unknown share: {tot.get('unknown', 0) / max(n, 1):.0%}")
    unres = [a for a, r in resolved.items() if not r.get("matched")]
    print(f"      addresses not geocoded: {len(unres)} (their city rules are answered from the dataset or reported unknown)")

    section("3. changes.json")
    tests, where = C.load_tests()
    check(set(changes_doc) >= {t["test_id"] for t in tests}, f"all {len(tests)} tests present", "a test is missing from changes.json")
    addr_state = {r["address_id"]: r["state"] for r in rows}
    addr_city = {r["address_id"]: (resolved.get(r["address_id"]) or {}).get("legal_city") or (resolved.get(r["address_id"]) or {}).get("fallback_city") or "" for r in rows}
    for t in tests:
        tid, c = t["test_id"], changes_doc.get(t["test_id"], {})
        aff, flg = set(c.get("affected_address_ids", [])), set(c.get("conflict_flag_address_ids", []))
        check(aff <= set(ids) and flg <= set(ids), f"{tid}: address ids exist", f"{tid}: unknown address ids")
        check(bool(c.get("notes")), f"{tid}: notes present", f"{tid}: notes missing")
        states = set(t.get("states") or [])
        scope = {a for a in ids if not states or addr_state[a] in states}
        if tid == "T1" or t["type"] == "as_of" and not t.get("conflict_with"):
            check(aff == scope, f"{tid}: affected = every {'/'.join(sorted(states))} address ({len(scope)})", f"{tid}: affected {len(aff)} but {len(scope)} addresses are in scope; missing {sorted(scope - aff)[:5]}")
        if t["type"] == "boundary":
            newark = {a for a in ids if addr_city[a] == "Newark, NJ"}
            check(not (aff & newark), f"{tid}: no Newark address is affected", f"{tid}: Newark addresses affected: {sorted(aff & newark)[:5]}")
            hob = {a for a in ids if addr_city[a] == "Hoboken, NJ"}
            jc = {a for a in ids if addr_city[a] == "Jersey City, NJ"}
            check(aff and aff <= (hob | jc), f"{tid}: affected addresses are only in Hoboken and Jersey City ({len(aff)})", f"{tid}: affected set is empty or reaches outside Hoboken/Jersey City")
            check(bool(aff & hob) and bool(aff & jc), f"{tid}: both the Hoboken ban and the Jersey City ban apply inside their own city", f"{tid}: a local ban is missing (Hoboken hit: {bool(aff & hob)}, Jersey City hit: {bool(aff & jc)})")
        if t.get("conflict_with"):
            want = {a for a in ids if addr_city[a] in ("Hoboken, NJ", "Jersey City, NJ") and addr_state[a] in states}
            check(aff == scope, f"{tid}: affected = every NJ address ({len(scope)})", f"{tid}: affected {len(aff)} of {len(scope)}")
            check(flg == want, f"{tid}: conflict flags on exactly the Hoboken and Jersey City addresses ({len(want)})", f"{tid}: flagged {len(flg)} addresses, expected the {len(want)} Hoboken and Jersey City ones; missing {sorted(want - flg)[:4]}, extra {sorted(flg - want)[:4]}")
        if t["type"] == "pending":
            check(aff == scope, f"{tid}: affected = every Massachusetts address ({len(scope)})", f"{tid}: affected {len(aff)} of {len(scope)}")
        if t["type"] == "negative":
            check(not aff, f"{tid}: affected set is empty", f"{tid}: affected set should be empty")
    # T5: no Boston / Cambridge answer should be a rent cap
    caps = [(a, e["team_rule_id"]) for a, es in lk.items() if addr_state[a] == "MA" for e in es
            if e["result"] == "applies" and by_id[e["team_rule_id"]]["category"] == "rent_increase_limits" and by_id[e["team_rule_id"]]["status"] != "failed"
            and by_id[e["team_rule_id"]]["jurisdiction"] != "MA"]
    check(not caps, "T5: no local rent cap is reported for any Massachusetts address", f"a local rent rule applies in Massachusetts: {caps[:5]}")
    if det:
        for tid, d in det["tests"].items():
            if d.get("gaps"):
                warn(f"{tid} gaps: {d['gaps']}")

    section("4. coverage of the rule set")
    abbr = {"rent_increase_limits": "rent", "just_cause_eviction": "just", "security_deposits": "depo", "application_screening_fees": "fees", "screening_restrictions": "scrn", "algorithmic_rent_setting": "algo"}
    print(f"{'':20s}" + "".join(f"{a:>6s}" for a in abbr.values()))
    empty_cells = []
    for j in E.JURISDICTIONS:
        counts = [sum(1 for r in rules if r["jurisdiction"] == j and r["category"] == c and r["status"] != "failed") for c in abbr]
        print(f"{j:20s}" + "".join(f"{x if x else '.':>6}" for x in counts))
        if not any(counts):
            empty_cells.append(j)
    check(not empty_cells, "every jurisdiction has at least one rule", f"no rule at all for: {', '.join(empty_cells)} (source text missing or not extracted?)", soft=True)

    app = E.ROOT / "web" / "index.html"
    if app.exists():
        txt = app.read_text(encoding="utf-8", errors="replace").lower()
        check("not legal advice" in txt, "app labels itself 'not legal advice'", "web/index.html does not say 'not legal advice'")
    print(f"\n{len(fails)} FAIL, {len(warns)} WARN")
    try:
        from datetime import datetime, timezone
        (OUT / "selfcheck.json").write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pass": sum(1 for x in LOG if x["status"] == "PASS"), "warn": len(warns), "fail": len(fails), "checks": LOG},
            ensure_ascii=False, indent=1), encoding="utf-8")
        import build_web as BW  # noqa: E402 - refresh the demo page so it shows these results
        _o = sys.stdout
        try:
            sys.stdout = open(os.devnull, "w")
            BW.main()
        finally:
            sys.stdout = _o
    except Exception as exc:  # noqa: BLE001
        print(f"(could not refresh the demo page: {exc})")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
