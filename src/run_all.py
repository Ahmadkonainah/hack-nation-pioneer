#!/usr/bin/env python3
"""
Run the whole pipeline, one step after the other, and pack the results for review.

    python src\\run_all.py                 all steps
    python src\\run_all.py --from enrich   start at a step (cached model answers are reused, so reruns are cheap)
    python src\\run_all.py --steps lookup,changes,selfcheck

Steps: extract, consolidate, enrich, resolve, lookup, changes, build_web, selfcheck
Writes run_report.txt (everything printed) and outputs_for_claude.zip (the files to send for review).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("NAVIGATOR_ROOT") or HERE.parent)
STEPS = ["extract", "consolidate", "enrich", "resolve", "lookup", "changes", "build_web", "selfcheck"]
PACK = ["outputs/rules.json", "outputs/rules_enriched.json", "outputs/lookups.json", "outputs/changes.json", "outputs/changes_detailed.json",
        "outputs/resolved_addresses.json", "outputs/extraction_log.json", "outputs/consolidation_log.json", "outputs/enrichment_log.json",
        "outputs/rejected.json", "outputs/selfcheck.json", "run_report.txt"]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", choices=STEPS, default=None)
    ap.add_argument("--steps", default="")
    args = ap.parse_args()
    steps = [s.strip() for s in args.steps.split(",") if s.strip()] or (STEPS[STEPS.index(args.start):] if args.start else STEPS)
    bad = [s for s in steps if s not in STEPS]
    if bad:
        sys.exit(f"unknown step(s): {bad}. Choose from {STEPS}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    report = open(ROOT / "run_report.txt", "w", encoding="utf-8")

    def say(s=""):
        print(s)
        report.write(s + "\n")
        report.flush()

    t0, failed = time.time(), None
    for i, s in enumerate(steps, 1):
        say(f"\n{'=' * 70}\n[{i}/{len(steps)}] {s}\n{'=' * 70}")
        p = subprocess.Popen([sys.executable, str(HERE / f"{s}.py")], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            say(line.rstrip("\n"))
        rc = p.wait()
        if rc != 0 and s != "selfcheck":
            failed = (s, rc)
            say(f"\nSTOPPED: step '{s}' ended with an error (code {rc}). Fix it or send me run_report.txt.")
            break
    say(f"\nFinished in {time.time() - t0:.0f}s" + (f" (stopped at {failed[0]})" if failed else ""))
    report.close()
    zp = ROOT / "outputs_for_claude.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in PACK:
            if (ROOT / f).exists():
                z.write(ROOT / f, f)
    print(f"\nSend me this one file: {zp}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
