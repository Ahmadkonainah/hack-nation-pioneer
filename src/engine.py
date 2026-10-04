"""
Module B engine - which rules apply at one address on one date.

Three-valued logic: every test is True, False or None (unknown). A rule covers an address when its
conditions are all True and no exemption is fully True. If a missing fact could change the answer, the
result is "unknown". Nothing is guessed except two assumptions that are written into every explanation:
  * the sample parcels are assessor-classified multifamily housing (so "hotel / dormitory / care facility"
    exemptions are ruled out unless the parcel's own description names them), and
  * special status (deed-restricted, subsidised, Section 8 ...) cannot be seen in parcel data, so it is listed
    as "not checked" and never blocks an answer when it is only an exemption.

Each rule gets one result per address: applies, superseded (another rule that applies here takes precedence),
not_yet_effective, pending (a bill) or unknown. Failed rules, and rules that clearly do not cover the building,
are left out. A person may also type facts ("what if"); they override the parcel facts for that one lookup, and
typed units and owner status are labelled "entered by you" in the explanation.

Pure functions, no I/O: the same logic is ported to web/engine.js and the two are compared by tests.
The explanation text is part of the answer, so both engines must give identical results AND identical wording
(src/parity_test.py checks this). Change one engine and you must change the other.
"""
from __future__ import annotations

import re
from datetime import date  # noqa: F401  (type of the dates returned by parse_date)

from dates import QUERY_DATE, minus_years, parse_date, status_at  # noqa: F401  (re-exported: lookup.py and tests use G.parse_date etc.)

# Every possible result, in the order lookup.py prints its summary columns. engine.js exports the same list.
RESULT_ORDER = ["applies", "superseded", "not_yet_effective", "pending", "unknown"]

# --------------------------------------------------------------------------------------
# Dates and facts
# --------------------------------------------------------------------------------------


def to_int(x):
    """Read a whole number from text or a number ("1975" and "1975.0" both work; decimals are cut). None if empty or not numeric."""
    try:
        v = int(float(str(x).strip()))
        return v
    except (ValueError, TypeError):
        return None


def units_from_text(desc: str, code: str, dataset: str):
    """Unit-count bounds read from the parcel's own use description when the units column is empty.
    Returns (lo, hi, basis) or None. hi None means no upper bound ("five or more" is (5, None)).
    basis is plain English and is shown in explanations to say where the bounds came from."""
    # The description is read first because it is the most specific source. Every pattern gives a bound, never a
    # single guessed count, so a test such as "units <= 4" stays unknown instead of passing or failing on a guess.
    d = (desc or "").lower()
    m = re.search(r"(\d+)\s*(?:to|-)\s*(\d+)\s*[- ]?units?", d)
    if m:
        return int(m.group(1)), int(m.group(2)), f"use description '{desc}'"
    # "N units or less" runs from 1 to N: a building has at least one unit.
    m = re.search(r"(\d+)\s*[- ]?\s*units?\s*or\s*(more|less)", d)
    if m:
        n = int(m.group(1))
        return (n, None, f"use description '{desc}'") if m.group(2) == "more" else (1, n, f"use description '{desc}'")
    m = re.search(r"(\d+)\s*\+", d)
    if m:
        return int(m.group(1)), None, f"use description '{desc}'"
    # "> 4 units" means strictly more than 4, so the lower bound is 5.
    m = re.search(r">\s*(\d+)[- ]?unit", d)
    if m:
        return int(m.group(1)) + 1, None, f"use description '{desc}'"
    m = re.search(r"(\d+)-(\d+)-unit", d)
    if m:
        return int(m.group(1)), int(m.group(2)), f"use description '{desc}'"
    if re.search(r"five or more", d):
        return 5, None, f"use description '{desc}'"
    # New Jersey property class 4C means apartments of five or more units. The code is only read for the NJ
    # dataset, where we know what it means.
    if (code or "").strip().upper() == "4C" and "njogis" in (dataset or "").lower():
        return 5, None, "New Jersey property class 4C (apartments, 5 or more units)"
    return None


