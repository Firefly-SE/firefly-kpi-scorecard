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

import json
import os
import pathlib
import sys
from html import escape as _esc

REPO = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("KPI_HOME") or REPO / "data").expanduser().resolve()
SNAP_DIR = ROOT / "snapshots"
OUT = ROOT / "reports" / "firefly-kpi-scorecard.html"

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
    }


# --------------------------------------------------------------- KPI catalog
# (key, label, unit, direction, target, note)
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
]

KPI_KEYS = [m[0] for _, _, ms in _CATALOG for m in ms]


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
    if unit == "%":
        return f"{val:,.2f}%".replace(".00%", "%")
    if unit == "score":
        return f"{val:g}"
    return f"{val:,.0f}"


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


def delta_chip(cur, prev, unit, direction):
    if cur is None or prev is None:
        return '<span class="chip chip-flat">baseline</span>'
    diff = cur - prev
    if abs(diff) < 1e-9:
        return '<span class="chip chip-flat">no change</span>'
    sign = "+" if diff > 0 else "−"
    mag = fmt(abs(diff), unit).replace("no data", "")
    if direction == "flat":
        tone = "flat"
    else:
        good = diff > 0 if direction == "up" else diff < 0
        tone = "good" if good else "bad"
    return f'<span class="chip chip-{tone}">{sign}{mag}</span>'


def sparkline(series, direction):
    pts = [v for v in series if v is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1
    w, h = 76, 22
    step = w / (len(pts) - 1)
    coords = [(i * step, h - 2 - (v - lo) / span * (h - 5)) for i, v in enumerate(pts)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"0,{h} {line} {w},{h}"
    lx, ly = coords[-1]
    trend = pts[-1] - pts[-2]
    if direction == "flat" or trend == 0:
        cls = "spark-flat"
    else:
        good = trend > 0 if direction == "up" else trend < 0
        cls = "spark-good" if good else "spark-bad"
    return (
        f'<svg class="spark {cls}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'aria-hidden="true" focusable="false">'
        f'<polygon points="{area}" class="spark-fill"/>'
        f'<polyline points="{line}" class="spark-line"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.2" class="spark-dot"/></svg>'
    )


def tile(key, label, unit, direction, target, note, cur, prev, history):
    val = cur.get(key)
    st = status(val, direction, target)
    tgt = f'<span class="tile-target">target {fmt(target, unit)}</span>' if target is not None else ""
    return f"""      <div class="tile tile-{st}">
        <div class="tile-head"><span class="tile-label">{label}</span>{sparkline(history, direction)}</div>
        <div class="tile-value{' tile-value-empty' if val is None else ''}">{fmt(val, unit)}</div>
        <div class="tile-foot">{delta_chip(val, prev.get(key) if prev else None, unit, direction)}{tgt}</div>
        <p class="tile-note">{note}</p>
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


def watchlist(cur, prev, pillars):
    """Every off-target metric, worst first, with its delta — the action list."""
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
                         delta_chip(val, prev.get(key) if prev else None, unit, direction)))
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
    prev = derived[-2] if len(derived) > 1 else None
    prev_week = loaded[-2]["week"] if len(loaded) > 1 else None

    def hist(key):
        return [d.get(key) for d in derived]

    bands = []
    for pillar, blurb, metrics in pillars:
        tiles = "\n".join(
            tile(k, lbl, u, d, t, n, cur, prev, hist(k))
            for k, lbl, u, d, t, n in metrics
        )
        bands.append(f"""  <section class="band">
    <div class="band-rail"><h2>{pillar}</h2><p>{blurb}</p></div>
    <div class="grid">
{tiles}
    </div>
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
    caveats += [f"<strong>standing</strong> — {_esc(c)}" for c in standing_caveats()]
    caveat_html = "".join(f"<li>{c}</li>" for c in caveats)

    cov = cur.get("coverage_pct")
    cov_prev = prev.get("coverage_pct") if prev else None
    cov_move = ""
    if cov is not None and cov_prev is not None:
        d = cov - cov_prev
        tone = "good" if d > 0 else ("bad" if d < 0 else "flat")
        cov_move = (f'<span class="chip chip-{tone}">'
                    f'{"+" if d > 0 else "−"}{abs(d):.2f} pt vs {prev_week}</span>')
    else:
        cov_move = '<span class="chip chip-flat">baseline week</span>'

    subtitle = (f"Week over week against {prev_week}" if prev_week
                else "Baseline week — deltas begin next collection")

    name = _esc(str(latest.get("account_name") or "Account"))
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
.spark-dot {{ stroke:none; }}

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
      account {_esc(str(latest.get('account_id', '')))}<br>
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
{watchlist(cur, prev, pillars)}
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
