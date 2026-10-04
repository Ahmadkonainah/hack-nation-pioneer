"""
Module B engine - which rules apply at one address on one date.

Three-valued logic: every test is True, False or None (unknown). A rule covers an address when its
conditions are all True and no exemption is fully True. If a missing fact could change the answer, the
result is "unknown". Nothing is guessed except two assumptions that are written into every explanation:
  * the sample parcels are assessor-classified multifamily housing (so "hotel / dormitory / care facility"
    exemptions are ruled out unless the parcel's own description names them), and
  * special status (deed-restricted, subsidised, Section 8 ...) cannot be seen in parcel data, so it is listed
    as "not checked" and never blocks an answer when it is only an exemption.

Pure functions, no I/O: the same logic is ported to web/engine.js and the two are compared by tests.
"""
from __future__ import annotations

import re
from datetime import date

QUERY_DATE = "2026-10-01"
RESULT_ORDER = ["applies", "superseded", "not_yet_effective", "pending", "unknown"]

# --------------------------------------------------------------------------------------
# Dates and facts
# --------------------------------------------------------------------------------------


def parse_date(s):
    m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", (s or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:
        return None


def minus_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year - n)
    except ValueError:                      # 29 Feb
        return d.replace(year=d.year - n, day=28)


def status_at(legal_state, effective_date, as_of):
    if legal_state == "failed":
        return "failed"
    if legal_state == "pending":
        return "pending"
    eff, asof = parse_date(effective_date), parse_date(as_of)
    if eff and asof and asof < eff:
        return "not_yet_effective"
    return "in_force"


def to_int(x):
    try:
        v = int(float(str(x).strip()))
        return v
    except (ValueError, TypeError):
        return None


def units_from_text(desc: str, code: str, dataset: str):
    """Unit-count bounds read from the parcel's own use description when the units column is empty.
    Returns (lo, hi, basis) with hi None = no upper bound, or None."""
    t = f"{desc or ''} {code or ''}".lower()
    d = (desc or "").lower()
    m = re.search(r"(\d+)\s*(?:to|-)\s*(\d+)\s*[- ]?units?", d)
    if m:
        return int(m.group(1)), int(m.group(2)), f"use description '{desc}'"
    m = re.search(r"(\d+)\s*[- ]?\s*units?\s*or\s*(more|less)", d)
    if m:
        n = int(m.group(1))
        return (n, None, f"use description '{desc}'") if m.group(2) == "more" else (1, n, f"use description '{desc}'")
    m = re.search(r"(\d+)\s*\+", d)
    if m:
        return int(m.group(1)), None, f"use description '{desc}'"
    m = re.search(r">\s*(\d+)[- ]?unit", d)
    if m:
        return int(m.group(1)) + 1, None, f"use description '{desc}'"
    m = re.search(r"(\d+)-(\d+)-unit", d)
    if m:
        return int(m.group(1)), int(m.group(2)), f"use description '{desc}'"
    if re.search(r"five or more", d):
        return 5, None, f"use description '{desc}'"
    if (code or "").strip().upper() == "4C" and "njogis" in (dataset or "").lower():
        return 5, None, "New Jersey property class 4C (apartments, 5 or more units)"
    return None


def building_facts(row: dict) -> dict:
    y = to_int(row.get("year_built"))
    if y is not None and not (1700 <= y <= 2100):
        y = None
    n = to_int(row.get("units"))
    u = units_from_text(row.get("use_description"), row.get("use_code"), row.get("source_dataset"))
    if n is not None and n > 0:
        lo, hi, basis = n, n, "parcel units column"
        if u and (n < u[0] or (u[1] is not None and n > u[1])):
            lo, hi, basis = None, None, f"units column ({n}) conflicts with {u[2]}"      # contradictory data: treat as unknown
    else:
        lo, hi, basis = u if u else (None, None, None)
    return {"year_built": y, "units_lo": lo, "units_hi": hi, "units_basis": basis,
            "use_code": row.get("use_code") or "", "use_description": row.get("use_description") or ""}


# --------------------------------------------------------------------------------------
# Coverage conditions from the Module A shape (fallback when enrich.py was not run)
# --------------------------------------------------------------------------------------


