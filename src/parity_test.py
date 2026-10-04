#!/usr/bin/env python3
"""Check that the browser engine (web/engine.js) gives exactly the same answers as the Python engine
for every address on many as-of dates. Needs Node.js (https://nodejs.org); skipped if it is not installed.

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
import extract as E  # noqa: E402
import lookup as L  # noqa: E402
import build_web as B  # noqa: E402

DATES = ["2020-01-01", "2023-06-24", "2024-04-01", "2024-07-01", "2025-12-31", "2026-01-02", "2026-03-01", "2026-05-01", "2026-07-01",
         "2026-10-01", "2027-01-01", "2027-07-01", "2027-07-02", "2030-12-31"]
JS = r"""
const E = require(process.argv[2]); const fs = require('fs');
const d = JSON.parse(fs.readFileSync(process.argv[3], 'utf8')); const dates = JSON.parse(process.argv[4]);
const out = {};
for (const dt of dates) { out[dt] = {};
  for (const a of d.addresses) {
    const r = E.lookupAddress({address_id: a.id, state: a.state, facts: a.facts, city: a.city, basis: a.basis, unverified: a.unverified}, d.rules, dt);
    out[dt][a.id] = r.entries.map(e => [e.team_rule_id, e.result, e.explanation, e.conflict_flag, e.confidence]);
  } }
process.stdout.write(JSON.stringify(out));
"""


def main() -> int:
    if not shutil.which("node"):
        print("Node.js not found: parity test skipped (it is an optional developer check).")
        return 0
    rules, resolved, rows, enriched, srcname = L.load_inputs()
    B.main()
    web = E.ROOT / "web"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(JS)
    data = json.loads((web / "data.js").read_text(encoding="utf-8")[len("window.NAV_DATA = "):].rstrip().rstrip(";"))
    jp = Path(tempfile.gettempdir()) / "nav_data.json"
    jp.write_text(json.dumps(data), encoding="utf-8")
    r = subprocess.run(["node", f.name, str(web / "engine.js"), str(jp), json.dumps(DATES)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        return 1
    js = json.loads(r.stdout)
    bad = n = 0
    for dt in DATES:
        for row in rows:
            res = resolved.get(row["address_id"]) or {"matched": False}
            py = [[e["team_rule_id"], e["result"], e["explanation"], e["conflict_flag"], e["_confidence"]] for e in G.lookup_address(row, res, rules, dt)["entries"]]
            n += 1
            if py != js[dt][row["address_id"]]:
                bad += 1
                if bad <= 3:
                    for a, b in zip(py, js[dt][row["address_id"]]):
                        if a != b:
                            print("DIFF", dt, row["address_id"], "\n  py:", a, "\n  js:", b)
                            break
                    if len(py) != len(js[dt][row["address_id"]]):
                        print("DIFF length", dt, row["address_id"], len(py), len(js[dt][row["address_id"]]))
    print(f"{n - bad}/{n} address-date answers identical between Python and JavaScript ({len(DATES)} dates x {len(rows)} addresses)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
