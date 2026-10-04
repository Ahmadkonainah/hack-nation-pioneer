/**
 * Rental Housing Law Navigator - lookup engine (the browser-side JavaScript twin of src/engine.py).
 * Not legal advice.
 *
 * WHAT IT DOES
 *   Given one address (its state, legal city and building facts), a bundle of rules and an "as of" date, it decides
 *   for every rule that could reach that address whether the rule applies there on that date, and writes the
 *   plain-English explanation the app shows. It is pure: no network, no clock, no storage, no DOM. The same inputs
 *   always give the same answer. Facts a person types into "what if I know more" are used in memory only.
 *
 * SAME ANSWERS AS PYTHON (important)
 *   This file must produce exactly the same answers as src/engine.py (and src/dates.py for the date helpers):
 *   the same result, the same explanation text down to the punctuation, the same conflict flag, the same confidence.
 *   src/parity_test.py runs both engines on every sample address for 14 dates (7,000 comparisons) and on 8 sets of
 *   what-if facts (4,000 comparisons) and fails on any difference. If you change logic or wording here, make the
 *   identical change in engine.py, then run the parity test.
 *
 * THREE-VALUED LOGIC
 *   Every test gives true (met), false (not met) or null (unknown: a fact needed to decide is missing).
 *   null is never turned into a yes or a no. It travels through AND / OR / NOT (tAnd, tOr, tNot) so a missing fact
 *   ends up as "unknown" in the result, with a sentence naming the missing fact. It is only overridden when another
 *   test already settles the answer (a false inside an AND, a true inside an OR). Unknown beats a guess.
 *
 * RESULT VALUES (one per rule per address), in RESULT_ORDER
 *   applies            in force on the as-of date and the building is covered
 *   superseded         would apply, but a stricter rule that applies here displaces it (the answer points to that rule)
 *   not_yet_effective  enacted, but its effective date is after the as-of date
 *   pending            only a bill so far; not law
 *   unknown            cannot be decided from the data (missing year built, unit count, owner-occupancy, special
 *                      status, an unverified city, or a displacing rule that is itself uncertain)
 *   A rule that definitely does not cover the building, or that failed (struck down, defeated), gets no entry at all.
 *
 * FACTS AND RULES (shapes the functions expect; the bundle is built by src/build_web.py from the Python side)
 *   facts: {year_built, units_lo, units_hi, units_basis, use_code, use_description, owner_occupied?}
 *          units_hi null means "no upper bound" (for example "five or more units"). Missing values are null.
 *   rule:  {team_rule_id, jurisdiction, level, legal_state, effective_date, overrides, cov, ...}
 *          cov = {scope, conditions: [...], exemptions: [{label, conditions: [...]}]}
 */
