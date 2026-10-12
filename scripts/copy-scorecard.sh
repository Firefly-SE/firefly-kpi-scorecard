#!/bin/bash
# Copy the built scorecard into the folder named by "copy_to" in config.json, stamped
# with the HTML's own build time (YYYYMMDD-HHMM-). Never overwrites an existing file.
# Does nothing (exit 0) when copy_to is not set. Safe to run by hand.
#
# Tunables: KPI_HOME (default <repo>/data), DEST_DIR (overrides config.json copy_to).

set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KPI_HOME="${KPI_HOME:-$REPO/data}"
SRC="$KPI_HOME/reports/firefly-kpi-scorecard.html"

DEST_DIR="${DEST_DIR:-$(python3 -c 'import json,os,sys; v=json.load(open(sys.argv[1])).get("copy_to"); print(os.path.expanduser(v) if v else "")' "$KPI_HOME/config.json" 2>/dev/null)}"

[ -n "$DEST_DIR" ] || { echo "copy-scorecard: no copy_to configured, skipping"; exit 0; }
[ -f "$SRC" ]      || { echo "copy-scorecard: source missing: $SRC"; exit 1; }
[ -d "$DEST_DIR" ] || { echo "copy-scorecard: destination folder missing: $DEST_DIR"; exit 1; }

# Stamp with the file's own modification time, not "now", so an old build can't look fresh.
EPOCH=$(stat -c %Y "$SRC" 2>/dev/null || stat -f %m "$SRC")
STAMP=$(date -r "$EPOCH" +%Y%m%d-%H%M 2>/dev/null || date -d "@$EPOCH" +%Y%m%d-%H%M)
DEST="$DEST_DIR/${STAMP}-firefly-kpi-scorecard.html"

[ -e "$DEST" ] && { echo "copy-scorecard: already exists, not overwriting: $DEST"; exit 0; }
cp "$SRC" "$DEST" && echo "copy-scorecard: wrote $DEST"
