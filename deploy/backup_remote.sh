#!/usr/bin/env bash
# Runs ON THE SERVER, streamed over SSH by deploy/backup.sh:
#     ssh <render-service> bash -s -- <STAMP> <KEEP>  < deploy/backup_remote.sh
#
# Snapshots every file that holds human work, plus the seed, into /data/backups/<STAMP>/:
#   corrections.db  main.db  place_cache.db     via SQLite's online-backup API (safe on a live db)
#   projects_at.sql                             plain copy (the seed never changes on the server)
#   the review-UI stores (*.jsonl) and review_decisions.csv
# The copies are taken while holding the platform's own cross-process write lock, so no
# reviewer write can land between them (the server answers 503 "maintenance in progress" for
# the second or two this takes). The lockfile is opened, never unlinked (multi_user_safety §9).
#
# Writes ONLY under /data/backups. Never modifies anything under /data/working.
# Keeps the newest <KEEP> stamp-named snapshots on the server disk; the maintainer's
# downloaded copies are the real archive (deploy/README.md, "Backups").
set -euo pipefail
STAMP="${1:?stamp (YYYYMMDDTHHMMSSZ)}"
KEEP="${2:-5}"
W="${FLORACCO_DATA_DIR:-/data/working}"
B="${FLORACCO_BACKUP_DIR:-/data/backups}"
dest="$B/$STAMP"
mkdir -p "$dest"

python3 - "$W" "$dest" "$STAMP" <<'PY'
import fcntl, json, os, shutil, sqlite3, sys, time
from datetime import datetime, timezone

W, dest, stamp = sys.argv[1:4]

# Hold the write lock for the whole snapshot. Bounded non-blocking retry, like the
# server itself; if a maintenance task holds it for longer we still snapshot, and say so.
fd = os.open(os.path.join(W, ".write.lock"), os.O_RDWR | os.O_CREAT, 0o644)
locked = False
for _ in range(20):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        break
    except BlockingIOError:
        time.sleep(0.25)

copied = []
try:
    for name in ("corrections.db", "main.db", "place_cache.db"):
        src = os.path.join(W, "sqlite", name)
        if not os.path.exists(src):
            continue
        source = sqlite3.connect(src, timeout=15)
        target = sqlite3.connect(os.path.join(dest, name))
        with target:
            source.backup(target)
        source.close()
        target.close()
        copied.append(name)
    for rel in (
        "sqlite/projects_at.sql",
        "derived/word-pipeline/08_review_decisions/review_decisions.csv",
        "derived/word-pipeline/10_corrections/corrections_proposals.jsonl",
        "derived/word-pipeline/10_corrections/corrections_events.jsonl",
        "derived/word-pipeline/10_corrections/flag_dismissals.jsonl",
        "derived/word-pipeline/10_corrections/correction_candidate_dismissals.jsonl",
        "derived/word-pipeline/10_corrections/find_questions.jsonl",
    ):
        path = os.path.join(W, rel)
        if os.path.exists(path):
            shutil.copy2(path, dest)
            copied.append(os.path.basename(path))
finally:
    os.close(fd)  # the kernel releases the flock with the descriptor

# A freshly closed backup target of a WAL database leaves an empty -wal and a -shm beside it.
for name in copied:
    for suffix in ("-wal", "-shm"):
        side = os.path.join(dest, name + suffix)
        if os.path.exists(side) and (suffix == "-shm" or os.path.getsize(side) == 0):
            os.remove(side)

json.dump(
    {
        "stamp": stamp,
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": W,
        "hostname": os.uname().nodename,
        "locked_snapshot": locked,
        "files": copied,
    },
    open(os.path.join(dest, "snapshot.json"), "w"),
    indent=1,
)
PY

( cd "$dest" && sha256sum $(ls | grep -v '^SHA256SUMS$') > SHA256SUMS )

# Prune: keep only the newest $KEEP stamp-named snapshot folders on the server disk.
ls -1d "$B"/20??????T??????Z 2>/dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf

echo "server snapshot $dest ($(du -sh "$dest" | cut -f1)); locked=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['locked_snapshot'])" "$dest/snapshot.json")"
