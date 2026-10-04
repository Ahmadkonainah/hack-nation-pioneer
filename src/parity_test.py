#!/usr/bin/env python3
"""Check that the browser engine (web/engine.js) gives exactly the same answers as the Python engine
for every address on many as-of dates. Needs Node.js (https://nodejs.org); skipped if it is not installed.

Why: the lookup logic exists twice (engine.py and engine.js). If the copies drift, the app would show a person
a different answer from the submitted files. Each answer is compared in full: rule id, result, explanation,
conflict flag and confidence. Exit code 1 on any difference.

    python src\\parity_test.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as G  # noqa: E402
from common import ROOT  # noqa: E402
import lookup as L  # noqa: E402
import build_web as B  # noqa: E402

# Old and far dates, plus the days on both sides of the change-test dates (T1: 2025-12-31 and 2026-01-02; T3: 2026-10-01
# and 2027-07-02, around the FAIR Act's own date 2027-07-01), where an off-by-one in the date logic would show up.
DATES = ["2020-01-01", "2023-06-24", "2024-04-01", "2024-07-01", "2025-12-31", "2026-01-02", "2026-03-01", "2026-05-01", "2026-07-01",
         "2026-10-01", "2027-01-01", "2027-07-01", "2027-07-02", "2030-12-31"]
# Facts a person can type in on the Lookup page ("what if I know more about this building").
WHATIFS = [{"year_built": 1950}, {"year_built": 1979}, {"year_built": 2005}, {"units": 2}, {"units": 12}, {"owner_occupied": True},
           {"owner_occupied": False}, {"year_built": 1975, "units": 4, "owner_occupied": False}]
# A small Node script: loads the browser engine and the same data.js content the app ships with, runs every address
# on every date, and prints the answers as JSON (by date, then address id; what-if answers under "__whatif").
JS = r"""
const E = require(process.argv[2]); const fs = require('fs');
const d = JSON.parse(fs.readFileSync(process.argv[3], 'utf8')); const dates = JSON.parse(process.argv[4]);
const whatifs = JSON.parse(process.argv[5]);
const out = {};
for (const dt of dates) { out[dt] = {};
  for (const a of d.addresses) {
    const r = E.lookupAddress({address_id: a.id, state: a.state, facts: a.facts, city: a.city, basis: a.basis, unverified: a.unverified}, d.rules, dt);
    out[dt][a.id] = r.entries.map(e => [e.team_rule_id, e.result, e.explanation, e.conflict_flag, e.confidence]);
  } }
out.__whatif = {};
whatifs.forEach((w, i) => { out.__whatif[i] = {};
  for (const a of d.addresses) {
    const r = E.lookupAddress({address_id: a.id, state: a.state, facts: a.facts, city: a.city, basis: a.basis, unverified: a.unverified}, d.rules, '2026-10-01', w);
    out.__whatif[i][a.id] = r.entries.map(e => [e.team_rule_id, e.result, e.explanation, e.conflict_flag, e.confidence]);
  } });
process.stdout.write(JSON.stringify(out));
"""


def main() -> int:
    """Run both engines over every address and date and compare. Returns 1 on any difference; 0 if all match or Node.js is missing."""
    if not shutil.which("node"):
        print("Node.js not found: parity test skipped (it is an optional developer check).")
        return 0
    rules, resolved, rows, enriched, srcname = L.load_inputs()
    # Rebuild data.js first, so the JavaScript side tests exactly the data the Python side uses, not a stale file.
    B.main()
    web = ROOT / "web"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(JS)
    # data.js is JavaScript, not JSON: strip the "window.NAV_DATA = " prefix and the closing ";" (format set in build_web.py)
    data = json.loads((web / "data.js").read_text(encoding="utf-8")[len("window.NAV_DATA = "):].rstrip().rstrip(";"))
    # The data goes through a file: it is far too large for a command-line argument. Dates and what-ifs are small, so they go as arguments.
    jp = Path(tempfile.gettempdir()) / "nav_data.json"
    jp.write_text(json.dumps(data), encoding="utf-8")
    r = subprocess.run(["node", f.name, str(web / "engine.js"), str(jp), json.dumps(DATES), json.dumps(WHATIFS)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        return 1
    js = json.loads(r.stdout)
    # Compare as lists, not sets: the order of the entries is part of what a person sees. The Python side calls the
    # confidence "_confidence"; the JavaScript side calls it "confidence". Both fill the fifth slot.
    bad = n = 0
    for dt in DATES:
        for row in rows:
            res = resolved.get(row["address_id"]) or {"matched": False}
            py = [[e["team_rule_id"], e["result"], e["explanation"], e["conflict_flag"], e["_confidence"]] for e in G.lookup_address(row, res, rules, dt)["entries"]]
            n += 1
            if py != js[dt][row["address_id"]]:
                bad += 1
                if bad <= 3:       # show only the first few: one cause usually repeats across hundreds of addresses
                    for a, b in zip(py, js[dt][row["address_id"]]):
                        if a != b:
                            print("DIFF", dt, row["address_id"], "\n  py:", a, "\n  js:", b)
                            break
                    if len(py) != len(js[dt][row["address_id"]]):
                        print("DIFF length", dt, row["address_id"], len(py), len(js[dt][row["address_id"]]))
    print(f"{n - bad}/{n} address-date answers identical between Python and JavaScript ({len(DATES)} dates x {len(rows)} addresses)")
    # What-if answers: the date is fixed at 2026-10-01, so only the typed-in facts vary. Keys are strings because they went through JSON.
    wbad = wn = 0
    for i, w in enumerate(WHATIFS):
        for row in rows:
            res = resolved.get(row["address_id"]) or {"matched": False}
            py = [[e["team_rule_id"], e["result"], e["explanation"], e["conflict_flag"], e["_confidence"]] for e in G.lookup_address(row, res, rules, "2026-10-01", what_if=w)["entries"]]
            wn += 1
            if py != js["__whatif"][str(i)][row["address_id"]]:
                wbad += 1
                if wbad <= 3:
                    print("WHAT-IF DIFF", w, row["address_id"])
    print(f"{wn - wbad}/{wn} what-if answers identical ({len(WHATIFS)} sets of typed-in facts x {len(rows)} addresses)")
    return 1 if (bad or wbad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
