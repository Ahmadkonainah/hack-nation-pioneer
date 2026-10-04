/* Rental Housing Law Navigator - lookup engine (JavaScript port of src/engine.py).
   Same three-valued logic, same wording. tests/parity compares the two on every address and many dates.
   Not legal advice. */
(function (root) {
  "use strict";
  const QUERY_DATE = "2026-10-01";
  const RESULT_ORDER = ["applies", "superseded", "not_yet_effective", "pending", "unknown"];

  function parseDate(s) {
    const m = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(String(s || "").trim());
    if (!m) return null;
    const y = +m[1], mo = +(m[2] || 1), d = +(m[3] || 1);
    const t = Date.UTC(y, mo - 1, d), dt = new Date(t);
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
    return t;
  }
  function minusYears(t, n) {
    const dt = new Date(t), y = dt.getUTCFullYear() - n, m = dt.getUTCMonth(), d = dt.getUTCDate();
    let r = Date.UTC(y, m, d);
    if (new Date(r).getUTCMonth() !== m) r = Date.UTC(y, m, 28);
    return r;
  }
  const iso = (t) => new Date(t).toISOString().slice(0, 10);

  function statusAt(legalState, effectiveDate, asOf) {
    if (legalState === "failed") return "failed";
    if (legalState === "pending") return "pending";
    const eff = parseDate(effectiveDate), asof = parseDate(asOf);
    if (eff !== null && asof !== null && asof < eff) return "not_yet_effective";
    return "in_force";
  }

  const tAnd = (v) => (v.some((x) => x === false) ? false : v.some((x) => x === null) ? null : true);
  const tOr = (v) => (v.some((x) => x === true) ? true : v.some((x) => x === null) ? null : false);
  const tNot = (v) => (v === null ? null : !v);

  function cmpDateYear(op, y, T) {
    const lo = Date.UTC(y, 0, 1), hi = Date.UTC(y, 11, 31);
    if (op === "on_or_before") return hi <= T ? true : lo > T ? false : null;
    if (op === "before") return hi < T ? true : lo >= T ? false : null;
    if (op === "after") return lo > T ? true : hi <= T ? false : null;
    if (op === "on_or_after") return lo >= T ? true : hi < T ? false : null;
    return null;
  }
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
  const OP_WORDS = { on_or_before: "on or before", before: "before", after: "after", on_or_after: "on or after" };

  // returns [value, kind, phrase]
  function evalCond(c, facts, asOf, inExemption, ref) {
    const fact = c.fact, op = c.op, val = String(c.value == null ? "" : c.value);
    if (fact === "first_occupancy_date") {
      const yrs = parseInt(c.years_before_as_of || 0, 10);
      const T = yrs > 0 ? minusYears(parseDate(asOf), yrs) : parseDate(val);
      if (T === null) return [null, "fact", "a construction-date test could not be read"];
      const need = `first occupied ${OP_WORDS[op] || op} ${iso(T)}`;
      const y = facts.year_built;
      if (y === null || y === undefined) return [null, "fact", `year built is missing in the parcel data (needed: ${need})`];
      const v = cmpDateYear(op, y, T);
      if (v === null) return [null, "fact", `built ${y}, the same year as the ${iso(T)} cut-off, and the certificate-of-occupancy date is not in the data (needed: ${need})`];
      return [v, "fact", `built ${y}: ${v ? "meets" : "does not meet"} the test (${need})`];
    }
    if (fact === "units") {
      let lo = facts.units_lo, hi = facts.units_hi;
      if (lo === undefined) lo = null;
      if (hi === undefined) hi = null;
      const n = /^\d+$/.test(val) ? parseInt(val, 10) : null;
      if (n === null) return [null, "fact", "a unit-count test could not be read"];
      const need = `units ${op} ${n}`;
      if (lo === null) return [null, "fact", `${facts.units_basis || "unit count is missing in the parcel data"} (needed: ${need})`];
      const v = cmpUnits(op, lo, hi, n);
      const have = lo === hi ? `${lo} units` : hi === null ? `${lo} or more units` : `${lo} to ${hi} units`;
      if (v === null) return [null, "fact", `${have} (${facts.units_basis}) is not enough to decide the test ${need}`];
      return [v, "fact", `${have} (${facts.units_basis}): ${v ? "meets" : "does not meet"} the test (${need})`];
    }
    if (fact === "owner_occupied") return [null, "fact", "whether an owner lives in the building is not in the parcel data"];
    if (fact === "use_type") {
      const words = new Set(((`${val} ${c.text || ""}`).toLowerCase().match(/[a-z]{5,}/g)) || []);
      const have = `${facts.use_description} ${facts.use_code}`.toLowerCase();
      for (const w of words) if (have.includes(w)) return [null, "fact", `the parcel's use description ('${facts.use_description}') may match: ${val}`];
      if (inExemption) return [false, "caveat", `the building is not a ${val}`];
      return [null, "fact", `whether the building is a ${val} is not in the parcel data`];
    }
    if (fact === "status_not_in_data") {
      if (inExemption) return [false, "caveat", `${val || c.text || ""}`];
      return [null, "status", `requires ${val || c.text || ""}, which is not in the parcel data`];
    }
    if (fact === "tenancy") {
      if (inExemption) return [false, "caveat", `${val || c.text || ""} (depends on the tenancy)`];
      return [true, "note", `applies to tenancies where: ${val || c.text || ""}`];
    }
    if (fact === "rule_coverage") {
      const other = ref(val);
      const v = op === "is" ? other : tNot(other);
      const title = ref.title(val);
      if (v === null) return [null, "fact", `depends on whether ${title} covers this building, which is not certain`];
      return [v, "fact", `${other ? "covered" : "not covered"} by ${title}`];
    }
    return [null, "fact", "an unreadable test"];
  }

  function evalCoverage(cov, facts, asOf, ref) {
    const out = { value: null, true_facts: [], false_facts: [], blockers: [], caveats: [], notes: [], exempt_by: null, ruled_out: [], hard: false };
    const scope = cov.scope || "conditional";
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
      if (v === null && kind !== "status") out.hard = true;
      (v === true ? out.true_facts : v === false ? out.false_facts : out.blockers).push(ph);
    }
    const covered = cvals.length ? tAnd(cvals) : true;
    const evals = [];
    for (const ex of cov.exemptions || []) {
      const label = ex.label || "an exemption";
      const ev = [], trues = [], falses = [], blocks = [], cav = [];
      const conds = ex.conditions || [];
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
        if (falses.length) out.ruled_out.push(label);
        if (!falses.length) out.caveats.push(...cav);
      } else {
        if (blocks.length) out.blockers.push(...blocks.map((b) => `${label}: ${b}`));
        else out.blockers.push(label);
        out.caveats.push(...cav);
        out.hard = true;
      }
    }
    out.value = tAnd([covered, tNot(exempt)]);
    return out;
  }

  function short(s, n) {
    n = n === undefined ? 60 : n;
    s = String(s || "").replace(/\s+/g, " ").trim();
    return s.length <= n ? s : s.slice(0, n - 1) + "…";
  }
  const cap1 = (s) => s.slice(0, 1).toUpperCase() + s.slice(1);
  function join(items, n, width) {
    n = n === undefined ? 3 : n;
    width = width === undefined ? 140 : width;
    return items.filter((i) => i).map((i) => short(i, width)).slice(0, n).join("; ");
  }

  function factsLine(f) {
    const bits = [];
    if (f.year_built) bits.push(`built ${f.year_built}`);
    const lo = f.units_lo === undefined ? null : f.units_lo, hi = f.units_hi === undefined ? null : f.units_hi;
    if (lo !== null) bits.push(lo === hi ? `${lo} units` : hi === null ? `at least ${lo} units` : `${lo} to ${hi} units`);
    return bits.join(", ");
  }

  /* addr = {address_id, state, facts, city, basis, unverified};  rules = bundle rules with .cov */
  function lookupAddress(addr, rules, asOf) {
    asOf = asOf || QUERY_DATE;
    const facts = addr.facts, state = String(addr.state).trim();
    const city = addr.city || null, basis = addr.basis, unverified = addr.unverified || null;
    const byId = new Map(rules.map((r) => [r.team_rule_id, r]));
    const cands = rules.filter((r) => r.jurisdiction === state || (city && r.jurisdiction === city) || (unverified && r.jurisdiction === unverified));
    const memo = new Map(), stack = new Set();
    const partnersOf = new Map();
    const addP = (a, b) => { if (!partnersOf.has(a)) partnersOf.set(a, new Set()); partnersOf.get(a).add(b); };
    for (const r of rules) for (const o of r._conflicts_with || []) { addP(r.team_rule_id, o); addP(o, r.team_rule_id); }

    function covValue(rid) {
      if (memo.has(rid)) return memo.get(rid).value;
      if (stack.has(rid) || !byId.has(rid)) return null;
      stack.add(rid);
      const r = byId.get(rid);
      const here = r.jurisdiction === state || (city && r.jurisdiction === city);
      memo.set(rid, here ? evalCoverage(r.cov, facts, asOf, ref) : { value: false });
      stack.delete(rid);
      return memo.get(rid).value;
    }
    const ref = (rid) => covValue(rid);
    ref.title = (rid) => { const r = byId.get(rid); return r ? `${r.citation || r.title} (${rid})` : rid; };

    const base = new Map();
    for (const r of cands) {
      const rid = r.team_rule_id;
      const st = statusAt(r.legal_state, r.effective_date, asOf);
      if (st === "failed") continue;
      const unverifiedCity = !!(unverified && r.jurisdiction === unverified);
      const ev = st !== "pending" ? evalCoverage(r.cov, facts, asOf, ref) : null;
      let result;
      if (unverifiedCity) result = st === "in_force" ? "unknown" : st === "not_yet_effective" ? "not_yet_effective" : "pending";
      else if (st === "pending") result = "pending";
      else if (st === "not_yet_effective") { if (ev.value === false) continue; result = "not_yet_effective"; }
      else { if (ev.value === false) continue; result = ev.value === true ? "applies" : "unknown"; }
      base.set(rid, { rule: r, status: st, result, ev, unverified_city: unverifiedCity, by: null });
    }
    const orig = new Map([...base].map(([k, b]) => [k, b.result]));
    for (const [rid, b] of base) {
      if (b.result !== "applies" && b.result !== "unknown") continue;
      for (const oid of b.rule.overrides || []) {
        if (!base.has(oid)) continue;
        if (orig.get(oid) === "applies") { b.result = "superseded"; b.by = oid; break; }
        if (orig.get(oid) === "unknown" && b.result === "applies") {
          const o = base.get(oid);
          const hard = !o.ev || o.unverified_city || o.ev.hard !== false;
          if (hard) { b.result = "unknown"; b.by = oid; }
          else (b.may_yield = b.may_yield || []).push(oid);
        }
      }
    }
    const entries = [];
    for (const rid of [...base.keys()].sort()) {
      const b = base.get(rid), r = b.rule;
      const partners = [...(partnersOf.get(rid) || [])].filter((p) => base.has(p)).sort();
      const flag = partners.length > 0 || (!!r.conflict_flag && !(partnersOf.get(rid) && partnersOf.get(rid).size));
      entries.push(makeEntry(r, b, base, partners, flag, asOf, basis, facts));
    }
    return { address_id: addr.address_id, as_of: asOf, city, city_basis: basis, entries };
  }

  function headOf(r) {
    const cite = r.citation || r.title;
    const where = r.level === "state" ? "Statewide rule" : `${r.jurisdiction} rule`;
    const plain = String(r.plain_en || "").replace(/\s+/g, " ").trim().replace(/[. ]+$/, "");
    return plain ? `${plain} (${where}: ${cite})` : `${where} (${cite})`;
  }

  function makeEntry(r, b, base, partners, flag, asOf, basis, facts) {
    const rid = r.team_rule_id, res = b.result, ev = b.ev;
    const where = headOf(r);
    const caveats = ev ? ev.caveats.slice() : [];
    let text;
    if (res === "applies") {
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
    if (res === "applies" && caveats.length) text += " Not checked from parcel data: " + join(caveats, 3) + ".";
    if (res === "applies" && b.may_yield) {
      for (const oid of b.may_yield) {
        const o = base.get(oid);
        const ob = o.ev ? o.ev.blockers : [];
        text += ` Could yield to ${o.rule.citation || o.rule.title} (${oid}), which cannot be checked here: ${join(ob, 1, 160)}.`;
      }
    }
    if (ev && ev.notes.length && (res === "applies" || res === "unknown")) text += " " + cap1(ev.notes[0]) + ".";
    if (basis === "city_dataset" && r.level === "city") text += " City taken from the parcel dataset (not geocoded).";
    if (flag) {
      text += " Flagged: possible conflict with " +
        (partners.length ? partners.map((p) => `${base.get(p).rule.citation || base.get(p).rule.title} (${p})`).join(", ") : "another rule") +
        "; needs human review.";
    }
    let conf = res === "applies" ? (caveats.length || b.may_yield ? "medium" : "high") : res === "unknown" ? "low" : "high";
    const ec = r.confidence === null || r.confidence === undefined ? 1 : r.confidence;
    if (ec < 0.7 && conf === "high") conf = "medium";
    return { team_rule_id: rid, result: res, explanation: text, conflict_flag: flag, confidence: conf, caveats, partners, superseded_by: res === "superseded" || res === "unknown" ? b.by : null, may_yield: b.may_yield || [] };
  }

  const api = { QUERY_DATE, RESULT_ORDER, parseDate, statusAt, lookupAddress, evalCoverage };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NavEngine = api;
})(typeof self !== "undefined" ? self : this);
