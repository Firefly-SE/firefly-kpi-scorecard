---
name: firefly-weekly-kpi
description: Collect this week's Firefly KPI snapshot for the configured account, store it as an immutable weekly JSON file, rebuild the HTML scorecard and CSV/JSON/Markdown exports, and write the week-over-week read. Use for the weekly KPI review and whenever the user says "run the KPI report", "run the weekly KPIs", "pull this week's metrics", "refresh the Firefly scorecard", "take this week's snapshot", "compare W-whatever to last week", or asks how coverage, drift, ghost ratio, workspaces or adoption are trending. Also use to regenerate the report after hand-correcting a snapshot.
---

# Firefly weekly KPI collection

Produces one snapshot per ISO week, a rebuilt scorecard, and a written week-over-week read.

**Setup comes first.** Everything account-specific lives in the KPI home, `data/` in this
repo (or `$KPI_HOME`), written by `python3 setup.py`:

| File | Holds |
|---|---|
| `data/config.json` | `account_name`, `account_id`, `mcp_server`, `providers`, `runner_enabled`, `hidden_metrics` |
| `data/targets.json` | KPI targets |
| `data/caveats.md` | standing caveats, rendered into every report |
| `data/snapshots/` | one JSON per ISO week |

If `data/config.json` does not exist, stop and tell the user to run `python3 setup.py`.
Read it now; the rest of this skill refers to its fields.

KPI definitions, formulas and data-source notes live in **`KPI-FRAMEWORK.md`**. Read it before
quoting a number. **Do not redefine a KPI here; change the framework doc.**

## Tool names

Firefly tools are served by the MCP server named in `config.json` → `mcp_server`. In Claude
Code each tool is `mcp__<mcp_server>__<tool>`, for example
`mcp__firefly__inventory-api___get-iac-coverage`. The tables below give the `<tool>` part.
If no tool with that server prefix exists, stop: the MCP server is not connected or not
approved. Tell the user to run `claude` interactively in this folder and approve it.

## Step 0: establish the week

```bash
python3 -c "import datetime as d; t=d.date.today(); y,w,_=t.isocalendar(); \
print(f'week={y}-W{w:02d}'); print(f'today={t}'); print(f'start={t - d.timedelta(days=7)}')"
```

Target file: `data/snapshots/<week>.json`.

If it already exists, **overwrite it without asking**. A second run in the same ISO week is a
refresh, and a consistent point-in-time reading beats a stale one. Immutability only matters
*across* weeks: never touch a previous week's file. Say in your summary that you replaced one.

This step must never block on a question; the weekly run is unattended.

## Step 1: collect

Make these calls **in parallel batches**; they are independent. Calls 1-14 are count-only
reads: pass the smallest page size and read the total. Calls 15 and 16 (Cloud Waste) are the
exception and are described under the table.

| # | Tool | Arguments | Read from response |
|---|---|---|---|
| 1 | `inventory-api___get-iac-coverage` | none | `iacCoverage.{codified,unmanaged,ghost,drifted}` |
| 2 | `iacStacks-api___search-iac-stacks` | `pageSize: 1` | `totalObjects` → `stacks_total` |
| 3 | `iacStacks-api___search-iac-stacks` | `pageSize: 1`, `screenFilters: {stateType:["terraform"]}` | `totalObjects` → `stacks_terraform` |
| 4 | `governance-api___get_frameworks_score` | `frameworks: ["SOC2","ISO27001","HIPAA","PCI-DSS","NIST","CIS","GDPR","AWS Well-Architected","FireflyBestPractices"]` | `<name>.score` per framework |
| 5 | `governance-api___get_insights` | `page:1, page_size:1, fields:"id"` | `total` → `policies_total` |
| 6 | `governance-api___get_insights` | `page:1, page_size:1, onlyEnabled:true, fields:"id"` | `total` → `policies_enabled` |
| 7 | `governance-api___get_insights` | `page:1, page_size:1, onlySubscribed:true, fields:"id"` | `total` → `policies_custom` |
| 8 | `ci-api___list-guardrails` | `pageSize: 50`, `projection: ["name","type","severity","isEnabled"]` | count rows; tally by `type`; count `isEnabled` |
| 9 | `ci-api___list-workspaces` | `pageSize: 100`, `projection: ["workspaceName","lastRunStatus"]` | row count → `ci_workspaces` |
| 10 | `runners-api___list-projects-flat` | **`maxDepth: 5`**, see below. **Skip if `runner_enabled` is false.** | `totalCount` → `runner_projects` |
| 11 | `runners-api___list-project-workspaces` | for **every** project from #10 with a `workspaceCount`, `pageSize: 300`. Skip if runner disabled. | **sum** of `workspaceCount` → `runner_workspaces` |
| 12 | `integrations-api___list-integrations` | once per `providerType` in `config.json` → `providers` (aws, azurerm, gcp, k8s) | row count per provider |
| 13 | `auditLog-api___query-audit-log-activities` | `limit:5, actor_types:["user"], start_time:<start>T00:00:00Z, end_time:<today>T23:59:59Z` | `total`, `facets.actor_emails`, `facets.scopes` |
| 14 | `events-api___query-events` | `size:1`, `query:{startTime,endTime}`, `facets:[{field:"eventType",size:20},{field:"actionType",size:10}]` | facet counts for `click_ops`, `cli_sdk`, `denied`, and actions |
| 15 | `governance-api___get_insights` | `frameworks:["Cloud Waste"]`, `category:"Optimization"`, `onlyMatchingAssets:true`, `page:1`, `page_size:20`; page until rows collected equals `total`. **Do not pass `fields`.** | per row: `id`, `name`, `type`, `total_assets`, `cost` → `cloud_waste.policies` |
| 16 | `inventory-api___search-inventory` | `filters:{assetState:"modified", deleted:false}`, `source:["costDelta"]`, page with `afterKey`. **Use `"modified"`, never `"drifted"`.** | per asset: `costDelta` → `cloud_waste.leakage` |

