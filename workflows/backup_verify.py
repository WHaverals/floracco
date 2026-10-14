"""Verify a downloaded backup folder and write its manifest (step 3 of deploy/backup.sh).

A backup folder is the complete, self-contained record of the live site's human work:

    corrections.db     the authoritative change log: field edits, hide/restore, created rows,
                       term links, People decisions                      (irreplaceable)
    projects_at.sql    the frozen seed                                   (needed to rebuild)
    main.db            the working database as it was on the server      (convenience copy)
    *.jsonl, *.csv     the review-UI stores: proposal statuses and rationales, dismissed
                       flags, Reconcile decisions                        (human work)
    place_cache.db     machine place resolutions + coordinates           (regenerable, but paid for)

This module proves the folder is usable, not just present:

    1. every file matches the SHA256SUMS the server computed right after the snapshot;
    2. the log replays onto a FRESH seed, built from the folder's own projects_at.sql,
       with zero conflicts (the rebuild runs in a temporary directory, never in data/);
    3. that rebuild is row-for-row identical to the live main.db, every table. Any
       difference is an edit that reached the database without reaching the log, which the
       architecture forbids (docs/workflows/db_corrections_design.md), or a stale build.

The outcome goes into manifest.json, README.txt (what each file is, how to rebuild or
restore from this folder alone) and one line of BACKUP_LOG.md at the backup root.

A folder whose human-work fingerprint and equivalence result equal the previous backup's
is dropped again, the log line records the heartbeat, unless --force. So a daily schedule
leaves one folder per distinct state of the human work.

    python -m workflows.backup_verify data/backups/20261011T230104Z [--root data/backups] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")

# Without these three a folder cannot rebuild the working database from scratch.
REQUIRED = ("corrections.db", "projects_at.sql", "main.db")

# The files that ARE human work. Their checksums form the fingerprint that decides whether
# a new folder is kept; main.db (derived), the seed (static) and place_cache.db (machine
# output) are deliberately left out of it.
HUMAN_WORK = (
    "corrections.db",
    "corrections_proposals.jsonl",
    "corrections_events.jsonl",
    "flag_dismissals.jsonl",
    "correction_candidate_dismissals.jsonl",
    "review_decisions.csv",
)

FILE_ROLES = {
    "corrections.db": "THE authoritative human-change log: field edits, hide/restore, created rows, "
                      "term links, People decisions. Irreplaceable.",
    "projects_at.sql": "The frozen seed (IAS MySQL export). With corrections.db this rebuilds the "
                       "working database.",
    "main.db": "The working database as it was on the server (= seed + replay of the log + live "
               "edits). Convenience copy; rebuildable from the two files above.",
    "corrections_proposals.jsonl": "Review-UI store: one proposal per inline edit, with status and "
                                   "rationale. Human work; not rebuildable from the log.",
    "corrections_events.jsonl": "Review-UI store: proposal lifecycle events "
                                "(created / approved / applied / reverted).",
    "flag_dismissals.jsonl": "Review-UI store: 'Needs review' flags a reviewer dismissed as not an "
                             "error, with reasons.",
    "correction_candidate_dismissals.jsonl": "Review-UI store: dismissed Word-vs-DB correction "
                                             "candidates.",
    "review_decisions.csv": "Reconcile decisions (Word entry to database row links).",
    "place_cache.db": "Machine output: LLM place resolutions, coordinates, duplicate suggestions. "
                      "Regenerable, but it cost ~733 LLM calls, so it travels along.",
    "SHA256SUMS": "Checksums computed on the server right after the snapshot.",
    "snapshot.json": "When and where the snapshot was taken, and whether the write lock was held.",
    "manifest.json": "This verification's result: counts, replay outcome, rebuild-equivalence, status.",
    "README.txt": "This file.",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_sums(folder: Path) -> tuple[list[str], list[str]]:
    """Compare every file listed in SHA256SUMS. Returns (verified, mismatched-or-missing)."""
    listing = folder / "SHA256SUMS"
    if not listing.exists():
        return [], ["SHA256SUMS"]
    verified: list[str] = []
    bad: list[str] = []
    for line in listing.read_text().splitlines():
        if not line.strip():
            continue
        expected, name = line.split(None, 1)
        name = name.strip()
        target = folder / name
        if target.exists() and sha256(target) == expected:
            verified.append(name)
        else:
            bad.append(name)
    return verified, bad


def fingerprint(folder: Path) -> str:
    """One digest over the human-work files present (name + content), nothing derived."""
    digest = hashlib.sha256()
    for name in HUMAN_WORK:
        path = folder / name
        if path.exists():
            digest.update(name.encode())
            digest.update(sha256(path).encode())
    return digest.hexdigest()


def log_counts(path: Path) -> dict[str, Any]:
    """What the log holds, by author, so the manifest reads without opening the database."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def tally(sql: str) -> dict[str, int]:
        counts = Counter(conn.execute(sql))
        return {" / ".join(str(part) for part in key): n for key, n in sorted(counts.items(), key=lambda kv: str(kv[0]))}

    def scalar(sql: str) -> Any:
        return conn.execute(sql).fetchone()[0]

    try:
        latest = [
            scalar("select max(created_at) from change_request"),
            scalar("select max(created_at) from reference_link"),
            scalar("select max(created_at) from person_review_event"),
        ]
        return {
            "change_request": scalar("select count(*) from change_request"),
            "change_request_by_author_status": tally("select created_by, status from change_request"),
            "change_event": scalar("select count(*) from change_event"),
            "reference_link_active": scalar("select count(*) from reference_link where status='active'"),
            "reference_link_by_kind_status_author": tally("select kind, status, created_by from reference_link"),
            "person_review_event": scalar("select count(*) from person_review_event"),
            "person_review_event_by_action_author": tally("select action, created_by from person_review_event"),
            "latest_activity_utc": max((t for t in latest if t), default=None),
        }
    finally:
        conn.close()


