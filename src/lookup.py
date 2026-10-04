#!/usr/bin/env python3
"""
Step B2 - answer "which rules apply here?" for every sample address.

Reads   outputs/rules_enriched.json (or rules_consolidated.json), outputs/resolved_addresses.json, data/sample_addresses.csv
Writes  outputs/lookups.json          the submission file (4 fields per entry)
        outputs/lookups_detailed.json the same answers with citations, quotes, facts and confidence (for the app)

Answers use parcel facts only. Facts a person types into the browser ("what if") never reach these files.

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
from common import OUT, ROOT  # noqa: E402

DATA = ROOT / "data" / "sample_addresses.csv"


def load_inputs():
    """Load everything a lookup needs. Returns (rules, resolved, rows, enriched, rules_file_name).
    Rules come from rules_enriched.json when it exists (it holds the testable coverage), else from rules_consolidated.json,
    where the engine falls back to the rough Module A conditions. resolved maps address_id to its geocoder record.
    Stops with a plain message if a required file is missing."""
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
    # utf-8-sig drops a byte-order mark if the CSV has one, so the first column is still called address_id.
    rows = list(csv.DictReader(open(DATA, newline="", encoding="utf-8-sig")))
    return rules, resolved, rows, enriched, src.name


def detail(entry: dict, rule: dict) -> dict:
    """One answer entry plus the rule's own facts (citation, requirement, quote, source link) for the app.
    The engine's private keys (they start with an underscore) are dropped; the useful ones come back under plain names."""
    d = {k: v for k, v in entry.items() if not k.startswith("_")}
    # rule_status is the status stored on the rule record (set by consolidate.py for the default date). The entry's own
    # "result" is the answer for this run's as-of date. conflict_note is shown only where this entry is flagged: the rule's
    # note may be about a partner rule that is not in play at this address.
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
    """Answer every address as of one date. Returns (sub, det): sub is the submission shape {address_id: [4-field entries]},
    det is the detailed per-address record for the app."""
    by_id = {r["team_rule_id"]: r for r in rules}
    sub, det = {}, {}
    for row in rows:
        # An address with no resolver record is treated as not geocoded: only state rules are checked for it.
        res = resolved.get(row["address_id"]) or {"matched": False}
        # No what_if here: the submission files use parcel facts only.
        out = G.lookup_address(row, res, rules, as_of)
        # The submission file keeps exactly these four fields per entry.
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
    """Print answer counts overall and per legal city, and how many entries carry a conflict flag."""
    tot = Counter()
    by = defaultdict(Counter)
    for a in det.values():
        for e in a["entries"]:
            tot[e["result"]] += 1
            # Addresses with no legal city are grouped under their state.
            by[a["legal_city"] or a["state"]][e["result"]] += 1
    print("\nAnswers by result:", dict(tot))
    print(f"{'':18s}" + "".join(f"{k[:9]:>11s}" for k in G.RESULT_ORDER) + "   addresses")
    cnt = Counter(a["legal_city"] or a["state"] for a in det.values())
    for k in sorted(by):
        print(f"{k:18s}" + "".join(f"{by[k][r]:>11d}" for r in G.RESULT_ORDER) + f"   {cnt[k]}")
    flagged = sum(1 for a in det.values() for e in a["entries"] if e["conflict_flag"])
    print(f"\nEntries with a conflict flag: {flagged}")


def main(argv=None) -> int:
    """Command line entry: read the inputs, answer all addresses as of --as-of, write the two output files and print a summary."""
    # Windows consoles often use a legacy code page; without this, printing a character such as "…" could crash the run.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=G.QUERY_DATE)
    args = ap.parse_args(argv)
    # Stop on an unreadable date. The engine would treat every enacted rule as in force and give confident wrong answers.
    if G.parse_date(args.as_of) is None:
        sys.exit("--as-of must look like 2026-10-01")
    rules, resolved, rows, enriched, srcname = load_inputs()
    if not enriched:
        print("NOTE: outputs/rules_enriched.json not found; using the rough coverage conditions. Run  python src\\enrich.py  for better answers.")
    sub, det = run(args.as_of, rules, resolved, rows)
    OUT.mkdir(exist_ok=True)
    (OUT / "lookups.json").write_text(json.dumps({"as_of": args.as_of, "lookups": sub}, ensure_ascii=False, indent=1), encoding="utf-8")
    # generated_at lives only in the detailed file, so lookups.json is byte-for-byte the same on every replay.
    (OUT / "lookups_detailed.json").write_text(json.dumps({
        "as_of": args.as_of, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_file": srcname, "addresses": det}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(sub)} addresses answered as of {args.as_of}  (rules from {srcname})")
    summary(det)
    print("\nWrote outputs/lookups.json and outputs/lookups_detailed.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
