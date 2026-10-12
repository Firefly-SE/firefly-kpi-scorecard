#!/usr/bin/env python3
"""Export the KPI history as CSV, JSON and Markdown.

Imports the derivation logic straight from build_report.py so the export can never
disagree with the scorecard. One column per ISO week, plus the delta between the
last two weeks.

  python3 scripts/export_metrics.py            # all weeks on file
  python3 scripts/export_metrics.py W34 W35    # only these weeks

Writes <home>/reports/kpi-metrics.{csv,json,md}  (<home> is ./data, or $KPI_HOME).
"""

import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_report import (ROOT, catalog, comparison, derive,  # noqa: E402
                          load_snapshots, standing_caveats, status)

OUT_CSV = ROOT / "reports" / "kpi-metrics.csv"
OUT_JSON = ROOT / "reports" / "kpi-metrics.json"
OUT_MD = ROOT / "reports" / "kpi-metrics.md"

# The metrics that lead the Markdown summary, in reading order.
HIGHLIGHTS = ["coverage_pct", "codified", "ghost", "ghost_ratio", "drifted",
              "ci_workspaces", "runner_workspaces", "wau", "top_user_share", "nist"]

MOVE = {"improving": "better", "worsening": "worse", "no change": "", "": ""}
VERDICT = {"ok": "on target", "watch": "watch", "off": "off target", "none": ""}


def md_num(val, unit):
    if val is None:
        return "no data"
    if unit == "percent":
        return f"{val:,.2f}%"
    if unit == "score":
        return f"{val:g}"
    return f"{val:,.0f}"


def md_delta(row, unit):
    d = row["delta"]
    if d is None:
        return "—"
    vs = f" vs {row['delta_vs'].split('-')[-1]}" if row.get("delta_vs") and row["delta_gap"] > 1 else ""
    if d == 0:
        return "no change" + vs
    sign = "+" if d > 0 else "−"
    mag = f"{abs(d):,.2f} pt" if unit == "percent" else f"{abs(d):,.0f}"
    pc = row["pct_change"]
    tail = f" ({sign}{abs(pc):.1f}%)" if pc not in (None, 0) and unit != "percent" else ""
    return f"{sign}{mag}{tail}{vs}"


def write_markdown(snaps, weeks, rows, pillars):
    cur, prev = snaps[-1], (snaps[-2] if len(snaps) > 1 else None)
    by_key = {r["key"]: r for r in rows}
    wa, wb = (weeks[-2], weeks[-1]) if len(weeks) > 1 else (None, weeks[-1])

    L = []
    L.append(f"# Firefly KPI report — {cur.get('account_name')}")
    L.append("")
    if cur.get("account_id") not in (None, "", "unspecified"):
        L.append(f"**Account** `{cur.get('account_id')}`  ")
    L.append(f"**Comparison** {wa} → {wb}" if wa else f"**Week** {wb}")
    L.append("  ")
    for s_ in snaps:
        w = s_.get("window") or {}
        L.append(f"**{s_['week']}** collected {s_['collected_at']} "
                 f"(window {w.get('start')} → {w.get('end')})  ")
    L.append("")
    L.append("Definitions: `KPI-FRAMEWORK.md`; targets: `data/targets.json`. "
             "A metric that failed to collect reads **no data**, never 0.")
    L.append("")

    # ---- headline ----
    L.append("## Headline")
    L.append("")
    hdr = f"| Metric | {wa} | {wb} | Change | Target | Status |" if wa else \
          f"| Metric | {wb} | Target | Status |"
    L.append(hdr)
    L.append("|---|---:|---:|---:|---:|---|" if wa else "|---|---:|---:|---|")
    for key in HIGHLIGHTS:
        r = by_key.get(key)
        if not r:
            continue
        u = r["unit"]
        tgt = md_num(r["target"], u) if r["target"] not in (None, "") else "—"
        verdict = VERDICT.get(r["vs_target"], "")
        word = MOVE.get(r["movement"], "")
        mv = md_delta(r, u) + (f" — {word}" if word else "")
        if wa:
            L.append(f"| **{r['metric']}** | {md_num(r[wa], u)} | "
                     f"{md_num(r[wb], u)} | {mv} | {tgt} | {verdict} |")
        else:
            L.append(f"| **{r['metric']}** | {md_num(r[wb], u)} | {tgt} | {verdict} |")
    L.append("")

    # ---- full tables per pillar ----
    L.append("## All metrics")
    for pillar, blurb, _ in pillars:
        L.append("")
        L.append(f"### {pillar}")
        L.append("")
        L.append(f"_{blurb}_")
        L.append("")
        cols = ([f"| Metric |"] + [f" {w} |" for w in weeks]
                + [" Change | Target | Status | Definition |"])
        L.append("".join(cols))
        L.append("|---|" + "---:|" * len(weeks) + "---:|---:|---|---|")
        for r in [x for x in rows if x["pillar"] == pillar]:
            u = r["unit"]
            tgt = md_num(r["target"], u) if r["target"] not in (None, "") else "—"
            cells = "".join(f" {md_num(r[w], u)} |" for w in weeks)
            L.append(f"| {r['metric']} |{cells} {md_delta(r, u)} | {tgt} | "
                     f"{VERDICT.get(r['vs_target'], '')} | {r['definition']} |")
    L.append("")

    # ---- watchlist ----
    off = [r for r in rows if r["vs_target"] in ("off", "watch")]
    off.sort(key=lambda r: r["vs_target"] != "off")
    L.append("## Watchlist")
    L.append("")
    if not off:
        L.append("Every tracked KPI is at or above target.")
    else:
        L.append(f"| Severity | Pillar | Metric | Current | Target | Change |")
        L.append("|---|---|---|---:|---:|---:|")
        for r in off:
            u = r["unit"]
            L.append(f"| {'**critical**' if r['vs_target']=='off' else 'watch'} | "
                     f"{r['pillar']} | {r['metric']} | {md_num(r[wb], u)} | "
                     f"{md_num(r['target'], u)} | {md_delta(r, u)} |")
    L.append("")

    # ---- caveats ----
    L.append("## Measurement caveats")
    L.append("")
    L.append("Read these before quoting any number externally.")
    L.append("")
    if cur.get("notes"):
        L.append(f"- **this week** — {cur['notes']}")
    notes = (cur.get("integrations", {}).get("notes") or {})
    for k, v in notes.items():
        L.append(f"- **{k}** — {v}")
    if cur.get("events", {}).get("note"):
        L.append(f"- **events** — {cur['events']['note']}")
    fw = cur.get("governance", {}).get("frameworks") or {}
    unscored = [k for k, v in fw.items() if not isinstance(v, (int, float))]
    if unscored:
        L.append(f"- **frameworks** — {', '.join(unscored)} return a null score with zero "
                 "matched assets: no subscribed policies, *not* a score of zero.")
    for n in cur.get("notable", []):
        L.append(f"- **this week** — {n}")
    for c in standing_caveats():
        L.append(f"- **standing** — {c}")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"Generated by `scripts/export_metrics.py` from "
             f"{', '.join('snapshots/' + w + '.json' for w in weeks)}. "
             f"Machine-readable equivalents: `kpi-metrics.csv`, `kpi-metrics.json`.")
    L.append("")

    OUT_MD.write_text("\n".join(L))


