#!/usr/bin/env python3
"""Interactive setup for the Firefly weekly KPI scorecard.

  python3 setup.py                      # guided, prompts for everything
  python3 setup.py --answers a.json     # unattended (see README); no prompts
  python3 setup.py --skip-check         # do not test the API credentials

Writes, for ONE Firefly account:
  .mcp.json                 Firefly API credentials for Claude Code (mode 600, gitignored)
  data/config.json          account name/id, MCP server name, providers, schedule
  data/targets.json         your KPI targets
  data/caveats.md           standing measurement caveats shown in every report
  (optional) a launchd agent on macOS for the weekly run

Safe to re-run: existing values are offered as defaults and nothing is deleted.
Stdlib only.
"""

import argparse
import getpass
import json
import os
import pathlib
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "scripts"))
from build_report import DEFAULT_TARGETS, KPI_KEYS, _CATALOG  # noqa: E402

MCP_URL = "https://mcpv2.firefly.ai/mcp"
MCP_JSON = pathlib.Path(os.environ.get("KPI_MCP_JSON") or REPO / ".mcp.json")  # override is for tests
PROVIDERS = ["aws", "azurerm", "gcp", "k8s"]
# Counts that only make sense relative to the size of one estate: never defaulted.
SCALE_KEYS = ["unmanaged", "drifted", "ci_workspaces", "runner_workspaces"]
LABELS = {k: lbl for _, _, ms in _CATALOG for k, lbl, *_ in ms}

CAVEATS_TEMPLATE = """\
# Standing caveats

Every line that starts with "- " is printed under "Measurement caveats" in every
report. Use it for things a reader must know before quoting a number: known API
limits, a team that is excluded, an integration that is not connected yet.
Delete the example below.

- Example: Azure integration counts may be capped by the API; treat them as a floor.
"""


class Prompter:
    def __init__(self, answers):
        self.answers = answers          # None => interactive
        self.interactive = answers is None

    def ask(self, key, question, default=None, secret=False, required=False, check=None):
        while True:
            if not self.interactive:
                val = self.answers.get(key, default)
                val = "" if val is None else str(val)
            else:
                hint = f" [{default}]" if default not in (None, "") and not secret else ""
                text = f"{question}{hint}: "
                val = (getpass.getpass(text) if secret else input(text)).strip()
                if not val and default is not None:
                    val = str(default)
            if required and not val:
                if not self.interactive:
                    sys.exit(f"answers file is missing required value '{key}'")
                print("  A value is required.")
                continue
            if check and val:
                err = check(val)
                if err:
                    if not self.interactive:
                        sys.exit(f"invalid '{key}': {err}")
                    print(f"  {err}")
                    continue
            return val

    def yes(self, key, question, default=False):
        d = "Y/n" if default else "y/N"
        if not self.interactive:
            v = self.answers.get(key, default)
            return v if isinstance(v, bool) else str(v).lower() in ("y", "yes", "true", "1")
        v = input(f"{question} [{d}]: ").strip().lower()
        return default if not v else v.startswith("y")


