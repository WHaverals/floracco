#!/usr/bin/env bash
# The one backup command.
#
# Snapshots the live site's human work on the server (deploy/backup_remote.sh), downloads
# it into a dated folder, then verifies it (workflows/backup_verify.py): every checksum, a
# replay of the log onto a FRESH seed, and a row-for-row comparison of that rebuild with
# the live database. Writes manifest.json + README.txt into the folder and one line into
# BACKUP_LOG.md at the backup root. A folder whose human work equals the previous backup's
# is dropped again (the log line records the heartbeat), so a daily schedule leaves one
# folder per distinct state.
#
#   deploy/backup.sh            # daily use
#   deploy/backup.sh --force    # keep the folder even if nothing changed
#
# Environment (all optional):
#   FLORACCO_BACKUP_ROOT         where dated folders go. Default <repo>/data/backups (gitignored).
#                                Point it at a Google-Drive-synced folder for off-site copies.
#   FLORACCO_RENDER_SSH          user@host of the Render service's SSH endpoint
#   FLORACCO_BACKUP_KEEP_SERVER  snapshots to keep on the server disk (default 5)
#   FLORACCO_PYTHON              python with the repo's deps (default <repo>/.floracco/bin/python)
#
# Schedule it: deploy/install_backup_schedule.sh (launchd, daily). Runbook: deploy/README.md.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${FLORACCO_BACKUP_ROOT:-$REPO/data/backups}"
SSH_TARGET="${FLORACCO_RENDER_SSH:-srv-d8o0vtvlk1mc73a7upng@ssh.ohio.render.com}"
KEEP_SERVER="${FLORACCO_BACKUP_KEEP_SERVER:-5}"
PY="${FLORACCO_PYTHON:-$REPO/.floracco/bin/python}"
FORCE=""
[ "${1:-}" = "--force" ] && FORCE="--force"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ROOT"
echo "[$stamp] backup: server snapshot → $ROOT/$stamp"

# Render's SSH proxy prints a cosmetic "bad signature … incorrectsignature" line on every
# connection (deploy/README.md gotchas). Hide exactly that; everything else stays visible.
quiet() { grep -v -i -E 'bad signature|incorrectsignature' >&2 || true; }

ssh -o BatchMode=yes -o ConnectTimeout=30 "$SSH_TARGET" bash -s -- "$stamp" "$KEEP_SERVER" \
    < "$REPO/deploy/backup_remote.sh" 2> >(quiet)

rsync -az --exclude '*-wal' --exclude '*-shm' \
    "$SSH_TARGET:/data/backups/$stamp/" "$ROOT/$stamp/" 2> >(quiet)

cd "$REPO"
exec "$PY" -m workflows.backup_verify "$ROOT/$stamp" --root "$ROOT" $FORCE