def building_facts(row: dict) -> dict:
    """Turn one parcel row into the facts the engine tests.
    Returns year_built (None if missing), units_lo / units_hi (a range; hi None = no upper bound, lo None = unknown),
    units_basis (plain-English source of the range, shown in explanations), use_code and use_description.
    There is no owner_occupied key: parcel data never has it, only apply_what_if can add it."""
    y = to_int(row.get("year_built"))
    # Years outside 1700-2100 are data errors (for example 0 or 9999). Treat them as missing, not as a very old building.
    if y is not None and not (1700 <= y <= 2100):
        y = None
    n = to_int(row.get("units"))
    u = units_from_text(row.get("use_description"), row.get("use_code"), row.get("source_dataset"))
    # A count of 0 or less is a data gap, not a fact: a rental building has at least one unit.
    if n is not None and n > 0:
        lo, hi, basis = n, n, "parcel units column"
        # The column and the use text disagree (for example 2 units, but class 4C means 5 or more). We cannot tell which
        # is right, so the unit count becomes unknown and the basis says why. Picking one would hide the doubt.
        if u and (n < u[0] or (u[1] is not None and n > u[1])):
            lo, hi, basis = None, None, f"units column ({n}) conflicts with {u[2]}"      # contradictory data: treat as unknown
    else:
        lo, hi, basis = u if u else (None, None, None)
    return {"year_built": y, "units_lo": lo, "units_hi": hi, "units_basis": basis,
            "use_code": row.get("use_code") or "", "use_description": row.get("use_description") or ""}


def apply_what_if(facts: dict, w: dict | None) -> dict:
    """Layer facts a person typed in ("what if I know more about this building") over the parcel facts. Returns a copy; parcel facts are never changed.
    w may hold: year_built (int), units (int), owner_occupied (True/False). Missing, None or out-of-range values leave the parcel fact as it is."""
    # Typed facts override parcel data because the person may know the building better, but they stay visibly theirs:
    # typed units get the basis "entered by you", and owner status can only ever come from a person. They apply to this
    # one call and are not written anywhere.
    f = dict(facts)
    if not w:
        return f
    # Same year range as building_facts, so a typo such as 19750 is ignored instead of giving a wrong answer.
    y = to_int(w.get("year_built"))
    if y is not None and 1700 <= y <= 2100:
        f["year_built"] = y
    # A typed unit count is exact (lo == hi) and replaces any parcel range, including a conflict between the two parcel sources.
    n = to_int(w.get("units"))
    if n is not None and n > 0:
        f["units_lo"], f["units_hi"], f["units_basis"] = n, n, "entered by you"
    # Only a real True or False counts. Anything else leaves owner-occupancy unknown; it is never guessed.
    if w.get("owner_occupied") in (True, False):
        f["owner_occupied"] = w["owner_occupied"]
    return f


# --------------------------------------------------------------------------------------
# Coverage conditions from the Module A shape (fallback when enrich.py was not run)
# --------------------------------------------------------------------------------------


def coverage_from_v1(rule: dict) -> dict:
    """Convert Module A's rough coverage (all_of / exemptions) into the engine's condition shape.
    Only used when enrich.py did not write coverage_v2. Only year built, units and owner-occupancy can be tested
    from parcel data; every other condition becomes status_not_in_data."""
    cov = rule.get("coverage") or {}

    def conv(c):
        """One Module A condition in the engine's shape (fact, op, value, years_before_as_of, text)."""
        fact = c.get("fact")
        op = c.get("op") or "is"
        val = str(c.get("value") or "")
        yrs = int(c.get("years_before_as_of") or 0)
        if fact in ("first_occupancy_date", "units", "owner_occupied"):
            # Module A gives free text here ("true", "corporation" ...). The engine only asks "does an owner live in the
            # building?", so the test is fixed to "is true".
            if fact == "owner_occupied":
                op, val = "is", "true"
            return {"fact": fact, "op": op, "value": val, "years_before_as_of": yrs, "text": c.get("text", "")}
        # owner_type, "other" and anything unrecognised cannot be tested from parcel data. status_not_in_data makes it
        # unknown, or "not checked" when it sits inside an exemption.
        return {"fact": "status_not_in_data", "op": "is", "value": val, "years_before_as_of": 0, "text": c.get("text", "")}

    conds = [conv(c) for c in cov.get("all_of", [])]
    exs = [{"label": e.get("label", ""), "conditions": [conv(c) for c in e.get("all_of", [])]} for e in cov.get("exemptions", [])]
    # No conditions means the rule covers every address in its place (exemptions are still tested).
    return {"scope": "conditional" if conds else "universal", "conditions": conds, "exemptions": exs,
            "support": "Module A conditions (coverage step not run)", "reasoning": ""}


