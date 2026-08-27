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

# Hold the cross-process write lock for the whole restore so no live server
# write can interleave with the rsync. NON-blocking: if a server holds it we
# refuse (stop the service first). The lockfile itself is EXCLUDED from the
# rsync and never deleted — flock identity lives in the inode; deleting and
# recreating it would hand out a second "exclusive" lock (multi_user_safety §9).
python3 - "$WORKING" "$PRISTINE" <<'PY'
import fcntl, os, subprocess, sys
working, pristine = sys.argv[1], sys.argv[2]
lock = os.path.join(working, ".write.lock")
fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("write lock is held (a live server?) — stop the service, then rerun")
subprocess.run(
    ["rsync", "-a", "--delete", "--exclude", ".write.lock", pristine + "/", working + "/"],
    check=True,
)
os.close(fd)
PY
echo "reset working data ($WORKING) from pristine ($PRISTINE)"
echo "NOTE: restart the Render service now — the server caches derived files in memory."
