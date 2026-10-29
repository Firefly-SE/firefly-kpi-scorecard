#!/usr/bin/env python3
"""Build the Firefly KPI scorecard HTML from the snapshots on file.

Reads every <home>/snapshots/*.json, derives the KPI set defined in
KPI-FRAMEWORK.md, computes week-over-week deltas against the previous
snapshot, and writes <home>/reports/firefly-kpi-scorecard.html.

<home> is ./data by default; override with the KPI_HOME environment variable.
Per-account settings come from <home>/config.json, <home>/targets.json and
<home>/caveats.md (all written by setup.py).

Stdlib only. A metric that is absent or null renders as "no data", never 0.
"""

import datetime
import json
import os
import pathlib
import sys
from html import escape as _esc
from urllib.parse import quote

REPO = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("KPI_HOME") or REPO / "data").expanduser().resolve()
SNAP_DIR = ROOT / "snapshots"
OUT = ROOT / "reports" / "firefly-kpi-scorecard.html"

APP_URL = "https://app.firefly.ai"
CLOUD_WASTE = "Cloud Waste"
URL_GOVERNANCE = f"{APP_URL}/governance"
URL_LEAKAGE = f"{APP_URL}/inventory?defaultView=false&state=modified"


def url_policy(pid):
    return f"{APP_URL}/governance?id={quote(str(pid), safe='')}"


def url_policy_assets(pid):
    return f"{APP_URL}/inventory?defaultView=false&classifications={quote(str(pid), safe='')}"


# Suggested targets, applied by setup.py when the user accepts the defaults. Scale-dependent
# counts (unmanaged, drifted, workspaces) are deliberately absent: a target that is right
# for one estate prints meaningless green/red on another. Set those per account.
DEFAULT_TARGETS = {
    "coverage_pct": 75, "ghost_ratio": 15, "drift_rate": 0.10,
    "cis": 90, "nist": 90, "hipaa": 95, "frameworks_scored": 5, "policies_custom": 10,
    "guardrails": 10, "guardrail_types": 4,
    "wau": 20, "scopes_touched": 10, "top_scope_share": 40, "top_user_share": 50,
}


def load_json(name, default):
    p = ROOT / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"{p} is not valid JSON: {e}")


def load_config():
    return load_json("config.json", {})


def load_targets():
    """Per-account targets. A key set to null, or absent, means 'no target'."""
    return load_json("targets.json", {})


def standing_caveats():
    """Bullets ('- ' lines) from caveats.md; everything else is ignored."""
    p = ROOT / "caveats.md"
    if not p.exists():
        return []
    return [ln[2:].strip() for ln in p.read_text().splitlines()
            if ln.startswith("- ") and ln[2:].strip()]


def load_snapshots(week_filter=None):
    """Load snapshots ordered by their internal week field.

    Refuses to mix accounts: if config.json names an account_id, every snapshot
    must carry it. Without a config, all snapshots must at least agree.
    """
    snaps = []
    for p in SNAP_DIR.glob("*.json"):
        try:
            snaps.append(json.loads(p.read_text()))
        except json.JSONDecodeError as e:
            sys.exit(f"{p} is not valid JSON: {e}")
    snaps.sort(key=lambda s: s.get("week", ""))
    want = (load_config().get("account_id") or "").strip()
    ids = {s.get("account_id") for s in snaps}
    if want:
        bad = sorted(s.get("week", "?") for s in snaps if s.get("account_id") != want)
        if bad:
            sys.exit(f"snapshot(s) {', '.join(bad)} are not for account_id {want} "
                     "(config.json). Refusing to compute deltas across accounts.")
    elif len(ids) > 1:
        sys.exit(f"snapshots mix account ids {sorted(map(str, ids))}; refusing to compute deltas.")
    if week_filter:
        keep = {w.upper() for w in week_filter}
        nums = {w.lstrip("W") for w in keep}
        snaps = [s for s in snaps
                 if s["week"].upper() in keep or s["week"].split("-W")[-1] in nums]
    return snaps

# ---------------------------------------------------------------- derivation

def g(d, *path):
    """Nested get that returns None on any missing link."""
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def pct(num, den):
    if num is None or den in (None, 0):
        return None
    return round(100.0 * num / den, 2)


def derive(snap):
    """Flatten a snapshot into the metric namespace the report renders."""
    cod = g(snap, "inventory", "codified")
    unm = g(snap, "inventory", "unmanaged")
    gho = g(snap, "inventory", "ghost")
    dri = g(snap, "inventory", "drifted")

    managed = None if cod is None or dri is None else cod + dri
    in_cloud = None if managed is None or unm is None else managed + unm
    total = None if in_cloud is None or gho is None else in_cloud + gho

    fw = g(snap, "governance", "frameworks") or {}
    scored = [k for k, v in fw.items() if isinstance(v, (int, float))]

    by_type = g(snap, "automation", "guardrails_by_type") or {}
    gr_types = sum(1 for v in by_type.values() if v)

    stacks = g(snap, "iac_estate", "stacks_total")
    tf = g(snap, "iac_estate", "stacks_terraform")

    integ = g(snap, "integrations") or {}
    conn = [integ.get(k) for k in ("aws", "azurerm", "gcp", "k8s")]
    conn_total = sum(v for v in conn if isinstance(v, int)) or None

    acts = g(snap, "adoption", "weekly_activities")
    top = g(snap, "adoption", "top_scope_count")
    users = g(snap, "adoption", "users") or []
    top_user = max((u.get("count", 0) for u in users), default=None)

    waste_monthly = g(snap, "cloud_waste", "monthly_usd")
    leak_monthly = g(snap, "cloud_waste", "leakage", "monthly_usd")

    return {
        "total_assets": total,
        "coverage_pct": pct(managed, in_cloud),
        "codified": cod,
        "unmanaged": unm,
        "ghost": gho,
        "ghost_ratio": pct(gho, managed),
        "drifted": dri,
        "drift_rate": pct(dri, managed),
        "accounts_connected": conn_total,
        "stacks_total": stacks,
        "stacks_terraform": tf,
        "stacks_other": None if stacks is None or tf is None else stacks - tf,
        "cis": fw.get("CIS"),
        "nist": fw.get("NIST"),
        "hipaa": fw.get("HIPAA"),
        "frameworks_scored": len(scored),
        "frameworks_total": len(fw) or None,
        "policies_total": g(snap, "governance", "policies_total"),
        "policies_custom": g(snap, "governance", "policies_custom"),
        "guardrails": g(snap, "automation", "guardrails_total"),
        "guardrail_types": gr_types,
        "ci_workspaces": g(snap, "automation", "ci_workspaces"),
        "runner_workspaces": g(snap, "automation", "runner_workspaces"),
        "wau": g(snap, "adoption", "weekly_active_users"),
        "activities": acts,
        "scopes_touched": g(snap, "adoption", "scopes_touched"),
        "top_scope_share": pct(top, acts),
        "top_user_share": pct(top_user, acts),
        "click_ops": g(snap, "events", "click_ops"),
        "denied": g(snap, "events", "denied"),
        "waste_yearly": None if waste_monthly is None else round(waste_monthly * 12, 2),
        "waste_monthly": waste_monthly,
        "waste_assets": g(snap, "cloud_waste", "violating_assets"),
        "leakage_yearly": None if leak_monthly is None else round(leak_monthly * 12, 2),
        "leakage_costed": g(snap, "cloud_waste", "leakage", "costed_assets"),
        "leakage_assets": g(snap, "cloud_waste", "leakage", "assets"),
    }