def coverage_of(rule: dict) -> dict:
    """The testable coverage of a rule: the enriched coverage_v2 when present, else the converted Module A conditions."""
    return rule.get("coverage_v2") or coverage_from_v1(rule)


# --------------------------------------------------------------------------------------
# Condition evaluation (True / False / None)
# --------------------------------------------------------------------------------------

def t_and(vals):
    """AND for True / False / None. One False settles it even if other parts are unknown; otherwise any unknown keeps it unknown. Empty list is True."""
    if any(v is False for v in vals):
        return False
    if any(v is None for v in vals):
        return None
    return True


def t_or(vals):
    """OR for True / False / None. One True settles it even if other parts are unknown; otherwise any unknown keeps it unknown. Empty list is False."""
    if any(v is True for v in vals):
        return True
    if any(v is None for v in vals):
        return None
    return False


def t_not(v):
    """NOT for True / False / None. Unknown stays unknown: not knowing a fact says nothing about its opposite."""
    return None if v is None else (not v)


def cmp_date_year(op: str, y: int, T: date):
    """Compare a year built with a cut-off date T. Returns True, False or None (unknown).
    The data holds only the year, so the building could date from any day between 1 Jan and 31 Dec of y. The answer is
    True or False only when the whole year lies on one side of T; None when the year straddles T. Then only the real
    certificate-of-occupancy date could decide, and we do not have it."""
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
    """Compare a unit range [lo, hi] with v in a test such as "units >= v". Returns True, False or None (unknown).
    hi None means no upper bound; a missing lo counts as 1. True or False only when every count in the range gives
    the same answer, so "5 or more" passes ">= 5" but leaves "<= 10" unknown."""
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


# Words for the first_occupancy_date operators, used in explanation text (engine.js has the same table).
OP_WORDS = {"on_or_before": "on or before", "before": "before", "after": "after", "on_or_after": "on or after"}


