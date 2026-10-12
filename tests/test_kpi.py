import json
import os
import pathlib
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
        for w in ("2026-W01", "2026-W02"):
            p = self.home / "snapshots" / f"{w}.json"
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
                 and p.name != ".mcp.json"]
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