# --------------------------------------------------------------- KPI catalog
# (key, label, unit, direction, target, note)
#   unit: "n" count, "%" percent, "score", "usd" whole dollars, "usd2" dollars and cents
#   direction: "up" good rising, "down" good falling, "flat" informational
#   target: always None here. Real targets come from <home>/targets.json via catalog().
_CATALOG = [
    ("Inventory & Coverage", "What Firefly sees and how much of it is owned by code.", [
        ("coverage_pct",       "IaC coverage",        "%",     "up",   None, "(codified + drifted) / assets present in cloud"),
        ("total_assets",       "Total assets",        "n",     "flat", None, "all four states summed"),
        ("codified",           "Codified",            "n",     "up",   None, "managed by IaC"),
        ("unmanaged",          "Unmanaged",           "n",     "down", None, "in cloud, absent from IaC"),
        ("ghost",              "Ghost",               "n",     "down", None, "in state, gone from cloud"),
        ("ghost_ratio",        "Ghost ratio",         "%",     "down", None, "state-file hygiene"),
        ("drifted",            "Drifted",             "n",     "down", None, "config diverged from code"),
        ("drift_rate",         "Drift rate",          "%",     "down", None, "drifted / managed"),
        ("accounts_connected", "Cloud accounts",      "n",     "up",   None, "active integrations, partial — see caveats"),
    ]),
    ("IaC Estate", "State files discovered across every backend and IaC flavour.", [
        ("stacks_total",     "Total stacks",       "n", "up",   None, "discovered state files"),
        ("stacks_terraform", "Terraform",          "n", "flat", None, "stateType = terraform"),
        ("stacks_other",     "Non-Terraform",      "n", "flat", None, "Pulumi, CFN, Helm, K8s, OpenTofu"),
    ]),
    ("Governance & Posture", "Framework scores and the policy library behind them.", [
        ("cis",                "CIS score",         "score", "up",   None, "0–100"),
        ("nist",               "NIST score",        "score", "up",   None, "0–100"),
        ("hipaa",              "HIPAA score",       "score", "up",   None, "0–100"),
        ("frameworks_scored",  "Frameworks scored", "n",     "up",   None, "of the frameworks requested"),
        ("policies_total",     "Policies available","n",     "flat", None, "OPA insights in account"),
        ("policies_custom",    "Custom policies",   "n",     "up",   None, "account-authored"),
    ]),
    ("Automation & Guardrails", "Pre-apply enforcement: guardrails and Firefly-run workspaces.", [
        ("guardrails",        "Guardrails",         "n", "up", None, "total rules configured"),
        ("guardrail_types",   "Types in use",       "n", "up", None, "of policy, cost, resource, tag"),
        ("ci_workspaces",     "CI workspaces",      "n", "up", None, "pipelines reporting in"),
        ("runner_workspaces", "Runner workspaces",  "n", "up", None, "Firefly-native IaC"),
    ]),
    ("Adoption", "Trailing 7 days: who is using the platform and how broadly.", [
        ("wau",             "Weekly active users",  "n", "up",   None, "distinct human actors"),
        ("activities",       "Activities",          "n", "up",   None, "user-attributed audit events"),
        ("scopes_touched",   "Feature breadth",     "n", "up",   None, "distinct product areas touched"),
        ("top_scope_share",  "Top-scope share",     "%", "down", None, "concentration in one feature"),
        ("top_user_share",   "Top-user share",      "%", "down", None, "concentration in one person — key-person risk"),
        ("click_ops",        "ClickOps events",     "n", "down", None, "console changes bypassing IaC"),
        ("denied",           "Denied events",       "n", "down", None, "blocked API calls"),
    ]),
    (CLOUD_WASTE, "Spend Firefly policies flag as waste, and cost that has crept in through drift. Every dollar is list price.", [
        ("waste_yearly",    "Cloud waste / year",   "usd",  "down", None, "list price, monthly × 12"),
        ("waste_monthly",   "Cloud waste / month",  "usd2", "down", None, "list price, costed policies only"),
        ("waste_assets",    "Violating assets",     "n",    "down", None, "costed policies only"),
        ("leakage_yearly",  "Cost leakage / year",  "usd",  "down", None, "list price, positive drift deltas × 12"),
        ("leakage_costed",  "Drifted, with cost",   "n",    "down", None, "the assets behind the dollars above"),
        ("leakage_assets",  "Drifted, all",         "n",    "down", None, "most carry no cost impact"),
    ]),
]

KPI_KEYS = [m[0] for _, _, ms in _CATALOG for m in ms]