def eval_cond(c: dict, facts: dict, as_of: str, in_exemption: bool, ref):
    """Test one coverage condition against the building facts. Returns (value, kind, phrase).
    value is True, False or None (unknown). phrase is the plain-English reason shown in the explanation.
    kind tells eval_coverage how to treat it:
      'fact'    a real test on a building fact; None means that fact is missing
      'caveat'  False only by assumption (an exemption we cannot check); reported as "not checked", never blocks
      'status'  a special status the parcel data cannot show; None, but not a missing building fact
      'note'    no truth value, just text about the tenancy
    in_exemption is True when the condition sits inside an exemption. ref(rule_id) gives another rule's coverage
    value here, and ref.title(rule_id) names that rule."""
    fact, op, val = c["fact"], c["op"], str(c.get("value") or "")
    if fact == "first_occupancy_date":
        yrs = int(c.get("years_before_as_of") or 0)
        # A rolling test ("first occupied within the last 15 years") counts back from the as-of date, so its answer can
        # change from one date to the next. A fixed cut-off is used exactly as the rule states it.
        T = minus_years(parse_date(as_of), yrs) if yrs > 0 else parse_date(val)
        if T is None:
            return None, "fact", "a construction-date test could not be read"
        word = OP_WORDS.get(op, op)
        need = f"first occupied {word} {T.isoformat()}"
        # A missing year is unknown, never "old" or "new": a guess would silently decide who is covered.
        y = facts["year_built"]
        if y is None:
            return None, "fact", f"{facts.get('year_basis') or 'year built is missing in the parcel data'} (needed: {need})"
        v = cmp_date_year(op, y, T)
        # Built in the cut-off year: only the year is known, so the real date and the answer are unknown.
        if v is None:
            return None, "fact", f"built {y}, the same year as the {T.isoformat()} cut-off, and the certificate-of-occupancy date is not in the data (needed: {need})"
        return v, "fact", f"built {y}: {'meets' if v else 'does not meet'} the test ({need})"
    if fact == "units":
        lo, hi = facts["units_lo"], facts["units_hi"]
        n = int(val) if re.fullmatch(r"\d+", val) else None
        if n is None:
            return None, "fact", "a unit-count test could not be read"
        need = f"units {op} {n}"
        # No lower bound means no usable unit count. units_basis then holds the reason, for example a conflict
        # between the units column and the use description.
        if lo is None:
            return None, "fact", f"{facts['units_basis'] or 'unit count is missing in the parcel data'} (needed: {need})"
        v = cmp_units(op, lo, hi, n)
        have = f"{lo} units" if lo == hi else (f"{lo} or more units" if hi is None else f"{lo} to {hi} units")
        if v is None:
            return None, "fact", f"{have} ({facts['units_basis']}) is not enough to decide the test {need}"
        return v, "fact", f"{have} ({facts['units_basis']}): {'meets' if v else 'does not meet'} the test ({need})"
    if fact == "owner_occupied":
        oo = facts.get("owner_occupied")                      # only set when a person types it in (what-if); parcel data never has it
        if oo is None:
            return None, "fact", "whether an owner lives in the building is not in the parcel data"
        # The only source of this fact is a person typing it in, so the explanation always says "entered by you".
        # A value other than "false" reads as "an owner lives there"; any op other than "is" flips the test.
        want = val.strip().lower() != "false"
        v = (oo == want) if op == "is" else (oo != want)
        return v, "fact", f"{'an owner lives' if oo else 'no owner lives'} in the building (entered by you): {'meets' if v else 'does not meet'} the test (owner-occupied)"
    if fact == "use_type":
        # A word from the exemption (for example "hotel") found in the parcel's own use text means we cannot rule the
        # exemption out, so it is unknown. Only words of five letters or more are compared, to avoid matching short common words.
        words = {w for w in re.findall(r"[a-z]{5,}", f"{val} {c.get('text', '')}".lower())}
        have = f"{facts['use_description']} {facts['use_code']}".lower()
        if any(w in have for w in words):
            return None, "fact", f"the parcel's use description ('{facts['use_description']}') may match: {val}"
        # Not named in the parcel text. Inside an exemption we assume an ordinary multifamily parcel (see the module
        # docstring), so the exemption is ruled out as a caveat. As a coverage condition it stays unknown.
        if in_exemption:
            return False, "caveat", f"the building is not a {val}"
        return None, "fact", f"whether the building is a {val} is not in the parcel data"
    # Parcel data can never show deed-restricted, subsidised or similar status. As an exemption it is assumed False and
    # listed as "not checked"; letting it block would turn almost every answer into unknown. As a coverage
    # condition it stays unknown, because the rule only covers buildings with that status.
    if fact == "status_not_in_data":
        if in_exemption:
            return False, "caveat", f"{val or c.get('text', '')}"
        return None, "status", f"requires {val or c.get('text', '')}, which is not in the parcel data"
    # A tenancy test is about one lease, not the building, so it cannot narrow which buildings a rule covers.
    # In coverage conditions it is only a note; in an exemption it is assumed False and listed as "not checked".
    if fact == "tenancy":
        if in_exemption:
            return False, "caveat", f"{val or c.get('text', '')} (depends on the tenancy)"
        return True, "note", f"applies to tenancies where: {val or c.get('text', '')}"
    # "Covered by (or not subject to) rule X" is answered by evaluating X at this same address. ref() gives False for a
    # rule of another place and None for a rule that points back at itself, so loops end as unknown.
    if fact == "rule_coverage":
        other = ref(val)
        v = other if op == "is" else t_not(other)
        title = ref.title(val)
        if v is None:
            return None, "fact", f"depends on whether {title} covers this building, which is not certain"
        return v, "fact", f"{'covered' if other else 'not covered'} by {title}"
    # An unrecognised fact name must never count as true or false.
    return None, "fact", "an unreadable test"


