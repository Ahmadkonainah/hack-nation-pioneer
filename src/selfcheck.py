#!/usr/bin/env python3
"""
Self-check. The organisers do not share their scorer, so we check our own submission files the way a careful reviewer would:
  1. rules.json   required fields, allowed values, quoted text really is in the source document
  2. lookups.json all 500 addresses, allowed results, every rule id exists, explanations present
  3. changes.json T1-T5 behave as the test definitions say
  4. gaps         jurisdictions and categories with no rule, share of 'unknown' answers

    python src\\selfcheck.py            prints PASS / WARN / FAIL lines, exit code 1 if anything FAILs

WARN means "look at this" (for example a source page that is not stored in this copy); only FAIL changes the exit
code. It also writes outputs/selfcheck.json and rebuilds the demo page, so the app can show the latest results.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CATEGORIES, CORPUS, JURISDICTIONS, OUT as _OUT, ROOT, fold_typography  # noqa: E402
import changes as C  # noqa: E402
import translate as T  # noqa: E402

OUT = _OUT
# Allowed values. RESULTS are the five answers the engine may give; STATUSES are what dates.status_at can return.
# REQUIRED is the fallback list when schema/rule_record.schema.json is missing.
RESULTS = {"applies", "unknown", "superseded", "not_yet_effective", "pending"}
STATUSES = {"in_force", "not_yet_effective", "pending", "failed"}
REQUIRED = ["team_rule_id", "jurisdiction", "level", "category", "status", "title", "requirement", "citation", "source_url", "quoted_span"]
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")      # YYYY, YYYY-MM or YYYY-MM-DD
# Results collect in module-level lists: fails decides the exit code, warns are only counted, and LOG becomes
# outputs/selfcheck.json (the app shows it). SECTION is a one-item list so section() can set the heading by item assignment.
fails, warns = [], []
LOG, SECTION = [], [""]


def section(title):
    """Start a new group of checks: remember its name for the log and print a heading."""
    SECTION[0] = title
    print("\n== " + title + " ==")


def ok(msg):
    """Record and print a passing check."""
    print("PASS  " + msg)
    LOG.append({"status": "PASS", "section": SECTION[0], "check": msg})


def warn(msg):
    """Record a WARN: worth a look, but it does not change the exit code."""
    print("WARN  " + msg)
    warns.append(msg)
    LOG.append({"status": "WARN", "section": SECTION[0], "check": msg})


def fail(msg):
    """Record a FAIL: the output is wrong or incomplete. The exit code becomes 1."""
    print("FAIL  " + msg)
    fails.append(msg)
    LOG.append({"status": "FAIL", "section": SECTION[0], "check": msg})


def check(cond, msg_ok, msg_bad, soft=False):
    """PASS with msg_ok if `cond` is true, otherwise FAIL with msg_bad (WARN when soft=True). Returns `cond`.
    Use soft=True for something that may be a gap in the data rather than a bug."""
    if cond:
        ok(msg_ok)
    else:
        (warn if soft else fail)(msg_bad)
    return cond


def norm(s: str) -> str:
    """Normalise text before a quote is looked up in its page: fold curly quotes and dashes, collapse spaces, lowercase.
    Models copy text back with small typographic changes; the check is about the words, not the punctuation style."""
    return re.sub(r"\s+", " ", fold_typography(s or "")).strip().lower()


def load(name):
    """Read outputs/<name> as JSON. A missing file is recorded as a FAIL and None is returned."""
    p = OUT / name
    if not p.exists():
        fail(f"outputs/{name} is missing")
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    """Run the four groups of checks, print a summary, write outputs/selfcheck.json and refresh the demo page.
    Returns 1 if any check FAILed, otherwise 0."""
    # a rule title with unusual characters must not crash the report on a console that cannot print it
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    rules_doc, lookups_doc, changes_doc = load("rules.json"), load("lookups.json"), load("changes.json")
    det = load("changes_detailed.json")
    rows = list(csv.DictReader(open(ROOT / "data" / "sample_addresses.csv", newline="", encoding="utf-8-sig")))
    resolved = {r["address_id"]: r for r in (load("resolved_addresses.json") or {"addresses": []})["addresses"]}
    # Without the three main files nothing else can be checked (load() has already recorded the FAILs).
    if not (rules_doc and lookups_doc and changes_doc):
        print("\nRun the earlier steps first (extract, consolidate, enrich, resolve, lookup, changes).")
        return 1
    rules = rules_doc["rules"]
    by_id = {r["team_rule_id"]: r for r in rules}

    section("1. rules.json")
    # The schema file is the source of truth for required fields and allowed values. The built-in lists are a
    # fallback so the check still runs when schema/ is missing.
    required = REQUIRED
    sp = ROOT / "schema" / "rule_record.schema.json"
    if sp.exists():
        sch = json.loads(sp.read_text(encoding="utf-8"))
        required = sch.get("required", REQUIRED)
        enums = {k: v["enum"] for k, v in sch.get("properties", {}).items() if isinstance(v, dict) and "enum" in v}
    else:
        enums = {"level": ["state", "city"], "category": CATEGORIES, "status": sorted(STATUSES)}
    # Shape checks: these catch records that a schema validator would reject.
    check(len(by_id) == len(rules), f"{len(rules)} rules, ids unique", "duplicate team_rule_id values")
    miss = [(r["team_rule_id"], k) for r in rules for k in required if r.get(k) in (None, "")]
    check(not miss, "every rule has all required fields", f"missing required fields: {miss[:5]}")
    badenum = [(r["team_rule_id"], k, r.get(k)) for r in rules for k, vals in enums.items() if r.get(k) not in vals]
    check(not badenum, "allowed values for level, category and status", f"bad enum values: {badenum[:5]}")
    # A very short quote could match almost any page, which would make the quote check below meaningless.
    check(all(len(r["quoted_span"]) >= 20 for r in rules), "quoted_span has at least 20 characters", "a quoted_span is shorter than 20 characters")
    check(all(not r.get("effective_date") or DATE_RE.match(r["effective_date"]) for r in rules), "effective_date format", "an effective_date is not ISO")
    check(all(r["jurisdiction"] in JURISDICTIONS for r in rules), "jurisdictions are in scope", "a jurisdiction is out of scope")
    # The anti-hallucination test: every quote (the primary one and each supporting source's) must appear in its
    # saved page text. Both sides go through norm() so curly quotes and spacing do not cause false alarms.
    texts, unfound, unstored, checked = {}, [], [], 0
    for r in rules:
        for src, span in [(r["source_doc_id"], r["quoted_span"])] + [(s["doc_id"], s["quoted_span"]) for s in r.get("supporting_sources") or []]:
            if src not in texts:
                tf = CORPUS / "text" / f"{src}.txt"
                texts[src] = norm(tf.read_text(encoding="utf-8", errors="replace")) if tf.exists() else None
            if texts[src] is None:
                unstored.append((r["team_rule_id"], src))          # page text is not on this computer
            else:
                checked += 1
                if norm(span) not in texts[src]:
                    unfound.append((r["team_rule_id"], src))
    check(not unfound, f"every quoted span is found in its source document ({checked} checked)", f"quoted span not found in the source text for {unfound[:6]}")
    if unstored:
        # Hand-saved pages whose terms do not allow copying are not stored in the public repository. Their quotes were
        # verified word for word when the page was read (outputs/extraction_log.json), so this is a note, not a failure.
        warn(f"{len(unstored)} quoted spans belong to {len({u[1] for u in unstored})} source pages that are not stored in this copy of the repository "
             f"(they were verified when the pages were read): {sorted({u[1] for u in unstored})}")
    # An overrides id that points nowhere would tell the reader a rule is superseded by a rule that does not exist.
    dangling = [(r["team_rule_id"], o) for r in rules for o in r.get("overrides") or [] if o not in by_id]
    check(not dangling, "every overrides id points at a rule", f"overrides point at unknown rules: {dangling[:5]}")
    sc = Counter(r["status"] for r in rules)
    print(f"      rules by status: {dict(sc)}")
    # T5 and T4 need specific records. These are soft (WARN): on a copy without the hand-saved pages they can be missing.
    ballot = [r for r in rules if r["status"] == "failed" and r["jurisdiction"] in ("MA", "Boston, MA", "Cambridge, MA") and re.search(r"ballot|25-21|initiative", " ".join([r["title"], r["requirement"], r["citation"]]), re.I)]
    check(bool(ballot), "the failed Massachusetts ballot question is recorded as failed (T5)", "no failed rule mentions the Massachusetts ballot question (IP 25-21); its source text (D059) may be missing", soft=True)
    check(sc.get("pending", 0) >= 2, "pending bills recorded (T4)", "fewer than two pending rules (T4 needs S.2983 and H.5222)", soft=True)

    section("2. lookups.json")
    lk = lookups_doc["lookups"]
    ids = [r["address_id"] for r in rows]
    # Completeness: a silently skipped address would look like "no rule applies".
    check(set(lk) == set(ids), f"all {len(ids)} addresses answered", f"{len(set(ids) - set(lk))} addresses missing, {len(set(lk) - set(ids))} unknown ids")
    # Vocabulary: only the five allowed results. A typo would reach a user as if it were an answer.
    badres = [(a, e["team_rule_id"], e["result"]) for a, es in lk.items() for e in es if e["result"] not in RESULTS]
    check(not badres, "result values are allowed", f"bad result values: {badres[:5]}")
    badid = [(a, e["team_rule_id"]) for a, es in lk.items() for e in es if e["team_rule_id"] not in by_id]
    check(not badid, "every lookup points at a rule in rules.json", f"unknown rule ids in lookups: {badid[:5]}")
    # Every answer must explain itself. A bare verdict would break the promise that each result can be traced.
    check(all(isinstance(e["conflict_flag"], bool) and len(e["explanation"]) > 15 for es in lk.values() for e in es), "every entry has an explanation and a boolean conflict_flag", "entry without explanation or conflict_flag")
    # One answer per rule per address: a duplicate would be counted twice in the app and in the change tests.
    dup = [a for a, es in lk.items() if len({e["team_rule_id"] for e in es}) != len(es)]
    check(not dup, "no rule listed twice for one address", f"duplicate rule entries for {dup[:5]}")
    # soft: an address with no answer at all is suspicious but not always wrong
    empty = [a for a, es in lk.items() if not es]
    check(not empty, "every address has at least one answer", f"{len(empty)} addresses have no entry: {empty[:5]}", soft=True)
    # Geography: an address may only get rules of its own state or its own legal city. This would catch a Hoboken ban
    # showing up for a Newark address.
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
    # The tests come from changes.py (the five supplied tests, or a copy on disk), so this check uses the same
    # definitions as the step that produced changes.json.
    tests, where = C.load_tests()
    check(set(changes_doc) >= {t["test_id"] for t in tests}, f"all {len(tests)} tests present", "a test is missing from changes.json")
    addr_state = {r["address_id"]: r["state"] for r in rows}
    addr_city = {r["address_id"]: (resolved.get(r["address_id"]) or {}).get("legal_city") or (resolved.get(r["address_id"]) or {}).get("fallback_city") or "" for r in rows}
    # Each branch below checks what its test type promises. They test the output files, not the engine's logic.
    for t in tests:
        tid, c = t["test_id"], changes_doc.get(t["test_id"], {})
        aff, flg = set(c.get("affected_address_ids", [])), set(c.get("conflict_flag_address_ids", []))
        check(aff <= set(ids) and flg <= set(ids), f"{tid}: address ids exist", f"{tid}: unknown address ids")
        check(bool(c.get("notes")), f"{tid}: notes present", f"{tid}: notes missing")
        states = set(t.get("states") or [])
        scope = {a for a in ids if not states or addr_state[a] in states}
        # as_of (T1): the law is statewide, so every address in the test's states must be affected
        if tid == "T1" or t["type"] == "as_of" and not t.get("conflict_with"):
            check(aff == scope, f"{tid}: affected = every {'/'.join(sorted(states))} address ({len(scope)})", f"{tid}: affected {len(aff)} but {len(scope)} addresses are in scope; missing {sorted(scope - aff)[:5]}")
        # boundary (T2): each city ban must reach only its own city. Newark has no ban and is the control: a hit
        # there means a ban leaked across a city line.
        if t["type"] == "boundary":
            newark = {a for a in ids if addr_city[a] == "Newark, NJ"}
            check(not (aff & newark), f"{tid}: no Newark address is affected", f"{tid}: Newark addresses affected: {sorted(aff & newark)[:5]}")
            hob = {a for a in ids if addr_city[a] == "Hoboken, NJ"}
            jc = {a for a in ids if addr_city[a] == "Jersey City, NJ"}
            check(aff and aff <= (hob | jc), f"{tid}: affected addresses are only in Hoboken and Jersey City ({len(aff)})", f"{tid}: affected set is empty or reaches outside Hoboken/Jersey City")
            check(bool(aff & hob) and bool(aff & jc), f"{tid}: both the Hoboken ban and the Jersey City ban apply inside their own city", f"{tid}: a local ban is missing (Hoboken hit: {bool(aff & hob)}, Jersey City hit: {bool(aff & jc)})")
        # T3: the FAIR Act reaches every NJ address, and the preemption flag must sit on exactly the Hoboken and
        # Jersey City addresses (fewer means a missed flag, more means a false alarm)
        if t.get("conflict_with"):
            want = {a for a in ids if addr_city[a] in ("Hoboken, NJ", "Jersey City, NJ") and addr_state[a] in states}
            check(aff == scope, f"{tid}: affected = every NJ address ({len(scope)})", f"{tid}: affected {len(aff)} of {len(scope)}")
            check(flg == want, f"{tid}: conflict flags on exactly the Hoboken and Jersey City addresses ({len(want)})", f"{tid}: flagged {len(flg)} addresses, expected the {len(want)} Hoboken and Jersey City ones; missing {sorted(want - flg)[:4]}, extra {sorted(flg - want)[:4]}")
        # pending (T4): a bill is reported for every address in its state
        if t["type"] == "pending":
            check(aff == scope, f"{tid}: affected = every Massachusetts address ({len(scope)})", f"{tid}: affected {len(aff)} of {len(scope)}")
        # negative (T5): a struck-down measure must affect nobody
        if t["type"] == "negative":
            check(not aff, f"{tid}: affected set is empty", f"{tid}: affected set should be empty")
    # T5, from the answers side: no Boston / Cambridge answer should be a rent cap. A local rent cap that applies in
    # Massachusetts without being marked failed would mean the struck-down ballot measure leaked through as law.
    caps = [(a, e["team_rule_id"]) for a, es in lk.items() if addr_state[a] == "MA" for e in es
            if e["result"] == "applies" and by_id[e["team_rule_id"]]["category"] == "rent_increase_limits" and by_id[e["team_rule_id"]]["status"] != "failed"
            and by_id[e["team_rule_id"]]["jurisdiction"] != "MA"]
    check(not caps, "T5: no local rent cap is reported for any Massachusetts address", f"a local rent rule applies in Massachusetts: {caps[:5]}")
    # gaps the change step itself reported (for example a rule it could not find) are shown so they are not lost
    if det:
        for tid, d in det["tests"].items():
            if d.get("gaps"):
                warn(f"{tid} gaps: {d['gaps']}")

    section("4. coverage of the rule set")
    # A table of rules per jurisdiction and category. Failed measures are not counted: they are not law.
    abbr = {"rent_increase_limits": "rent", "just_cause_eviction": "just", "security_deposits": "depo", "application_screening_fees": "fees", "screening_restrictions": "scrn", "algorithmic_rent_setting": "algo"}
    print(f"{'':20s}" + "".join(f"{a:>6s}" for a in abbr.values()))
    empty_cells = []
    for j in JURISDICTIONS:
        counts = [sum(1 for r in rules if r["jurisdiction"] == j and r["category"] == c and r["status"] != "failed") for c in abbr]
        print(f"{j:20s}" + "".join(f"{x if x else '.':>6}" for x in counts))
        if not any(counts):
            empty_cells.append(j)
    # soft: an empty jurisdiction usually means its source page was missing or not extracted, not that no law exists
    check(not empty_cells, "every jurisdiction has at least one rule", f"no rule at all for: {', '.join(empty_cells)} (source text missing or not extracted?)", soft=True)

    section("5. Spanish view (optional)")
    es_path = OUT / "rules_es.json"
    if not es_path.exists():
        ok("not generated yet (optional): the app then has no Spanish button. Run  python src/translate.py")
    else:
        es = json.loads(es_path.read_text(encoding="utf-8"))
        tr = es.get("translations", {})
        # The same test translate.py applies: every number in the English must come back unchanged. Re-checked
        # here so a hand-edited or stale file cannot slip a changed amount into the app.
        changed = [rid for rid in tr if rid in by_id and T.check_one(T.source_records([by_id[rid]])[0], tr[rid])]
        check(not changed, f"Spanish text keeps every number of the English ({len(tr)} rules)", f"Spanish text changes a number or is left in English: {changed[:6]}")
        absent = [r["team_rule_id"] for r in rules if r["team_rule_id"] not in tr]
        check(not absent, "every rule has a Spanish translation", f"{len(absent)} rule(s) have no Spanish text and stay in English: {absent[:6]}", soft=True)
        check(bool(es.get("label")), "Spanish text carries its 'automatic translation, not reviewed' label", "rules_es.json has no label")

    # The disclaimer is a promise to users, so a missing one is a FAIL, not a WARN.
    app = ROOT / "web" / "index.html"
    if app.exists():
        txt = app.read_text(encoding="utf-8", errors="replace").lower()
        check("not legal advice" in txt, "app labels itself 'not legal advice'", "web/index.html does not say 'not legal advice'")
    print(f"\n{len(fails)} FAIL, {len(warns)} WARN")
    # Save the results and refresh the demo page, which embeds selfcheck.json. Wrapped in try so a problem in the
    # page build can never change the exit code of the check itself (build_web is imported late for the same reason).
    try:
        from datetime import datetime, timezone
        (OUT / "selfcheck.json").write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pass": sum(1 for x in LOG if x["status"] == "PASS"), "warn": len(warns), "fail": len(fails), "checks": LOG},
            ensure_ascii=False, indent=1), encoding="utf-8")
        import build_web as BW  # noqa: E402 - refresh the demo page so it shows these results
        _o = sys.stdout        # silenced while the page builds, so its messages stay out of the check report
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