# Tooltip text per KPI, shown on the tile's info icon. Mirrors KPI-FRAMEWORK.md:
# change the definition there first, then update it here.
METRIC_HELP = {
    "coverage_pct": "(codified + drifted) / (codified + drifted + unmanaged). Ghosts are excluded because they no longer exist in the cloud. Treat as a floor: repo-scoped attribution can read low.",
    "total_assets": "Codified + unmanaged + ghost + drifted. Tracked, not targeted.",
    "codified": "Assets in the cloud that are managed by IaC.",
    "unmanaged": "Assets that exist in the cloud but are absent from IaC.",
    "ghost": "Assets recorded in IaC state that no longer exist in the cloud.",
    "ghost_ratio": "ghost / (codified + drifted). A high value means state files outlive the resources they describe, or crawlers are reading stale backends.",
    "drifted": "Assets in both the cloud and IaC whose configuration has diverged.",
    "drift_rate": "drifted / (codified + drifted).",
    "accounts_connected": "Active integrations summed across the configured providers. A provider that failed to collect is left out, so read this as a floor.",
    "stacks_total": "IaC state files Firefly has discovered, across all backends.",
    "stacks_terraform": "Discovered stacks with stateType = terraform.",
    "stacks_other": "Total stacks minus Terraform: Pulumi, CloudFormation, Helm, Kubernetes, OpenTofu.",
    "cis": "CIS posture score, 0-100. Shows 'no data' when the framework has no subscribed policies; that is not a zero.",
    "nist": "NIST posture score, 0-100. Shows 'no data' when the framework has no subscribed policies; that is not a zero.",
    "hipaa": "HIPAA posture score, 0-100. Shows 'no data' when the framework has no subscribed policies; that is not a zero.",
    "frameworks_scored": "Frameworks that returned a non-null score. A null score means no subscribed policies, not a failing grade.",
    "policies_total": "All OPA policy insights available in the account.",
    "policies_custom": "Policies authored by the account, as opposed to Firefly defaults.",
    "guardrails": "Total guardrail rules configured. Zero guardrails means Firefly is observing only, so everything above is detection after the fact.",
    "guardrail_types": "Distinct guardrail types in use, out of policy, cost, resource and tag.",
    "ci_workspaces": "Pipelines reporting into Firefly. This is a count, not a measure of workspace health. A count of exactly 100 may be a page-size floor.",
    "runner_workspaces": "Firefly-native IaC workspaces: the sum of workspaceCount across the whole project tree, not just the root. A key that cannot see the projects reads low.",
    "wau": "Distinct human users with audit-log activity in the trailing 7 days.",
    "activities": "User-attributed audit-log events in the trailing 7 days.",
    "scopes_touched": "Distinct product areas (audit-log scopes) touched in the trailing 7 days.",
    "top_scope_share": "The largest scope's share of all activity. Lower means usage is spread across more features.",
    "top_user_share": "The largest single user's share of all activity. High means key-person risk.",
    "click_ops": "Events where infrastructure was changed from the cloud console, bypassing IaC. Track the absolute count, not a share of total events.",
    "denied": "Events that were blocked.",
    "waste_yearly": "The dashboard's Cloud Waste (Projected Savings) figure: monthly Cloud Waste cost × 12. List price, before any discount, commitment or credit. A key that cannot see every project reads low with no error.",
    "waste_monthly": "Sum of monthly cost across Cloud Waste policies that have a cost model. List price. Policies without a cost model add nothing here and are listed separately.",
    "waste_assets": "Sum of total_assets across the Cloud Waste policies that have a cost model, so the count matches the dollars beside it. An asset that violates two policies is counted twice. Policies with no cost model are listed in the table below and not counted here.",
    "leakage_yearly": "The dashboard's Cost Leakage (Drift Impact) figure: sum of the positive costDelta of drifted assets (state modified), × 12. List price. Negative deltas (savings) are excluded rather than netted off; this is not confirmed against a tenant that has any.",
    "leakage_costed": "Drifted assets with a positive costDelta: the assets that produce the leakage dollars. Read this count, not the all-drifted count, next to the dollar figure.",
    "leakage_assets": "Every asset in the modified (drifted) state. Ghost assets are counted separately and are not included, so this is smaller than a list that shows drifted and ghost assets together. Most usually carry no cost impact, so do not read this as the number of assets behind the leakage dollars.",
}

# key: (url, link text, hint). The Cloud Waste filter cannot be set from a URL, and state=modified
# opens drifted AND ghost assets, so each link says what it really opens. "Drifted, all" has no
# link on purpose: a link from its count (90) to a page of thousands of rows reads as a bug.
_OPEN_GOV = (URL_GOVERNANCE, "Open Governance, then pick Cloud Waste", None)
_OPEN_DRIFT = (URL_LEAKAGE, "Opens drifted and ghost assets",
               "Sort by Drift Cost Impact to see the ones behind the dollars.")
METRIC_LINKS = {
    "waste_yearly": _OPEN_GOV, "waste_monthly": _OPEN_GOV, "waste_assets": _OPEN_GOV,
    "leakage_yearly": _OPEN_DRIFT, "leakage_costed": _OPEN_DRIFT,
}

CLOUD_WASTE_CAVEATS = [
    "Every dollar figure is list price, before discounts, commitments and credits.",
    "A key that cannot see all projects returns low numbers with no error. If waste or leakage "
    "drops sharply, check the collecting key's project membership before believing it.",
    "A policy with no cost model has no cost field, so it adds nothing to the dollar totals. "
    "It is listed with its asset count and labelled \"no cost model\".",
    "Cost leakage counts two things: all drifted assets, and the subset with a positive cost "
    "impact. Only the second produces the dollars. The link opens drifted and ghost assets, unsorted.",
    "A change in a total can come from a policy appearing or disappearing, not only from waste "
    "rising or falling. Check the policy table before reading a tile's change as remediation.",
]

FIRST_WASTE_WEEK_CAVEAT = ("This is the first week with Cloud Waste data, so it has no "
                           "week-over-week change yet.")


def cloud_waste_visible(pillars):
    """False when hidden_metrics removed every Cloud Waste tile: then its caveats must go too."""
    return any(name == CLOUD_WASTE for name, _, _ in pillars)


def cloud_waste_caveats(snap, earlier=()):
    """Plain-text caveats for a snapshot that carries a cloud_waste section; empty otherwise.

    `earlier` is the snapshots before this one. The first-week line shows only when none of
    them has a cloud_waste block, so it cannot go stale on the third week.
    """
    cw = g(snap, "cloud_waste")
    if not isinstance(cw, dict):
        return []
    out = list(CLOUD_WASTE_CAVEATS)
    if not any(isinstance(g(s, "cloud_waste"), dict) for s in earlier):
        out.append(FIRST_WASTE_WEEK_CAVEAT)
    # Only these reach the reader. "Not collected" is generated here, not typed by the collector,
    # and `caveats` is an explicit reader-facing list. `collector_note` (process detail) and the
    # deprecated free-text `note` are never rendered.
    lead = []
    leak = cw.get("leakage") if isinstance(cw.get("leakage"), dict) else {}
    if cw.get("policies") is None or cw.get("monthly_usd") is None:
        lead.append("Cloud Waste policies were not collected this week, so the waste figures read “no data”.")
    if any(leak.get(k) is None for k in ("assets", "costed_assets", "monthly_usd")):
        lead.append("Cost leakage was not collected this week, so the leakage figures read “no data”.")
    lead += [str(c).strip() for c in (cw.get("caveats") or []) if str(c).strip()]
    return lead + out


def catalog():
    """The KPI catalog with this account's targets applied and hidden metrics removed.

    Targets come from targets.json; hidden metrics from config.json "hidden_metrics"
    (e.g. runner_workspaces for a tenant that does not use Firefly Runners).
    """
    targets = load_targets()
    hidden = set(load_config().get("hidden_metrics") or [])
    out = []
    for pillar, blurb, metrics in _CATALOG:
        rows = [(k, lbl, u, d, targets.get(k), n)
                for k, lbl, u, d, _, n in metrics if k not in hidden]
        if rows:
            out.append((pillar, blurb, rows))
    return out


# ------------------------------------------------------------------ renderers

def fmt(val, unit):
    if val is None:
        return "no data"
    if unit == "usd":
        return f"${val:,.0f}"
    if unit == "usd2":
        return f"${val:,.2f}"
    if unit == "%":
        return f"{val:,.2f}%".replace(".00%", "%")
    if unit == "score":
        return f"{val:g}"
    return f"{val:,.0f}"


# Decimals each unit is displayed with. A change that rounds to zero at that precision is "no
# change": printing it gives "−0.00 pt" or "+0%". Units with no entry compare at 1e-9.
DECIMALS = {"%": 2, "percent": 2, "usd2": 2, "usd": 0, "n": 0, "count": 0}


def shows_as_zero(diff, unit):
    return float(f"{abs(diff):.{DECIMALS.get(unit, 9)}f}") == 0


