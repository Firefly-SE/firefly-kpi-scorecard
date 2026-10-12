#!/usr/bin/env python3
"""Sanity-check a weekly snapshot before it is trusted.

  python3 scripts/validate_snapshot.py            # newest snapshot
  python3 scripts/validate_snapshot.py 2026-W42   # a specific week

Exit 0: usable (warnings may print).  Exit 1: the snapshot is wrong and must be
fixed or re-collected before building a report.

What it catches, and why:
  * structure   - the report reads fixed key paths; a missing one renders "no data".
  * account_id  - must match config.json, or deltas would mix accounts.
  * 0 vs null   - a count of exactly 0 on a metric that is almost never 0 is
                  usually a failed collection written as zero. Warned, not blocked.
  * scope drop  - runner / CI workspace counts that fall by more than half against
                  the previous snapshot usually mean the API key can see only part of
                  the project tree, not that the workspaces vanished. Warned.
"""

import json
import sys

from build_report import ROOT, SNAP_DIR, derive, g, load_config

REQUIRED = [
    ("week",), ("collected_at",), ("account_id",), ("account_name",),
    ("inventory", "codified"), ("inventory", "unmanaged"),
    ("inventory", "ghost"), ("inventory", "drifted"),
]
EXPECTED_SECTIONS = ["iac_estate", "governance", "automation", "integrations", "adoption", "events"]
RARELY_ZERO = [("inventory", "codified"), ("inventory", "unmanaged"),
               ("iac_estate", "stacks_total"), ("governance", "policies_total")]
SCOPE_SENSITIVE = [("automation", "runner_workspaces"), ("automation", "ci_workspaces")]


def main():
    cfg = load_config()
    files = sorted(SNAP_DIR.glob("*.json"))
    if not files:
        sys.exit(f"no snapshots in {SNAP_DIR}")
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    snaps = [(p, json.loads(p.read_text())) for p in files]
    snaps.sort(key=lambda t: t[1].get("week", ""))
    idx = len(snaps) - 1
    if wanted:
        idx = next((i for i, (_, s) in enumerate(snaps) if s.get("week") == wanted), None)
        if idx is None:
            sys.exit(f"no snapshot with week {wanted}")
    path, snap = snaps[idx]
    prev = snaps[idx - 1][1] if idx > 0 else None

    errors, warnings = [], []

    for key in REQUIRED:
        if g(snap, *key) is None:
            errors.append(f"missing required field {'.'.join(key)}")
    for sec in EXPECTED_SECTIONS:
        if not isinstance(snap.get(sec), dict):
            warnings.append(f"section '{sec}' is missing - its metrics will read 'no data'")

    if path.stem != snap.get("week"):
        warnings.append(f"filename {path.name} does not match week {snap.get('week')}")

    want_id = (cfg.get("account_id") or "").strip()
    if want_id and snap.get("account_id") != want_id:
        errors.append(f"account_id {snap.get('account_id')} != config.json {want_id}")

    for key in RARELY_ZERO:
        if g(snap, *key) == 0:
            warnings.append(f"{'.'.join(key)} is 0 - if the call failed this must be null, not 0")

    if prev:
        for key in SCOPE_SENSITIVE:
            now, before = g(snap, *key), g(prev, *key)
            if isinstance(now, int) and isinstance(before, int) and before >= 4 and now < before / 2:
                warnings.append(
                    f"{'.'.join(key)} fell {before} -> {now} (more than half). Check that the "
                    "API key sees the whole project tree before believing this; if it does not, "
                    "record null with a note instead of the low number.")

    d = derive(snap)
    print(f"{path.name}: coverage {d['coverage_pct']}%  total assets {d['total_assets']}")
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        sys.exit(1)
    print("ok" if not warnings else "ok (with warnings)")


if __name__ == "__main__":
    main()
