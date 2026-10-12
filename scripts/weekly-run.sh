#!/bin/bash
# Weekly Firefly KPI collection, run unattended (launchd / cron) or by hand.
#
# Starts Claude Code headlessly from the repo root, so the project's .mcp.json
# (Firefly credentials) and .claude/skills/firefly-weekly-kpi are both found, runs
# the skill, then rebuilds every report from whatever snapshots are on disk.
#
# Everything reads the KPI home: $KPI_HOME, default <repo>/data.
# Tunables (testing): NET_ATTEMPTS, NET_WAIT, NET_URL, SKIP_COLLECT, CLAUDE

set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KPI_HOME="${KPI_HOME:-$REPO/data}"
export KPI_HOME
cd "$REPO" || exit 1

cfg() {  # cfg <key> -> value from config.json, empty if unset
  python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2]); print("" if v is None else v)' \
    "$KPI_HOME/config.json" "$1" 2>/dev/null
}

[ -f "$KPI_HOME/config.json" ] || { echo "No $KPI_HOME/config.json - run: python3 setup.py" >&2; exit 1; }

SERVER="$(cfg mcp_server)"; SERVER="${SERVER:-firefly}"
CLAUDE="${CLAUDE:-$(cfg claude_bin)}"
[ -n "$CLAUDE" ] && [ -x "$CLAUDE" ] || CLAUDE="$(command -v claude || true)"
mkdir -p "$KPI_HOME/logs" "$KPI_HOME/snapshots" "$KPI_HOME/reports"

WEEK=$(python3 -c "import datetime as d;y,w,_=d.date.today().isocalendar();print(f'{y}-W{w:02d}')")
LOG="$KPI_HOME/logs/$WEEK.log"
SNAP="$KPI_HOME/snapshots/$WEEK.json"
HTML="$KPI_HOME/reports/firefly-kpi-scorecard.html"
STARTED=$(date +%s)

NET_ATTEMPTS=${NET_ATTEMPTS:-10}
NET_WAIT=${NET_WAIT:-180}
NET_URL=${NET_URL:-https://mcpv2.firefly.ai/mcp}

mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0; }  # GNU first: BSD rejects -c, but GNU accepts -f

notify() {  # notify <subtitle> <message>; must never decide the exit code
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"$2\" with title \"Firefly KPI $WEEK\" subtitle \"$1\"" 2>/dev/null || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send "Firefly KPI $WEEK - $1" "$2" 2>/dev/null || true
  fi
}

{
  echo "=== Firefly weekly KPI run - $WEEK - $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

  # A machine that wakes from sleep may not have a network yet. Wait for the API.
  online=0
  for attempt in $(seq 1 "$NET_ATTEMPTS"); do
    if curl -s -o /dev/null -m 15 "$NET_URL"; then
      online=1; echo "network reachable on attempt $attempt"; break
    fi
    echo "no network (attempt $attempt/$NET_ATTEMPTS) - retrying in ${NET_WAIT}s"
    sleep "$NET_WAIT"
  done

  if [ "$online" -eq 1 ]; then
    if [ "${SKIP_COLLECT:-0}" = "1" ]; then
      echo "SKIP_COLLECT=1 - not invoking claude (test mode)"
    elif [ -z "$CLAUDE" ]; then
      echo "ERROR: Claude Code ('claude') not found. Install it or set CLAUDE=/path/to/claude."
    else
      "$CLAUDE" -p "/firefly-weekly-kpi" \
        --allowedTools "Bash,Read,Write,Edit,Glob,Grep,mcp__$SERVER" \
        --permission-mode acceptEdits 2>&1
      echo
      echo "--- claude exited $? ---"
    fi

    # Rebuild every output from whatever snapshots exist, so a partial run still
    # refreshes the deliverables.
    python3 "$REPO/scripts/validate_snapshot.py" 2>&1 || echo "WARN: snapshot validation reported errors"
    python3 "$REPO/scripts/build_report.py" 2>&1
    python3 "$REPO/scripts/export_metrics.py" 2>&1
  else
    echo "ABORT: no network after $NET_ATTEMPTS attempts - no collection attempted"
  fi

  echo "=== finished $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
} >>"$LOG" 2>&1

# Success means "this week's snapshot was written by THIS run", not "the log looks fine":
# the report scripts print a coverage line from old snapshots even when collection failed.
if [ -f "$SNAP" ] && [ "$(mtime "$SNAP")" -ge "$STARTED" ]; then
  if [ -f "$HTML" ] && [ "$(mtime "$HTML")" -ge "$STARTED" ]; then
    "$REPO/scripts/copy-scorecard.sh" >>"$LOG" 2>&1 || echo "WARN: scorecard not copied" >>"$LOG"
  else
    echo "WARN: scorecard not rebuilt by this run - not copied" >>"$LOG"
  fi
  COVERAGE=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["inventory"]; m=d["codified"]+d["drifted"]; print("coverage %.2f%%" % (100*m/(m+d["unmanaged"])))' "$SNAP" 2>/dev/null)
  notify "Snapshot collected" "${COVERAGE:-snapshot written}"
  exit 0
else
  notify "Needs attention" "No $WEEK snapshot written - see $LOG"
  exit 1
fi