Providers **not** listed in `config.json` are written as `null` with no note: they were never in
scope. If runner is disabled, write `runner_projects` and `runner_workspaces` as `null`.

### Cloud Waste (calls 15 and 16)

All dollar figures are **list price** (monthly USD as returned by the API). Never apply a rate
of your own, and never describe them as actual spend.

**Call 15, policies.**
- `fields` returned zero hits with `frameworks` set, so leave it out. Each row carries base64 Rego,
  so keep `page_size` at 20 and read results from the saved file with a script, not inline.
- `cost` is **absent**, not null or 0, when a policy has no cost model. Write that policy with
  `monthly_usd: null`. It still counts as a policy with its `total_assets`; it is not $0.
- `type` is an array. Write `asset_type` as the entries joined with `", "`.
- `cloud_waste.monthly_usd` is the sum of `cost` over the policies that have one, rounded to
  2 places. `cloud_waste.violating_assets` is the sum of `total_assets` over those same costed
  policies only, so the count matches the dollars. The yearly figure is derived by the report
  (monthly × 12); never store it.

**Call 16, cost leakage.**
- The tool description says `"drifted"`; it errors. `"modified"` works and is what the UI calls the state.
- Each asset comes back as a full object (about 2 KB) even with `source`. Read results from the
  saved file with a script; do not paste them inline. Results are under `responseObjects`, the
  total is `totalObjects`. `size` can go up to 10000; page with `afterKey`, but stop when rows
  collected equals `totalObjects`: the last page still returns an `afterKey`.
- `leakage.assets` is the count of **all** modified assets. `leakage.costed_assets` is the count
  with `costDelta > 0`. `leakage.monthly_usd` is the sum of the **positive** `costDelta` values,
  **each rounded to 2 places before summing** (the UI rounds per row; summing first can be a cent
  off). Treat a missing `costDelta` as 0. Negative values are excluded, not netted. Do not filter by policy through
  this tool; it ignores that filter.
- `leakage.assets` should equal `iacCoverage.drifted` from call 1. If they differ, add a sentence
  saying so to `cloud_waste.caveats`. If they match, write nothing.

**Failures.** A failed call leaves its fields `null`; the report adds its own "not collected" line,
so write no note for it. If call 15
fails, `policies`, `monthly_usd` and `violating_assets` are all `null`. An empty policy list that
came from a failed or empty response is a failure, not "no waste".

**Notes versus caveats.** `cloud_waste.caveats` is a list of sentences a reader of the scorecard
needs, and every entry is printed on the report. Leave it empty unless a number needs explaining.
`cloud_waste.collector_note` is for process detail (page counts, what came back inline, how totals
were computed) and is never shown. Do not write to `cloud_waste.note`: it is no longer rendered and
the validator flags it. Test for a caveat: would someone reading the scorecard need this sentence?
"13 policies fit one page" is a collector note.

**The key's reach.** A key that cannot see every project returns low waste and leakage numbers
with no error. If either falls by more than half against last week, the validator warns. That
may be a real cleanup or a key problem: check the key's project membership, and record `null`
with a note if it is the key.

### Runner projects are a tree: do not query only the root

Two easy mistakes silently undercount `runner_workspaces`:

- `list-projects-flat` **without `maxDepth`** returns only the top level. Always pass `maxDepth: 5`.
- `list-project-workspaces` on the **root project alone** returns only workspaces attached
  directly to the root. Real ones usually live in nested children.

