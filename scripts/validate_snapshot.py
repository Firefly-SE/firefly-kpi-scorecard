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


def cloud_waste_checks(snap, prev):
    """Cloud Waste consistency: totals against rows, suspicious zero, big drops."""
    cw = snap.get("cloud_waste")
    if not isinstance(cw, dict):
        return []
    out = []
    if cw.get("note"):
        out.append("cloud_waste.note is no longer shown in reports. Put process detail in "
                   "cloud_waste.collector_note (never shown), or a sentence a reader needs in "
                   "cloud_waste.caveats.")
    pols = cw.get("policies")
    if isinstance(pols, list):
        costed = [p for p in pols if isinstance(p, dict) and isinstance(p.get("monthly_usd"), (int, float))]
        cost = round(sum(p["monthly_usd"] for p in costed), 2)
        assets = sum(p.get("total_assets") or 0 for p in costed)
        if cw.get("monthly_usd") is not None and abs(cw["monthly_usd"] - cost) > 0.01:
            out.append(f"cloud_waste.monthly_usd {cw['monthly_usd']} != sum of costed policies {cost}")
        if cw.get("violating_assets") is not None and cw["violating_assets"] != assets:
            out.append(f"cloud_waste.violating_assets {cw['violating_assets']} != "
                       f"sum over costed policies {assets} (policies with no cost model are not counted)")
    leak = cw.get("leakage") or {}
    if isinstance(leak.get("assets"), int) and isinstance(leak.get("costed_assets"), int) \
            and leak["costed_assets"] > leak["assets"]:
        out.append("cloud_waste.leakage.costed_assets is larger than leakage.assets")
    if cw.get("monthly_usd") == 0 and (cw.get("violating_assets") or 0) == 0 and pols:
        out.append("cloud_waste.monthly_usd is 0 with policies listed: no cost model, or a failed "
                   "collection written as 0? Use null for the second.")
    pcw = (prev or {}).get("cloud_waste")
    if isinstance(pcw, dict):
        for label, now, before in (
                ("cloud_waste.monthly_usd", cw.get("monthly_usd"), pcw.get("monthly_usd")),
                ("cloud_waste.leakage.monthly_usd", leak.get("monthly_usd"), (pcw.get("leakage") or {}).get("monthly_usd"))):
            if isinstance(now, (int, float)) and isinstance(before, (int, float)) \
                    and before > 0 and now < before / 2:
                out.append(f"{label} fell {before:,.2f} -> {now:,.2f} (more than half). This may be a "
                           "real cleanup, or a key that cannot see every project. Check the key's "
                           "project membership; if it is the key, record null with a note.")
    return out


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

    warnings += cloud_waste_checks(snap, prev)

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
