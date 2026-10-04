#!/usr/bin/env python3
"""
Step A2 - consolidate the extracted rules.

Module A reads one document at a time, so the same law shows up several times (an official statute
and a city web page that explains it) and one law can be split into several records. This step:
  1. asks Claude to group the records so there is ONE record per legal instrument, per category,
     per jurisdiction, to pick the best source, to write the official citation, and to link rules
     that yield to or may conflict with each other,
  2. checks the answer in code (every record used once, groups never mix jurisdiction/category),
  3. merges the groups and keeps every extra source as supporting evidence,
  4. writes outputs/rules.json (the submission file) and outputs/rules_consolidated.json (used by
     the address lookup).

Run from the repo root with the virtual environment active:
    python src/consolidate.py
One short API call; the answer is cached in cache/consolidate/.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract as E  # noqa: E402

CACHE_DIR = E.ROOT / "cache" / "consolidate"

SYSTEM = """You consolidate rule records that were extracted one document at a time from a corpus of rental-housing law (three states and ten cities). The same legal rule often appears in several records, because several pages describe it, or because one law was split into several records.

TASK 1 - GROUPS
Put every record into exactly one group. One group = one legal instrument, in one jurisdiction, in one category. All records in a group must have the same jurisdiction and the same category.
Merge:
 (a) records that describe the same law from different pages (an official statute, and a city or agency page that explains it);
 (b) records for different sub-provisions of one instrument in the same category (a deposit cap and the same statute's return-deadline or interest rules; relocation payment amounts and the same ordinance's just-cause rules; a rent cap and its yearly adjustment notices).
Keep separate: different instruments, even in the same category and jurisdiction (a statewide rent cap versus a city rent-control ordinance; two different statutes limiting screening); never merge across jurisdictions or categories. When unsure, keep separate.
What each category should headline: rent_increase_limits = the cap or formula, covered buildings, exemptions; just_cause_eviction = causes, notice, relocation, coverage; security_deposits = the maximum, exceptions, date; application_screening_fees = fee caps and allowed upfront charges; screening_restrictions = limits on criminal-history and income-source screening; algorithmic_rent_setting = the ban or limits.
primary_id = the record in the group that best states the rule. Prefer official legal text (statute, ordinance, bill text) over agency web pages, and those over rate tables and notices; then the higher confidence.
canonical_citation = the official citation of the instrument in standard form, taken from or assembled from the citations the group's records give (for example "Cal. Civ. Code § 1950.5", "S.F. Admin. Code ch. 37"). Never invent a citation that no record supports. Never use a page title.
canonical_title = a short plain title of the instrument.
date_conflict = true ONLY if two records in the group give different effective dates for the SAME provision (for example two sources disagree on the start date of one ban). Set it to false when the dates belong to different sub-provisions (a cap that started in 2024 and a notice rule that started in 2025 are not a conflict). A reference date is not an effective date (a base-rent date, a building-age cut-off, the date a bill was introduced), so it never makes a conflict. date_conflict_note = one sentence naming the dates and sources when true, otherwise "".

TASK 2 - RELATIONS BETWEEN GROUPS (use primary_ids)
Only record a relation when a record's own text supports it. Do not link two laws just because they cover the same topic.
 - yields_to: the groups that govern INSTEAD of this group's rule where both could apply, because this rule's text says so (it defers to, is displaced by, or exempts housing covered by the other). Example: a statewide rent cap whose text exempts housing under a stricter local rent-control ordinance yields to that local ordinance; statewide just cause that defers to local just-cause ordinances yields to them. Do NOT use yields_to when a local law merely repeats, incorporates or adds to a state law (a city fee-disclosure ordinance that incorporates the state fee cap does not yield to it: both apply).
 - Delegation is not yielding. A law that says it sets no rule of its own and leaves the subject to cities (for example "the State has no rent control; municipalities may pass rent control") is not displaced by the local rules. Leave its yields_to empty and describe the delegation in interaction_note.
 - When a record says that housing under a stricter or more protective local law is exempt or yields (stricter local rent control; more protective local just-cause rules), list in yields_to EVERY group in the same state that is such a local law: every city rent-cap group for a rent cap, every city just-cause group for statewide just cause. Do not list only one of them. Do not list local groups of a different category.
 - conflicts_with: groups that may be preempted by, or may conflict with, this group's rule and need a human reviewer, only when a record's text says a higher-level law bars, limits or may displace lower-level rules (for example "municipalities may not enact ordinances that conflict with this act"). Statewide law against local law only. Two local laws never conflict; a law that says nothing about local rules gets no conflicts_with. List the local rules that the text points at, if they appear in the records.
 - interaction_note: one or two sentences explaining the relation; "" if none.

