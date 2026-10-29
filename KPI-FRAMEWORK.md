# Firefly KPI Framework

The single source of truth for what each KPI means, how it is computed, and where its data comes
from. The report scripts and the collection skill implement this document; if they disagree, fix
them, not this.

**Targets are not here.** They are per account, in `data/targets.json`, set by `python3 setup.py`.
A target that suits one estate prints misleading status on another.

**Cadence:** weekly, on ISO week boundaries (Mon-Sun). A snapshot may be taken any day and is stamped
with its ISO week. Re-running within the same week replaces that week's file; earlier weeks are never touched.

Every KPI below reads from a live Firefly API call. Where an API is unreliable or a metric carries a
caveat, that is stated here, and the caveat travels into the report.

---

## Pillar 1: Inventory & IaC Coverage

Four mutually exclusive asset states from `inventory-api/get-iac-coverage`.

| KPI | Definition | Direction |
|---|---|---|
| **Total assets** | codified + unmanaged + ghost + drifted | tracked, not targeted |
| **IaC Coverage %** | `(codified + drifted) / (codified + drifted + unmanaged)`. Ghosts are excluded: they no longer exist in the cloud | up |
| **Codified assets** | assets managed by IaC | up |
| **Unmanaged assets** | exist in the cloud, absent from IaC | down |
| **Ghost assets** | in IaC state, no longer in the cloud | down |
| **Ghost ratio** | `ghost / (codified + drifted)`: state-file hygiene | down |
| **Drifted assets** | in both, configuration diverged | down |
| **Drift rate** | `drifted / (codified + drifted)` | down |
| **Cloud accounts connected** | sum of active integrations across the providers in scope | up |

**Treat coverage as a floor.** Firefly can attribute IaC state to a repository by *module source*
rather than by the repository that owns the unit, and this was observed to apply only to units at
directory depth 2 or less. Any repo-scoped coverage figure can therefore read low, and the
account-level coverage KPI may be understated by an unquantified margin. Do not quote
repo-scoped coverage numbers.

**Why ghost ratio matters:** a high ghost count against codified means state files are retained long
after resources are destroyed, or crawlers are reading stale backends. It is a conversation, not
just a number.

## Pillar 2: IaC Estate

From `iacStacks-api/search-iac-stacks`.

| KPI | Definition | Direction |
|---|---|---|
| **Total stacks** | discovered IaC state files | up |
| **Terraform stacks** | `stateType = terraform` | n/a |
| **Non-Terraform stacks** | total minus Terraform (Pulumi, CloudFormation, Helm, Kubernetes, OpenTofu) | n/a |

## Pillar 3: Governance & Posture

From `governance-api/get_frameworks_score` and `get_insights`.

| KPI | Definition | Direction |
|---|---|---|
| **CIS / NIST / HIPAA score** | posture score, 0-100 | up |
| **Frameworks scored** | frameworks returning a non-null score | up |
| **Policies available** | total OPA insights in the account | n/a |
| **Custom policies** | account-authored, not Firefly default (`onlySubscribed`) | up |

**Caveat:** a framework with `score: null` and `totalAssets: 0` has no subscribed policies. That is
*not* a zero score. The report renders it as "not scored", never 0.

## Pillar 4: Automation & Guardrails

From `ci-api/list-guardrails`, `ci-api/list-workspaces` and `runners-api`.

| KPI | Definition | Direction |
|---|---|---|
| **Guardrails configured** | total guardrail rules | up |
| **Guardrail type coverage** | distinct types in use out of {policy, cost, resource, tag} | up |
| **Guardrails enabled** | `isEnabled = true` | up |
| **CI workspaces detected** | pipelines reporting into Firefly | up |
| **Runner workspaces** | Firefly-native IaC workspaces: the **sum of `workspaceCount` across the whole project tree**, not the root alone. Hidden when the account does not use Runners | up |

Zero guardrails and zero CI workspaces means Firefly is read-only observability: everything in
Pillar 1 is detection after the fact.

## Pillar 5: Adoption

From `auditLog-api/query-audit-log-activities` and `events-api/query-events`, over the trailing 7 days.

| KPI | Definition | Direction |
|---|---|---|
| **Weekly active users** | distinct `actor_emails` with actor_type=user | up |
| **Weekly activities** | total user-attributed audit events | up |
| **Feature breadth** | distinct `scopes` touched | up |
| **Top scope concentration** | largest scope's share of activity | down |
| **Top user concentration** | largest single user's share of activity (key-person risk) | down |
| **ClickOps events** | `eventType = click_ops`: console changes bypassing IaC | down |
| **Denied events** | `eventType = denied` | down |

**Top user concentration** exists because activity volume alone hides key-person risk: total
activity can rise in the same week one person comes to carry most of it.

**ClickOps caveat:** in estates with heavy data-plane service activity (for example data-warehouse
job service accounts), `cli_sdk` events can dwarf everything else. A click-ops-as-percent-of-total
ratio is then meaningless. Track the **absolute `click_ops` count** only.

## Pillar 6: Cloud Waste

From `governance-api/get_insights` and `inventory-api/search-inventory`. **Every dollar figure is
list price**: the monthly USD the API returns, with no discount, commitment or credit applied. The
scripts never apply a rate of their own.

