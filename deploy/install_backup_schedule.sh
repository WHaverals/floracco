#!/usr/bin/env bash
# Install (or update) the daily launchd job that runs deploy/backup.sh on this Mac.
#
#   deploy/install_backup_schedule.sh                       # 07:30 local, folders under <repo>/data/backups
#   FLORACCO_BACKUP_ROOT="$HOME/Library/CloudStorage/GoogleDrive-you@example.org/My Drive/FlorAcco backups" \
#       deploy/install_backup_schedule.sh                   # folders land straight in Google Drive
#   FLORACCO_BACKUP_HOUR=22 FLORACCO_BACKUP_MINUTE=0 deploy/install_backup_schedule.sh
#   deploy/install_backup_schedule.sh --uninstall
#
# launchd user agents run while you are logged in; a run missed because the Mac was asleep
# or off happens at the next wake / login. The job's output goes to <root>/backup.log and
# every run leaves a line in <root>/BACKUP_LOG.md.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="org.accomandite.floracco-backup"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ROOT="${FLORACCO_BACKUP_ROOT:-$REPO/data/backups}"
HOUR="${FLORACCO_BACKUP_HOUR:-7}"
MINUTE="${FLORACCO_BACKUP_MINUTE:-30}"
uid="$(id -u)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$uid/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

mkdir -p "$ROOT" "$HOME/Library/LaunchAgents"
sed -e "s|__REPO__|$REPO|g" -e "s|__ROOT__|$ROOT|g" -e "s|__HOME__|$HOME|g" \
    -e "s|__HOUR__|$HOUR|g" -e "s|__MINUTE__|$MINUTE|g" \
    "$REPO/deploy/launchd/$LABEL.plist.template" > "$PLIST"

launchctl bootout "gui/$uid/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$uid" "$PLIST"

printf 'installed %s: daily at %02d:%02d local time\n' "$LABEL" "$HOUR" "$MINUTE"
echo "  backups:  $ROOT"
echo "  job log:  $ROOT/backup.log   (per-run lines: $ROOT/BACKUP_LOG.md)"
echo "  run now:  launchctl kickstart -k gui/$uid/$LABEL"
echo "  status:   launchctl print gui/$uid/$LABEL | grep -E 'state|last exit'"