def eval_coverage(cov: dict, facts: dict, as_of: str, ref):
    """Evaluate a rule's coverage at one address. value is True (covered), False (not covered) or None (unknown).
    The other keys explain it: true_facts / false_facts (tests that passed or failed), blockers (what is missing or
    undecided), caveats (exemptions assumed away, "not checked"), notes, exempt_by (label of an exemption that holds),
    ruled_out (exemptions excluded by a known fact).
    hard is True when a building fact is missing, an exemption cannot be decided, or the source gives no usable test.
    It is False when only a special status the data can never show is missing. lookup_address uses it to decide
    whether an unsure rule can hold back a rule that yields to it."""
    out = {"value": None, "true_facts": [], "false_facts": [], "blockers": [], "caveats": [], "notes": [], "exempt_by": None,
           "ruled_out": [], "hard": False}
    scope = cov.get("scope", "conditional")
    # The source says nothing about who is covered, so there is nothing to test. It counts as a real unknown (hard).
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
    # A rule with no testable conditions covers every building in its place.
    covered = t_and(cvals) if cvals else True
    # Each exemption is an AND of its own tests. The building is exempt if ANY exemption holds.
    evals = []
    for ex in cov.get("exemptions", []):
        label = ex.get("label") or "an exemption"
        ev, trues, falses, blocks, cav = [], [], [], [], []
        conds = ex.get("conditions", [])
        # An exemption with no stated test cannot be checked. It stays unknown; we do not assume it away.
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
            # An exemption excluded by a known fact (for example year built) is worth naming. One that is only assumed away
            # (a status the data cannot show) is a caveat. If a known fact already excludes it, the assumed parts no longer matter.
            if falses:
                out["ruled_out"].append(label)
            out["caveats"] += cav if not falses else []
        else:
            # The exemption might hold and we cannot tell. That keeps the whole answer unknown.
            out["blockers"] += [f"{label}: {b}" for b in (blocks or [])] or [label]
            out["caveats"] += cav
            out["hard"] = True
    # Covered AND NOT exempt. An unknown exemption keeps a covered rule unknown instead of letting it apply.
    out["value"] = t_and([covered, t_not(exempt)])
    return out


# --------------------------------------------------------------------------------------
# One address, one date
# --------------------------------------------------------------------------------------

def short(s: str, n: int = 60) -> str:
    """Collapse whitespace and cut the text to n characters, ending with an ellipsis if it was cut."""
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def city_for(res: dict):
    """Pick the city to trust for an address. Returns (city, basis, unverified_city).
    basis is 'census_geocoder' (the geocoder placed it; city is None if that is outside the cities we cover),
    'city_dataset' (not geocoded, but the parcel dataset belongs to one city, so we use it) or 'unresolved'.
    unverified_city is only set when unresolved: a guess from the postal city name. Its city rules are answered
    unknown, never applies."""
    # The geocoder's legal city wins over the postal city: postal city is not legal city (Dorchester is Boston).
    if res.get("matched"):
        return res.get("legal_city"), "census_geocoder", None
    fb = res.get("fallback_city")
    if fb and res.get("fallback_reason") == "city-specific dataset":
        return fb, "city_dataset", None
    return None, "unresolved", fb