| KPI | Definition | Direction |
|---|---|---|
| **Cloud waste / month** | sum of `cost` over Cloud Waste policies that have a cost model | down |
| **Cloud waste / year** | monthly × 12. The dashboard calls this Cloud Waste (Projected Savings) | down |
| **Violating assets** | sum of `total_assets` over the **costed** policies, so it matches the dollars | down |
| **Cost leakage / year** | sum of positive `costDelta` over modified (drifted) assets, each rounded to 2 places first (as the UI column does), × 12. The dashboard's Drift Impact | down |
| **Drifted, with cost** | modified assets with `costDelta > 0`: the assets that produce the leakage dollars | down |
| **Drifted, all** | every modified asset. Same count as the drifted figure in Pillar 1 | down |

**No cost model is not zero.** `cost` is absent when a policy has no cost model. Such a policy is
listed with its asset count and a "no cost model" label and adds nothing to any dollar figure or to
violating assets.

**Two drifted counts, on purpose.** Most drifted assets carry no cost impact, so a tile that read
"200 assets, $5K a year" would imply all 200 produce the dollars. Read "Drifted, with cost" next to
the leakage figure, and "Drifted, all" for the estate-wide count.

**Negative `costDelta`** (a drifted asset that costs less than its code says) is excluded, not
netted off. This follows how the UI appears to compute its tile; it has not been confirmed on an
estate that has negative values.

**An asset that violates two policies is counted twice** in violating assets. Whether the API
de-duplicates is not established.

**The policy set can change.** A policy that appears (new, enabled, or newly matching assets) raises
the totals, and one that disappears (fixed, disabled, or out of the key's view) lowers them, with no
change in underlying waste. The policy table marks new policies "new" and shows a policy missing from
this week's list muted as "no longer reported" with last week's figures. It is never labelled
"resolved".

**Notes versus caveats.** A snapshot's `cloud_waste` block has two text fields and the report prints only one.
`caveats` is a list of sentences a reader of the scorecard needs, for example that leakage assets differ from
the drifted count. `collector_note` is process detail (how the data was collected, page counts, what was
copied) and is never shown. The "not collected" lines are generated by the report from null fields, not typed
by the collector. The old free-text `note` is no longer rendered, and the validator flags it.

**Collecting-key scope.** A key that cannot see every project returns low waste and leakage with no
error. A drop of more than half is flagged as "may be a cleanup or a key that can't see every project".

**Links.** Policy details `governance?id=<id>`, a policy's violating assets
`inventory?defaultView=false&classifications=<id>`, leakage `inventory?defaultView=false&state=modified`.
**That page opens drifted *and ghost* assets, unsorted** (reported by the account owner on 2026-10-28,
roughly 2,800 rows in one tenant; not checked here). The API's `assetState: "modified"` returns drifted
assets only, so the two do not match. The sort cannot be set from a URL: the dashboard sets it through its
own state. Sorting the page by Drift Cost Impact is expected to surface the assets behind the leakage
dollars, but that column sort has not been confirmed. The Cloud Waste filter cannot be set from a URL,
so overall waste links to `/governance`, labelled "Open Governance, then pick Cloud Waste". "Drifted, all"
has no link, because a link from its count to that page would look broken.

---

## Data sources & reliability

| API | Tool | Notes |
|---|---|---|
| IaC coverage | `inventory-api___get-iac-coverage` | fast |
| Inventory counts | `inventory-api___search-inventory` | can intermittently 500; retry once. Not needed for the core snapshot |
| IaC stacks | `iacStacks-api___search-iac-stacks` | read `totalObjects` with `pageSize: 1` |
| Framework scores | `governance-api___get_frameworks_score` | |
| Policies | `governance-api___get_insights` | read `total` with `page_size: 1` |
| Guardrails | `ci-api___list-guardrails` | |
| CI workspaces | `ci-api___list-workspaces` | page size caps at 100; a count of exactly 100 may be a floor |
| Runner projects | `runners-api___list-projects-flat` | **must pass `maxDepth: 5`**, or nested workspaces are invisible |
| Integrations | `integrations-api___list-integrations` | a provider can 504; a count without a pagination cursor may be capped. Record partial and flag |
| Cloud Waste policies | `governance-api___get_insights` | `frameworks:["Cloud Waste"]`, `category:"Optimization"`, `onlyMatchingAssets:true`. **Do not pass `fields`** (zero hits). Rows are large (base64 Rego): `page_size` 20, paginate |
| Cost leakage | `inventory-api___search-inventory` | `assetState:"modified"`; `"drifted"` errors. Full asset objects (~2 KB each) come back regardless of `source` |
| Audit log | `auditLog-api___query-audit-log-activities` | facets give users and scopes in one call |
| Events | `events-api___query-events` | use `size: 1` plus facets; never enumerate |

**Collecting-key scope.** Runner and CI counts depend on which API key collects them. A key that
is not a member of the relevant projects reads a low number with no error. See the skill for the
checks.

## Reporting rules

1. **Never invent a number.** A metric that failed to collect is `null` and renders as "no data",
   never 0, and is never carried forward from a previous week.
2. **Deltas are week-over-week**, against the previous snapshot on file. If a metric has no value
   in that snapshot, the delta falls back to the most recent earlier snapshot that has one, up to
   4 ISO weeks back, and the report labels it (for example `+1,200 vs W40`). Beyond 4 weeks it reads
   "no recent data". A metric with no earlier value at all reads "baseline". Only real measurements
   are compared; nothing is carried forward into a snapshot.
3. **A metric with a caveat carries the caveat into the report.** No silently misleading ratios.
4. **Snapshots are per week and per account.** `snapshots/YYYY-Www.json`; the builders refuse to
   compute deltas if a snapshot's `account_id` differs from `data/config.json`.
5. **Standing caveats** specific to an account go in `data/caveats.md`, not in this file.