def main():
    snaps = load_snapshots(sys.argv[1:])
    if not snaps:
        sys.exit("no snapshots matched")
    weeks = [s["week"] for s in snaps]
    derived = [derive(s) for s in snaps]

    pillars = catalog()
    rows = []
    for pillar, _, metrics in pillars:
        for key, label, unit, direction, target, note in metrics:
            vals = [d.get(key) for d in derived]
            cur = vals[-1]
            comp = comparison(key, derived, weeks)
            prev = comp["value"]

            delta = None if cur is None or prev is None else round(cur - prev, 4)
            if cur is None or prev in (None, 0):
                pct_change = None
            else:
                pct_change = round(100.0 * (cur - prev) / abs(prev), 2)

            if delta is None or delta == 0 or direction == "flat":
                movement = "no change" if delta == 0 else ""
            else:
                good = delta > 0 if direction == "up" else delta < 0
                movement = "improving" if good else "worsening"

            rows.append({
                "pillar": pillar,
                "metric": label,
                "key": key,
                "unit": {"n": "count", "%": "percent", "score": "score"}[unit],
                "target": target,
                "good_direction": direction,
                **{w: v for w, v in zip(weeks, vals)},
                "delta": delta,
                "pct_change": pct_change,
                "movement": movement,
                "vs_target": status(cur, direction, target),
                "definition": note,
                "delta_vs": comp["week"] if comp["state"] == "ok" else "",
                "delta_gap": comp["gap"] if comp["state"] == "ok" else None,
            })

    fields = (["pillar", "metric", "key", "unit", "target", "good_direction"]
              + weeks + ["delta", "pct_change", "movement", "vs_target", "definition",
                "delta_vs"])

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})

    OUT_JSON.write_text(json.dumps({
        "account": snaps[-1].get("account_name"),
        "account_id": snaps[-1].get("account_id"),
        "weeks": [{"week": s["week"], "collected_at": s["collected_at"],
                   "window": s.get("window")} for s in snaps],
        "comparison": f"{weeks[-2]} to {weeks[-1]}" if len(weeks) > 1 else weeks[-1],
        "metrics": rows,
    }, indent=2))

    write_markdown(snaps, weeks, rows, pillars)

    print(f"wrote {OUT_CSV.relative_to(ROOT)}  ({len(rows)} metrics x {len(weeks)} weeks)")
    print(f"wrote {OUT_JSON.relative_to(ROOT)}")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"weeks: {', '.join(weeks)}")


if __name__ == "__main__":
    main()
