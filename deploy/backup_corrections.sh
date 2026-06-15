#!/usr/bin/env bash
# Timestamped backup of the irreplaceable human-change log (corrections.db) and
# the review-decision CSV, taken from the live working data dir.
#
# Why: everything else on the box is regenerable (seed from IAS, code from git,
# derived files from the pipeline). corrections.db is the ONLY authoritative
# record of human changes; Render's daily disk snapshots are a floor, this is
# the real copy. Cron it (e.g. hourly) and sync the output off-box.
#
#   FLORACCO_DATA_DIR    working data root (required)
#   FLORACCO_BACKUP_DIR  where to write backups (default: $FLORACCO_DATA_DIR/../backups)
#   FLORACCO_BACKUP_KEEP how many to retain (default: 48)
set -euo pipefail

DATA_DIR="${FLORACCO_DATA_DIR:?set FLORACCO_DATA_DIR}"
BACKUP_DIR="${FLORACCO_BACKUP_DIR:-$DATA_DIR/../backups}"
KEEP="${FLORACCO_BACKUP_KEEP:-48}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="$BACKUP_DIR/$stamp"
mkdir -p "$dest"

# Use SQLite's online backup API (safe on a live WAL database) via Python — no
# sqlite3 CLI dependency on the host.
python3 - "$DATA_DIR/sqlite/corrections.db" "$dest/corrections.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
source = sqlite3.connect(src)
target = sqlite3.connect(dst)
with target:
    source.backup(target)
source.close()
target.close()
PY

decisions="$DATA_DIR/derived/word-pipeline/08_review_decisions/review_decisions.csv"
[ -f "$decisions" ] && cp -f "$decisions" "$dest/" || true

echo "backed up corrections.db + decisions to $dest"

# Retain only the newest $KEEP backups.
ls -1dt "$BACKUP_DIR"/*/ 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -rf