def lookup_address(addr: dict, res: dict, rules: list[dict], as_of: str = QUERY_DATE, what_if: dict | None = None) -> dict:
    """Answer "which rules apply at this address on as_of?".
    addr = a sample_addresses row; res = its resolved_addresses record; rules = enriched rules;
    what_if = optional facts a person typed in (see apply_what_if).
    Returns {address_id, as_of, city, city_basis, facts, entries}. entries are sorted by rule id, one per rule that
    matters here. Each result is applies, superseded, not_yet_effective, pending or unknown. Failed rules and rules
    that cannot cover the building are left out."""
    # --- 1. Facts and candidate rules ---
    facts = apply_what_if(building_facts(addr), what_if)
    state = addr["state"].strip()
    city, basis, unverified = city_for(res)
    by_id = {r["team_rule_id"]: r for r in rules}
    # Only rules of this state or city are candidates. A city we could not verify is included on purpose, so its rules
    # show up as unknown instead of silently disappearing.
    cands = [r for r in rules if r["jurisdiction"] == state or (city and r["jurisdiction"] == city)
             or (unverified and r["jurisdiction"] == unverified)]
    # memo caches each rule's coverage at this address; stack catches rules that point at each other.
    memo, stack = {}, set()
    # A conflict listed on one rule counts for both, so both rules get flagged.
    partners_of = {}                      # conflicts are symmetric
    for r in rules:
        for o in r.get("_conflicts_with") or []:
            partners_of.setdefault(r["team_rule_id"], set()).add(o)
            partners_of.setdefault(o, set()).add(r["team_rule_id"])

    # --- 2. Coverage of other rules (for rule_coverage tests) ---
    def cov_value(rid):
        """Coverage value (True / False / None) of rule rid at this address, computed once.
        None for an unknown rule id or a loop between rules, so a loop ends as unknown instead of recursing forever."""
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
        """What eval_cond receives as ref: call it with a rule id for that rule's coverage here, or use title() for its name."""

        def __call__(self, rid):
            """Coverage value of rule rid at this address."""
            return cov_value(rid)

        def title(self, rid):
            """Citation and id of rule rid, for explanation text."""
            r = by_id.get(rid)
            return f"{r['citation'] or r['title']} ({rid})" if r else rid

    ref = Ref()
    # --- 3. First-pass result for each candidate rule ---
    base = {}
    for r in cands:
        rid = r["team_rule_id"]
        st = status_at(r["legal_state"], r["effective_date"], as_of)
        # A failed measure (struck down or defeated) is never reported: it is not law and not a live bill.
        if st == "failed":
            continue
        unverified_city = bool(unverified and r["jurisdiction"] == unverified)
        # A bill is not law, so its coverage is not tested. Every address in its place sees it as pending.
        ev = eval_coverage(coverage_of(r), facts, as_of, ref) if st != "pending" else None
        # We could not confirm the address is in this city, so a rule in force there can only be unknown.
        # Future and pending rules keep their status: that does not depend on the building.
        if unverified_city:
            if st == "in_force":
                result = "unknown"
            elif st == "not_yet_effective":
                result = "not_yet_effective"
            else:
                result = "pending"
        elif st == "pending":
            result = "pending"
        # A future rule that cannot cover this building is left out. One that might cover it stays, so people see what is coming.
        elif st == "not_yet_effective":
            if ev["value"] is False:
                continue
            result = "not_yet_effective"
        # In force. A rule that clearly does not cover the building is left out: results say what applies, not what does not.
        else:
            if ev["value"] is False:
                continue
            result = "applies" if ev["value"] is True else "unknown"
        base[rid] = {"rule": r, "status": st, "result": result, "ev": ev, "unverified_city": unverified_city, "by": None}

    # --- 4. Precedence ---
    # A rule yields to the rules listed in `overrides` when one of them applies here. The links come from the sources
    # (for example a state minimum that gives way to a local rule) and consolidate.py keeps only sensible ones; the
    # engine does not judge which rule is stricter. That is how a state rule becomes "superseded" at a city address.
    # Everything is judged on `orig`, the results before any yielding, so the outcome does not depend on loop order
    # and a chain (A yields to B, B yields to C) cannot cascade.
    orig = {rid: b["result"] for rid, b in base.items()}
    for rid, b in base.items():
        # Only a rule that might apply can be displaced.
        if b["result"] not in ("applies", "unknown"):
            continue
        for oid in b["rule"].get("overrides") or []:
            o = base.get(oid)
            # The other rule is not in play here (failed, other place, or it cannot cover this building).
            if not o:
                continue
            # Only a rule that applies here today displaces another. A pending or not-yet-effective rule displaces nothing.
            if orig[oid] == "applies":
                b["result"], b["by"] = "superseded", oid
                break
            # The other rule might apply. Our "applies" then depends on it, so it becomes unknown unless the other rule
            # is unsure only because of a status the data can never show (see below).
            if orig[oid] == "unknown" and b["result"] == "applies":
                hard = o["ev"] is None or o["unverified_city"] or o["ev"].get("hard", True)
                if hard:
                    b["result"], b["by"] = "unknown", oid
                else:                          # the other rule hinges only on a status the data cannot show: keep our answer, say so
                    b.setdefault("may_yield", []).append(oid)

    # --- 5. Build the entries ---
    entries = []
    for rid in sorted(base):
        b = base[rid]
        r = b["rule"]
        # Only partners that are also in play at this address count.
        partners = sorted(p for p in partners_of.get(rid, ()) if p in base)
        # A conflict with a rule that is absent here means nothing at this address, so it raises no flag. A rule's own
        # conflict_flag (for example two source dates) counts only when it has no listed partners at all.
        flag = bool(partners) or (bool(r.get("conflict_flag")) and not partners_of.get(rid))
        entries.append(make_entry(r, b, base, facts, partners, flag, as_of, basis, city, state))
    return {"address_id": addr["address_id"], "as_of": as_of, "city": city, "city_basis": basis, "facts": facts, "entries": entries}