(function (root) {
  "use strict";
  /* The project's default "today". It is fixed, not read from the system clock, so answers are repeatable and match
     the shipped lookups.json. Same value as QUERY_DATE in src/dates.py. */
  const QUERY_DATE = "2026-10-01";
  /* The five possible results, in the order the app lists them. */
  const RESULT_ORDER = ["applies", "superseded", "not_yet_effective", "pending", "unknown"];

  /**
   * Read "YYYY", "YYYY-MM" or "YYYY-MM-DD" as a date. A partial date means the first day of that period.
   * @param {string|null|undefined} s date text from a rule or from the as-of box
   * @returns {number|null} UTC-midnight timestamp in milliseconds, or null if it is not a real date
   */
  function parseDate(s) {
    const m = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(String(s || "").trim());
    if (!m) return null;
    const y = +m[1], mo = +(m[2] || 1), d = +(m[3] || 1);
    // Dates are kept as UTC-midnight numbers, so "before" and "after" are plain number comparisons with no time-zone
    // or daylight-saving shift. (Same order as comparing ISO "YYYY-MM-DD" text, which iso() produces for display.)
    const t = Date.UTC(y, mo - 1, d), dt = new Date(t);
    // Date.UTC quietly rolls 2026-02-31 over to March 3. Reading the parts back catches that, so an impossible date
    // is unknown (null), never a guess. Python gets the same result from date() raising ValueError.
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
    return t;
  }
  /**
   * Move a date back by n whole years, for rolling tests such as "first occupied at least 15 years before the as-of date".
   * @param {number} t UTC-midnight timestamp
   * @param {number} n years to go back
   * @returns {number} UTC-midnight timestamp; 29 February becomes 28 February when the earlier year has no 29th
   */
  function minusYears(t, n) {
    const dt = new Date(t), y = dt.getUTCFullYear() - n, m = dt.getUTCMonth(), d = dt.getUTCDate();
    let r = Date.UTC(y, m, d);
    // If the month changed, the day did not exist in that year (29 Feb) and JS rolled into March: use 28 Feb, as Python does.
    if (new Date(r).getUTCMonth() !== m) r = Date.UTC(y, m, 28);
    return r;
  }
  /* Timestamp -> "YYYY-MM-DD". Matches Python's date.isoformat(), so dates inside explanation sentences are identical. */
  const iso = (t) => new Date(t).toISOString().slice(0, 10);

  /**
   * Whether a rule is in force on a date. Computed by code from dates, never written by the model.
   * @param {string} legalState what the law is: "enacted", "pending" (a bill) or "failed"
   * @param {string|null} effectiveDate rule start date, "YYYY", "YYYY-MM" or "YYYY-MM-DD"
   * @param {string} asOf the date being asked about
   * @returns {"failed"|"pending"|"not_yet_effective"|"in_force"}
   */
  function statusAt(legalState, effectiveDate, asOf) {
    // The legal state wins over the dates: a failed measure is never in force, and a bill's effective date is only a hope.
    if (legalState === "failed") return "failed";
    if (legalState === "pending") return "pending";
    const eff = parseDate(effectiveDate), asof = parseDate(asOf);
    // An enacted rule with a missing or unreadable effective date counts as already in force; only a readable date
    // after the as-of date holds it back.
    if (eff !== null && asof !== null && asof < eff) return "not_yet_effective";
    return "in_force";
  }

  /* Three-valued AND / OR / NOT over true, false and null (unknown).
     A definite answer beats an unknown one: AND with any false is false, OR with any true is true, even if other tests
     are unknown. Otherwise one unknown makes the whole answer unknown, so a missing fact is never silently read as yes
     or no. An empty AND is true and an empty OR is false. */
  const tAnd = (v) => (v.some((x) => x === false) ? false : v.some((x) => x === null) ? null : true);
  const tOr = (v) => (v.some((x) => x === true) ? true : v.some((x) => x === null) ? null : false);
  const tNot = (v) => (v === null ? null : !v);

  /**
   * Compare a building's year built with a cut-off date. We only know the year, not the day, so the building could
   * have been first occupied any time from 1 January to 31 December of that year.
   * @param {"on_or_before"|"before"|"after"|"on_or_after"} op how the building's date must relate to the cut-off
   * @param {number} y year built
   * @param {number} T cut-off, UTC-midnight timestamp
   * @returns {true|false|null} true or false only if the whole year is on one side of the cut-off; null if the
   *   cut-off falls inside that year (the certificate-of-occupancy date would be needed) or op is not recognised
   */
  function cmpDateYear(op, y, T) {
    const lo = Date.UTC(y, 0, 1), hi = Date.UTC(y, 11, 31);
    if (op === "on_or_before") return hi <= T ? true : lo > T ? false : null;
    if (op === "before") return hi < T ? true : lo >= T ? false : null;
    if (op === "after") return lo > T ? true : hi <= T ? false : null;
    if (op === "on_or_after") return lo >= T ? true : hi < T ? false : null;
    return null;
  }
  /**
   * Compare a building's unit count with a number. The count is often only a range (for example "five or more").
   * @param {"<="|"<"|">="|">"|"=="|"!="} op
   * @param {number|null} lo fewest units the building can have (null is read as 1)
   * @param {number|null} hi most units it can have; null means no upper bound
   * @param {number} v the number in the rule
   * @returns {true|false|null} true or false only if every count in the range gives the same answer; null if the
   *   range straddles the threshold or op is not recognised
   */
  function cmpUnits(op, lo, hi, v) {
    if (lo === null) lo = 1;
    if (op === "<=") return hi !== null && hi <= v ? true : lo > v ? false : null;
    if (op === "<") return hi !== null && hi < v ? true : lo >= v ? false : null;
    if (op === ">=") return lo >= v ? true : hi !== null && hi < v ? false : null;
    if (op === ">") return lo > v ? true : hi !== null && hi <= v ? false : null;
    if (op === "==") {
      if (lo === hi && hi === v) return true;
      return v < lo || (hi !== null && v > hi) ? false : null;
    }
    if (op === "!=") return tNot(cmpUnits("==", lo, hi, v));
    return null;
  }
  /* How each date operator is worded inside explanation sentences. An operator not listed is printed as it is. */
  const OP_WORDS = { on_or_before: "on or before", before: "before", after: "after", on_or_after: "on or after" };

  /**
   * Test one coverage condition (or one condition of an exemption) against the building's facts.
   * @param {{fact: string, op: string, value: *, years_before_as_of?: number, text?: string}} c the condition
   * @param {object} facts building facts (parcel facts, possibly overridden by what-if facts)
   * @param {string} asOf date being asked about, used by rolling "N years before" tests
   * @param {boolean} inExemption true when the condition belongs to an exemption rather than to the coverage test;
   *   an exemption we cannot check is assumed not to apply, while an unchecked coverage test stays unknown
   * @param {function} ref ref(ruleId) gives another rule's coverage value (true/false/null); ref.title(ruleId) names it
   * @returns {[true|false|null, "fact"|"status"|"caveat"|"note", string]} [value, kind, phrase]. kind says how the
   *   phrase is used: "fact" is a building fact that decided the test or is missing; "status" is a special status
   *   (deed-restricted, subsidised ...) the parcel data can never show; "caveat" is an exemption we assumed away and only
   *   list as "not checked"; "note" is information that does not change the answer.
   */
  function evalCond(c, facts, asOf, inExemption, ref) {
    const fact = c.fact, op = c.op, val = String(c.value == null ? "" : c.value);
    if (fact === "first_occupancy_date") {
      // A rolling test ("at least N years old") moves with the as-of date; otherwise the cut-off is the fixed date in the rule.
      const yrs = parseInt(c.years_before_as_of || 0, 10);
      const T = yrs > 0 ? minusYears(parseDate(asOf), yrs) : parseDate(val);
      if (T === null) return [null, "fact", "a construction-date test could not be read"];
      const need = `first occupied ${OP_WORDS[op] || op} ${iso(T)}`;
      const y = facts.year_built;
      // The phrase says what was needed, so the app can tell the person which fact would settle it.
      if (y === null || y === undefined) return [null, "fact", `${facts.year_basis || "year built is missing in the parcel data"} (needed: ${need})`];
      const v = cmpDateYear(op, y, T);
      // Year built is not a certificate-of-occupancy date: when the cut-off falls inside the build year we cannot tell.
      if (v === null) return [null, "fact", `built ${y}, the same year as the ${iso(T)} cut-off, and the certificate-of-occupancy date is not in the data (needed: ${need})`];
      return [v, "fact", `built ${y}: ${v ? "meets" : "does not meet"} the test (${need})`];
    }
    if (fact === "units") {
      let lo = facts.units_lo, hi = facts.units_hi;
      // A missing key is read as "not known", the same as null (Python's facts always carry the keys).
      if (lo === undefined) lo = null;
      if (hi === undefined) hi = null;
      const n = /^\d+$/.test(val) ? parseInt(val, 10) : null;
      if (n === null) return [null, "fact", "a unit-count test could not be read"];
      const need = `units ${op} ${n}`;
      // units_basis says where the count came from (parcel column, use description, or "entered by you"), so it is quoted.
      if (lo === null) return [null, "fact", `${facts.units_basis || "unit count is missing in the parcel data"} (needed: ${need})`];
      const v = cmpUnits(op, lo, hi, n);
      const have = lo === hi ? `${lo} units` : hi === null ? `${lo} or more units` : `${lo} to ${hi} units`;
      if (v === null) return [null, "fact", `${have} (${facts.units_basis}) is not enough to decide the test ${need}`];
      return [v, "fact", `${have} (${facts.units_basis}): ${v ? "meets" : "does not meet"} the test (${need})`];
    }
    if (fact === "owner_occupied") {
      const oo = facts.owner_occupied;                      // only set when a person types it in (what-if); parcel data never has it
      if (oo === undefined || oo === null) return [null, "fact", "whether an owner lives in the building is not in the parcel data"];
      // A rule value of "false" asks for a building with no owner living in it; any other value asks for one with an owner.
      const want = val.trim().toLowerCase() !== "false";
      const v = op === "is" ? oo === want : oo !== want;
      // Parcel data never records owner-occupancy, so this fact can only come from the person's own what-if input.
      // The wording says "entered by you" so the reader knows the answer rests on what they typed, not on a public record.
      return [v, "fact", `${oo ? "an owner lives" : "no owner lives"} in the building (entered by you): ${v ? "meets" : "does not meet"} the test (owner-occupied)`];
    }
    if (fact === "use_type") {
      // Look for any long word (5+ letters) of the rule's wording inside the parcel's own use description and code.
      const words = new Set(((`${val} ${c.text || ""}`).toLowerCase().match(/[a-z]{5,}/g)) || []);
      const have = `${facts.use_description} ${facts.use_code}`.toLowerCase();
      // A word match is a hint, not proof, so it gives unknown (a person should check), never true.
      for (const w of words) if (have.includes(w)) return [null, "fact", `the parcel's use description ('${facts.use_description}') may match: ${val}`];
      // No match: the sample parcels are assessor-classified housing, so an exemption such as "hotel" or "dormitory" is
      // assumed not to apply and listed as not checked. As a coverage condition the same gap stays unknown.
      if (inExemption) return [false, "caveat", `the building is not a ${val}`];
      return [null, "fact", `whether the building is a ${val} is not in the parcel data`];
    }
    if (fact === "status_not_in_data") {
      // Special status (deed restriction, subsidy, Section 8 ...) cannot be seen in parcel data. As an exemption it is
      // assumed not to apply and listed as not checked, so it never blocks an answer. As a coverage condition it is
      // unknown, but kind "status" tells evalCoverage this is not a missing building fact (see its "hard" flag).
      if (inExemption) return [false, "caveat", `${val || c.text || ""}`];
      return [null, "status", `requires ${val || c.text || ""}, which is not in the parcel data`];
    }
    if (fact === "tenancy") {
      // This depends on the lease, not the building. As a coverage condition it is only a note shown to the reader and
      // does not change the answer (true, kind "note"); as an exemption it is assumed away and listed as not checked.
      if (inExemption) return [false, "caveat", `${val || c.text || ""} (depends on the tenancy)`];
      return [true, "note", `applies to tenancies where: ${val || c.text || ""}`];
    }
    if (fact === "rule_coverage") {
      // "Covered only if another rule covers (or does not cover) this building." The other rule's value is looked up
      // through ref, which is memoised and guards against rules that refer to each other in a circle.
      const other = ref(val);
      const v = op === "is" ? other : tNot(other);
      const title = ref.title(val);
      if (v === null) return [null, "fact", `depends on whether ${title} covers this building, which is not certain`];
      return [v, "fact", `${other ? "covered" : "not covered"} by ${title}`];
    }
    // A condition type this engine does not know is unknown, never guessed.
    return [null, "fact", "an unreadable test"];
  }

  /**
   * Decide whether a rule covers the building: all of its coverage conditions must be true and no exemption may be
   * fully true.
   * @param {{scope?: string, conditions?: object[], exemptions?: {label?: string, conditions?: object[]}[]}} cov
   *   scope "undefined" means the source never says which buildings the rule covers
   * @param {object} facts building facts
   * @param {string} asOf date being asked about
   * @param {function} ref see evalCond
   * @returns {{value: true|false|null, true_facts: string[], false_facts: string[], blockers: string[], caveats: string[],
   *   notes: string[], exempt_by: string|null, ruled_out: string[], hard: boolean}}
   *   value is the coverage answer. The string lists feed the explanation: tests that were met, tests that were not,
   *   what is missing (blockers), exemptions assumed away and not checked (caveats), side notes, the label of the
   *   exemption that applies (exempt_by) and exemptions that known facts rule out (ruled_out). hard is true when an
   *   unknown hinges on a building fact (or an unreadable test) rather than on a special status the data can never show.
   */
  function evalCoverage(cov, facts, asOf, ref) {
    const out = { value: null, true_facts: [], false_facts: [], blockers: [], caveats: [], notes: [], exempt_by: null, ruled_out: [], hard: false };
    const scope = cov.scope || "conditional";
    // The source does not say who is covered, so there is nothing to test: value stays null (unknown).
    if (scope === "undefined") {
      out.blockers.push("the source does not say which buildings this rule covers");
      out.hard = true;
      return out;
    }
    const cvals = [];
    for (const c of cov.conditions || []) {
      const [v, kind, ph] = evalCond(c, facts, asOf, false, ref);
      if (kind === "note") { out.notes.push(ph); continue; }
      cvals.push(v);
      // A missing building fact is "hard": lookupAddress uses it to decide whether another rule's uncertainty can flip an
      // answer. A special status the data cannot show (kind "status") is not hard.
      if (v === null && kind !== "status") out.hard = true;
      (v === true ? out.true_facts : v === false ? out.false_facts : out.blockers).push(ph);
    }
    // All coverage conditions must hold (three-valued AND). No conditions at all means the rule covers every building.
    const covered = cvals.length ? tAnd(cvals) : true;
    // Each exemption is itself an AND of its conditions; the exemptions are then OR-ed (any one fully true exempts).
    const evals = [];
    for (const ex of cov.exemptions || []) {
      const label = ex.label || "an exemption";
      const ev = [], trues = [], falses = [], blocks = [], cav = [];
      const conds = ex.conditions || [];
      // An exemption with no stated test cannot be ruled out, so it stays unknown rather than being ignored.
      if (!conds.length) { evals.push([null, label, [], [], [`${label}: its test is not stated`], []]); out.hard = true; continue; }
      for (const c of conds) {
        const [v, kind, ph] = evalCond(c, facts, asOf, true, ref);
        if (kind === "note") continue;
        ev.push(v);
        if (v === true) trues.push(ph);
        else if (v === false && kind === "caveat") cav.push(ph);
        else if (v === false) falses.push(ph);
        else blocks.push(ph);
      }
      evals.push([ev.length ? tAnd(ev) : null, label, trues, falses, blocks, cav]);
    }
    const exempt = evals.length ? tOr(evals.map((e) => e[0])) : false;
    for (const [v, label, trues, falses, blocks, cav] of evals) {
      if (v === true) {
        out.exempt_by = label;
        out.true_facts.push(...trues);
      } else if (v === false) {
        // An exemption that a known fact rules out is worth saying; one that is only assumed away is a "not checked" caveat.
        if (falses.length) out.ruled_out.push(label);
        if (!falses.length) out.caveats.push(...cav);
      } else {
        if (blocks.length) out.blockers.push(...blocks.map((b) => `${label}: ${b}`));
        else out.blockers.push(label);
        out.caveats.push(...cav);
        out.hard = true;
      }
    }
    // Covered AND NOT exempt. An unknown on either side keeps the answer unknown unless the other side already settles it.
    out.value = tAnd([covered, tNot(exempt)]);
    return out;
  }

  /**
   * Shorten text for use inside a sentence: collapse whitespace, then cut to n characters with an ellipsis.
   * @param {string} s
   * @param {number} [n=60] longest result in characters
   * @returns {string}
   */
  function short(s, n) {
    n = n === undefined ? 60 : n;
    s = String(s || "").replace(/\s+/g, " ").trim();
    return s.length <= n ? s : s.slice(0, n - 1) + "…";
  }
  /* Capitalise the first letter. The phrases are written to sit mid-sentence, so they start in lower case. */
  const cap1 = (s) => s.slice(0, 1).toUpperCase() + s.slice(1);
  /**
   * Join the first few non-empty phrases with "; ". The cap keeps each explanation to a few lines; the full detail
   * stays available in the app's detail cards.
   * @param {string[]} items phrases (empty ones are skipped)
   * @param {number} [n=3] how many phrases to keep
   * @param {number} [width=140] longest single phrase, in characters
   * @returns {string}
   */
  function join(items, n, width) {
    n = n === undefined ? 3 : n;
    width = width === undefined ? 140 : width;
    return items.filter((i) => i).map((i) => short(i, width)).slice(0, n).join("; ");
  }

  /**
   * What the parcel data says about this building, in a few words ("built 1975, at least 5 units"). Empty if nothing is known.
   * @param {object} f building facts
   * @returns {string}
   */
  function factsLine(f) {
    const bits = [];
    if (f.year_built) bits.push(`built ${f.year_built}`);
    const lo = f.units_lo === undefined ? null : f.units_lo, hi = f.units_hi === undefined ? null : f.units_hi;
    if (lo !== null) bits.push(lo === hi ? `${lo} units` : hi === null ? `at least ${lo} units` : `${lo} to ${hi} units`);
    return bits.join(", ");
  }

  /**
   * Overlay facts a person typed in ("what if I know more about this building") on the parcel facts.
   * @param {object} facts parcel facts
   * @param {{year_built?: *, units?: *, owner_occupied?: boolean}|null} w the typed facts; missing, null, blank or
   *   invalid keys leave the parcel fact as it is
   * @returns {object} a new facts object; the parcel facts passed in are never changed
   */
  function applyWhatIf(facts, w) {
    // Work on a copy so the caller can run the engine twice (parcel facts, then typed facts) and show what changed.
    const f = Object.assign({}, facts);
    if (!w) return f;
    // Whole numbers only. Anything else (blank, "abc", "12.5") is ignored instead of throwing, so a half-typed field is harmless.
    const toInt = (v) => (v === null || v === undefined || v === "" || !/^-?\d+$/.test(String(v).trim()) ? null : parseInt(v, 10));
    const y = toInt(w.year_built);
    // Typed values replace the parcel value for the whole run; the range check rejects typos such as 197 or 19750.
    if (y !== null && y >= 1700 && y <= 2100) f.year_built = y;
    const n = toInt(w.units);
    // An exact count replaces any range. units_basis becomes "entered by you" so the explanation credits the person's input.
    if (n !== null && n > 0) { f.units_lo = n; f.units_hi = n; f.units_basis = "entered by you"; }
    // Only a real true or false counts; "yes", 1 or an empty box do not set owner-occupancy.
    if (w.owner_occupied === true || w.owner_occupied === false) f.owner_occupied = w.owner_occupied;
    return f;
  }

  /**
   * Work out which rules apply at one address on one date. This is the main entry point.
   * @param {{address_id: *, state: string, facts: object, city: string|null, basis: string, unverified: string|null}} addr
   *   city is the legal city (null if none); basis says how it was found ("census_geocoder", "city_dataset" or
   *   "unresolved"); unverified is a city named by the parcel data that the geocoder could not confirm
   * @param {object[]} rules bundle rules, each with its coverage test in .cov
   * @param {string} [asOf] date to answer for; defaults to QUERY_DATE
   * @param {object} [whatIf] facts typed in by a person, see applyWhatIf
   * @returns {{address_id: *, as_of: string, city: string|null, city_basis: string, entries: object[]}} one entry per
   *   rule that reaches the address (see makeEntry), sorted by rule id
   */
  function lookupAddress(addr, rules, asOf, whatIf) {
    asOf = asOf || QUERY_DATE;
    // What-if facts are applied once, up front, so every rule (including rules that refer to another rule's coverage)
    // is judged against the same facts.
    const facts = applyWhatIf(addr.facts, whatIf), state = String(addr.state).trim();
    const city = addr.city || null, basis = addr.basis, unverified = addr.unverified || null;
    const byId = new Map(rules.map((r) => [r.team_rule_id, r]));
    // Candidates: statewide rules, rules of the legal city, and rules of the unverified city (in-force ones become unknown in step 1).
    const cands = rules.filter((r) => r.jurisdiction === state || (city && r.jurisdiction === city) || (unverified && r.jurisdiction === unverified));
    const memo = new Map(), stack = new Set();
    // A conflict listed on one rule is recorded for both rules, so each one is flagged.
    const partnersOf = new Map();
    const addP = (a, b) => { if (!partnersOf.has(a)) partnersOf.set(a, new Set()); partnersOf.get(a).add(b); };
    for (const r of rules) for (const o of r._conflicts_with || []) { addP(r.team_rule_id, o); addP(o, r.team_rule_id); }

    /* Coverage value of any rule by id, for conditions of type rule_coverage. Each rule is evaluated once (memo). The
       stack stops circular references: a rule that depends on itself, directly or through others, is unknown (null). */
    function covValue(rid) {
      if (memo.has(rid)) return memo.get(rid).value;
      if (stack.has(rid) || !byId.has(rid)) return null;
      stack.add(rid);
      const r = byId.get(rid);
      const here = r.jurisdiction === state || (city && r.jurisdiction === city);
      // A rule of some other place never covers this address (note the unverified city is not "here": it is not confirmed).
      // ref is defined just below; it is only called after that point.
      memo.set(rid, here ? evalCoverage(r.cov, facts, asOf, ref) : { value: false });
      stack.delete(rid);
      return memo.get(rid).value;
    }
    const ref = (rid) => covValue(rid);
    ref.title = (rid) => { const r = byId.get(rid); return r ? `${r.citation || r.title} (${rid})` : rid; };

    // Step 1: each candidate rule's own result, before any rule gives way to another.
    const base = new Map();
    for (const r of cands) {
      const rid = r.team_rule_id;
      const st = statusAt(r.legal_state, r.effective_date, asOf);
      // A failed measure never appears: it is not law and was never going to be.
      if (st === "failed") continue;
      const unverifiedCity = !!(unverified && r.jurisdiction === unverified);
      // A pending bill is not law, so which buildings it would cover is moot and is not evaluated.
      const ev = st !== "pending" ? evalCoverage(r.cov, facts, asOf, ref) : null;
      let result;
      // A rule of a city the geocoder could not confirm: if it is in force we cannot say it applies, so it is unknown.
      // Pending and not-yet-effective keep their label because those do not depend on the address being in that city.
      if (unverifiedCity) result = st === "in_force" ? "unknown" : st === "not_yet_effective" ? "not_yet_effective" : "pending";
      else if (st === "pending") result = "pending";
      // Not yet effective but certainly not covering this building: leave it out. Otherwise show it, covered or not certain.
      else if (st === "not_yet_effective") { if (ev.value === false) continue; result = "not_yet_effective"; }
      // In force: not covered is dropped, covered applies, and anything uncertain is unknown (null never becomes "applies").
      else { if (ev.value === false) continue; result = ev.value === true ? "applies" : "unknown"; }
      base.set(rid, { rule: r, status: st, result, ev, unverified_city: unverifiedCity, by: null });
    }
    // Step 2: stricter rules displace the rules that list them in `overrides`. Every rule is judged against the results
    // from step 1 (orig), not against answers already changed here, so the outcome does not depend on the order of the loop.
    const orig = new Map([...base].map(([k, b]) => [k, b.result]));
    for (const [rid, b] of base) {
      // Only a rule that is, or may be, in force can be displaced; pending and not-yet-effective rules have nothing to give up.
      if (b.result !== "applies" && b.result !== "unknown") continue;
      for (const oid of b.rule.overrides || []) {
        if (!base.has(oid)) continue;
        // The overriding rule definitely applies here: this one is superseded and points to it. The first such rule wins.
        if (orig.get(oid) === "applies") { b.result = "superseded"; b.by = oid; break; }
        // The overriding rule might apply. We cannot keep saying "applies" unless the doubt is only about something the data can never show.
        if (orig.get(oid) === "unknown" && b.result === "applies") {
          const o = base.get(oid);
          // "hard" doubt (a missing building fact, an unverified city, or no coverage result) makes ours unknown too.
          // If the other rule is not explicitly hard=false, assume it is.
          const hard = !o.ev || o.unverified_city || o.ev.hard !== false;
          if (hard) { b.result = "unknown"; b.by = oid; }
          // The other rule hinges only on a special status the data cannot show: keep "applies" and say it may yield.
          else (b.may_yield = b.may_yield || []).push(oid);
        }
      }
    }
    const entries = [];
    // Sorted by rule id so the output order is stable and identical to the Python engine's.
    for (const rid of [...base.keys()].sort()) {
      const b = base.get(rid), r = b.rule;
      const partners = [...(partnersOf.get(rid) || [])].filter((p) => base.has(p)).sort();
      // Flag for human review (we never decide preemption ourselves): a conflicting rule is also present at this address,
      // or the rule carries its own conflict flag but names no partner at all. If its named partners are absent here, no flag.
      const flag = partners.length > 0 || (!!r.conflict_flag && !(partnersOf.get(rid) && partnersOf.get(rid).size));
      entries.push(makeEntry(r, b, base, partners, flag, asOf, basis, facts));
    }
    return { address_id: addr.address_id, as_of: asOf, city, city_basis: basis, entries };
  }

  /**
   * How a rule is named inside an explanation: its plain-English summary followed by the jurisdiction and citation.
   * @param {object} r rule
   * @returns {string} "<summary> (Statewide rule: <citation>)" or "<summary> (<jurisdiction> rule: <citation>)"; without
   *   a summary just "Statewide rule (<citation>)" or "<jurisdiction> rule (<citation>)". The citation falls back to the title.
   */
  function headOf(r) {
    const cite = r.citation || r.title;
    const where = r.level === "state" ? "Statewide rule" : `${r.jurisdiction} rule`;
    // Trailing full stops and spaces are removed so the sentence that embeds this can add its own punctuation.
    const plain = String(r.plain_en || "").replace(/\s+/g, " ").trim().replace(/[. ]+$/, "");
    return plain ? `${plain} (${where}: ${cite})` : `${where} (${cite})`;
  }

  /**
   * Build the answer for one rule at one address: the result, the explanation sentence, and the flags the app shows.
   * The explanation wording must match src/engine.py character for character (src/parity_test.py compares it).
   * @param {object} r the rule
   * @param {{result: string, ev: object|null, by: string|null, unverified_city: boolean, may_yield?: string[]}} b this
   *   rule's working record from lookupAddress (ev is null for a pending rule, by is the rule it gives way to)
   * @param {Map<string, object>} base working records of every rule that reaches this address, by rule id
   * @param {string[]} partners rules in conflict with this one that are also present at this address
   * @param {boolean} flag true if a person should review a possible conflict
   * @param {string} asOf date answered for
   * @param {string} basis how the city was found; "city_dataset" means it came from the parcel data, not the geocoder
   * @param {object} facts building facts used (parcel facts, possibly with what-if facts applied)
   * @returns {{team_rule_id: string, result: string, explanation: string, conflict_flag: boolean,
   *   confidence: "high"|"medium"|"low", caveats: string[], partners: string[], superseded_by: string|null,
   *   may_yield: string[]}}
   */
  function makeEntry(r, b, base, partners, flag, asOf, basis, facts) {
    const rid = r.team_rule_id, res = b.result, ev = b.ev;
    const where = headOf(r);
    const caveats = ev ? ev.caveats.slice() : [];
    let text;
    // One sentence group per result, always starting with the result word so the answer is clear at a glance.
    if (res === "applies") {
      // Lead with the facts that decided the test, at most two; a rule with no building test says so plainly.
      const why = join(ev.true_facts.concat(ev.false_facts), 2);
      text = `Applies. ${where}. `;
      if (why) text += cap1(why) + ".";
      else if (!ev.ruled_out.length) text += "No building-based coverage test applies.";
      if (ev.ruled_out.length) {
        const fl = factsLine(facts);
        text += (fl ? ` Parcel data: ${fl}.` : "") + " Exemptions ruled out by that data: " + join(ev.ruled_out, 3, 80) + ".";
      }
    } else if (res === "superseded") {
      const o = base.get(b.by).rule;
      text = `Superseded. ${where}. Here it gives way to ${o.citation || o.title} (${o.team_rule_id}), which applies at this address.`;
    } else if (res === "not_yet_effective") {
      text = `Not yet effective. ${where}. It takes effect ${r.effective_date || "on a date not stated"}; the as-of date is ${asOf}.`;
      if (ev && ev.value === null) text += " Coverage would depend on: " + join(ev.blockers, 2) + ".";
    } else if (res === "pending") {
      text = `Pending. ${where}. It has not been enacted, so it is not in force.`;
    } else if (b.unverified_city) {
      // Unknown has three different reasons, each worded differently: unconfirmed city, a possibly-displacing rule, or a missing fact.
      text = `Unknown. ${where}. This address could not be placed in ${r.jurisdiction} by the geocoder, so this city rule cannot be confirmed.`;
    } else if (b.by) {
      const o = base.get(b.by).rule;
      text = `Unknown. ${where}. It would apply unless ${o.citation || o.title} (${o.team_rule_id}) does, and that is not certain here.`;
      const ob = base.get(b.by).ev;
      if (ob && ob.blockers.length) text += " " + cap1(join(ob.blockers, 2)) + ".";
    } else {
      text = `Unknown. ${where}. Coverage depends on: ${join(ev.blockers, 3)}.`;
      if (ev.ruled_out.length) text += " Exemptions ruled out by the parcel data: " + join(ev.ruled_out, 3, 80) + ".";
    }
    // Assumptions are never hidden: an "applies" that rests on an exemption we assumed away says so, and gets medium confidence below.
    if (res === "applies" && caveats.length) text += " Not checked from parcel data: " + join(caveats, 3) + ".";
    // Still "applies", but another rule could displace it and the only doubt is a status the data cannot show.
    if (res === "applies" && b.may_yield) {
      for (const oid of b.may_yield) {
        const o = base.get(oid);
        const ob = o.ev ? o.ev.blockers : [];
        text += ` Could yield to ${o.rule.citation || o.rule.title} (${oid}), which cannot be checked here: ${join(ob, 1, 160)}.`;
      }
    }
    // Side notes (for example a tenancy condition) are shown for live answers only; only the first one, to keep the text short.
    if (ev && ev.notes.length && (res === "applies" || res === "unknown")) text += " " + cap1(ev.notes[0]) + ".";
    // Say so when the city came from the parcel dataset instead of the Census geocoder, since that is a weaker basis.
    if (basis === "city_dataset" && r.level === "city") text += " City taken from the parcel dataset (not geocoded).";
    // Possible preemption or conflict is flagged for a person to review, never decided here.
    if (flag) {
      text += " Flagged: possible conflict with " +
        (partners.length ? partners.map((p) => `${base.get(p).rule.citation || base.get(p).rule.title} (${p})`).join(", ") : "another rule") +
        "; needs human review.";
    }
    // Confidence in the answer: high if nothing was assumed, medium if an assumption or a possible override is involved,
    // low for unknown. Dates and legal status are computed by code, so the other results are high.
    let conf = res === "applies" ? (caveats.length || b.may_yield ? "medium" : "high") : res === "unknown" ? "low" : "high";
    // A rule the model extracted with low confidence (under 0.7) cannot give a "high" answer; no stated confidence is not penalised.
    const ec = r.confidence === null || r.confidence === undefined ? 1 : r.confidence;
    if (ec < 0.7 && conf === "high") conf = "medium";
    // superseded_by is set only when another rule displaces this one (superseded) or may do so (unknown).
    return { team_rule_id: rid, result: res, explanation: text, conflict_flag: flag, confidence: conf, caveats, partners, superseded_by: res === "superseded" || res === "unknown" ? b.by : null, may_yield: b.may_yield || [] };
  }

  /* Public API. In the browser it is the global NavEngine (the app uses lookupAddress, applyWhatIf and parseDate); under Node it is
     module.exports, which is how src/parity_test.py loads this file. The IIFE keeps every helper above private. */
  const api = { QUERY_DATE, RESULT_ORDER, parseDate, statusAt, lookupAddress, evalCoverage, applyWhatIf };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NavEngine = api;
})(typeof self !== "undefined" ? self : this);