Return one JSON object that follows the schema, and nothing else."""

SCHEMA = E._strictify({
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "primary_id": {"type": "string"},
                    "member_ids": {"type": "array", "items": {"type": "string"}},
                    "canonical_citation": {"type": "string"},
                    "canonical_title": {"type": "string"},
                    "date_conflict": {"type": "boolean"},
                    "date_conflict_note": {"type": "string"},
                    "yields_to": {"type": "array", "items": {"type": "string"}},
                    "conflicts_with": {"type": "array", "items": {"type": "string"}},
                    "interaction_note": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
})

SUBMISSION_FIELDS = [
    "team_rule_id", "jurisdiction", "level", "category", "status", "title", "requirement", "key_value",
    "coverage_conditions", "exemptions", "overrides", "interaction", "effective_date", "citation",
    "source_doc_id", "source_url", "quoted_span", "confidence", "conflict_flag", "conflict_note",
    "retrieved_at", "supporting_sources",
]


def clip(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def compact(records: list[dict], source_types: dict) -> str:
    lines = []
    for r in records:
        lines.append(json.dumps({
            "id": r["team_rule_id"], "jurisdiction": r["jurisdiction"], "category": r["category"],
            "status": r["status"], "effective_date": r["effective_date"], "citation": clip(r["citation"], 140),
            "title": clip(r["title"], 110), "key_value": clip(r["key_value"], 110),
            "doc": r["source_doc_id"], "source_type": source_types.get(r["source_doc_id"], ""),
            "confidence": r["confidence"], "requirement": clip(r["requirement"], 240),
            "interaction": clip(r["interaction"], 220), "yields_to_text": r.get("yields_to") or [],
        }, ensure_ascii=False))
    return "\n".join(lines)


def repair_groups(groups: list[dict], records: list[dict]):
    """Make the model's grouping safe. Returns (groups, repairs)."""
    by_id = {r["team_rule_id"]: r for r in records}
    repairs, used, fixed = [], set(), []
    for g in groups:
        members = []
        for m in g.get("member_ids", []):
            if m in by_id and m not in used:
                members.append(m)
                used.add(m)
            else:
                repairs.append(f"dropped unknown or repeated id {m!r} from a group")
        if not members:
            continue
        # a group may not mix jurisdiction or category: keep the largest consistent part, split the rest off
        keys = {}
        for m in members:
            keys.setdefault((by_id[m]["jurisdiction"], by_id[m]["category"]), []).append(m)
        if len(keys) > 1:
            repairs.append(f"group {members} mixed jurisdiction/category; split into {len(keys)} groups")
        for part in keys.values():
            gg = dict(g, member_ids=part)
            if gg.get("primary_id") not in part:
                best = max(part, key=lambda i: by_id[i]["confidence"] or 0)
                if gg.get("primary_id") is not None:
                    repairs.append(f"primary {gg.get('primary_id')!r} not in its group; used {best}")
                gg["primary_id"] = best
            fixed.append(gg)
    for r in records:
        if r["team_rule_id"] not in used:
            repairs.append(f"{r['team_rule_id']} was missing from the grouping; kept as its own group")
            fixed.append({"primary_id": r["team_rule_id"], "member_ids": [r["team_rule_id"]], "canonical_citation": r["citation"],
                          "canonical_title": r["title"], "date_conflict": False, "date_conflict_note": "",
                          "yields_to": [], "conflicts_with": [], "interaction_note": "",
                          "reason": "not grouped by the model"})
    return fixed, repairs