def baseline_pillars(pillars, derived, weeks):
    """Pillars where no metric has an earlier real value to compare against."""
    out = []
    for pillar, _, metrics in pillars:
        keys = [m[0] for m in metrics if derived[-1].get(m[0]) is not None]
        if keys and all(comparison(k, derived, weeks)["state"] != "ok" for k in keys):
            out.append(pillar)
    return out


def offweek_notes(pillars, derived, weeks, prev_week):
    """Per section, the comparison week when it is not the previous snapshot's week.

    "Cloud Waste against 2026-W43 (W44 not collected)", or "3 of 6 figures" when only some of
    the section's figures had to fall back. The chips already say "vs W43"; this makes the
    subtitle agree with them.
    """
    short = prev_week.split("-")[-1]
    notes = []
    for pillar, _, metrics in pillars:
        have = [m[0] for m in metrics if derived[-1].get(m[0]) is not None]
        by_week = {}
        for k in have:
            c = comparison(k, derived, weeks)
            if c["state"] == "ok" and c["week"] != prev_week:
                by_week[c["week"]] = by_week.get(c["week"], 0) + 1
        for wk, n in sorted(by_week.items(), reverse=True):
            who = pillar if n == len(have) else f"{pillar}: {n} of {len(have)} figures"
            notes.append(f"{who} against {wk} ({short} not collected)")
    return notes


def status(val, direction, target):
    """off | watch | ok | none — drives the tile's severity stripe."""
    if val is None or target is None or direction == "flat":
        return "none"
    if direction == "up":
        if val >= target:
            return "ok"
        return "off" if val < target * 0.6 else "watch"
    if val <= target:
        return "ok"
    return "off" if val > target * 1.6 else "watch"


MAX_LOOKBACK_WEEKS = 4


def _iso_monday(week):
    y, w = week.split("-W")
    return datetime.date.fromisocalendar(int(y), int(w), 1)


