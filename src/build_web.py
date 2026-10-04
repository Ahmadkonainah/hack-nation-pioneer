#!/usr/bin/env python3
"""
Build the app's data file: web/data.js  (window.NAV_DATA = {...}).
The app runs the same lookup engine in the browser (web/engine.js), so any as-of date can be asked live.

    python src\\build_web.py
Reads outputs/rules_enriched.json (or rules_consolidated.json), outputs/resolved_addresses.json, outputs/changes*.json
Then calls build_site to rebuild the one-file demo page (web/index.html) with this data inside.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as G  # noqa: E402
from common import OUT, ROOT  # noqa: E402
import lookup as L  # noqa: E402

WEB = ROOT / "web"


def bundle_rule(r: dict) -> dict:
    """One rule as the browser needs it: display fields, provenance, the relation lists, and `cov`.
    `cov` is the testable coverage (coverage_v2, or the Module A conditions when enrich.py was not run), so engine.js
    and engine.py test the same conditions. Supporting sources keep only doc id, link, date and quote."""
    cov = G.coverage_of(r)
    # Key names match the Python rule record on purpose: engine.js reads them as they are (including _conflicts_with).
    return {
        "team_rule_id": r["team_rule_id"], "jurisdiction": r["jurisdiction"], "level": r["level"], "category": r["category"],
        "legal_state": r["legal_state"], "status": r["status"], "title": r["title"], "requirement": r["requirement"],
        "key_value": r.get("key_value"), "plain_en": r.get("plain_en"), "citation": r["citation"], "effective_date": r.get("effective_date"),
        "source_doc_id": r["source_doc_id"], "source_url": r["source_url"], "quoted_span": r["quoted_span"],
        "retrieved_at": r.get("retrieved_at"), "confidence": r.get("confidence"), "overrides": r.get("overrides") or [],
        "_conflicts_with": r.get("_conflicts_with") or [], "conflict_flag": bool(r.get("conflict_flag")),
        "conflict_note": r.get("conflict_note"), "coverage_conditions": r.get("coverage_conditions"),
        "exemptions": r.get("exemptions"), "interaction": r.get("interaction"),
        "supporting_sources": [{"doc_id": s["doc_id"], "source_url": s["source_url"], "retrieved_at": s.get("retrieved_at"),
                                "quoted_span": s["quoted_span"]} for s in r.get("supporting_sources") or []],
        "cov": cov,
    }


def bundle_address(row: dict, res: dict) -> dict:
    """One address as the browser needs it: the parcel row, the geocoder result, the city that governs it and the building facts.
    `basis` says how the city is known (census_geocoder, city_dataset or unresolved). `unverified` is a city guessed from
    the postal city when the address could not be placed; its city rules are answered unknown, never applies."""
    facts = G.building_facts(row)
    city, basis, unverified = G.city_for(res)
    return {
        "id": row["address_id"], "street": row["street_address"], "postal_city": row["postal_city"], "zip": row["zip"],
        "state": row["state"], "county": res.get("county"), "matched_address": res.get("matched_address"),
        "lat": res.get("lat"), "lon": res.get("lon"), "city": city, "basis": basis, "unverified": unverified,
        "facts": facts, "dataset": row["source_dataset"], "retrieved_at": row["retrieved_at"], "geocoded": bool(res.get("matched")),
        "note": res.get("note"),
    }


def main() -> int:
    """Write web/data.js from the pipeline outputs, then rebuild the demo page. Returns 0."""
    rules, resolved, rows, enriched, srcname = L.load_inputs()
    ch = {}
    for name in ("changes.json", "changes_detailed.json"):
        p = OUT / name
        ch[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    # changes.json (the submission file) has only affected ids and notes. Titles, types and rule ids live in
    # changes_detailed.json, so they are merged in and the app can describe each test.
    tests = {}
    for tid, c in ch["changes.json"].items():
        d = (ch["changes_detailed.json"].get("tests") or {}).get(tid, {})
        tests[tid] = {**c, "title": d.get("title"), "type": d.get("type"), "team_rule_ids": d.get("team_rule_ids", []),
                      "dates": d.get("dates"), "gaps": d.get("gaps", [])}
    # Self-check results are shown in the app. On the first run the file does not exist yet (selfcheck runs after
    # this step and then calls this module again), so a missing or unreadable file gives {} instead of stopping the build.
    sc = {}
    scp = OUT / "selfcheck.json"
    if scp.exists():
        try:
            sc = json.loads(scp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            sc = {}
    # Optional Spanish view. Without outputs/rules_es.json (translate.py not run yet) the app has no Spanish button.
    es_path = OUT / "rules_es.json"
    i18n = {}
    if es_path.exists():
        es = json.loads(es_path.read_text(encoding="utf-8"))
        i18n["es"] = {"label": es.get("label", ""), "model": es.get("model", ""), "rules": es.get("translations", {})}
    data = {
        "meta": {"selfcheck": sc, "query_date": G.QUERY_DATE, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "rules_file": srcname, "disclaimer": "Not legal advice. Prototype built on a public corpus not reviewed by counsel."},
        "rules": [bundle_rule(r) for r in rules],
        "addresses": [bundle_address(r, resolved.get(r["address_id"]) or {"matched": False}) for r in rows],
        "tests": tests,
        "i18n": i18n,
    }
    WEB.mkdir(exist_ok=True)
    # A script that sets a global, not a JSON file: the page makes no network requests (see the CSP in build_site) and
    # must open from disk. Compact separators keep it small. parity_test.py strips this exact prefix, so keep the format.
    js = "window.NAV_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (WEB / "data.js").write_text(js, encoding="utf-8")
    print(f"Wrote web/data.js  ({len(js) / 1024:.0f} KB, {len(data['rules'])} rules, {len(data['addresses'])} addresses)")
    try:
        import build_site as BS  # noqa: E402
        BS.build()
    except SystemExit:
        raise                  # build_site exits on purpose when engine.js, data.js or a template marker is missing
    except Exception as exc:  # noqa: BLE001 - the demo page is a bonus; never block the data build
        print(f"(demo page not built: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