def say(msg=""):
    print(msg)


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def check_credentials(access, secret):
    """POST an MCP 'initialize' to the Firefly endpoint. Returns (ok_or_None, message).

    None means 'could not tell' (network problem); only a clear 401/403 is a failure.
    """
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "firefly-kpi-setup", "version": "1"}},
    }).encode()
    req = urllib.request.Request(MCP_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-access-Key": access, "X-secret-Key": secret,
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, f"HTTP {e.code}: the keys were rejected"
        return None, f"HTTP {e.code} (could not confirm the keys either way)"
    except Exception as e:  # noqa: BLE001 - any network failure is 'could not tell'
        return None, f"no answer ({e.__class__.__name__}: {e})"


def write_mcp_json(server, access, secret):
    path = MCP_JSON
    data = load_json(path, {})
    data.setdefault("mcpServers", {})[server] = {
        "type": "http", "url": MCP_URL,
        "headers": {"X-access-Key": access, "X-secret-Key": secret},
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(path, 0o600)


def configure_targets(p, existing, runner=True):
    say("\nTargets drive the green / amber / red status on each tile. A wrong target prints")
    say("misleading status, so the choice matters more than it looks.")
    say("  1) Firefly suggested defaults for ratios and scores (recommended)")
    say("  2) No targets: show the numbers and trends only")
    say("  3) Walk through every KPI and set each one")
    mode = p.ask("targets_mode", "Choose 1, 2 or 3", "1", check=lambda v: None if v in "123" and len(v) == 1 else "Enter 1, 2 or 3")
    skip = set() if runner else {"runner_workspaces"}   # hidden KPI: no target, no prompt
    targets = {} if mode == "2" else dict(DEFAULT_TARGETS)
    for k, v in existing.items():          # a re-run keeps what was set before
        if k in KPI_KEYS and mode != "2":
            targets[k] = v
    if mode == "3" and p.interactive:
        say("For each KPI press Enter to keep the value shown, type a number to change it, or 'none' to clear.")
        for k in (x for x in KPI_KEYS if x not in skip):
            cur = targets.get(k)
            raw = input(f"  {LABELS[k]} ({k}) [{'none' if cur is None else cur}]: ").strip().lower()
            if raw == "none":
                targets.pop(k, None)
            elif raw:
                try:
                    targets[k] = float(raw) if "." in raw else int(raw)
                except ValueError:
                    say("    not a number, keeping previous value")
    elif mode != "2":
        if p.yes("scale_targets", "\nSet targets for estate-size-dependent counts (unmanaged, drifted, CI and runner workspaces)?", False):
            for k in (x for x in SCALE_KEYS if x not in skip):
                raw = p.ask(f"target_{k}", f"  Target for {LABELS[k]} (blank = none)", targets.get(k))
                if raw:
                    try:
                        targets[k] = float(raw) if "." in raw else int(raw)
                    except ValueError:
                        say("    not a number, skipped")
    if not p.interactive:
        for k in KPI_KEYS:               # unattended: explicit target_<key> answers win
            if f"target_{k}" in p.answers:
                v = p.answers[f"target_{k}"]
                if v is None:
                    targets.pop(k, None)
                else:
                    targets[k] = v
    return {k: targets[k] for k in KPI_KEYS if k not in skip and targets.get(k) is not None}


def install_schedule(cfg, claude_bin, load):
    """Write (and optionally load) a launchd agent. macOS only."""
    slug = re.sub(r"[^a-z0-9]+", "-", cfg["account_name"].lower()).strip("-") or "account"
    label = f"ai.firefly.kpi.{slug}"
    home = pathlib.Path(os.environ.get("KPI_HOME") or REPO / "data").resolve()
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path_env = ":".join(filter(None, [
        str(pathlib.Path(claude_bin).parent) if claude_bin else "",
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
    plist = {
        "Label": label,
        "ProgramArguments": ["/bin/bash", str(REPO / "scripts" / "weekly-run.sh")],
        "WorkingDirectory": str(REPO),
        "StartCalendarInterval": {"Weekday": cfg["schedule"]["weekday"],
                                  "Hour": cfg["schedule"]["hour"],
                                  "Minute": cfg["schedule"]["minute"]},
        "EnvironmentVariables": {"PATH": path_env, "KPI_HOME": str(home)},
        "StandardOutPath": str(logs / "launchd.out.log"),
        "StandardErrorPath": str(logs / "launchd.err.log"),
    }
    dest = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        plistlib.dump(plist, fh)
    say(f"  wrote {dest}")
    if load:
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)], capture_output=True, text=True)
        if r.returncode == 0:
            say(f"  loaded {label}")
        else:
            say(f"  could not load it ({r.stderr.strip()}). Load it yourself:")
            say(f"    launchctl bootstrap gui/{uid} {dest}")
    else:
        say(f"  not loaded. To enable:  launchctl bootstrap gui/$(id -u) {dest}")
    return label


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answers", help="JSON file of answers; runs without prompting")
    ap.add_argument("--skip-check", action="store_true", help="do not test the API credentials")
    ap.add_argument("--no-load", action="store_true", help="write the launchd plist but do not load it")
    args = ap.parse_args()

    answers = json.loads(pathlib.Path(args.answers).read_text()) if args.answers else None
    p = Prompter(answers)
    home = pathlib.Path(os.environ.get("KPI_HOME") or REPO / "data").expanduser().resolve()
    old = load_json(home / "config.json", {})
    old_targets = load_json(home / "targets.json", {})

    say("Firefly weekly KPI scorecard: setup")
    say(f"Config and data will live in {home}\n")

    # -- account -----------------------------------------------------------
    name = p.ask("account_name", "Account name (shown on the report)", old.get("account_name"), required=True)
    # Only needed by someone running this against several Firefly accounts (an SE, a consultant):
    # it stops snapshots from one account being mixed into another's history.
    acct = ""
    multi = p.yes("multi_account", "Do you run this against more than one Firefly account (e.g. as an SE or consultant)?",
                  bool(old.get("account_id")) or (not p.interactive and "account_id" in p.answers))
    if multi:
        acct = p.ask("account_id", "Firefly account ID (Integrations > Connected integrations, under the page title)",
                     old.get("account_id"), required=True,
                     check=lambda v: "No spaces allowed" if re.search(r"\s", v) else None)
    if acct and not re.fullmatch(r"[0-9a-f]{24}", acct):
        say("  note: Firefly account IDs are normally 24 hex characters. Double-check this one.")

    # -- MCP server + credentials -----------------------------------------
    server = p.ask("mcp_server", "Name for the Firefly MCP server in Claude Code", old.get("mcp_server", "firefly"),
                   check=lambda v: None if re.fullmatch(r"[A-Za-z0-9_-]+", v) else "Use letters, digits, - and _ only")
    mcp = load_json(MCP_JSON, {})
    have_creds = server in (mcp.get("mcpServers") or {})
    if have_creds and not p.yes("replace_credentials", f"\n.mcp.json already has credentials for '{server}'. Replace them?", False):
        say("  keeping existing credentials")
    else:
        say("\nCreate an API key pair in the Firefly web console (Settings > Users > Create Key Pair).")
        say("The secret key is shown only once, so copy both now.")
        access = p.ask("access_key", "Access key", os.environ.get("FIREFLY_ACCESS_KEY"), required=True)
        secret = p.ask("secret_key", "Secret key (input hidden)", os.environ.get("FIREFLY_SECRET_KEY"),
                       secret=True, required=True)
        write_mcp_json(server, access, secret)
        say("  wrote .mcp.json (mode 600, gitignored)")
        if not args.skip_check:
            ok, msg = check_credentials(access, secret)
            if ok:
                say(f"  credential check: ok ({msg})")
            elif ok is False:
                say(f"  credential check FAILED: {msg}. Fix the keys and re-run setup.")
            else:
                say(f"  credential check inconclusive: {msg}")

    # -- scope ---------------------------------------------------------------
    say("\nWhich cloud providers does this account have integrations for?")
    provs = [x for x in PROVIDERS if p.yes(f"provider_{x}", f"  {x}?", x in (old.get("providers") or PROVIDERS))]
    runner = p.yes("runner_enabled", "\nDoes this account use Firefly Runners (Firefly-run IaC workspaces)?",
                   old.get("runner_enabled", True))
    hidden = [] if runner else ["runner_workspaces"]

    targets = configure_targets(p, old_targets, runner)

    # -- delivery ------------------------------------------------------------
    say()
    copy_to = p.ask("copy_to", "Folder to also copy each finished scorecard into (blank = none)", old.get("copy_to") or "")
    if copy_to and not pathlib.Path(copy_to).expanduser().is_dir():
        say(f"  note: {copy_to} does not exist yet; the copy step will log a warning until it does.")

    claude_bin = shutil.which("claude") or str(pathlib.Path.home() / ".local/bin/claude")
    if not pathlib.Path(claude_bin).exists():
        say("\n  WARNING: Claude Code ('claude') was not found. Weekly collection needs it. See README > Requirements.")

    sched = old.get("schedule") or {"enabled": False, "weekday": 1, "hour": 7, "minute": 0}
    mac = sys.platform == "darwin"
    if mac and p.yes("schedule", "\nRun the collection automatically every week (macOS launchd)?", sched.get("enabled", False)):
        sched["enabled"] = True
        sched["weekday"] = int(p.ask("weekday", "  Day of week (1=Mon ... 7=Sun)", sched.get("weekday", 1),
                                     check=lambda v: None if v.isdigit() and 1 <= int(v) <= 7 else "Enter 1-7"))
        sched["hour"] = int(p.ask("hour", "  Hour, 24h local time", sched.get("hour", 7),
                                  check=lambda v: None if v.isdigit() and 0 <= int(v) <= 23 else "Enter 0-23"))
        sched["minute"] = 0
        risky = [s for s in ("CloudStorage", "/Documents/", "/Desktop/", "/Downloads/") if s in str(REPO)]
        if risky:
            say(f"  WARNING: this folder is under a macOS-protected location ({risky[0]}). launchd jobs are")
            say("  blocked from reading it and will fail silently. Clone the repo somewhere like ~/code and")
            say("  re-run setup there before scheduling.")
    else:
        sched["enabled"] = False

    # -- write ---------------------------------------------------------------
    cfg = {
        "account_name": name, "account_id": acct, "mcp_server": server,
        "providers": provs, "runner_enabled": runner, "hidden_metrics": hidden,
        "claude_bin": claude_bin, "copy_to": copy_to or None, "schedule": sched,
    }
    for d in ("snapshots", "reports", "logs"):
        (home / d).mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    (home / "targets.json").write_text(json.dumps(targets, indent=2) + "\n")
    if not (home / "caveats.md").exists():
        (home / "caveats.md").write_text(CAVEATS_TEMPLATE)
    say(f"\nWrote {home}/config.json, targets.json, caveats.md")

    if sched["enabled"]:
        say("\nSchedule:")
        install_schedule(cfg, claude_bin, load=not args.no_load and (p.interactive or p.yes("load_schedule", "", True)))
    elif not mac:
        say("\nSchedule (not macOS): add this line with `crontab -e` to run every Monday 07:00:")
        say(f"  0 7 * * 1 KPI_HOME='{home}' /bin/bash '{REPO}/scripts/weekly-run.sh'")

    say("\nNext steps")
    say("  1. cd into this folder and run:  claude")
    say("     (approve the project's Firefly MCP server the first time Claude asks)")
    say("  2. Inside Claude, run:           /firefly-weekly-kpi")
    say("  3. Open data/reports/firefly-kpi-scorecard.html")
    say("  Re-run `python3 setup.py` any time to change settings.")


if __name__ == "__main__":
    main()