The reliable count is the **sum of `workspaceCount`** over every project returned with
`maxDepth: 5`. A project with `hasChildren: true` and no `workspaceCount` holds none itself; its
children do. Cross-check the total against `ci-api___list-workspaces` (#9): they should agree
closely, and a large divergence means one was collected wrong.

### The API key may not see the whole tree

Runner and CI counts depend on **which key collects them**. A key that is not a member of the
projects that hold the workspaces reads a low number, with no error. So:

- If a project has `hasChildren: true` but returns no children, or a count is far below last
  week's snapshot (`scripts/validate_snapshot.py` flags a drop of more than half), do **not**
  write the low number. Write `null` and a note naming the suspected cause (key scope).
- A count that is exactly the page size (for example 100 rows from #9) may be capped. Page
  through it if the tool offers a cursor; otherwise record it as a floor with a note.

### Known failure modes: handle, don't hide

- **`integrations-api___list-integrations` can time out (504) for a provider.** Retry once.
  If it still fails, record that provider as `null` and add a note under `integrations.notes`.
  Never write `0`: zero means "none connected", null means "we could not tell".
- **A provider that returns a count but no pagination cursor may be capped** by the API. Record
  the number and add a "treat as a floor" note under `integrations.notes`.
- **`inventory-api___search-inventory` can intermittently 500.** It is not needed for the core
  snapshot; use it only for drill-downs.
- **Any other failed call:** the field is `null` plus a note. **Never substitute a previous
  week's value or an estimate.**

## Step 2: write the snapshot

Write `data/snapshots/<week>.json` matching `templates/snapshot.template.json` exactly; the
report builder reads those key paths. Fill `account_id` and `account_name` from `config.json`. If `account_id` is blank there, write the string `"unspecified"`; never guess an ID.
Keep the `notes`, `events.note` and `integrations.notes` fields: they render as caveats, so write
them for a reader of the scorecard, not as a log of what you did.

Then validate. Do not continue past an `ERROR`:

```bash
python3 scripts/validate_snapshot.py
```

It checks required fields, that `account_id` matches `config.json`, suspicious zeros and
suspicious drops in workspace counts. Fix an `ERROR` by correcting or re-collecting. For a
`WARN`, decide: either the number is real (say so in `notes`) or it was a bad collection
(set it to `null`).

## Step 3: regenerate every deliverable

```bash
python3 scripts/build_report.py      # data/reports/firefly-kpi-scorecard.html
python3 scripts/export_metrics.py    # data/reports/kpi-metrics.csv, .json, .md
```

Both read every snapshot, order them by the `week` field, and compute week-over-week deltas.
`export_metrics.py` imports `derive()` from `build_report.py`, so the four outputs cannot
disagree. Never hand-edit them and never compute a KPI inline here; fix the snapshot or the
script instead. If either script errors, say so rather than reporting stale numbers.

| File | For |
|---|---|
| `data/reports/firefly-kpi-scorecard.html` | opening in a browser, or sharing as a file |
| `data/reports/kpi-metrics.md` | pasting into a wiki, ticket or chat |
| `data/reports/kpi-metrics.csv` | spreadsheets and decks |
| `data/reports/kpi-metrics.json` | anything downstream |

## Step 4: publish (optional, interactive sessions only)

The HTML file is self-contained, so sharing it as a file is enough. If the `Artifact` tool is
available in this session **and** `config.json` has an `artifact_url`, republish
`data/reports/firefly-kpi-scorecard.html` to that URL so the link stays stable. A headless
`claude -p` run has no `Artifact` tool: skip this step and say so. Never invent a URL.

## Step 5: the written read

The scorecard shows the tiles; this says what they mean. Lead with the answer.

For a quick summary, cover these and stop:

1. **IaC coverage** now, and the change from last week in points.
2. **What moved most**: the two or three largest week-over-week swings, with the number and
   a one-line read on why it plausibly moved.
3. **Watchlist changes**: anything that newly crossed a target, in either direction.
4. **Any metric that failed to collect**, named explicitly.
5. Where the report file is.

For a fuller report: a headline table of what moved or sits off target, then the metrics grouped
by pillar (Inventory & Coverage, IaC Estate, Governance & Posture, Automation & Guardrails,
Adoption, Cloud Waste), each with current, prior, change, target and status; then a watchlist ordered by
severity; then the caveats.

### Interpretation

Explain what moved and why, not just the deltas. Tie changes to events you can actually
see in the data, such as a crawler re-ingest, an integration outage, or a new team onboarding,
so the reader can tell platform activity from real remediation. Where a number changed for a
reason nobody has confirmed, say it is **unexplained**; do not guess.

A large single-week jump in assets, codified and ghost *together* is a crawler ingest event,
not remediation. Say which it is; do not let an ingest read as progress. Put the explanation in
the snapshot's top-level `notes` so it travels with the report.

### Caveats that must appear in every report

- **Coverage is a floor.** Repo-scoped coverage can be misattributed (see `KPI-FRAMEWORK.md`),
  so do not quote repo-scoped numbers.
- **Dollar figures are list price.** Say so every time you quote a Cloud Waste number.
  A leakage dollar figure comes from the assets with a cost impact, not from all drifted assets.
- **Workspace count is not workspace health.** Report the count with a pass figure beside it if
  you have one.
- Carry forward active collection failures (timeouts, capped counts) named in
  `integrations.notes`, plus everything in `data/caveats.md`.
- A metric that failed to collect reads **"no data"**: never 0, never carried forward from
  last week.

Then stop. Do not propose remediation work unless asked.