def rebuild_from(folder: Path, workdir: Path) -> dict[str, Any]:
    """Build seed + replay(log) in `workdir` from the folder's own files; return build()'s summary.

    Runs in a subprocess with the data root pointed at `workdir`, so nothing under the repo's
    data/ is read or written. The log is copied first because replay appends conflict events."""
    sqlite_dir = workdir / "sqlite"
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(folder / "corrections.db", sqlite_dir / "corrections.db")
    env = {
        **os.environ,
        "FLORACCO_DATA_DIR": str(workdir),
        "FLORACCO_DB_PATH": str(sqlite_dir / "main.db"),
        "FLORACCO_DB_DUMP_PATH": str(folder / "projects_at.sql"),
    }
    env.pop("FLORACCO_CORRECTIONS_DB_PATH", None)
    code = "import json; from workflows import db_import; print(json.dumps(db_import.build(backup=False)))"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _short(value: Any) -> Any:
    """Keep manifest samples readable: long narrative cells are cut at 80 characters."""
    return value[:80] + "…" if isinstance(value, str) and len(value) > 80 else value


def diff_databases(rebuilt: Path, live: Path) -> dict[str, Any]:
    """Row-for-row comparison of two SQLite files, every table, with named samples."""
    a = sqlite3.connect(f"file:{rebuilt}?mode=ro", uri=True)
    b = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        names = "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
        ta = [r[0] for r in a.execute(names)]
        tb = [r[0] for r in b.execute(names)]
        out: dict[str, Any] = {
            "tables_compared": 0,
            "differences": {},
            "only_in_rebuild": [t for t in ta if t not in tb],
            "only_in_live": [t for t in tb if t not in ta],
        }
        for table in ta:
            if table not in tb:
                continue
            info_a = list(a.execute(f"pragma table_info(`{table}`)"))
            cols_a = [r[1] for r in info_a]
            cols_b = [r[1] for r in b.execute(f"pragma table_info(`{table}`)")]
            if cols_a != cols_b:
                out["differences"][table] = {"columns_differ": True, "rebuild": cols_a, "live": cols_b}
                continue
            rows_a = set(a.execute(f"select * from `{table}`"))
            rows_b = set(b.execute(f"select * from `{table}`"))
            out["tables_compared"] += 1
            if rows_a == rows_b:
                continue
            key_idx = [i for i, r in enumerate(info_a) if r[5]] or [0]
            key = lambda row: tuple(row[i] for i in key_idx)  # noqa: E731
            only_a = {key(r): r for r in rows_a - rows_b}
            only_b = {key(r): r for r in rows_b - rows_a}
            samples = []
            for k in sorted(set(only_a) | set(only_b), key=str)[:5]:
                x, y = only_a.get(k), only_b.get(k)
                if x is not None and y is not None:
                    changed = [cols_a[i] for i in range(len(cols_a)) if x[i] != y[i]]
                    samples.append({
                        "pk": list(k), "columns": changed,
                        "rebuild": [_short(x[cols_a.index(c)]) for c in changed],
                        "live": [_short(y[cols_a.index(c)]) for c in changed],
                    })
                else:
                    samples.append({"pk": list(k), "only_in": "rebuild" if x is not None else "live"})
            out["differences"][table] = {
                "rows_only_in_rebuild": len(only_a),
                "rows_only_in_live": len(only_b),
                "samples": samples,
            }
        return out
    finally:
        a.close()
        b.close()