def coverage_from_v1(rule: dict) -> dict:
    cov = rule.get("coverage") or {}

    def conv(c):
        fact = c.get("fact")
        op = c.get("op") or "is"
        val = str(c.get("value") or "")
        yrs = int(c.get("years_before_as_of") or 0)
        if fact in ("first_occupancy_date", "units", "owner_occupied"):
            if fact == "owner_occupied":
                op, val = "is", "true"
            return {"fact": fact, "op": op, "value": val, "years_before_as_of": yrs, "text": c.get("text", "")}
        return {"fact": "status_not_in_data", "op": "is", "value": val, "years_before_as_of": 0, "text": c.get("text", "")}

    conds = [conv(c) for c in cov.get("all_of", [])]
    exs = [{"label": e.get("label", ""), "conditions": [conv(c) for c in e.get("all_of", [])]} for e in cov.get("exemptions", [])]
    return {"scope": "conditional" if conds else "universal", "conditions": conds, "exemptions": exs,
            "support": "Module A conditions (coverage step not run)", "reasoning": ""}


def coverage_of(rule: dict) -> dict:
    return rule.get("coverage_v2") or coverage_from_v1(rule)


# --------------------------------------------------------------------------------------
# Condition evaluation (True / False / None)
# --------------------------------------------------------------------------------------

def t_and(vals):
    if any(v is False for v in vals):
        return False
    if any(v is None for v in vals):
        return None
    return True


def t_or(vals):
    if any(v is True for v in vals):
        return True
    if any(v is None for v in vals):
        return None
    return False


def t_not(v):
    return None if v is None else (not v)


def cmp_date_year(op: str, y: int, T: date):
    lo, hi = date(y, 1, 1), date(y, 12, 31)
    if op == "on_or_before":
        return True if hi <= T else (False if lo > T else None)
    if op == "before":
        return True if hi < T else (False if lo >= T else None)
    if op == "after":
        return True if lo > T else (False if hi <= T else None)
    if op == "on_or_after":
        return True if lo >= T else (False if hi < T else None)
    return None


def cmp_units(op: str, lo, hi, v: int):
    if lo is None:
        lo = 1
    if op == "<=":
        return True if (hi is not None and hi <= v) else (False if lo > v else None)
    if op == "<":
        return True if (hi is not None and hi < v) else (False if lo >= v else None)
    if op == ">=":
        return True if lo >= v else (False if (hi is not None and hi < v) else None)
    if op == ">":
        return True if lo > v else (False if (hi is not None and hi <= v) else None)
    if op == "==":
        if lo == hi == v:
            return True
        return False if (v < lo or (hi is not None and v > hi)) else None
    if op == "!=":
        return t_not(cmp_units("==", lo, hi, v))
    return None


OP_WORDS = {"on_or_before": "on or before", "before": "before", "after": "after", "on_or_after": "on or after"}


def eval_cond(c: dict, facts: dict, as_of: str, in_exemption: bool, ref):
    """Return (value, kind, phrase). kind: 'fact' | 'caveat' | 'note'. `ref(rule_id)` gives another rule's coverage value."""
    fact, op, val = c["fact"], c["op"], str(c.get("value") or "")
    if fact == "first_occupancy_date":
        yrs = int(c.get("years_before_as_of") or 0)
        T = minus_years(parse_date(as_of), yrs) if yrs > 0 else parse_date(val)
        if T is None:
            return None, "fact", "a construction-date test could not be read"
        word = OP_WORDS.get(op, op)
        need = f"first occupied {word} {T.isoformat()}"
        y = facts["year_built"]
        if y is None:
            return None, "fact", f"year built is missing in the parcel data (needed: {need})"
        v = cmp_date_year(op, y, T)
        if v is None:
            return None, "fact", f"built {y}, the same year as the {T.isoformat()} cut-off, and the certificate-of-occupancy date is not in the data (needed: {need})"
        return v, "fact", f"built {y}: {'meets' if v else 'does not meet'} the test ({need})"
    if fact == "units":
        lo, hi = facts["units_lo"], facts["units_hi"]
        n = int(val) if re.fullmatch(r"\d+", val) else None
        if n is None:
            return None, "fact", "a unit-count test could not be read"
        need = f"units {op} {n}"
        if lo is None:
            return None, "fact", f"{facts['units_basis'] or 'unit count is missing in the parcel data'} (needed: {need})"
        v = cmp_units(op, lo, hi, n)
        have = f"{lo} units" if lo == hi else (f"{lo} or more units" if hi is None else f"{lo} to {hi} units")
        if v is None:
            return None, "fact", f"{have} ({facts['units_basis']}) is not enough to decide the test {need}"
        return v, "fact", f"{have} ({facts['units_basis']}): {'meets' if v else 'does not meet'} the test ({need})"
    if fact == "owner_occupied":
        return None, "fact", "whether an owner lives in the building is not in the parcel data"
    if fact == "use_type":
        words = {w for w in re.findall(r"[a-z]{5,}", f"{val} {c.get('text', '')}".lower())}
        have = f"{facts['use_description']} {facts['use_code']}".lower()
        if any(w in have for w in words):
            return None, "fact", f"the parcel's use description ('{facts['use_description']}') may match: {val}"
        if in_exemption:
            return False, "caveat", f"the building is not a {val}"
        return None, "fact", f"whether the building is a {val} is not in the parcel data"
    if fact == "status_not_in_data":
        if in_exemption:
            return False, "caveat", f"{val or c.get('text', '')}"
        return None, "status", f"requires {val or c.get('text', '')}, which is not in the parcel data"
    if fact == "tenancy":
        if in_exemption:
            return False, "caveat", f"{val or c.get('text', '')} (depends on the tenancy)"
        return True, "note", f"applies to tenancies where: {val or c.get('text', '')}"
    if fact == "rule_coverage":
        other = ref(val)
        v = other if op == "is" else t_not(other)
        title = ref.title(val)
        if v is None:
            return None, "fact", f"depends on whether {title} covers this building, which is not certain"
        return v, "fact", f"{'covered' if other else 'not covered'} by {title}"
    return None, "fact", "an unreadable test"