def _state_of(j: str) -> str:
    """'Los Angeles, CA' -> 'CA'; 'CA' -> 'CA'"""
    return j.rsplit(",", 1)[-1].strip() if "," in j else j.strip()


def _state_level(j: str) -> bool:
    return "," not in j


def yield_ok(me: str, other: str) -> bool:
    """May a rule of jurisdiction `me` give way to a rule of jurisdiction `other`?
    Same state only. A state rule may give way to a local rule; a local rule only to a state rule
    (or to another rule of its own city). Never across states, never from one city to another city."""
    if _state_of(me) != _state_of(other):
        return False
    if me == other:
        return True
    return _state_level(me) or _state_level(other)


def empty_cov(c):
    return not c or (not c.get("all_of") and not c.get("exemptions"))


def build(records: list[dict], groups: list[dict]):
    by_id = {r["team_rule_id"]: r for r in records}
    order = {j: i for i, j in enumerate(E.JURISDICTIONS)}
    corder = {c: i for i, c in enumerate(E.CATEGORIES)}
    groups = sorted(groups, key=lambda g: (order[by_id[g["primary_id"]]["jurisdiction"]], corder[by_id[g["primary_id"]]["category"]], by_id[g["primary_id"]]["source_doc_id"]))
    new_id = {g["primary_id"]: f"r-{i:04d}" for i, g in enumerate(groups, 1)}
    # the model may name a relation target by any member id, not just the primary: map every member to its group's new id
    to_new = {m: new_id[g["primary_id"]] for g in groups for m in g["member_ids"]}
    out, log = [], []
    for g in groups:
        prim = copy.deepcopy(by_id[g["primary_id"]])
        members = [by_id[m] for m in g["member_ids"]]
        others = [m for m in members if m["team_rule_id"] != g["primary_id"]]
        notes = []
        rec = prim
        rec["merged_from"] = [m["team_rule_id"] for m in members]
        rec["citation"] = (g.get("canonical_citation") or prim["citation"]).strip()
        rec["title"] = (g.get("canonical_title") or prim["title"]).strip()
        # dates
        dates = {m["effective_date"] for m in members if m["effective_date"]}
        if not rec["effective_date"]:
            stated = [m for m in others if m["effective_date"] and m.get("date_basis") == "stated"] or [m for m in others if m["effective_date"]]
            if stated:
                rec["effective_date"] = stated[0]["effective_date"]
                rec["date_basis"] = stated[0].get("date_basis", "stated")
                notes.append(f"effective date taken from {stated[0]['source_doc_id']}")
        if g.get("date_conflict"):
            rec["conflict_flag"] = True
            why = (g.get("date_conflict_note") or "").strip() or ("Sources give different dates: " + ", ".join(sorted(dates)) + ".")
            rec["conflict_note"] = ((rec["conflict_note"] + " ") if rec["conflict_note"] else "") + why
        elif len(dates) > 1:
            notes.append("members have different effective dates (" + ", ".join(sorted(dates)) + "); the model judged them separate provisions")
        states = {m["legal_state"] for m in members}
        if states == {"pending", "enacted"}:
            # a measure only moves forward: a document written while it was proposed (a staff report, a first reading)
            # cannot show that it was never adopted, and another source says it was. That is a later stage, not a conflict.
            late = [m for m in members if m["legal_state"] == "enacted"]
            rec["legal_state"] = "enacted"
            notes.append("status taken as enacted from " + ", ".join(m["source_doc_id"] for m in late) +
                         "; " + ", ".join(m["source_doc_id"] for m in members if m["legal_state"] == "pending") + " describes the proposal stage")
            if not rec["effective_date"] or rec.get("date_basis") != "stated":
                dated = [m for m in late if m["effective_date"]]
                if dated:
                    rec["effective_date"] = dated[0]["effective_date"]
                    rec["date_basis"] = dated[0].get("date_basis", "stated")
        elif len(states) > 1:
            rec["conflict_flag"] = True
            rec["conflict_note"] = ((rec["conflict_note"] + " ") if rec["conflict_note"] else "") + "Sources disagree on legal status: " + ", ".join(sorted(states)) + "."
        # coverage: if the best source says nothing, borrow the fullest statement from another source
        if empty_cov(rec.get("coverage")):
            richer = [m for m in others if not empty_cov(m.get("coverage"))]
            if richer:
                best = max(richer, key=lambda m: len(m["coverage"].get("all_of", [])) + len(m["coverage"].get("exemptions", [])))
                rec["coverage"] = best["coverage"]
                rec["coverage_conditions"] = best["coverage_conditions"]
                rec["exemptions"] = best["exemptions"]
                notes.append(f"coverage taken from {best['source_doc_id']}")
        # evidence from the other sources
        rec["supporting_sources"] = [{
            "doc_id": m["source_doc_id"], "source_url": m["source_url"], "retrieved_at": m["retrieved_at"],
            "citation_as_extracted": m["citation"], "quoted_span": m["quoted_span"], "effective_date": m["effective_date"],
        } for m in others]
        rec["status"] = E.status_at(rec["legal_state"], rec["effective_date"], E.QUERY_DATE)
        rec["_relations"] = {"yields_to": g.get("yields_to", []), "conflicts_with": g.get("conflicts_with", []),
                             "note": (g.get("interaction_note") or "").strip(), "self": new_id[g["primary_id"]]}
        out.append(rec)
        log.append({"new_id": new_id[g["primary_id"]], "primary": g["primary_id"], "members": rec["merged_from"],
                    "citation": rec["citation"], "reason": g.get("reason", ""), "adjustments": notes})
    # relations -> ids
    for rec in out:
        rel = rec.pop("_relations")
        me = rel["self"]
        mine = rec["jurisdiction"]
        keep_y, keep_c, dropped = set(), set(), []
        for x in rel["yields_to"]:
            t = to_new.get(x)
            if not t or t == me:
                continue
            if yield_ok(mine, by_id[x]["jurisdiction"]):
                keep_y.add(t)
            else:
                dropped.append(f"{t} ({by_id[x]['jurisdiction']})")
        for x in rel["conflicts_with"]:
            t = to_new.get(x)
            if not t or t == me:
                continue
            if _state_of(mine) == _state_of(by_id[x]["jurisdiction"]):
                keep_c.add(t)
            else:
                dropped.append(f"conflict with {t} ({by_id[x]['jurisdiction']})")
        rec["overrides"] = sorted(keep_y)
        rec["_conflicts_with"] = sorted(keep_c)
        if dropped:
            for lg in log:
                if lg["new_id"] == me:
                    lg["adjustments"].append("dropped links to rules of another state or city: " + ", ".join(dropped))
        if rel["note"] and rel["note"] not in (rec["interaction"] or ""):
            rec["interaction"] = ((rec["interaction"] + " ") if rec["interaction"] else "") + rel["note"]
    # symmetric conflict flags
    ids = {new_id[g["primary_id"]]: r for g, r in zip(groups, out)}
    for rid, rec in list(ids.items()):
        for other in rec["_conflicts_with"]:
            for a, b in ((rid, other), (other, rid)):
                t = ids[a]
                t["conflict_flag"] = True
                note = f"Possible conflict with {ids[b]['citation']} ({b}); needs human review."
                if note not in (t["conflict_note"] or ""):
                    t["conflict_note"] = ((t["conflict_note"] + " ") if t["conflict_note"] else "") + note
    for rec, g in zip(out, groups):
        rec["team_rule_id"] = new_id[g["primary_id"]]
        rec.pop("merged_from_ids", None)
    return out, log


