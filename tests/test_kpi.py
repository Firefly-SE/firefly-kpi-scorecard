import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DEMO = REPO / "examples" / "demo-home"
PY = sys.executable


def run(script, home, *args, env=None, check=False):
    e = {**os.environ, "KPI_HOME": str(home), **(env or {})}
    return subprocess.run([PY, str(REPO / script), *args], env=e, capture_output=True, text=True, check=check)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        shutil.copytree(DEMO, self.home)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def html(self):
        return (self.home / "reports" / "firefly-kpi-scorecard.html").read_text()


class TestReports(Base):
    def test_builds_all_outputs(self):
        self.assertEqual(run("scripts/build_report.py", self.home).returncode, 0)
        r = run("scripts/export_metrics.py", self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        for f in ("firefly-kpi-scorecard.html", "kpi-metrics.csv", "kpi-metrics.json", "kpi-metrics.md"):
            self.assertTrue((self.home / "reports" / f).exists(), f)
        self.assertIn("Example Corp", self.html())

    def test_null_renders_no_data_not_zero(self):
        run("scripts/build_report.py", self.home)
        md = (run("scripts/export_metrics.py", self.home) and
              (self.home / "reports" / "kpi-metrics.md").read_text())
        self.assertIn("azurerm", md)          # the demo's null-integration note is a caveat
        self.assertIn("Demo data only", md)   # standing caveat from caveats.md

    def test_targets_come_from_targets_json(self):
        (self.home / "targets.json").write_text("{}")
        run("scripts/build_report.py", self.home)
        self.assertNotIn('class="tile tile-ok"', self.html())
        self.assertNotIn('class="tile tile-off"', self.html())
        self.assertNotIn('class="tile tile-watch"', self.html())
        (self.home / "targets.json").write_text(json.dumps({"coverage_pct": 70}))
        run("scripts/build_report.py", self.home)
        self.assertIn('class="tile tile-ok"', self.html())
        self.assertIn("target 70%", self.html())

    def test_hidden_metric_is_not_rendered(self):
        cfg = json.loads((self.home / "config.json").read_text())
        cfg["hidden_metrics"] = ["runner_workspaces"]
        (self.home / "config.json").write_text(json.dumps(cfg))
        run("scripts/build_report.py", self.home)
        self.assertNotIn("Runner workspaces", self.html())
        self.assertIn("CI workspaces", self.html())

    def test_refuses_snapshot_from_another_account(self):
        p = self.home / "snapshots" / "2026-W02.json"
        s = json.loads(p.read_text())
        s["account_id"] = "ffffffffffffffffffffffff"
        p.write_text(json.dumps(s))
        for script in ("scripts/build_report.py", "scripts/export_metrics.py"):
            r = run(script, self.home)
            self.assertNotEqual(r.returncode, 0, script)
            self.assertIn("not for account_id", r.stderr)

    def test_unspecified_account_id_is_not_shown(self):
        for p in (self.home / "snapshots").glob("*.json"):
            s = json.loads(p.read_text())
            s["account_id"] = "unspecified"
            p.write_text(json.dumps(s))
        cfg = json.loads((self.home / "config.json").read_text())
        cfg["account_id"] = ""
        (self.home / "config.json").write_text(json.dumps(cfg))
        self.assertEqual(run("scripts/build_report.py", self.home).returncode, 0)
        self.assertNotIn("unspecified", self.html())
        self.assertEqual(run("scripts/export_metrics.py", self.home).returncode, 0)
        self.assertNotIn("unspecified", (self.home / "reports" / "kpi-metrics.md").read_text())

    def test_user_strings_are_escaped(self):
        p = self.home / "snapshots" / "2026-W02.json"
        s = json.loads(p.read_text())
        s["adoption"]["users"][0]["email"] = "<script>x</script>"
        p.write_text(json.dumps(s))
        run("scripts/build_report.py", self.home)
        self.assertNotIn("<script>x</script>", self.html())


class TestCloudWaste(Base):
    def snap(self, week="2026-W02"):
        return self.home / "snapshots" / f"{week}.json"

    def edit(self, week, fn):
        p = self.snap(week)
        s = json.loads(p.read_text())
        fn(s)
        p.write_text(json.dumps(s))

    def build(self):
        self.assertEqual(run("scripts/build_report.py", self.home).returncode, 0)
        return self.html()

    def section(self):
        """Only the Cloud Waste band and policy table, so other pillars cannot satisfy a check."""
        h = self.build()
        return h[h.index("Cloud Waste</h2>"):h.index("Watchlist</h2>")]

    def test_yearly_is_monthly_times_twelve_and_labelled_list_price(self):
        h = self.build()
        monthly = json.loads(self.snap().read_text())["cloud_waste"]["monthly_usd"]
        self.assertIn(f"${monthly * 12:,.0f}", h)
        self.assertIn("list price", h)
        self.assertIn("Monthly (list price)", h)

    def test_leakage_shows_costed_and_all_drifted_counts(self):
        h = self.build()
        self.assertIn("Drifted, with cost", h)
        self.assertIn("Drifted, all", h)

    def test_no_cost_model_policy_is_labelled_not_zero(self):
        h = self.build()
        self.assertIn("no cost model", h)
        self.assertNotIn("$0.00", h)

    def test_links_point_back_to_the_ui(self):
        h = self.build()
        self.assertIn('href="https://app.firefly.ai/governance"', h)
        self.assertIn("https://app.firefly.ai/governance?id=demo-policy-1", h)
        self.assertIn("https://app.firefly.ai/inventory?defaultView=false&amp;classifications=demo-policy-1", h)
        self.assertIn("https://app.firefly.ai/inventory?defaultView=false&amp;state=modified", h)

    def test_tile_links_say_what_they_open(self):
        sec = self.section()
        # Cut each tile at its own closing tag, or the last tile swallows the policy table after it.
        tile = lambda name: next(b for b in sec.split('<div class="tile ')
                                 if name in b).split("\n      </div>")[0]
        self.assertIn("Open Governance, then pick Cloud Waste", tile("Cloud waste / year"))
        for name in ("Cost leakage / year", "Drifted, with cost"):
            t = tile(name)
            self.assertIn("Opens drifted and ghost assets", t)
            self.assertIn("Sort by Drift Cost Impact", t)
            self.assertIn("state=modified", t)
        self.assertNotIn("href=", tile("Drifted, all"))      # 90 must not link to a page of thousands
        self.assertNotIn("Open in Firefly", sec)
        self.assertIn("drifted and ghost assets, unsorted", self.html())
        self.assertNotIn("opens the drifted assets unsorted", self.html())

    def test_week_over_week_chip_and_new_policy(self):
        h = self.build()
        self.assertIn("−$294.32", h)         # monthly waste, W02 vs W01
        self.assertIn(">new<", h)            # policy 5 only exists in W02

    def test_removed_policy_is_shown_muted_with_last_weeks_figures(self):
        self.edit("2026-W02", lambda s: s["cloud_waste"].update(
            policies=[p for p in s["cloud_waste"]["policies"] if p["id"] != "demo-policy-4"]))
        h = self.section()
        row = h[h.index("Demo: old machine images"):]
        row = row[:row.index("</tr>")]
        self.assertIn("no longer reported", row)
        self.assertIn("was 210", row)
        self.assertNotIn("resolved", h.lower())

    def test_decrease_is_green_and_increase_is_red(self):
        h = self.section()
        self.assertIn("chip-good", h)   # unattached volumes fell week over week
        self.edit("2026-W02", lambda s: s["cloud_waste"]["leakage"].update(monthly_usd=999.0))
        self.assertIn("chip-bad", self.section())   # leakage rose from W01's 310.25

    def test_headline_chip_never_prints_negative_zero(self):
        w1 = json.loads(self.snap("2026-W01").read_text())["inventory"]
        self.edit("2026-W02", lambda s: s["inventory"].update(w1))          # identical coverage
        h = self.build()
        self.assertIn("no change vs 2026-W01", h)
        self.assertNotIn("0.00 pt", h)
        # A real but sub-display change (about 0.001 pt) must not print "+0.00" either.
        self.edit("2026-W02", lambda s: s["inventory"].update(unmanaged=w1["unmanaged"] + 1))
        h = self.build()
        self.assertIn("no change vs 2026-W01", h)
        self.assertNotIn("0.00 pt", h)
        self.assertNotIn("+0%", h)

    def test_subtitle_names_pillars_with_nothing_to_compare(self):
        self.assertNotIn("no comparison yet", self.build())
        self.edit("2026-W01", lambda s: s.pop("cloud_waste", None))
        h = self.build()
        self.assertIn("Week over week against 2026-W01 · no comparison yet for Cloud Waste", h)

    def test_first_week_caveat_only_when_no_earlier_week_has_the_block(self):
        run("scripts/build_report.py", self.home)
        run("scripts/export_metrics.py", self.home)
        md = lambda: (self.home / "reports" / "kpi-metrics.md").read_text()
        # Demo: W02 has an earlier week (W01) with the block, so the line must not print.
        self.assertNotIn("first week with Cloud Waste data", self.html())
        self.assertNotIn("first week with Cloud Waste data", md())
        # Earlier weeks without the block: it is the first week, so the line prints.
        for wk in ("2025-W50", "2025-W51", "2025-W52", "2026-W01"):
            self.edit(wk, lambda s: s.pop("cloud_waste", None))
        run("scripts/build_report.py", self.home)
        run("scripts/export_metrics.py", self.home)
        self.assertIn("first week with Cloud Waste data", self.html())
        self.assertIn("first week with Cloud Waste data", md())
        self.assertNotIn("first run has no", self.html())

    def test_first_run_is_baseline_not_zero(self):
        (self.home / "snapshots" / "2025-W50.json").unlink()
        for wk in ("2025-W51", "2025-W52", "2026-W02"):
            self.snap(wk).unlink()
        h = self.section()
        self.assertIn("baseline", h)
        self.assertIn("nothing to compare against", h)
        self.assertNotIn("no change", h)

    def test_earlier_snapshots_without_the_block_give_baseline_not_a_spike(self):
        """Every account collected before this feature has snapshots like this."""
        for wk in ("2025-W50", "2025-W51", "2025-W52", "2026-W01"):
            self.edit(wk, lambda s: s.pop("cloud_waste", None))
        h = self.section()
        self.assertIn("baseline", h)
        self.assertIn("nothing to compare against", h)
        self.assertNotIn("chip-bad", h)
        self.assertNotIn("chip-good", h)
        self.assertNotIn("+$", h)

    def test_failed_fetch_reads_no_data_and_draws_no_drop_or_stale_sparkline(self):
        # Three weeks with real data so a sparkline could be drawn, then a failed fetch.
        w01 = json.loads(self.snap("2026-W01").read_text())["cloud_waste"]
        self.edit("2025-W52", lambda s: s.update(cloud_waste=w01))

        def fail(s):
            cw = s["cloud_waste"]
            cw.update(monthly_usd=None, violating_assets=None, policies=None,
                      collector_note="get_insights 504 twice; retried once")
        self.edit("2026-W02", fail)
        sec = self.section()
        tile = next(b for b in sec.split('<div class="tile ') if "Cloud waste / year" in b)
        self.assertIn("no data", tile)
        self.assertNotIn('class="spark', tile)      # would end on W01's value beside "no data"
        self.assertNotIn("chip-", tile)             # no delta, and above all no drop to zero
        self.assertNotIn("$0", sec)
        self.assertNotIn("Changes are against", sec)
        self.assertNotIn("shown muted", sec)         # no table, so no row can be muted
        run("scripts/export_metrics.py", self.home)
        md = (self.home / "reports" / "kpi-metrics.md").read_text()
        for out in (self.html(), md):       # the builder says so itself; the collector types nothing
            self.assertIn("Cloud Waste policies were not collected this week", out)
            self.assertNotIn("504", out)    # process detail stays out of the reader's caveats

    # ---- a gap in the waste data: good week, failed week, good week (synthetic fixtures only) ----

    def gap_fixture(self, failed_as="null"):
        """2025-W52 and 2026-W01 good, 2026-W02 failed (or absent), 2026-W03 good.

        W52 to W01 crosses a year boundary but is one ISO week. W03 drops one policy.
        """
        w01 = json.loads(self.snap("2026-W01").read_text())["cloud_waste"]
        self.edit("2025-W52", lambda s: s.update(cloud_waste=json.loads(json.dumps(w01))))
        w02 = json.loads(self.snap("2026-W02").read_text())
        w03 = json.loads(json.dumps(w02))
        w03.update(week="2026-W03", collected_at="2026-01-19",
                   window={"start": "2026-01-12", "end": "2026-01-19"})
        cw = json.loads(json.dumps(w01))                       # same policies as W01 ...
        cw["policies"] = [p for p in cw["policies"] if p["id"] != "demo-policy-4"]   # ... minus one
        for p_ in cw["policies"]:
            if p_["id"] == "demo-policy-1":
                p_["total_assets"], p_["monthly_usd"] = 30, 330.0                    # improved
        costed = [p_ for p_ in cw["policies"] if p_["monthly_usd"] is not None]
        cw["monthly_usd"] = round(sum(p_["monthly_usd"] for p_ in costed), 2)
        cw["violating_assets"] = sum(p_["total_assets"] for p_ in costed)
        w03["cloud_waste"] = cw
        self.snap("2026-W03").write_text(json.dumps(w03))
        if failed_as == "null":
            self.edit("2026-W02", lambda s: s["cloud_waste"].update(
                monthly_usd=None, violating_assets=None, policies=None))
        else:                                                   # no snapshot at all for that week
            self.snap("2026-W02").unlink()

    @staticmethod
    def tile_html(sec, name):
        return next(b for b in sec.split('<div class="tile ') if name in b).split("\n      </div>")[0]

    @staticmethod
    def spark_polylines(tile):
        """(solid, gap): x positions of each solid run and of each dashed gap segment."""
        out = {"spark-line": [], "spark-gap": []}
        for pts, cls in re.findall(r'<polyline points="([^"]+)" class="(spark-line|spark-gap)"', tile):
            out[cls].append([round(float(q.split(",")[0]), 1) for q in pts.split()])
        return out["spark-line"], out["spark-gap"]

    def test_failed_week_between_two_good_weeks(self):
        self.gap_fixture("null")
        h = self.build()
        sec = h[h.index("Cloud Waste</h2>"):h.index("Watchlist</h2>")]
        tile = self.tile_html(sec, "Cloud waste / year")
        # 1. Chips and table compare against W01, the last good week, and say so.
        self.assertIn("vs W01", tile)
        self.assertNotIn("baseline", tile)
        self.assertNotIn("no comparison yet", h)
        self.assertIn("Changes are against 2026-W01", sec)
        table = sec[sec.index("Cloud waste by policy"):]
        row = table[table.index("Demo: unattached block volumes"):]
        row = row[:row.index("</tr>")]
        self.assertEqual(re.findall(r'class="chip chip-\w+"[^>]*>([^<]+)<', row), ["−10 vs W01", "−$82.50 vs W01"])
        # 2. No policy is falsely flagged as new (the failed W02 must not be the baseline).
        self.assertNotIn(">new<", table)
        # 3. The removed policy carries the week its figures come from.
        gone = table[table.index('class="row-gone"'):]
        gone = gone[:gone.index("</tr>")]
        self.assertIn("no longer reported", gone)
        self.assertIn("was 210 (W01)", gone)
        # 4. The subtitle names both weeks and matches the chips.
        sub = re.search(r'class="sub">([^<]+)<', h).group(1)
        self.assertIn("Week over week against 2026-W02 · Cloud Waste: 3 of 6 figures against "
                      "2026-W01 (W02 not collected)", sub)
        # 5. Sparkline: one step W52 to W01 (year boundary), a dashed segment two steps wide across
        #    the failed week, nothing interpolated.
        solid, gap = self.spark_polylines(tile)
        self.assertEqual(solid, [[0.0, 25.3]])
        self.assertEqual(gap, [[25.3, 76.0]])

    def test_week_with_no_snapshot_at_all_leaves_the_same_gap(self):
        self.gap_fixture("absent")
        h = self.build()
        sec = h[h.index("Cloud Waste</h2>"):h.index("Watchlist</h2>")]
        solid, gap = self.spark_polylines(self.tile_html(sec, "Cloud waste / year"))
        self.assertEqual(solid, [[0.0, 25.3]])
        self.assertEqual(gap, [[25.3, 76.0]])
        self.assertIn("vs W01", self.tile_html(sec, "Cloud waste / year"))
        self.assertNotIn(">new<", sec)

    def test_hiding_every_cloud_waste_metric_hides_its_caveats_too(self):
        cfg = json.loads((self.home / "config.json").read_text())
        cfg["hidden_metrics"] = ["waste_yearly", "waste_monthly", "waste_assets",
                                 "leakage_yearly", "leakage_costed", "leakage_assets"]
        (self.home / "config.json").write_text(json.dumps(cfg))
        h = self.build()
        run("scripts/export_metrics.py", self.home)
        md = (self.home / "reports" / "kpi-metrics.md").read_text()
        self.assertNotIn("Cloud Waste</h2>", h)
        self.assertNotIn("cloud waste</strong>", h)       # no orphaned caveats under a missing section
        self.assertNotIn("**cloud waste**", md)
        self.assertNotIn("list price", md)

    def test_collector_notes_never_reach_the_reader(self):
        chatter = "13 policies fit one page, returned inline; totals computed by script from copied fields"
        def f(s):
            s["cloud_waste"]["collector_note"] = chatter
            s["cloud_waste"]["note"] = chatter            # deprecated free text, also never shown
            s["cloud_waste"]["caveats"] = ["Leakage assets differ from drifted by 2."]
        self.edit("2026-W02", f)
        run("scripts/build_report.py", self.home)
        run("scripts/export_metrics.py", self.home)
        md = (self.home / "reports" / "kpi-metrics.md").read_text()
        for out in (self.html(), md):
            self.assertNotIn("returned inline", out)
            self.assertNotIn("copied fields", out)
            self.assertIn("Leakage assets differ from drifted by 2.", out)   # an explicit caveat does show
        r = run("scripts/validate_snapshot.py", self.home)
        self.assertIn("cloud_waste.note is no longer shown", r.stdout)       # a legacy note is flagged

    def test_tooltip_says_ghosts_are_counted_separately(self):
        self.assertIn("Ghost assets are counted separately", self.build())

    def test_muted_row_sentence_only_when_there_is_a_prior_week(self):
        self.assertIn("shown muted", self.section())
        for wk in ("2025-W50", "2025-W51", "2025-W52", "2026-W01"):
            self.edit(wk, lambda s: s.pop("cloud_waste", None))
        self.assertNotIn("shown muted", self.section())

    def test_missing_section_reads_no_data(self):
        self.edit("2026-W02", lambda s: s.pop("cloud_waste"))
        h = self.section()
        self.assertIn("no data", h)
        self.assertNotIn("$", h)

    def test_policy_strings_are_escaped(self):
        def f(s):
            s["cloud_waste"]["policies"][0]["name"] = "<script>x</script>"
            s["cloud_waste"]["policies"][0]["id"] = 'a"b'
        self.edit("2026-W02", f)
        h = self.build()
        self.assertNotIn("<script>x</script>", h)
        self.assertNotIn('id=a"b', h)

    def test_exports_carry_dollars_and_caveats(self):
        run("scripts/build_report.py", self.home)
        self.assertEqual(run("scripts/export_metrics.py", self.home).returncode, 0)
        md = (self.home / "reports" / "kpi-metrics.md").read_text()
        self.assertIn("Cloud waste / year", md)
        self.assertIn("list price", md)
        self.assertIn("cannot see all projects", md)

    def test_validator_flags_total_that_disagrees_with_rows(self):
        self.edit("2026-W02", lambda s: s["cloud_waste"].update(monthly_usd=1.0))
        r = run("scripts/validate_snapshot.py", self.home)
        self.assertIn("sum of costed policies", r.stdout)

    def test_big_drop_warns_as_cleanup_or_key_not_a_fault(self):
        def f(s):
            cw = s["cloud_waste"]
            cw["monthly_usd"] = 10.0
            cw["violating_assets"] = 1
            cw["policies"] = [{"id": "x", "name": "n", "asset_type": "t", "total_assets": 1, "monthly_usd": 10.0}]
        self.edit("2026-W02", f)
        r = run("scripts/validate_snapshot.py", self.home)
        self.assertEqual(r.returncode, 0)
        self.assertIn("may be a real cleanup", r.stdout)
        self.assertNotIn("ERROR", r.stdout)


class TestValidator(Base):
    def test_demo_is_ok(self):
        r = run("scripts/validate_snapshot.py", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_wrong_account_is_an_error(self):
        p = self.home / "snapshots" / "2026-W02.json"
        s = json.loads(p.read_text())
        s["account_id"] = "ffffffffffffffffffffffff"
        p.write_text(json.dumps(s))
        r = run("scripts/validate_snapshot.py", self.home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ERROR", r.stdout)

    def test_missing_field_is_an_error(self):
        p = self.home / "snapshots" / "2026-W02.json"
        s = json.loads(p.read_text())
        del s["inventory"]["codified"]
        p.write_text(json.dumps(s))
        self.assertEqual(run("scripts/validate_snapshot.py", self.home).returncode, 1)

    def test_workspace_drop_warns(self):
        p1 = self.home / "snapshots" / "2026-W01.json"
        s = json.loads(p1.read_text())
        s["automation"]["runner_workspaces"] = 100
        p1.write_text(json.dumps(s))
        p2 = self.home / "snapshots" / "2026-W02.json"
        s = json.loads(p2.read_text())
        s["automation"]["runner_workspaces"] = 1
        p2.write_text(json.dumps(s))
        r = run("scripts/validate_snapshot.py", self.home)
        self.assertEqual(r.returncode, 0)
        self.assertIn("WARN", r.stdout)
        self.assertIn("runner_workspaces", r.stdout)


class TestSetup(Base):
    def test_unattended_setup_writes_config(self):
        home = self.tmp / "fresh"
        mcp = self.tmp / "mcp.json"
        answers = {"account_name": "Acme", "account_id": "a" * 24, "access_key": "AK", "secret_key": "SK",
                   "mcp_server": "acme-firefly", "targets_mode": "1", "provider_gcp": False,
                   "runner_enabled": False, "schedule": False}
        af = self.tmp / "a.json"
        af.write_text(json.dumps(answers))
        r = run("setup.py", home, "--answers", str(af), "--skip-check", env={"KPI_MCP_JSON": str(mcp)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = json.loads((home / "config.json").read_text())
        self.assertEqual(cfg["account_name"], "Acme")
        self.assertEqual(cfg["providers"], ["aws", "azurerm", "k8s"])
        self.assertEqual(cfg["hidden_metrics"], ["runner_workspaces"])
        targets = json.loads((home / "targets.json").read_text())
        self.assertEqual(targets["coverage_pct"], 75)
        for k in ("unmanaged", "drifted", "ci_workspaces", "runner_workspaces"):
            self.assertNotIn(k, targets)      # estate-size counts are never defaulted
        self.assertNotIn("runner_workspaces", targets)
        m = json.loads(mcp.read_text())
        self.assertEqual(m["mcpServers"]["acme-firefly"]["headers"]["X-secret-Key"], "SK")
        self.assertEqual(oct(mcp.stat().st_mode & 0o777), "0o600")
        self.assertTrue((home / "caveats.md").exists())

    def test_account_id_is_optional(self):
        home = self.tmp / "fresh"
        af = self.tmp / "a.json"
        af.write_text(json.dumps({"account_name": "Acme", "access_key": "AK", "secret_key": "SK"}))
        r = run("setup.py", home, "--answers", str(af), "--skip-check", env={"KPI_MCP_JSON": str(self.tmp / "m.json")})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((home / "config.json").read_text())["account_id"], "")

    def test_existing_server_needs_no_keys_and_writes_no_mcp_json(self):
        home = self.tmp / "fresh"
        mcp = self.tmp / "mcp.json"
        af = self.tmp / "a.json"
        af.write_text(json.dumps({"account_name": "Acme", "existing_server": True, "mcp_server": "my-firefly",
                                  "targets_mode": "1"}))
        r = run("setup.py", home, "--answers", str(af), "--skip-check", env={"KPI_MCP_JSON": str(mcp)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = json.loads((home / "config.json").read_text())
        self.assertEqual((cfg["mcp_server"], cfg["mcp_mode"]), ("my-firefly", "existing"))
        self.assertFalse(mcp.exists())

    def test_default_mode_is_project_scoped_keys(self):
        home = self.tmp / "fresh"
        af = self.tmp / "a.json"
        af.write_text(json.dumps({"account_name": "Acme", "access_key": "AK", "secret_key": "SK", "targets_mode": "1"}))
        run("setup.py", home, "--answers", str(af), "--skip-check", env={"KPI_MCP_JSON": str(self.tmp / "m.json")}, check=True)
        self.assertEqual(json.loads((home / "config.json").read_text())["mcp_mode"], "project")

    def test_rerun_keeps_existing_values(self):
        home = self.tmp / "fresh"
        mcp = self.tmp / "mcp.json"
        af = self.tmp / "a.json"
        af.write_text(json.dumps({"account_name": "Acme", "account_id": "a" * 24, "access_key": "AK",
                                  "secret_key": "SK", "targets_mode": "1", "target_wau": 33}))
        env = {"KPI_MCP_JSON": str(mcp)}
        run("setup.py", home, "--answers", str(af), "--skip-check", env=env, check=True)
        af.write_text(json.dumps({"account_name": "Acme", "account_id": "a" * 24, "targets_mode": "1"}))
        run("setup.py", home, "--answers", str(af), "--skip-check", env=env, check=True)
        self.assertEqual(json.loads((home / "targets.json").read_text())["wau"], 33)
        self.assertEqual(json.loads(mcp.read_text())["mcpServers"]["firefly"]["headers"]["X-access-Key"], "AK")


class TestNoAccountSpecificStrings(unittest.TestCase):
    """The repo is shared across accounts; nothing from the account it was extracted from may remain."""

    # Built piecewise so this file does not match its own search.
    FORBIDDEN = ["choreo" + "graph", "w" + "pp", "group" + "m", "nile" + "sh", "geo-" + "signals",
                 "68d9175" + "eb3", "PS" + "A-", "2six" + "ty", "ra" + "fa", "sat" + "ya", "kell" + "ish", "INFLY" + "GIM",
                 "mcp-" + "choreo", "firefly-mcp-"]

    def test_tracked_files_are_clean(self):
        files = [p for p in REPO.rglob("*") if p.is_file()
                 and not any(part in (".git", "data", "__pycache__") for part in p.parts)
                 and p.resolve() != pathlib.Path(__file__).resolve()
                 and p.name != ".mcp.json"
                 and p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif")]  # binary: random bytes can match
        self.assertTrue(files)
        hits = []
        for p in files:
            text = p.read_text(errors="ignore").lower()
            for bad in self.FORBIDDEN:
                if bad.lower() in text:
                    hits.append(f"{p.relative_to(REPO)}: {bad}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