def equivalence_summary(diff: dict[str, Any]) -> str:
    parts = []
    for table, d in diff["differences"].items():
        if d.get("columns_differ"):
            parts.append(f"{table}: columns differ")
            continue
        n = max(d["rows_only_in_rebuild"], d["rows_only_in_live"])
        cols = sorted({c for s in d["samples"] for c in s.get("columns", [])})
        parts.append(f"{table}: {n} row(s) differ" + (f" ({', '.join(cols)})" if cols else ""))
    for table in diff["only_in_rebuild"]:
        parts.append(f"{table}: table missing on the live side")
    for table in diff["only_in_live"]:
        parts.append(f"{table}: extra table on the live side")
    return "; ".join(parts) if parts else f"identical ({diff['tables_compared']} tables)"


def previous_manifest(root: Path, stamp: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for candidate in root.glob("*/manifest.json"):
        other = candidate.parent.name
        if other >= stamp or not STAMP_RE.match(other):
            continue
        try:
            data = json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        if best is None or other > best["backup_stamp"]:
            data["backup_stamp"] = other
            best = data
    return best


def is_unchanged(prev: dict[str, Any] | None, fp: str, eq_summary: str, status: str) -> bool:
    """Drop the new folder only when the human work AND the live database's relation to it
    are exactly as last time, and nothing is failing. A failing folder is evidence: kept."""
    if prev is None or status == "FAIL" or prev.get("status") == "FAIL":
        return False
    return prev.get("fingerprint") == fp and (prev.get("equivalence") or {}).get("summary") == eq_summary


def write_readme(folder: Path, manifest: dict[str, Any]) -> None:
    stamp = manifest["backup_stamp"]
    counts = manifest.get("log_counts") or {}
    lines = [
        f"FlorAcco backup {stamp}",
        f"Taken from {manifest['source'].get('site')} ({manifest['source'].get('path')}) at {manifest['taken_at_utc']} UTC",
        f"Verified {manifest['verified_at_utc']} UTC — status {manifest['status']}",
        "",
        "WHAT IS HERE",
    ]
    for name in sorted(manifest["files"]):
        lines.append(f"  {name:<40} {FILE_ROLES.get(name, '')}")
    lines += [
        "",
        "WHAT THE LOG HOLDS",
        f"  record edits (change_request): {counts.get('change_request')}  by author/status: {counts.get('change_request_by_author_status')}",
        f"  term + identity links (reference_link): {counts.get('reference_link_active')} active  by kind/status/author: {counts.get('reference_link_by_kind_status_author')}",
        f"  People decision events: {counts.get('person_review_event')}  by action/author: {counts.get('person_review_event_by_action_author')}",
        f"  latest human activity: {counts.get('latest_activity_utc')}",
        "",
        "VERIFIED FOR THIS FOLDER",
        "  checksums: " + ("all files match the server's SHA256SUMS" if manifest["checksums_ok"]
                           else "MISMATCH: " + str(manifest["checksum_mismatches"])),
        f"  replay onto a fresh seed: {manifest.get('replay_onto_fresh_seed')}",
        f"  rebuild versus the live main.db: {manifest['equivalence']['summary']}",
        "",
        "TO REBUILD THE WORKING DATABASE FROM THIS FOLDER ALONE",
        "  Put projects_at.sql and corrections.db under <data>/sqlite/ (the data root the",
        "  importer reads, FLORACCO_DATA_DIR or <repo>/data) and run:",
        "      uv run python workflows/db_import.py build",
        "  The result equals main.db here except where this folder's manifest says the live",
        "  database had drifted from seed + log.",
        "",
        "TO RESTORE THE LIVE SITE FROM THIS FOLDER",
        "  1. Stop the Render service.",
        "  2. Copy corrections.db and main.db to /data/working/sqlite/, the .jsonl files to",
        "     /data/working/derived/word-pipeline/10_corrections/, review_decisions.csv to",
        "     /data/working/derived/word-pipeline/08_review_decisions/.",
        "     Delete any stale corrections.db-wal / corrections.db-shm beside the restored log.",
        "  3. Restart the service, then run deploy/backup.sh: its manifest must say identical.",
        "",
        "RULES",
        "  The log flows DOWN only: never upload a local corrections.db over the server's.",
        "  Never run db_import.py build on the live server (it takes no lock).",
        "  Not included because regenerable: search.db (rebuilt on demand), person_cache.db",
        "  (workflows/person_linkage.py), the Word-pipeline derived files, the images.",
    ]
    if manifest.get("notes"):
        lines += ["", "NOTES"] + [f"  - {note}" for note in manifest["notes"]]
    (folder / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


LOG_HEADER = (
    "# Backup log\n\n"
    "One line per run of `deploy/backup.sh`, newest last. A run that found the same human work "
    "and the same rebuild-equivalence as the previous backup drops its folder and records a heartbeat.\n\n"
    "| stamp (UTC) | status | folder | record edits | links active | People events | replay applied/conflicts/skipped | rebuild versus live |\n"
    "|---|---|---|---|---|---|---|---|\n"
)


def append_log(root: Path, line: str) -> None:
    log = root / "BACKUP_LOG.md"
    if not log.exists():
        log.write_text(LOG_HEADER, encoding="utf-8")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")


def _drop_side_files(folder: Path) -> None:
    """Opening a WAL-mode copy, even read-only, leaves -shm/-wal beside it. The copies are
    closed and the -wal is empty by now, so removing them keeps the folder clean to sync."""
    for name in ("corrections.db", "main.db", "place_cache.db"):
        for suffix in ("-wal", "-shm"):
            side = folder / (name + suffix)
            if side.exists() and (suffix == "-shm" or side.stat().st_size == 0):
                side.unlink()


def _authors(by_author_status: dict[str, int]) -> str:
    per_author: Counter[str] = Counter()
    for key, n in by_author_status.items():
        per_author[key.split(" / ")[0]] += n
    return ", ".join(f"{a} {n}" for a, n in sorted(per_author.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="the downloaded backup folder (…/<STAMP>)")
    parser.add_argument("--root", help="backup root holding the dated folders (default: the folder's parent)")
    parser.add_argument("--force", action="store_true", help="keep the folder even if nothing changed")
    parser.add_argument("--record-failure", metavar="WHAT",
                        help="deploy/backup.sh uses this when a step before verification failed: "
                             "append a FAIL line for the stamp to BACKUP_LOG.md and exit 1")
    args = parser.parse_args(argv)

    folder = Path(args.folder).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else folder.parent
    stamp = folder.name
    if args.record_failure:
        append_log(root, f"| {stamp} | FAIL | not downloaded: failed at step {args.record_failure}; see backup.log | | | | | |")
        print(f"[{stamp}] FAIL recorded in BACKUP_LOG.md: {args.record_failure}")
        return 1
    snapshot: dict[str, Any] = {}
    if (folder / "snapshot.json").exists():
        snapshot = json.loads((folder / "snapshot.json").read_text())

    present = sorted(
        p.name for p in folder.iterdir()
        if p.is_file() and p.name not in ("manifest.json", "README.txt") and not p.name.endswith(("-wal", "-shm"))
    )
    missing = [name for name in REQUIRED if name not in present]
    verified, mismatches = check_sums(folder)
    notes: list[str] = []
    status = "OK"
    if missing:
        status = "FAIL"
        notes.append(f"required file(s) missing: {missing}")
    if mismatches:
        status = "FAIL"
        notes.append(f"checksum mismatch or listed-but-missing: {mismatches}")
    if snapshot and not snapshot.get("locked_snapshot", True):
        notes.append("the server's write lock was held by someone else during the snapshot; the copies may straddle a write")

    counts = log_counts(folder / "corrections.db") if "corrections.db" in present else {}
    fp = fingerprint(folder)
    replay: dict[str, Any] | None = None
    row_counts: dict[str, int] | None = None
    equivalence: dict[str, Any] = {"summary": "not run", "detail": {}}
    if status != "FAIL":
        with tempfile.TemporaryDirectory(prefix="floracco-rebuild-") as tmp:
            summary = rebuild_from(folder, Path(tmp))
            replay = summary["corrections_replay"]
            row_counts = summary["row_counts"]
            detail = diff_databases(Path(tmp) / "sqlite" / "main.db", folder / "main.db")
        equivalence = {"summary": equivalence_summary(detail), "detail": detail}
        if replay["conflicts"]:
            status = "FAIL"
            notes.append(f"{replay['conflicts']} logged edit(s) conflict with the seed: a reseed changed values under them; re-review before replaying")
        elif replay["skipped"]:
            status = "WARN"
            notes.append(f"{replay['skipped']} logged operation(s) are of a kind replay does not handle yet")
        if detail["differences"] or detail["only_in_live"] or detail["only_in_rebuild"]:
            status = "WARN" if status == "OK" else status
            notes.append("the live main.db differs from seed + log: " + equivalence["summary"]
                         + ". Either an edit reached the database without reaching the log, or the live file is a stale build; a clean rebuild pushed to the server removes it.")

    prev = previous_manifest(root, stamp)
    if is_unchanged(prev, fp, equivalence["summary"], status) and not args.force:
        if folder.parent == root and STAMP_RE.match(stamp):
            shutil.rmtree(folder)
        append_log(root, f"| {stamp} | unchanged | dropped: same human work and same rebuild-equivalence as {prev['backup_stamp']} | | | | | |")
        print(f"[{stamp}] no change since {prev['backup_stamp']}; folder not kept (--force keeps it)")
        return 0

    _drop_side_files(folder)
    manifest = {
        "backup_stamp": stamp,
        "taken_at_utc": snapshot.get("taken_at"),
        "verified_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"site": "review.accomandite.org", "path": snapshot.get("source"), "host": snapshot.get("hostname"),
                   "locked_snapshot": snapshot.get("locked_snapshot")},
        "status": status,
        "files": {name: {"sha256": sha256(folder / name), "bytes": (folder / name).stat().st_size} for name in present},
        "checksums_ok": not mismatches and not missing,
        "checksum_mismatches": mismatches,
        "missing_required": missing,
        "seed_sha256": sha256(folder / "projects_at.sql") if "projects_at.sql" in present else None,
        "fingerprint": fp,
        "fingerprint_covers": [name for name in HUMAN_WORK if name in present],
        "log_counts": counts,
        "replay_onto_fresh_seed": replay,
        "rebuild_row_counts": row_counts,
        "equivalence": equivalence,
        "previous_backup": prev["backup_stamp"] if prev else None,
        "notes": notes,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    write_readme(folder, manifest)
    replay_text = f"{replay['applied']}/{replay['conflicts']}/{replay['skipped']}" if replay else "not run"
    append_log(root, f"| {stamp} | {status} | kept | {counts.get('change_request')} ({_authors(counts.get('change_request_by_author_status', {}))}) "
                     f"| {counts.get('reference_link_active')} | {counts.get('person_review_event')} | {replay_text} | {equivalence['summary']} |")
    print(f"[{stamp}] {status}: {len(verified)} files verified; log holds {counts.get('change_request')} record edits, "
          f"{counts.get('reference_link_active')} active links, {counts.get('person_review_event')} People events; "
          f"replay {replay_text}; rebuild versus live: {equivalence['summary']}")
    for note in notes:
        print(f"  note: {note}")
    print(f"  folder: {folder}")
    return 0 if status != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