def submission_view(rec: dict) -> dict:
    d = {k: rec.get(k) for k in SUBMISSION_FIELDS}
    d["overrides"] = rec.get("overrides") or []
    d["supporting_sources"] = rec.get("supporting_sources") or []
    d["conflict_flag"] = bool(rec.get("conflict_flag"))
    return d


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Consolidate extracted rules into one record per legal instrument")
    ap.add_argument("--model", default=E.DEFAULT_MODEL)
    ap.add_argument("--thinking", choices=["off", "adaptive"], default="off")
    ap.add_argument("--refresh", action="store_true", help="ignore the cached answer and ask again")
    args = ap.parse_args(argv)
    E._THINKING["mode"] = args.thinking

    src = E.OUT / "extracted.json"
    if not src.exists():
        sys.exit("outputs/extracted.json not found. Run  python src\\extract.py  first.")
    data = json.loads(src.read_text(encoding="utf-8"))
    records = data["rules"]
    source_types = {r["doc_id"]: r.get("source_type", "") for r in E.load_manifest()}
    user = ("Records to consolidate (one JSON object per line):\n" + compact(records, source_types) +
            f"\n\nThere are {len(records)} records. Every id must appear in exactly one group's member_ids.")
    key = hashlib.sha256((SYSTEM + json.dumps(SCHEMA, sort_keys=True) + user + args.model).encode()).hexdigest()[:12]
    cache = CACHE_DIR / f"{key}.json"
    if cache.exists() and not args.refresh:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        print(f"Using the saved answer from cache/consolidate/{cache.name} (no API call).")
    else:
        client = E.get_client()
        print(f"Asking {args.model} to consolidate {len(records)} records ...")
        resp = E.call_structured(client, args.model, SYSTEM, user, SCHEMA, max_tokens=32000)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        if resp.stop_reason in ("max_tokens", "refusal"):
            sys.exit(f"The answer was not usable (stop_reason={resp.stop_reason}). Run again.")
        try:
            result = json.loads(text)
            assert isinstance(result.get("groups"), list)
        except Exception:  # noqa: BLE001
            sys.exit("The answer was not valid JSON. Run the same command again.")
        cin, cout = resp.usage.input_tokens, resp.usage.output_tokens
        print(f"tokens in/out: {cin:,}/{cout:,}   cost: ${E.usd(cin, cout, args.model):.2f}")
        payload = {"model": args.model, "called_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "input_tokens": cin, "output_tokens": cout, "result": result}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    groups, repairs = repair_groups(payload["result"]["groups"], records)
    merged, log = build(records, groups)
    E.OUT.mkdir(exist_ok=True)
    (E.OUT / "rules_consolidated.json").write_text(json.dumps({
        "query_date": E.QUERY_DATE, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "outputs/extracted.json", "rules": merged}, ensure_ascii=False, indent=1), encoding="utf-8")
    (E.OUT / "rules.json").write_text(json.dumps({"rules": [submission_view(r) for r in merged]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (E.OUT / "consolidation_log.json").write_text(json.dumps({"model_notes": payload["result"].get("notes", ""), "repairs": repairs, "groups": log},
                                                            ensure_ascii=False, indent=1), encoding="utf-8")
    merged_away = len(records) - len(merged)
    print(f"\n{len(records)} extracted records -> {len(merged)} consolidated rules ({merged_away} repeats merged)")
    if repairs:
        print(f"Repairs made to the model's grouping: {len(repairs)} (see outputs/consolidation_log.json)")
    print(f"Rules with a conflict flag: {sum(1 for r in merged if r['conflict_flag'])}   with 'yields to' links: {sum(1 for r in merged if r['overrides'])}")
    print("\nGroups that merged several records:")
    for l in log:
        if len(l["members"]) > 1:
            print(f"  {l['new_id']}  {l['citation'][:60]:60s}  <- {', '.join(l['members'])}")
    E.print_matrix(merged)
    print("\nWrote outputs/rules.json (submission), outputs/rules_consolidated.json, outputs/consolidation_log.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