def comparison(key, derived, weeks):
    """What the latest value of `key` is compared against.

    Walks back to the most recent earlier snapshot that has a value for the metric
    (a real measurement, never a carried-forward one), up to MAX_LOOKBACK_WEEKS ISO
    weeks back. state: "ok" (value, week, gap in weeks), "baseline" (no earlier value
    at all) or "stale" (earlier values exist, but all older than the cap).
    """
    for d, wk in zip(reversed(derived[:-1]), reversed(weeks[:-1])):
        v = d.get(key)
        if v is None:
            continue
        gap = max(1, (_iso_monday(weeks[-1]) - _iso_monday(wk)).days // 7)
        if gap > MAX_LOOKBACK_WEEKS:
            return {"state": "stale", "value": None, "week": wk, "gap": gap}
        return {"state": "ok", "value": v, "week": wk, "gap": gap}
    return {"state": "baseline", "value": None, "week": None, "gap": None}


def delta_chip(cur, comp, unit, direction):
    if cur is None:
        return ""
    if comp["state"] == "baseline":
        return '<span class="chip chip-flat">baseline</span>'
    if comp["state"] == "stale":
        return (f'<span class="chip chip-flat" title="Last value was {comp["week"]}, '
                f'more than {MAX_LOOKBACK_WEEKS} weeks ago">no recent data</span>')
    vs = f" vs {comp['week'].split('-')[-1]}" if comp["gap"] > 1 else ""
    diff = cur - comp["value"]
    if shows_as_zero(diff, unit):
        return f'<span class="chip chip-flat">no change{vs}</span>'
    sign = "+" if diff > 0 else "−"
    mag = fmt(abs(diff), unit).replace("no data", "")
    if direction == "flat":
        tone = "flat"
    else:
        good = diff > 0 if direction == "up" else diff < 0
        tone = "good" if good else "bad"
    return f'<span class="chip chip-{tone}">{sign}{mag}{vs}</span>'


def sparkline(series, direction, weeks=None):
    """Trend line positioned by ISO week date, one step per calendar week.

    A week with no reading, whether a null or no snapshot at all, leaves a visible gap: the
    segment across it is dashed and nothing is interpolated. Using the ISO Monday (not the week
    number) keeps W52 to W01 one step apart. Without `weeks` it falls back to list position.
    No current reading: draw nothing, or the line would end on last week's value next to a tile
    that says "no data".
    """
    if not series or series[-1] is None:
        return ""
    real = [i for i, v in enumerate(series) if v is not None]
    if len(real) < 2:
        return ""
    if weeks:
        base = _iso_monday(weeks[real[0]])
        pos = lambda i: (_iso_monday(weeks[i]) - base).days // 7
    else:
        pos = lambda i: i - real[0]
    total = pos(len(series) - 1)
    if total <= 0:
        return ""
    pts = [series[i] for i in real]
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1
    w, h = 76, 22
    step = w / total
    xy = [(pos(i) * step, h - 2 - (series[i] - lo) / span * (h - 5), pos(i)) for i in real]
    runs, gaps = [[xy[0]]], []
    for a_, b_ in zip(xy, xy[1:]):
        if b_[2] - a_[2] == 1:
            runs[-1].append(b_)
        else:
            gaps.append((a_, b_))
            runs.append([b_])
    parts = []
    for r in runs:
        if len(r) >= 2:
            line = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in r)
            parts.append(f'<polygon points="{r[0][0]:.1f},{h} {line} {r[-1][0]:.1f},{h}" class="spark-fill"/>')
            parts.append(f'<polyline points="{line}" class="spark-line"/>')
        elif r is not runs[-1]:
            parts.append(f'<circle cx="{r[0][0]:.1f}" cy="{r[0][1]:.1f}" r="1.6" class="spark-pt"/>')
    for a_, b_ in gaps:
        parts.append(f'<polyline points="{a_[0]:.1f},{a_[1]:.1f} {b_[0]:.1f},{b_[1]:.1f}" class="spark-gap"/>')
    lx, ly, _ = xy[-1]
    parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.2" class="spark-dot"/>')
    trend = pts[-1] - pts[-2]
    if direction == "flat" or trend == 0:
        cls = "spark-flat"
    else:
        good = trend > 0 if direction == "up" else trend < 0
        cls = "spark-good" if good else "spark-bad"
    return (f'<svg class="spark {cls}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'aria-hidden="true" focusable="false">{"".join(parts)}</svg>')


def tile(key, label, unit, direction, target, note, cur, comp, history, weeks=None):
    val = cur.get(key)
    st = status(val, direction, target)
    tgt = f'<span class="tile-target">target {fmt(target, unit)}</span>' if target is not None else ""
    help_text = METRIC_HELP.get(key)
    info = (
        f'<button type="button" class="tile-info" aria-label="About {_esc(label)}" '
        f'aria-describedby="tip-{key}">i</button>'
        f'<span class="tile-tip" role="tooltip" id="tip-{key}">{_esc(help_text)}</span>'
    ) if help_text else ""
    href, text, hint = METRIC_LINKS.get(key, (None, None, None))
    link = (f'\n        <a class="tile-link" href="{_esc(href)}" target="_blank" rel="noopener">'
            f'{_esc(text)} ↗</a>' + (f'<span class="tile-hint">{_esc(hint)}</span>' if hint else "")
            ) if href else ""
    return f"""      <div class="tile tile-{st}">
        <div class="tile-head"><span class="tile-label">{label}{info}</span>{sparkline(history, direction, weeks)}</div>
        <div class="tile-value{' tile-value-empty' if val is None else ''}">{fmt(val, unit)}</div>
        <div class="tile-foot">{delta_chip(val, comp, unit, direction)}{tgt}</div>
        <p class="tile-note">{note}</p>{link}
      </div>"""


def composition_bar(cur):
    parts = [
        ("codified", "Codified", "seg-cod"),
        ("drifted", "Drifted", "seg-dri"),
        ("unmanaged", "Unmanaged", "seg-unm"),
        ("ghost", "Ghost", "seg-gho"),
    ]
    vals = {k: cur.get(k) or 0 for k, _, _ in parts}
    total = sum(vals.values())
    if not total:
        return ""
    segs, legend = [], []
    for key, name, cls in parts:
        share = 100.0 * vals[key] / total
        if share > 0:
            segs.append(
                f'<div class="seg {cls}" style="flex:{share:.4f}" '
                f'title="{name}: {vals[key]:,} ({share:.1f}%)"></div>'
            )
        legend.append(
            f'<li><span class="dot {cls}"></span>{name}'
            f'<b>{vals[key]:,}</b><i>{share:.1f}%</i></li>'
        )
    return f"""    <div class="composition">
      <div class="comp-bar">{''.join(segs)}</div>
      <ul class="comp-legend">{''.join(legend)}</ul>
    </div>"""


def prior_policies(loaded):
    """(week, {policy id: policy}) from the latest earlier snapshot with a policy list.

    Same rule as comparison(): a real earlier measurement, at most MAX_LOOKBACK_WEEKS back.
    None when there is nothing to compare against.
    """
    for s in reversed(loaded[:-1]):
        pols = g(s, "cloud_waste", "policies")
        if isinstance(pols, list):
            gap = max(1, (_iso_monday(loaded[-1]["week"]) - _iso_monday(s["week"])).days // 7)
            if gap > MAX_LOOKBACK_WEEKS:
                return None
            return s["week"], gap, {p.get("id"): p for p in pols if isinstance(p, dict)}
    return None


def policy_table(loaded):
    """Per-policy Cloud Waste rows: name, asset type, violating assets, list-price cost."""
    pols = g(loaded[-1], "cloud_waste", "policies")
    if pols is None:
        return '<p class="empty">no data: the Cloud Waste policies were not collected this week.</p>'
    pols = [p for p in pols if isinstance(p, dict)]
    if not pols:
        return '<p class="empty">No Cloud Waste policy has matching assets.</p>'
    prior = prior_policies(loaded)

    def chip(cur, old, week, gap, unit):
        if cur is None or old is None:
            return ""
        comp = {"state": "ok", "value": old, "week": week, "gap": gap}
        return delta_chip(cur, comp, unit, "down")

    # Costed policies first, biggest spend first; then the ones with no cost model.
    pols.sort(key=lambda p: (p.get("monthly_usd") is None,
                             -(p.get("monthly_usd") or 0), -(p.get("total_assets") or 0)))
    rows = []
    for p in pols:
        pid = p.get("id")
        name = _esc(str(p.get("name") or pid or "unnamed policy"))
        name_cell = f'<a href="{_esc(url_policy(pid))}" target="_blank" rel="noopener">{name}</a>' if pid else name
        n_assets = p.get("total_assets")
        assets_txt = fmt(n_assets, "n")
        assets_cell = (f'<a href="{_esc(url_policy_assets(pid))}" target="_blank" rel="noopener">{assets_txt}</a>'
                       if pid and n_assets is not None else assets_txt)
        cost = p.get("monthly_usd")
        old = prior[2].get(pid) if prior and pid else None
        if prior and pid and old is None:
            a_chip = c_chip = '<span class="chip chip-flat">new</span>'
        elif old is not None:
            a_chip = chip(n_assets, old.get("total_assets"), prior[0], prior[1], "n")
            c_chip = chip(cost, old.get("monthly_usd"), prior[0], prior[1], "usd2")
        else:
            a_chip = c_chip = ""
        if cost is None:
            cost_cell, year_cell = '<span class="nocost">no cost model</span>', '<span class="nocost">—</span>'
            c_chip = ""
        else:
            cost_cell, year_cell = f"{fmt(cost, 'usd2')} {c_chip}", fmt(cost * 12, "usd")
        rows.append(
            f'<tr><td class="strong">{name_cell}</td><td>{_esc(str(p.get("asset_type") or "—"))}</td>'
            f'<td class="num">{assets_cell} {a_chip}</td><td class="num">{cost_cell}</td>'
            f'<td class="num">{year_cell}</td></tr>')
    # Policies reported last time and not now. Shown muted with last week's figures. Not called
    # "resolved": the policy may be fixed, disabled, or out of the collecting key's view.
    if prior:
        now_ids = {p.get("id") for p in pols}
        gone = sorted((p for pid, p in prior[2].items() if pid and pid not in now_ids),
                      key=lambda p: -(p.get("monthly_usd") or 0))
        wk = prior[0].split("-")[-1]
        for p in gone:
            pid = p["id"]
            name = _esc(str(p.get("name") or pid))
            was_cost = p.get("monthly_usd")
            was_cost_txt = (f'<span class="chip chip-flat">was {fmt(was_cost, "usd2")} ({wk})</span>'
                            if was_cost is not None else '<span class="nocost">no cost model</span>')
            rows.append(
                f'<tr class="row-gone"><td class="strong"><a href="{_esc(url_policy(pid))}" target="_blank" '
                f'rel="noopener">{name}</a> <span class="nocost">no longer reported</span></td>'
                f'<td>{_esc(str(p.get("asset_type") or "—"))}</td>'
                f'<td class="num"><span class="chip chip-flat">was {fmt(p.get("total_assets"), "n")} ({wk})</span></td>'
                f'<td class="num">{was_cost_txt}</td><td class="num"><span class="nocost">—</span></td></tr>')
    return f"""    <div class="table-wrap">
      <table>
        <thead><tr><th>Policy</th><th>Asset type</th><th class="num">Violating assets</th>
        <th class="num">Monthly (list price)</th><th class="num">Yearly (list price)</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>"""


def watchlist(derived, weeks, pillars):
    """Every off-target metric, worst first, with its delta — the action list."""
    cur = derived[-1]
    rows = []
    for pillar, _, metrics in pillars:
        for key, label, unit, direction, target, _ in metrics:
            val = cur.get(key)
            st = status(val, direction, target)
            if st not in ("off", "watch"):
                continue
            gap = ""
            if val is not None and target:
                if direction == "up":
                    gap = fmt(target - val, unit) + " to go"
                else:
                    gap = fmt(val - target, unit) + " over"
            rows.append((st, pillar, label, fmt(val, unit), fmt(target, unit), gap,
                         delta_chip(val, comparison(key, derived, weeks), unit, direction)))
    rows.sort(key=lambda r: (r[0] != "off",))
    if not rows:
        return '<p class="empty">Every tracked KPI is at or above target.</p>'
    body = "".join(
        f'<tr class="row-{st}"><td><span class="sev sev-{st}"></span>'
        f'{"critical" if st == "off" else "watch"}</td><td>{pillar}</td>'
        f'<td class="strong">{label}</td><td class="num">{v}</td>'
        f'<td class="num">{t}</td><td class="num">{gap}</td><td>{chip}</td></tr>'
        for st, pillar, label, v, t, gap, chip in rows
    )
    return f"""    <div class="table-wrap">
      <table class="watch">
        <thead><tr><th>Severity</th><th>Pillar</th><th>KPI</th><th class="num">Current</th>
        <th class="num">Target</th><th class="num">Gap</th><th>WoW</th></tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>"""


# ----------------------------------------------------------------------- main

def main():
    # Ordered by the ISO week recorded *inside* each snapshot, not by filename --
    # filenames are a convention, the week field is the fact.
    loaded = load_snapshots()
    if not loaded:
        sys.exit(f"no snapshots found in {SNAP_DIR}")
    pillars = catalog()
    derived = [derive(s) for s in loaded]

    cur, latest = derived[-1], loaded[-1]
    weeks = [s_["week"] for s_ in loaded]
    prev_week = loaded[-2]["week"] if len(loaded) > 1 else None

    def hist(key):
        return [d.get(key) for d in derived]

    bands = []
    for pillar, blurb, metrics in pillars:
        tiles = "\n".join(
            tile(k, lbl, u, d, t, n, cur, comparison(k, derived, weeks), hist(k), weeks)
            for k, lbl, u, d, t, n in metrics
        )
        bands.append(f"""  <section class="band">
    <div class="band-rail"><h2>{pillar}</h2><p>{blurb}</p></div>
    <div class="grid">
{tiles}
    </div>
  </section>""")
        if pillar == CLOUD_WASTE:
            prior = prior_policies(loaded)
            gone_note = (f" A policy missing from this week's list is shown muted with its figures from "
                         f"{_esc(prior[0])}; it may be fixed, disabled, or out of the collecting key's view."
                         if prior and g(latest, "cloud_waste", "policies") is not None else "")
            vs = ("" if g(latest, "cloud_waste", "policies") is None
                  else f"Changes are against {_esc(prior[0])}." if prior
                  else "No earlier week has Cloud Waste data, so there is nothing to compare against yet.")
            bands.append(f"""  <section class="section">
    <h2>Cloud waste by policy</h2>
    <p class="lede">Each Cloud Waste policy with matching assets. Dollar figures are list price. A policy
    without a cost model has no cost, only a count.{gone_note} {vs}</p>
{policy_table(loaded)}
  </section>""")

    users = g(latest, "adoption", "users") or []
    user_max = max((u["count"] for u in users), default=1)
    user_rows = "".join(
        f'<li><span class="u-name">{_esc(str(u["email"]))}</span>'
        f'<span class="u-bar"><i style="width:{100.0*u["count"]/user_max:.1f}%"></i></span>'
        f'<span class="u-num">{u["count"]}</span></li>'
        for u in users
    )

    scopes = g(latest, "adoption", "scopes") or []
    scope_max = max((s["count"] for s in scopes), default=1)
    scope_rows = "".join(
        f'<li><span class="u-name">{_esc(str(s["scope"]))}</span>'
        f'<span class="u-bar u-bar-alt"><i style="width:{100.0*s["count"]/scope_max:.1f}%"></i></span>'
        f'<span class="u-num">{s["count"]}</span></li>'
        for s in scopes
    )

    fw = g(latest, "governance", "frameworks") or {}
    unscored = [k for k, v in fw.items() if not isinstance(v, (int, float))]

    integ_notes = g(latest, "integrations", "notes") or {}
    caveats = [f"<strong>{_esc(k)}</strong> — {_esc(str(v))}" for k, v in integ_notes.items()]
    if g(latest, "notes"):
        caveats.insert(0, "<strong>this week</strong> — " + _esc(latest["notes"]))
    if g(latest, "events", "note"):
        caveats.append("<strong>events</strong> — " + _esc(latest["events"]["note"]))
    if unscored:
        caveats.append(
            "<strong>frameworks</strong> — " + ", ".join(unscored) +
            " return a null score with zero matched assets: no subscribed policies, "
            "not a score of zero. Rendered as “not scored”."
        )
    if cloud_waste_visible(pillars):
        caveats += [f"<strong>cloud waste</strong> — {_esc(c)}" for c in cloud_waste_caveats(latest, loaded[:-1])]
    caveats += [f"<strong>standing</strong> — {_esc(c)}" for c in standing_caveats()]
    caveat_html = "".join(f"<li>{c}</li>" for c in caveats)

    cov = cur.get("coverage_pct")
    cov_cmp = comparison("coverage_pct", derived, weeks)
    if cov is not None and cov_cmp["state"] == "ok":
        d = cov - cov_cmp["value"]
        if shows_as_zero(d, "%"):
            cov_move = f'<span class="chip chip-flat">no change vs {cov_cmp["week"]}</span>'
        else:
            tone = "good" if d > 0 else "bad"
            cov_move = (f'<span class="chip chip-{tone}">'
                        f'{"+" if d > 0 else "−"}{abs(d):.2f} pt vs {cov_cmp["week"]}</span>')
    elif cov is not None and cov_cmp["state"] == "stale":
        cov_move = '<span class="chip chip-flat">no recent data</span>'
    else:
        cov_move = '<span class="chip chip-flat">baseline week</span>'

    subtitle = (f"Week over week against {prev_week}" if prev_week
                else "Baseline week — deltas begin next collection")
    new_pillars = baseline_pillars(pillars, derived, weeks) if prev_week else []
    if new_pillars:
        subtitle += f" · no comparison yet for {', '.join(new_pillars)}"
    off = offweek_notes(pillars, derived, weeks, prev_week) if prev_week else []
    if off:
        subtitle += " · " + "; ".join(off)

    name = _esc(str(latest.get("account_name") or "Account"))
    aid = latest.get("account_id")
    account_line = (f"account {_esc(str(aid))}<br>\n      "
                    if aid and aid != "unspecified" else "")
    html = f"""<title>{name} — Firefly KPI scorecard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chivo:wght@700;900&family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root {{
  --ground:#eef2f2; --panel:#ffffff; --panel-2:#f7fafa;
  --ink:#0c1417; --ink-2:#33474d; --muted:#63797f; --rule:#d3dedf;
  --accent:#0b7a6e; --accent-soft:#d6ebe8;
  --good:#25714a; --bad:#a83a26; --warn:#8f6412;
  --cod:#0b7a6e; --dri:#8f6412; --unm:#a83a26; --gho:#7c6f9a;
  --shadow:0 1px 2px rgba(12,20,23,.06), 0 8px 24px -18px rgba(12,20,23,.28);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0a1215; --panel:#111d21; --panel-2:#16252a;
    --ink:#e6eeee; --ink-2:#b3c6ca; --muted:#8299a0; --rule:#22363c;
    --accent:#2fb9a6; --accent-soft:#12312f;
    --good:#4fbe7d; --bad:#e8735a; --warn:#ddaa41;
    --cod:#2fb9a6; --dri:#ddaa41; --unm:#e8735a; --gho:#a596cf;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px -20px rgba(0,0,0,.8);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0a1215; --panel:#111d21; --panel-2:#16252a;
  --ink:#e6eeee; --ink-2:#b3c6ca; --muted:#8299a0; --rule:#22363c;
  --accent:#2fb9a6; --accent-soft:#12312f;
  --good:#4fbe7d; --bad:#e8735a; --warn:#ddaa41;
  --cod:#2fb9a6; --dri:#ddaa41; --unm:#e8735a; --gho:#a596cf;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px -20px rgba(0,0,0,.8);
}}

*,*::before,*::after {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1180px; margin:0 auto; padding:clamp(20px,4vw,52px) clamp(16px,4vw,40px) 80px; }}
h1,h2,h3 {{ font-family:Chivo,"IBM Plex Sans",sans-serif; text-wrap:balance; margin:0; }}
.num, .tabular {{ font-variant-numeric:tabular-nums; }}

/* ---- masthead ---- */
.masthead {{ display:flex; flex-wrap:wrap; gap:16px 28px; align-items:flex-end;
  justify-content:space-between; padding-bottom:20px; border-bottom:2px solid var(--ink); }}
.eyebrow {{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--accent); margin:0 0 6px; }}
.masthead h1 {{ font-size:clamp(28px,4.4vw,44px); font-weight:900; letter-spacing:-.02em; line-height:1.02; }}
.masthead .sub {{ color:var(--muted); font-size:14px; margin:8px 0 0; }}
.stamp {{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-2);
  text-align:right; line-height:1.9; font-variant-numeric:tabular-nums; }}
.stamp b {{ display:block; font-size:26px; color:var(--ink); font-weight:600; letter-spacing:-.01em; }}

/* ---- headline ---- */
.headline {{ display:grid; grid-template-columns:minmax(200px,260px) 1fr; gap:clamp(20px,3vw,44px);
  align-items:center; background:var(--panel); border:1px solid var(--rule); border-radius:3px;
  padding:clamp(20px,3vw,32px); margin:26px 0 34px; box-shadow:var(--shadow); }}
.headline .big {{ font-family:Chivo,sans-serif; font-weight:900; font-size:clamp(52px,9vw,86px);
  line-height:.9; letter-spacing:-.035em; color:var(--accent); font-variant-numeric:tabular-nums; }}
.headline .cap {{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted); margin:10px 0 12px; }}
.composition {{ min-width:0; }}
.comp-bar {{ display:flex; height:30px; border-radius:2px; overflow:hidden; gap:1px; background:var(--rule); }}
.seg {{ min-width:2px; }}
.seg-cod{{background:var(--cod)}} .seg-dri{{background:var(--dri)}}
.seg-unm{{background:var(--unm)}} .seg-gho{{background:var(--gho)}}
.comp-legend {{ list-style:none; margin:16px 0 0; padding:0; display:grid;
  grid-template-columns:repeat(auto-fit,minmax(132px,1fr)); gap:10px 20px; }}
.comp-legend li {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:6px;
  font-size:12.5px; color:var(--muted); }}
.comp-legend b {{ font-family:"IBM Plex Mono",monospace; color:var(--ink); font-weight:600;
  font-variant-numeric:tabular-nums; margin-left:auto; }}
.comp-legend i {{ font-style:normal; font-family:"IBM Plex Mono",monospace; font-size:11px;
  color:var(--muted); flex-basis:100%; }}
.dot {{ width:9px; height:9px; border-radius:2px; flex:none; }}

/* ---- pillar bands ---- */
.band {{ display:grid; grid-template-columns:minmax(180px,232px) 1fr; gap:clamp(18px,3vw,40px);
  padding:32px 0; border-top:1px solid var(--rule); }}
.band-rail {{ position:sticky; top:20px; align-self:start; }}
.band-rail h2 {{ font-size:19px; font-weight:700; letter-spacing:-.01em; }}
.band-rail p {{ color:var(--muted); font-size:13px; margin:8px 0 0; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(196px,1fr)); gap:14px; }}

.tile {{ background:var(--panel); border:1px solid var(--rule); border-left:3px solid var(--rule);
  border-radius:3px; padding:14px 15px 13px; display:flex; flex-direction:column; gap:7px; }}
.tile-ok {{ border-left-color:var(--good); }}
.tile-watch {{ border-left-color:var(--warn); }}
.tile-off {{ border-left-color:var(--bad); }}
.tile-none {{ border-left-color:var(--rule); }}
.tile-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:8px; min-height:22px; }}
.tile-label {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.09em;
  text-transform:uppercase; color:var(--muted); line-height:1.35; }}
.tile-value {{ font-family:Chivo,sans-serif; font-weight:700; font-size:29px; line-height:1;
  letter-spacing:-.025em; font-variant-numeric:tabular-nums; }}
.tile-value-empty {{ font-family:"IBM Plex Mono",monospace; font-size:15px; font-weight:400;
  color:var(--muted); letter-spacing:0; }}
.tile-foot {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; }}
.tile-target {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; color:var(--muted); }}
.tile-note {{ margin:0; font-size:11.5px; color:var(--muted); line-height:1.4; }}
.tile {{ position:relative; }}
.tile:hover, .tile:focus-within {{ z-index:5; }}
.tile-info {{ display:inline-flex; align-items:center; justify-content:center; width:14px; height:14px;
  margin-left:6px; padding:0; border:1px solid var(--muted); border-radius:50%; background:none;
  color:var(--muted); font:italic 700 9px/1 Georgia,serif; text-transform:none; letter-spacing:0;
  cursor:help; vertical-align:1px; }}
.tile-info:hover, .tile-info:focus-visible {{ color:var(--ink); border-color:var(--ink); outline:none; }}
.tile-tip {{ display:none; position:absolute; left:8px; right:8px; top:38px; z-index:10;
  padding:9px 11px; background:var(--panel); color:var(--ink); border:1px solid var(--muted);
  border-radius:3px; box-shadow:0 4px 14px rgba(0,0,0,.18); font-family:Chivo,sans-serif;
  font-size:12px; font-weight:400; letter-spacing:0; text-transform:none; line-height:1.45; }}
.tile-hint {{ display:block; margin-top:3px; font-size:11px; color:var(--muted); line-height:1.4; }}
.tile-link {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; }}
.row-gone td {{ color:var(--muted); background:color-mix(in srgb, var(--muted) 6%, transparent); }}
.nocost {{ color:var(--muted); font-style:italic; font-family:"IBM Plex Sans",sans-serif; }}
td a {{ text-decoration-thickness:1px; text-underline-offset:2px; }}
.tile-info:hover + .tile-tip, .tile-info:focus + .tile-tip {{ display:block; }}

.chip {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; font-weight:600;
  padding:2px 6px; border-radius:2px; white-space:nowrap; font-variant-numeric:tabular-nums; }}
.chip-good {{ color:var(--good); background:color-mix(in srgb, var(--good) 13%, transparent); }}
.chip-bad {{ color:var(--bad); background:color-mix(in srgb, var(--bad) 13%, transparent); }}
.chip-flat {{ color:var(--muted); background:color-mix(in srgb, var(--muted) 12%, transparent); }}

.spark {{ flex:none; overflow:visible; }}
.spark-line {{ fill:none; stroke-width:1.5; stroke-linejoin:round; stroke-linecap:round; }}
.spark-fill {{ opacity:.14; stroke:none; }}
.spark-good .spark-line,.spark-good .spark-dot {{ stroke:var(--good); fill:var(--good); }}
.spark-good .spark-fill {{ fill:var(--good); }}
.spark-bad .spark-line,.spark-bad .spark-dot {{ stroke:var(--bad); fill:var(--bad); }}
.spark-bad .spark-fill {{ fill:var(--bad); }}
.spark-flat .spark-line,.spark-flat .spark-dot {{ stroke:var(--muted); fill:var(--muted); }}
.spark-flat .spark-fill {{ fill:var(--muted); }}
.spark-dot, .spark-pt {{ stroke:none; }}
.spark-gap {{ fill:none; stroke-width:1.2; stroke-dasharray:2 2; stroke-linecap:round; }}
.spark-good .spark-gap {{ stroke:var(--good); }} .spark-good .spark-pt {{ fill:var(--good); }}
.spark-bad .spark-gap {{ stroke:var(--bad); }} .spark-bad .spark-pt {{ fill:var(--bad); }}
.spark-flat .spark-gap {{ stroke:var(--muted); }} .spark-flat .spark-pt {{ fill:var(--muted); }}

/* ---- watchlist ---- */
.section {{ padding:32px 0; border-top:1px solid var(--rule); }}
.section > h2 {{ font-size:19px; font-weight:700; }}
.section > .lede {{ color:var(--muted); font-size:13px; margin:8px 0 18px; max-width:64ch; }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--rule); border-radius:3px; background:var(--panel); }}
table {{ border-collapse:collapse; width:100%; min-width:660px; font-size:13px; }}
th {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--muted); text-align:left; font-weight:400;
  padding:10px 14px; border-bottom:1px solid var(--rule); background:var(--panel-2); }}
td {{ padding:11px 14px; border-bottom:1px solid var(--rule); vertical-align:middle; }}
tbody tr:last-child td {{ border-bottom:none; }}
td.num, th.num {{ text-align:right; font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; }}
td.strong {{ font-weight:600; }}
.sev {{ display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; }}
.sev-off {{ background:var(--bad); }} .sev-watch {{ background:var(--warn); }}
.row-off td:first-child {{ color:var(--bad); font-weight:600; }}
.row-watch td:first-child {{ color:var(--warn); }}

/* ---- two-up lists ---- */
.duo {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:clamp(18px,3vw,32px); }}
.card {{ background:var(--panel); border:1px solid var(--rule); border-radius:3px; padding:18px 20px 20px; }}
.card h3 {{ font-size:14px; font-weight:700; margin-bottom:4px; }}
.card .hint {{ color:var(--muted); font-size:12px; margin:0 0 14px; }}
.bars {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }}
.bars li {{ display:grid; grid-template-columns:1fr 84px 40px; align-items:center; gap:10px; font-size:12.5px; }}
.u-name {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--ink-2); }}
.u-bar {{ height:6px; background:color-mix(in srgb, var(--muted) 18%, transparent); border-radius:3px; overflow:hidden; }}
.u-bar i {{ display:block; height:100%; background:var(--accent); border-radius:3px; }}
.u-bar-alt i {{ background:var(--gho); }}
.u-num {{ font-family:"IBM Plex Mono",monospace; text-align:right; color:var(--ink);
  font-variant-numeric:tabular-nums; font-size:12px; }}

.caveats {{ margin:14px 0 0; padding-left:20px; color:var(--muted); font-size:12.5px; }}
.caveats li {{ margin-bottom:8px; }}
.caveats strong {{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--ink-2); }}
.empty {{ color:var(--muted); font-size:13px; }}
footer {{ margin-top:40px; padding-top:20px; border-top:1px solid var(--rule);
  color:var(--muted); font-size:11.5px; font-family:"IBM Plex Mono",monospace; line-height:1.8; }}
a {{ color:var(--accent); }}
:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
@media (max-width:760px) {{
  .headline, .band {{ grid-template-columns:1fr; }}
  .band-rail {{ position:static; }}
}}
@media (prefers-reduced-motion:reduce) {{ * {{ animation:none !important; transition:none !important; }} }}
</style>

<div class="wrap">
  <header class="masthead">
    <div>
      <p class="eyebrow">Firefly · Weekly KPI Scorecard</p>
      <h1>{name}</h1>
      <p class="sub">{subtitle} · window {g(latest,'window','start')} to {g(latest,'window','end')}</p>
    </div>
    <div class="stamp">
      <b>{latest['week']}</b>
      collected {latest['collected_at']}<br>
      {account_line}
      {len(loaded)} snapshot{'s' if len(loaded) != 1 else ''} on file
    </div>
  </header>

  <div class="headline">
    <div>
      <p class="cap">IaC coverage</p>
      <div class="big">{fmt(cov, '%')}</div>
      <div style="margin-top:12px">{cov_move}</div>
    </div>
{composition_bar(cur)}
  </div>

{chr(10).join(bands)}

  <section class="section">
    <h2>Watchlist</h2>
    <p class="lede">Every KPI currently short of its target, worst first. This is the working
    agenda — anything not on this list is holding.</p>
{watchlist(derived, weeks, pillars)}
  </section>

  <section class="section">
    <h2>Who is using it</h2>
    <p class="lede">Named users and product areas over the trailing seven days. Concentration here
    is the early-warning signal: a shrinking roster or a single dominant scope means the platform
    is becoming one team's tool.</p>
    <div class="duo">
      <div class="card">
        <h3>Active users</h3>
        <p class="hint">{cur.get('wau') or 0} distinct actors · {cur.get('activities') or 0} activities</p>
        <ul class="bars">{user_rows}</ul>
      </div>
      <div class="card">
        <h3>Product areas touched</h3>
        <p class="hint">{cur.get('scopes_touched') or 0} scopes · top scope holds {fmt(cur.get('top_scope_share'), '%')}</p>
        <ul class="bars">{scope_rows}</ul>
      </div>
    </div>
  </section>

  <section class="section">
    <h2>Measurement caveats</h2>
    <p class="lede">Read these before quoting any number outside the team.</p>
    <ul class="caveats">{caveat_html}</ul>
  </section>

  <footer>
    Generated by scripts/build_report.py from snapshots/{latest['week']}.json ·
    definitions in KPI-FRAMEWORK.md · a metric that failed to collect renders as
    "no data", never zero.
  </footer>
</div>
"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(html.encode()):,} bytes, {len(loaded)} snapshot(s))")
    print(f"latest: {latest['week']}  coverage: {fmt(cov, '%')}")


if __name__ == "__main__":
    main()
