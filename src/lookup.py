#!/usr/bin/env python3
"""
Step B2 - answer "which rules apply here?" for every sample address.

Reads   outputs/rules_enriched.json (or rules_consolidated.json), outputs/resolved_addresses.json, data/sample_addresses.csv
Writes  outputs/lookups.json          the submission file (4 fields per entry)
        outputs/lookups_detailed.json the same answers with citations, quotes, facts and confidence (for the app)

    python src\\lookup.py                  as of 2026-10-01
    python src\\lookup.py --as-of 2027-07-02
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as G  # noqa: E402
import extract as E  # noqa: E402

DATA = E.ROOT / "data" / "sample_addresses.csv"


def load_inputs():
    OUT = E.OUT
    src = OUT / "rules_enriched.json"
    enriched = src.exists()
    if not enriched:
        src = OUT / "rules_consolidated.json"
    if not src.exists():
        sys.exit("outputs/rules_consolidated.json not found. Run extract.py, then consolidate.py first.")
    rules = json.loads(src.read_text(encoding="utf-8"))["rules"]
    rp = OUT / "resolved_addresses.json"
    if not rp.exists():
        sys.exit("outputs/resolved_addresses.json not found. Run  python src\\resolve.py  first.")
    resolved = {r["address_id"]: r for r in json.loads(rp.read_text(encoding="utf-8"))["addresses"]}
    rows = list(csv.DictReader(open(DATA, newline="", encoding="utf-8-sig")))
    return rules, resolved, rows, enriched, src.name


def detail(entry: dict, rule: dict) -> dict:
    d = {k: v for k, v in entry.items() if not k.startswith("_")}
    d.update({
        "title": rule["title"], "category": rule["category"], "jurisdiction": rule["jurisdiction"], "level": rule["level"],
        "citation": rule["citation"], "requirement": rule["requirement"], "key_value": rule.get("key_value"),
        "rule_status": rule["status"], "effective_date": rule.get("effective_date"),
        "source_doc_id": rule["source_doc_id"], "source_url": rule["source_url"], "quoted_span": rule["quoted_span"],
        "retrieved_at": rule.get("retrieved_at"), "extraction_confidence": rule.get("confidence"),
        "confidence": entry["_confidence"], "not_checked": entry["_caveats"],
        "conflict_note": rule.get("conflict_note") if entry["conflict_flag"] else None,
        "superseded_by": entry["_superseded_by"], "conflicts_with": entry["_partners"],
    })
    return d


def run(as_of: str, rules, resolved, rows):
    by_id = {r["team_rule_id"]: r for r in rules}
    sub, det = {}, {}
    for row in rows:
        res = resolved.get(row["address_id"]) or {"matched": False}
        out = G.lookup_address(row, res, rules, as_of)
        sub[row["address_id"]] = [{k: e[k] for k in ("team_rule_id", "result", "explanation", "conflict_flag")} for e in out["entries"]]
        det[row["address_id"]] = {
            "address_id": row["address_id"], "street_address": row["street_address"], "postal_city": row["postal_city"],
            "state": row["state"], "zip": row["zip"], "county": res.get("county"), "legal_city": out["city"],
            "city_basis": out["city_basis"], "matched_address": res.get("matched_address"),
            "lat": res.get("lat"), "lon": res.get("lon"),
            "facts": {k: out["facts"][k] for k in ("year_built", "units_lo", "units_hi", "units_basis", "use_code", "use_description")},
            "source_dataset": row["source_dataset"], "retrieved_at": row["retrieved_at"],
            "entries": [detail(e, by_id[e["team_rule_id"]]) for e in out["entries"]]}
    return sub, det


def summary(det: dict) -> None:
    tot = Counter()
    by = defaultdict(Counter)
    for a in det.values():
        for e in a["entries"]:
            tot[e["result"]] += 1
            by[a["legal_city"] or a["state"]][e["result"]] += 1
    print("\nAnswers by result:", dict(tot))
    print(f"{'':18s}" + "".join(f"{k[:9]:>11s}" for k in G.RESULT_ORDER) + "   addresses")
    cnt = Counter(a["legal_city"] or a["state"] for a in det.values())
    for k in sorted(by):
        print(f"{k:18s}" + "".join(f"{by[k][r]:>11d}" for r in G.RESULT_ORDER) + f"   {cnt[k]}")
    flagged = sum(1 for a in det.values() for e in a["entries"] if e["conflict_flag"])
    print(f"\nEntries with a conflict flag: {flagged}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=G.QUERY_DATE)
    args = ap.parse_args(argv)
    if G.parse_date(args.as_of) is None:
        sys.exit("--as-of must look like 2026-10-01")
    rules, resolved, rows, enriched, srcname = load_inputs()
    if not enriched:
        print("NOTE: outputs/rules_enriched.json not found; using the rough coverage conditions. Run  python src\\enrich.py  for better answers.")
    sub, det = run(args.as_of, rules, resolved, rows)
    E.OUT.mkdir(exist_ok=True)
    (E.OUT / "lookups.json").write_text(json.dumps({"as_of": args.as_of, "lookups": sub}, ensure_ascii=False, indent=1), encoding="utf-8")
    (E.OUT / "lookups_detailed.json").write_text(json.dumps({
        "as_of": args.as_of, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_file": srcname, "addresses": det}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(sub)} addresses answered as of {args.as_of}  (rules from {srcname})")
    summary(det)
    print("\nWrote outputs/lookups.json and outputs/lookups_detailed.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