def eval_coverage(cov: dict, facts: dict, as_of: str, ref):
    """Return dict(value, true_facts, false_facts, blockers, caveats, notes, exempt_by)."""
    out = {"value": None, "true_facts": [], "false_facts": [], "blockers": [], "caveats": [], "notes": [], "exempt_by": None,
           "ruled_out": [], "hard": False}
    scope = cov.get("scope", "conditional")
    if scope == "undefined":
        out["blockers"].append("the source does not say which buildings this rule covers")
        out["hard"] = True
        return out
    cvals = []
    for c in cov.get("conditions", []):
        v, kind, ph = eval_cond(c, facts, as_of, False, ref)
        if kind == "note":
            out["notes"].append(ph)
            continue
        cvals.append(v)
        if v is None and kind != "status":
            out["hard"] = True               # a building fact is missing (a special status the data cannot show is not "hard")
        (out["true_facts"] if v is True else out["false_facts"] if v is False else out["blockers"]).append(ph)
    covered = t_and(cvals) if cvals else True
    evals = []
    for ex in cov.get("exemptions", []):
        label = ex.get("label") or "an exemption"
        ev, trues, falses, blocks, cav = [], [], [], [], []
        conds = ex.get("conditions", [])
        if not conds:
            evals.append((None, label, [], [], [f"{label}: its test is not stated"], []))
            out["hard"] = True
            continue
        for c in conds:
            v, kind, ph = eval_cond(c, facts, as_of, True, ref)
            if kind == "note":
                continue
            ev.append(v)
            if v is True:
                trues.append(ph)
            elif v is False and kind == "caveat":
                cav.append(ph)
            elif v is False:
                falses.append(ph)
            else:
                blocks.append(ph)
        evals.append((t_and(ev) if ev else None, label, trues, falses, blocks, cav))
    exempt = t_or([e[0] for e in evals]) if evals else False
    for v, label, trues, falses, blocks, cav in evals:
        if v is True:
            out["exempt_by"] = label
            out["true_facts"] += trues
        elif v is False:
            # an exemption that cannot apply because of a known fact is worth saying; one that is only assumed away is a caveat
            if falses:
                out["ruled_out"].append(label)
            out["caveats"] += cav if not falses else []
        else:
            out["blockers"] += [f"{label}: {b}" for b in (blocks or [])] or [label]
            out["caveats"] += cav
            out["hard"] = True
    out["value"] = t_and([covered, t_not(exempt)])
    return out


# --------------------------------------------------------------------------------------
# One address, one date
# --------------------------------------------------------------------------------------

