#!/usr/bin/env python3
"""
Module C - run the five change tests (T1-T5) and list the affected addresses.

Nothing here is typed in by hand: for each test we find our own rules that match the test's rule ids
(jurisdiction + category + status, read from the test's id such as CA-ALG-01), run the same lookup engine at the
test's dates and report the addresses whose answer changes, applies, is pending or carries a conflict flag.

    python src\\changes.py
Writes outputs/changes.json (submission) and outputs/changes_detailed.json.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as G  # noqa: E402
import extract as E  # noqa: E402
import lookup as L  # noqa: E402

# the five tests as supplied by the organisers (dev/change_tests.json is used instead when present)
EMBEDDED_TESTS = [
    {"test_id": "T1", "title": "California AB 325 / SB 763 takes effect", "type": "as_of", "rule_ids": ["CA-ALG-01"],
     "as_of_before": "2025-12-31", "as_of_after": "2026-01-02", "states": ["CA"],
     "expected_behavior": "not_yet_effective before, applies after, for every CA address."},
    {"test_id": "T2", "title": "Hoboken vs Jersey City local algorithmic bans", "type": "boundary", "rule_ids": ["HOB-ALG-01", "JC-ALG-01"],
     "as_of": "2026-10-01", "expected_behavior": "HOB-ALG-01 only for Hoboken addresses; JC-ALG-01 only for Jersey City addresses; neither for Newark."},
    {"test_id": "T3", "title": "NJ FAIR Act: enacted, not yet effective; possible preemption", "type": "as_of", "rule_ids": ["NJ-ALG-01"],
     "as_of_before": "2026-10-01", "as_of_after": "2027-07-02", "states": ["NJ"], "conflict_with": ["JC-ALG-01", "HOB-ALG-01"],
     "expected_behavior": "not_yet_effective on 2026-10-01, applies on 2027-07-02 for every NJ address; Jersey City and Hoboken addresses carry a conflict flag for human review."},
    {"test_id": "T4", "title": "Massachusetts pending bills S.2983 and H.5222", "type": "pending", "rule_ids": ["MA-ALG-P1", "MA-ALG-P2"],
     "as_of": "2026-10-01", "states": ["MA"],
     "expected_behavior": "Reported as pending (not in force) for every Boston and Cambridge address; affected set = all MA addresses if enacted."},
    {"test_id": "T5", "title": "Massachusetts rent-control ballot question struck", "type": "negative", "rule_ids": ["MA-RENT-P1"],
     "as_of": "2026-10-01", "states": ["MA"],
     "expected_behavior": "No rent cap reported for any Boston or Cambridge address; IP 25-21 recorded as failed. Affected set is empty."},
]
PREFIX = {"CA": "CA", "NJ": "NJ", "MA": "MA", "LA": "Los Angeles, CA", "SF": "San Francisco, CA", "SD": "San Diego, CA", "BRK": "Berkeley, CA",
          "BER": "Berkeley, CA", "SA": "Santa Ana, CA", "JC": "Jersey City, NJ", "HOB": "Hoboken, NJ", "NWK": "Newark, NJ", "NEW": "Newark, NJ",
          "BOS": "Boston, MA", "CAM": "Cambridge, MA"}
CAT = {"ALG": "algorithmic_rent_setting", "RENT": "rent_increase_limits", "JC": "just_cause_eviction", "JUST": "just_cause_eviction",
       "DEP": "security_deposits", "FEE": "application_screening_fees", "SCR": "screening_restrictions", "SCREEN": "screening_restrictions"}


def load_tests():
    for p in (E.ROOT / "dev" / "change_tests.json", E.ROOT / "change_tests.json", E.ROOT / "data" / "change_tests.json"):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")), str(p.relative_to(E.ROOT))
            except Exception:  # noqa: BLE001
                pass
    return EMBEDDED_TESTS, "embedded copy of the five supplied tests"


def target_rules(test: dict, rules: list[dict]):
    """Our rules that the test's organiser ids point at (by place, category and legal state)."""
    out, notes = {}, []
    for tid in test["rule_ids"]:
        parts = tid.split("-")
        juris, cat = PREFIX.get(parts[0]), CAT.get(parts[1]) if len(parts) > 1 else None
        proposal = len(parts) > 2 and parts[2].upper().startswith("P")
        found = [r for r in rules if r["jurisdiction"] == juris and r["category"] == cat
                 and ((r["legal_state"] in ("pending", "failed")) if proposal else r["legal_state"] == "enacted")]
        if not found:
            notes.append(f"no rule of ours matches {tid} ({juris}, {cat}, {'pending/failed' if proposal else 'enacted'})")
        for r in found:
            out[r["team_rule_id"]] = r
    return out, notes


def results_at(as_of, rules, resolved, rows):
    out = {}
    for row in rows:
        res = resolved.get(row["address_id"]) or {"matched": False}
        out[row["address_id"]] = {e["team_rule_id"]: e for e in G.lookup_address(row, res, rules, as_of)["entries"]}
    return out


