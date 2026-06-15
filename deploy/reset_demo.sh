#!/usr/bin/env bash
# Resettable demo: restore the live WORKING data tree from the PRISTINE snapshot.
#
# Pilot users create / edit / hide / add records freely against the working
# copy; this wipes it back to a clean slate so a demo never drifts or
# accumulates noise in the real op-log. Run nightly (cron) or on demand.
# DESTRUCTIVE to the working copy by design — it backs up the op-log first.
#
#   FLORACCO_DATA_DIR      working data root, restored in place (required)
#   FLORACCO_PRISTINE_DIR  read-only golden snapshot to restore from (required)
set -euo pipefail

WORKING="${FLORACCO_DATA_DIR:?set FLORACCO_DATA_DIR}"
PRISTINE="${FLORACCO_PRISTINE_DIR:?set FLORACCO_PRISTINE_DIR}"
here="$(cd "$(dirname "$0")" && pwd)"

[ -d "$PRISTINE" ] || { echo "pristine dir missing: $PRISTINE" >&2; exit 1; }

# Don't silently throw away a demo session — snapshot the op-log first.
"$here/backup_corrections.sh" || echo "warning: pre-reset backup failed" >&2

rsync -a --delete "$PRISTINE"/ "$WORKING"/
echo "reset working data ($WORKING) from pristine ($PRISTINE)"
