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

The model makes the judgement call (which records are the same law). Code then guarantees the result is
safe, because a model answer can drop, repeat or mix records, and a lost rule would be a wrong answer for
a real address.

Run from the repo root with the virtual environment active:
    python src/consolidate.py
One short API call; the answer is cached in cache/consolidate/.
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
from common import (CATEGORIES, DEFAULT_MODEL, JURISDICTIONS, OUT, ROOT, is_state_level, load_manifest,  # noqa: E402
                    state_of, use_utf8_console)
from dates import QUERY_DATE, status_at  # noqa: E402
from extract import print_matrix  # noqa: E402
from llm import THINKING, cached_structured_call, strictify  # noqa: E402

CACHE_DIR = ROOT / "cache" / "consolidate"

# Do not edit SYSTEM, not even whitespace. It is part of the cache key (llm.cache_key), so any change throws
# away the saved answer and costs a new API call. Its relation rules are re-checked by code (yield_ok, build).
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

# strictify makes every field required, so the model must always give the relation fields: [] means "no
# relation" and "" means "no note". "reason" and "notes" are only written to the log; no code reads them.
SCHEMA = strictify({
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

# The organisers' rule-record fields plus retrieved_at and supporting_sources. rules.json carries only these;
# internal fields (coverage, _conflicts_with, merged_from ...) stay in rules_consolidated.json.
SUBMISSION_FIELDS = [
    "team_rule_id", "jurisdiction", "level", "category", "status", "title", "requirement", "key_value",
    "coverage_conditions", "exemptions", "overrides", "interaction", "effective_date", "citation",
    "source_doc_id", "source_url", "quoted_span", "confidence", "conflict_flag", "conflict_note",
    "retrieved_at", "supporting_sources",
]


def clip(s, n):
    """Collapse whitespace and cut `s` to at most `n` characters (the last one is an ellipsis). None becomes ""."""
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def compact(records: list[dict], source_types: dict) -> str:
    """One JSON object per line for the prompt: the model's whole view of the corpus.
    Long texts are clipped so every record fits one call; quotes and coverage are left out (grouping does not need them).
    `source_types` maps doc id to source type so the model can prefer official text. `yields_to_text` is Module A's free-text guess, not ids."""
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
    """Make the model's grouping safe to build from. Returns (groups, repairs).
    Fixes: unknown or repeated ids are dropped; a group that mixes jurisdiction or category is split; a primary that is
    not a member is replaced by the member with the highest confidence; a record the model forgot becomes its own group.
    `repairs` holds one plain-English note per fix and is written to consolidation_log.json."""
    # The model decides what belongs together; code enforces what the rest of the pipeline relies on:
    # every record in exactly one group, and one jurisdiction and one category per group.
    by_id = {r["team_rule_id"]: r for r in records}
    repairs, used, fixed = [], set(), []
    for g in groups:
        members = []
        for m in g.get("member_ids", []):
            # the first group that names an id keeps it
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
    # A forgotten record stays as its own group. Never drop it: a missing rule is worse than an unmerged duplicate.
    for r in records:
        if r["team_rule_id"] not in used:
            repairs.append(f"{r['team_rule_id']} was missing from the grouping; kept as its own group")
            fixed.append({"primary_id": r["team_rule_id"], "member_ids": [r["team_rule_id"]], "canonical_citation": r["citation"],
                          "canonical_title": r["title"], "date_conflict": False, "date_conflict_note": "",
                          "yields_to": [], "conflicts_with": [], "interaction_note": "",
                          "reason": "not grouped by the model"})
    return fixed, repairs


def yield_ok(me: str, other: str) -> bool:
    """May a rule of jurisdiction `me` give way to a rule of jurisdiction `other`?
    Same state only. A state rule may give way to a local rule; a local rule only to a state rule
    (or to another rule of its own city). Never across states, never from one city to another city.
    Used to drop links the model got wrong: a wrong link would mark a rule superseded where it still applies."""
    if state_of(me) != state_of(other):
        return False
    if me == other:
        return True
    return is_state_level(me) or is_state_level(other)


def empty_cov(c):
    """True when a coverage dict says nothing usable: no tests (all_of) and no exemptions."""
    return not c or (not c.get("all_of") and not c.get("exemptions"))


def build(records: list[dict], groups: list[dict]):
    """Merge every group into one rule record and turn the model's relations into rule ids. Returns (rules, log).
    The group's primary record is the base; the other members only fill gaps (effective date, coverage) and are kept as
    supporting_sources. Rules are renumbered r-0001... in jurisdiction and category order. `log` explains each merge.
    A relation is kept only if the two jurisdictions allow it (yield_ok for yields_to, same state for conflicts_with)."""
    by_id = {r["team_rule_id"]: r for r in records}
    order = {j: i for i, j in enumerate(JURISDICTIONS)}
    corder = {c: i for i, c in enumerate(CATEGORIES)}
    # Fixed order (jurisdiction, category, source doc) so an id like r-0007 is the same on every run. The ids feed
    # enrich's cache key, so an unstable order would force new API calls and break byte-for-byte replays.
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
        # --- effective date: the primary wins; borrow one only if it has none, preferring a date a page states outright ---
        dates = {m["effective_date"] for m in members if m["effective_date"]}
        if not rec["effective_date"]:
            stated = [m for m in others if m["effective_date"] and m.get("date_basis") == "stated"] or [m for m in others if m["effective_date"]]
            if stated:
                rec["effective_date"] = stated[0]["effective_date"]
                rec["date_basis"] = stated[0].get("date_basis", "stated")
                notes.append(f"effective date taken from {stated[0]['source_doc_id']}")
        # Only the model's date_conflict flag raises a conflict. Members often carry different dates for different
        # sub-provisions (see the prompt), so differing dates alone are just logged.
        if g.get("date_conflict"):
            rec["conflict_flag"] = True
            why = (g.get("date_conflict_note") or "").strip() or ("Sources give different dates: " + ", ".join(sorted(dates)) + ".")
            rec["conflict_note"] = ((rec["conflict_note"] + " ") if rec["conflict_note"] else "") + why
        elif len(dates) > 1:
            notes.append("members have different effective dates (" + ", ".join(sorted(dates)) + "); the model judged them separate provisions")
        # --- legal state ---
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
            # any other mix (for example enacted and failed) is a real disagreement: flag it for a person
            rec["conflict_flag"] = True
            rec["conflict_note"] = ((rec["conflict_note"] + " ") if rec["conflict_note"] else "") + "Sources disagree on legal status: " + ", ".join(sorted(states)) + "."
        # --- coverage: if the best source says nothing, borrow the fullest statement from another source ---
        # (the primary is chosen for its legal text, which may not say who is covered; a summary page may)
        if empty_cov(rec.get("coverage")):
            richer = [m for m in others if not empty_cov(m.get("coverage"))]
            if richer:
                best = max(richer, key=lambda m: len(m["coverage"].get("all_of", [])) + len(m["coverage"].get("exemptions", [])))
                rec["coverage"] = best["coverage"]
                rec["coverage_conditions"] = best["coverage_conditions"]
                rec["exemptions"] = best["exemptions"]
                notes.append(f"coverage taken from {best['source_doc_id']}")
        # --- evidence: every non-primary member stays as a supporting source, so no quote or link is lost in the merge ---
        rec["supporting_sources"] = [{
            "doc_id": m["source_doc_id"], "source_url": m["source_url"], "retrieved_at": m["retrieved_at"],
            "citation_as_extracted": m["citation"], "quoted_span": m["quoted_span"], "effective_date": m["effective_date"],
        } for m in others]
        # recomputed: the legal state and the date above may have changed since the primary was extracted
        rec["status"] = status_at(rec["legal_state"], rec["effective_date"], QUERY_DATE)
        # relations are resolved in the pass below, once every group has its new id
        rec["_relations"] = {"yields_to": g.get("yields_to", []), "conflicts_with": g.get("conflicts_with", []),
                             "note": (g.get("interaction_note") or "").strip(), "self": new_id[g["primary_id"]]}
        out.append(rec)
        log.append({"new_id": new_id[g["primary_id"]], "primary": g["primary_id"], "members": rec["merged_from"],
                    "citation": rec["citation"], "reason": g.get("reason", ""), "adjustments": notes})
    # --- relations -> ids. A second pass, because a target may be a group that comes later in the list. ---
    # Links that break the jurisdiction rules are dropped and logged: a wrong yields_to would mark a rule
    # "superseded" at addresses where it still applies.
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
        # a conflict also needs the same state; links across states are dropped like bad yields_to links
        for x in rel["conflicts_with"]:
            t = to_new.get(x)
            if not t or t == me:
                continue
            if state_of(mine) == state_of(by_id[x]["jurisdiction"]):
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
    # --- symmetric conflict flags: a possible conflict is flagged on BOTH rules, so the warning shows whether a
    # reader opens the state rule or the local one ---
    ids = {new_id[g["primary_id"]]: r for g, r in zip(groups, out)}
    for rid, rec in list(ids.items()):
        for other in rec["_conflicts_with"]:
            for a, b in ((rid, other), (other, rid)):
                t = ids[a]
                t["conflict_flag"] = True
                note = f"Possible conflict with {ids[b]['citation']} ({b}); needs human review."
                if note not in (t["conflict_note"] or ""):
                    t["conflict_note"] = ((t["conflict_note"] + " ") if t["conflict_note"] else "") + note
    # `out` was built in the order of `groups`, so zip pairs each record with its group
    for rec, g in zip(out, groups):
        rec["team_rule_id"] = new_id[g["primary_id"]]
        rec.pop("merged_from_ids", None)
    return out, log


def submission_view(rec: dict) -> dict:
    """The fields of a rule that go into the submission file rules.json.
    overrides and supporting_sources default to [] and conflict_flag to False, so they are never null."""
    d = {k: rec.get(k) for k in SUBMISSION_FIELDS}
    d["overrides"] = rec.get("overrides") or []
    d["supporting_sources"] = rec.get("supporting_sources") or []
    d["conflict_flag"] = bool(rec.get("conflict_flag"))
    return d


def main(argv=None) -> int:
    """CLI: ask the model for groups (cached), repair and merge them, write the three output files, print a summary. Returns 0."""
    use_utf8_console()
    ap = argparse.ArgumentParser(description="Consolidate extracted rules into one record per legal instrument")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--thinking", choices=["off", "adaptive"], default="off")
    ap.add_argument("--refresh", action="store_true", help="ignore the cached answer and ask again")
    args = ap.parse_args(argv)
    THINKING["mode"] = args.thinking

    src = OUT / "extracted.json"
    if not src.exists():
        sys.exit("outputs/extracted.json not found. Run  python src\\extract.py  first.")
    data = json.loads(src.read_text(encoding="utf-8"))
    records = data["rules"]
    source_types = {r["doc_id"]: r.get("source_type", "") for r in load_manifest()}
    # Restating "every id exactly once" next to the data helps, but repair_groups does not trust it.
    user = ("Records to consolidate (one JSON object per line):\n" + compact(records, source_types) +
            f"\n\nThere are {len(records)} records. Every id must appear in exactly one group's member_ids.")
    payload = cached_structured_call(cache_dir=CACHE_DIR, system=SYSTEM, schema=SCHEMA, user=user, model=args.model,
                                     label=f"consolidate {len(records)} records", list_key="groups", refresh=args.refresh)

    groups, repairs = repair_groups(payload["result"]["groups"], records)
    merged, log = build(records, groups)
    OUT.mkdir(exist_ok=True)
    # rules_consolidated.json keeps the internal fields the lookup needs; rules.json is the trimmed submission view.
    (OUT / "rules_consolidated.json").write_text(json.dumps({
        "query_date": QUERY_DATE, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "outputs/extracted.json", "rules": merged}, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "rules.json").write_text(json.dumps({"rules": [submission_view(r) for r in merged]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "consolidation_log.json").write_text(json.dumps({"model_notes": payload["result"].get("notes", ""), "repairs": repairs, "groups": log},
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
    print_matrix(merged)
    print("\nWrote outputs/rules.json (submission), outputs/rules_consolidated.json, outputs/consolidation_log.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