def cap1(s: str) -> str:
    """Upper-case the first letter only. Unlike str.capitalize it leaves the rest alone, so "AB 325" keeps its case."""
    return s[:1].upper() + s[1:]


def join(items, n=3, width=140):
    """Shorten each non-empty phrase to width characters and join the first n with "; ". Keeps explanations readable."""
    items = [short(i, width) for i in items if i]
    return "; ".join(items[:n])


def facts_line(facts):
    """What is known about the building in a few words, for example "built 1975, at least 5 units". Empty if nothing is known."""
    bits = []
    if facts.get("year_built"):
        bits.append(f"built {facts['year_built']}")
    lo, hi = facts.get("units_lo"), facts.get("units_hi")
    if lo is not None:
        bits.append(f"{lo} units" if lo == hi else (f"at least {lo} units" if hi is None else f"{lo} to {hi} units"))
    return ", ".join(bits)


def head_of(r) -> str:
    """How a rule is named inside an explanation: its plain-English sentence plus citation, or just "Statewide rule (citation)" / "<city> rule (citation)" when it has no plain-English text."""
    cite = r["citation"] or r["title"]
    where = "Statewide rule" if r["level"] == "state" else f"{r['jurisdiction']} rule"
    plain = re.sub(r"\s+", " ", (r.get("plain_en") or "")).strip().rstrip(". ")
    return f"{plain} ({where}: {cite})" if plain else f"{where} ({cite})"


def make_entry(r, b, base, facts, partners, flag, as_of, basis, city, state):
    """Build one answer entry. r = the rule, b = its record from lookup_address, base = all records for this address.
    The public keys (team_rule_id, result, explanation, conflict_flag) go into lookups.json. Keys starting with "_" are
    internal: confidence, caveats, conflict partners, and the rule that displaced this one (_superseded_by is set for
    "superseded", and for "unknown" when a rule that might displace it is itself uncertain)."""
    # The sentences below are part of the answer and engine.js builds the same ones. Reword here and there together,
    # or parity_test fails.
    rid, res, ev = r["team_rule_id"], b["result"], b["ev"]
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
    # Confidence reflects how much was assumed, not only how sure the model was. "applies" with unchecked exemptions or a
    # possible yield is medium, "unknown" is low, and a weak extraction (below 0.7) caps "high" at medium.
    if res == "applies":
        conf = "high" if not (caveats or b.get("may_yield")) else "medium"
    elif res == "unknown":
        conf = "low"
    else:
        conf = "high"
    stated = r.get("confidence")  # None = the model gave no confidence; 0 is a real (very low) value
    if (1 if stated is None else stated) < 0.7 and conf == "high":
        conf = "medium"
    return {"team_rule_id": rid, "result": res, "explanation": text, "conflict_flag": flag,
            "_confidence": conf, "_caveats": caveats, "_partners": partners, "_superseded_by": b["by"] if res in ("superseded", "unknown") else None,
            "_may_yield": list(b.get("may_yield") or [])}