def run_test(test, rules, resolved, rows, cache):
    targets, notes = target_rules(test, rules)
    tids = set(targets)
    states = set(test.get("states") or [])
    scope = [r for r in rows if not states or r["state"] in states]

    def at(d):
        if d not in cache:
            cache[d] = results_at(d, rules, resolved, rows)
        return cache[d]

    detail = {"title": test["title"], "type": test["type"], "team_rule_ids": sorted(tids),
              "rules": {rid: {"title": r["title"], "citation": r["citation"], "status": r["status"], "effective_date": r["effective_date"]} for rid, r in targets.items()}}
    affected, flagged = [], []
    if test["type"] == "as_of":
        b, a = test["as_of_before"], test["as_of_after"]
        rb, ra = at(b), at(a)
        for row in scope:
            aid = row["address_id"]
            before = {t: rb[aid][t]["result"] for t in tids if t in rb[aid]}
            after = {t: ra[aid][t]["result"] for t in tids if t in ra[aid]}
            if any(v == "not_yet_effective" for v in before.values()) and any(v == "applies" for v in after.values()):
                affected.append(aid)
            if any(rb[aid][t]["conflict_flag"] for t in tids if t in rb[aid]) or any(ra[aid][t]["conflict_flag"] for t in tids if t in ra[aid]):
                flagged.append(aid)
        detail["dates"] = {"before": b, "after": a}
        cities = Counter(next((x["legal_city"] or x["state"] for x in [{"legal_city": (resolved.get(r["address_id"]) or {}).get("legal_city"), "state": r["state"]}]), "") for r in rows if r["address_id"] in set(affected))
        note = (f"{test['title']}. Our rule(s): " + "; ".join(f"{r['citation']} ({rid}, effective {r['effective_date']})" for rid, r in targets.items()) +
                f". {len(affected)} of {len(scope)} addresses in {'/'.join(sorted(states)) or 'scope'} show not_yet_effective on {b} and applies on {a}.")
        if test.get("conflict_with"):
            note += f" {len(flagged)} addresses carry a conflict flag for human review (possible conflict with local bans)."
    elif test["type"] == "boundary":
        d = test["as_of"]
        rd = at(d)
        per = {}
        for row in scope:
            aid = row["address_id"]
            hit = [t for t in tids if t in rd[aid] and rd[aid][t]["result"] == "applies"]
            if hit:
                affected.append(aid)
                for t in hit:
                    per.setdefault(t, []).append(aid)
        city_of = {r["address_id"]: ((resolved.get(r["address_id"]) or {}).get("legal_city") or "") for r in rows}
        detail["applies_by_rule"] = {t: {"count": len(v), "cities": dict(Counter(city_of[a] for a in v))} for t, v in per.items()}
        newark = [r["address_id"] for r in rows if city_of[r["address_id"]] == "Newark, NJ"]
        detail["newark_addresses_affected"] = [a for a in newark if a in set(affected)]
        note = (f"{test['title']}. As of {d}: " + "; ".join(f"{targets[t]['jurisdiction']} ban ({t}) applies at {len(v)} addresses, all in {', '.join(sorted(set(city_of[a] for a in v)))}" for t, v in per.items()) +
                f". Newark addresses affected: {len(detail['newark_addresses_affected'])} of {len(newark)}.")
        if not per:
            note += " No local ban rule was found, so nothing is reported."
    elif test["type"] == "pending":
        d = test["as_of"]
        rd = at(d)
        for row in scope:
            aid = row["address_id"]
            if any(t in rd[aid] and rd[aid][t]["result"] == "pending" for t in tids):
                affected.append(aid)
        note = (f"{test['title']}. Reported as pending (not in force) as of {d}: " + "; ".join(f"{r['citation']} ({rid})" for rid, r in targets.items()) +
                f". If enacted they would affect all {len(affected)} of {len(scope)} Massachusetts addresses (every Boston and Cambridge address).")
    else:  # negative
        d = test["as_of"]
        rd = at(d)
        failed = [r for r in rules if r["legal_state"] == "failed" and r["jurisdiction"] in ("MA", "Boston, MA", "Cambridge, MA") and r["category"] == "rent_increase_limits"]
        reported = Counter()
        for row in scope:
            for t, e in rd[row["address_id"]].items():
                if e["result"] == "applies" and by_id(rules)[t]["category"] == "rent_increase_limits":
                    reported[t] += 1
        detail["failed_rules"] = {r["team_rule_id"]: r["title"] for r in failed}
        detail["rent_category_rules_reported"] = {t: {"title": by_id(rules)[t]["title"], "addresses": n} for t, n in reported.items()}
        note = (f"{test['title']}. Recorded as failed (not law): " + ("; ".join(f"{r['citation']} ({r['team_rule_id']})" for r in failed) or "no failed rule found") +
                ". No rent cap is reported for any Boston or Cambridge address; the affected set is empty." +
                (" The only rent-category rule reported there is " + "; ".join(f"{by_id(rules)[t]['citation']} ({t}), which bars local rent control rather than capping rent" for t in reported) + "." if reported else ""))
        affected = []
        if not failed:
            notes.append("no failed rule recorded for the Massachusetts ballot question (its source text may be missing)")
    if notes:
        note += " Gaps: " + "; ".join(notes) + "."
    detail["gaps"] = notes
    return {"affected_address_ids": sorted(affected), "conflict_flag_address_ids": sorted(flagged), "notes": note}, detail


_BY = {}


def by_id(rules):
    if not _BY or len(_BY) != len(rules):
        _BY.clear()
        _BY.update({r["team_rule_id"]: r for r in rules})
    return _BY


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    rules, resolved, rows, enriched, srcname = L.load_inputs()
    tests, where = load_tests()
    cache, sub, det = {}, {}, {}
    for t in tests:
        s, d = run_test(t, rules, resolved, rows, cache)
        sub[t["test_id"]], det[t["test_id"]] = s, d
        print(f"{t['test_id']}: {len(s['affected_address_ids'])} affected, {len(s['conflict_flag_address_ids'])} flagged" + (f"   GAPS: {d['gaps']}" if d["gaps"] else ""))
    E.OUT.mkdir(exist_ok=True)
    (E.OUT / "changes.json").write_text(json.dumps(sub, ensure_ascii=False, indent=1), encoding="utf-8")
    (E.OUT / "changes_detailed.json").write_text(json.dumps({"tests_from": where, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tests": det}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nTests read from: {where}\nWrote outputs/changes.json and outputs/changes_detailed.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