def short(s: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def city_for(res: dict):
    """(city label or None, basis, unverified_city_label)"""
    if res.get("matched"):
        return res.get("legal_city"), "census_geocoder", None
    fb = res.get("fallback_city")
    if fb and res.get("fallback_reason") == "city-specific dataset":
        return fb, "city_dataset", None
    return None, "unresolved", fb


def lookup_address(addr: dict, res: dict, rules: list[dict], as_of: str = QUERY_DATE) -> dict:
    """addr = a sample_addresses row; res = its resolved_addresses record; rules = enriched rules."""
    facts = building_facts(addr)
    state = addr["state"].strip()
    city, basis, unverified = city_for(res)
    by_id = {r["team_rule_id"]: r for r in rules}
    cands = [r for r in rules if r["jurisdiction"] == state or (city and r["jurisdiction"] == city)
             or (unverified and r["jurisdiction"] == unverified)]
    memo, stack = {}, set()
    partners_of = {}                      # conflicts are symmetric
    for r in rules:
        for o in r.get("_conflicts_with") or []:
            partners_of.setdefault(r["team_rule_id"], set()).add(o)
            partners_of.setdefault(o, set()).add(r["team_rule_id"])

    def cov_value(rid):
        if rid in memo:
            return memo[rid]["value"]
        if rid in stack or rid not in by_id:
            return None
        stack.add(rid)
        r = by_id[rid]
        applies_here = r["jurisdiction"] == state or (city and r["jurisdiction"] == city)
        if not applies_here:
            memo[rid] = {"value": False}          # a rule of another place never covers this address
        else:
            memo[rid] = eval_coverage(coverage_of(r), facts, as_of, ref)
        stack.discard(rid)
        return memo[rid]["value"]

    class Ref:
        def __call__(self, rid):
            return cov_value(rid)

        def title(self, rid):
            r = by_id.get(rid)
            return f"{r['citation'] or r['title']} ({rid})" if r else rid

    ref = Ref()
    base = {}
    for r in cands:
        rid = r["team_rule_id"]
        st = status_at(r["legal_state"], r["effective_date"], as_of)
        if st == "failed":
            continue
        unverified_city = bool(unverified and r["jurisdiction"] == unverified)
        ev = eval_coverage(coverage_of(r), facts, as_of, ref) if st != "pending" else None
        if unverified_city:
            if st == "in_force":
                result = "unknown"
            elif st == "not_yet_effective":
                result = "not_yet_effective"
            else:
                result = "pending"
        elif st == "pending":
            result = "pending"
        elif st == "not_yet_effective":
            if ev["value"] is False:
                continue
            result = "not_yet_effective"
        else:
            if ev["value"] is False:
                continue
            result = "applies" if ev["value"] is True else "unknown"
        base[rid] = {"rule": r, "status": st, "result": result, "ev": ev, "unverified_city": unverified_city, "by": None}

    # a rule yields to the rules listed in `overrides` when one of them applies here (judged on the results before any yielding)
    orig = {rid: b["result"] for rid, b in base.items()}
    for rid, b in base.items():
        if b["result"] not in ("applies", "unknown"):
            continue
        for oid in b["rule"].get("overrides") or []:
            o = base.get(oid)
            if not o:
                continue
            if orig[oid] == "applies":
                b["result"], b["by"] = "superseded", oid
                break
            if orig[oid] == "unknown" and b["result"] == "applies":
                hard = o["ev"] is None or o["unverified_city"] or o["ev"].get("hard", True)
                if hard:
                    b["result"], b["by"] = "unknown", oid
                else:                          # the other rule hinges only on a status the data cannot show: keep our answer, say so
                    b.setdefault("may_yield", []).append(oid)

    entries = []
    for rid in sorted(base):
        b = base[rid]
        r = b["rule"]
        partners = sorted(p for p in partners_of.get(rid, ()) if p in base)
        flag = bool(partners) or (bool(r.get("conflict_flag")) and not partners_of.get(rid))
        entries.append(make_entry(r, b, base, facts, partners, flag, as_of, basis, city, state))
    return {"address_id": addr["address_id"], "as_of": as_of, "city": city, "city_basis": basis, "facts": facts, "entries": entries}


def cap1(s: str) -> str:
    return s[:1].upper() + s[1:]


def join(items, n=3, width=140):
    items = [short(i, width) for i in items if i]
    return "; ".join(items[:n])


def facts_line(facts):
    """what the parcel data says about this building, in a few words"""
    bits = []
    if facts.get("year_built"):
        bits.append(f"built {facts['year_built']}")
    lo, hi = facts.get("units_lo"), facts.get("units_hi")
    if lo is not None:
        bits.append(f"{lo} units" if lo == hi else (f"at least {lo} units" if hi is None else f"{lo} to {hi} units"))
    return ", ".join(bits)


def head_of(r) -> str:
    """how a rule is named inside an explanation: plain sentence plus citation, or the old 'rule (citation)' form"""
    cite = r["citation"] or r["title"]
    where = "Statewide rule" if r["level"] == "state" else f"{r['jurisdiction']} rule"
    plain = re.sub(r"\s+", " ", (r.get("plain_en") or "")).strip().rstrip(". ")
    return f"{plain} ({where}: {cite})" if plain else f"{where} ({cite})"


def make_entry(r, b, base, facts, partners, flag, as_of, basis, city, state):
    rid, res, ev = r["team_rule_id"], b["result"], b["ev"]
    cite = r["citation"] or r["title"]
    where = head_of(r)
    caveats = list(ev["caveats"]) if ev else []
    if res == "applies":
        why = join(ev["true_facts"] + ev["false_facts"], 2)
        text = f"Applies. {where}. "
        if why:
            text += cap1(why) + "."
        elif not ev["ruled_out"]:
            text += "No building-based coverage test applies."
        if ev["ruled_out"]:
            fl = facts_line(facts)
            text += (f" Parcel data: {fl}." if fl else "") + " Exemptions ruled out by that data: " + join(ev["ruled_out"], 3, 80) + "."
    elif res == "superseded":
        o = base[b["by"]]["rule"]
        text = f"Superseded. {where}. Here it gives way to {o['citation'] or o['title']} ({o['team_rule_id']}), which applies at this address."
    elif res == "not_yet_effective":
        text = f"Not yet effective. {where}. It takes effect {r['effective_date'] or 'on a date not stated'}; the as-of date is {as_of}."
        if ev and ev["value"] is None:
            text += " Coverage would depend on: " + join(ev["blockers"], 2) + "."
    elif res == "pending":
        text = f"Pending. {where}. It has not been enacted, so it is not in force."
    else:
        if b["unverified_city"]:
            text = f"Unknown. {where}. This address could not be placed in {r['jurisdiction']} by the geocoder, so this city rule cannot be confirmed."
        elif b["by"]:
            o = base[b["by"]]["rule"]
            text = f"Unknown. {where}. It would apply unless {o['citation'] or o['title']} ({o['team_rule_id']}) does, and that is not certain here."
            ob = base[b["by"]]["ev"]
            if ob and ob["blockers"]:
                text += " " + cap1(join(ob["blockers"], 2)) + "."
        else:
            why = join(ev["blockers"], 3)
            text = f"Unknown. {where}. Coverage depends on: {why}."
            if ev["ruled_out"]:
                text += " Exemptions ruled out by the parcel data: " + join(ev["ruled_out"], 3, 80) + "."
    if res == "applies" and caveats:
        text += " Not checked from parcel data: " + join(caveats, 3) + "."
    if res == "applies" and b.get("may_yield"):
        for oid in b["may_yield"]:
            o = base[oid]
            ob = o["ev"]["blockers"] if o["ev"] else []
            text += f" Could yield to {o['rule']['citation'] or o['rule']['title']} ({oid}), which cannot be checked here: {join(ob, 1, 160)}."
    if ev and ev["notes"] and res in ("applies", "unknown"):
        text += " " + cap1(ev["notes"][0]) + "."
    if basis == "city_dataset" and r["level"] == "city":
        text += " City taken from the parcel dataset (not geocoded)."
    if flag:
        text += " Flagged: possible conflict with " + (", ".join(f"{base[p]['rule']['citation'] or base[p]['rule']['title']} ({p})" for p in partners) if partners else "another rule") + "; needs human review."
    if res == "applies":
        conf = "high" if not (caveats or b.get("may_yield")) else "medium"
    elif res == "unknown":
        conf = "low"
    else:
        conf = "high"
    if (r.get("confidence") or 1) < 0.7 and conf == "high":
        conf = "medium"
    return {"team_rule_id": rid, "result": res, "explanation": text, "conflict_flag": flag,
            "_confidence": conf, "_caveats": caveats, "_partners": partners, "_superseded_by": b["by"] if res in ("superseded", "unknown") else None,
            "_may_yield": list(b.get("may_yield") or [])}
