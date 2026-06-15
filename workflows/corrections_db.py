"""Authoritative human-change log for the working SQLite database.

`corrections.db` is the source of truth for every human change to `main.db`
(edit a field, hide/restore a record, …). It is a separate SQLite file, **never**
touched by the `db_import` seed, so corrections survive a reseed: the importer
re-applies the log onto the freshly-seeded `main.db` (see `db_import.replay`).

This module owns only the *log* (`corrections.db`). Writing the change into
`main.db` is done by the caller (the review server, or the importer during
replay), so `main.db` keeps a single, guarded writer per process.

Design: docs/workflows/db_corrections_design.md
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Canonical primary key per editable table (the schema declares no foreign keys;
# integrity is logical). Composite keys are ordered.
TABLE_PRIMARY_KEYS: dict[str, list[str]] = {
    "contract": ["contract_id"],
    "sub_contract": ["contract_id"],  # sub_contract's own PK is contract_id
    "person": ["person_id"],
    "investor": ["investor_id"],
    "investment": ["investment_id"],
    "contract_place": ["place_id", "contract_id"],
    "investor_group": ["investor_id", "investment_id"],
}

# Lookup lists (place / title / currency / economic_activity). Values are raw,
# interpretive phrases entered "exactly as in the document" — the platform may
# CREATE new rows here (so replay must re-insert them, or a created contract's
# FK would dangle after a reseed) but never normalizes or merges existing ones.
LOOKUP_PRIMARY_KEYS: dict[str, list[str]] = {
    "place": ["place_id"],
    "title": ["title_id"],
    "currency": ["currency_id"],
    "economic_activity": ["ec_activity_id"],
}

# Every table the replay knows how to handle.
ALL_TABLE_PRIMARY_KEYS: dict[str, list[str]] = {**TABLE_PRIMARY_KEYS, **LOOKUP_PRIMARY_KEYS}

# Tables that carry the soft-delete flag (added post-import by db_import).
# Lookup tables deliberately excluded — they are never hidden, only referenced.
SOFT_DELETE_TABLES = tuple(TABLE_PRIMARY_KEYS)
IS_DELETED_COLUMN = "is_deleted"

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS change_request (
  request_id      TEXT PRIMARY KEY,
  op              TEXT NOT NULL CHECK (op IN ('update','relink','create','delete','restore')),
  db_table        TEXT NOT NULL,
  pk              TEXT NOT NULL,
  field           TEXT,
  before_value    TEXT,
  after_value     TEXT,
  hard            INTEGER NOT NULL DEFAULT 0,
  status          TEXT NOT NULL CHECK (status IN ('proposed','approved','applied','rejected','reverted','conflict')),
  origin          TEXT NOT NULL,
  reason          TEXT,
  source_entry_id TEXT,
  source_quote    TEXT,
  pre_image_hash  TEXT,
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  reviewed_by     TEXT,
  reviewed_at     TEXT
);

CREATE TABLE IF NOT EXISTS change_event (
  event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id  TEXT NOT NULL REFERENCES change_request(request_id),
  event       TEXT NOT NULL,
  at          TEXT NOT NULL,
  by          TEXT NOT NULL,
  run_id      TEXT,
  pre_image   TEXT,
  post_image  TEXT,
  note        TEXT
);

CREATE INDEX IF NOT EXISTS ix_request_target ON change_request(db_table, pk);
CREATE INDEX IF NOT EXISTS ix_event_request  ON change_event(request_id);
"""


def default_path() -> Path:
    """Location of corrections.db.

    Honors, in order: an explicit FLORACCO_CORRECTIONS_DB_PATH; else the shared
    FLORACCO_DATA_DIR (so the op-log stays co-located with main.db and the
    derived outputs under one relocatable data root); else <repo>/data.
    """
    root = Path(__file__).resolve().parents[1]
    explicit = os.getenv("FLORACCO_CORRECTIONS_DB_PATH")
    if explicit:
        return Path(explicit) if Path(explicit).is_absolute() else root / explicit
    data_dir = os.getenv("FLORACCO_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().resolve() / "sqlite/corrections.db"
    return root / "data/sqlite/corrections.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pk_json(pk: dict[str, Any]) -> str:
    """Stable JSON for a (possibly composite) primary key."""
    return json.dumps({k: pk[k] for k in sorted(pk)}, separators=(",", ":"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def record_operation(
    conn: sqlite3.Connection,
    *,
    op: str,
    db_table: str,
    pk: dict[str, Any],
    by: str,
    status: str = "applied",
    field: str | None = None,
    before_value: Any = None,
    after_value: Any = None,
    hard: bool = False,
    origin: str = "human_direct",
    reason: str | None = None,
    source_entry_id: str | None = None,
    source_quote: str | None = None,
    run_id: str | None = None,
    note: str | None = None,
) -> str:
    """Log one operation (+ its first event) in a single transaction. Returns request_id.

    The caller is responsible for the corresponding write to `main.db`; this only
    records the authoritative log entry.
    """
    request_id = str(uuid.uuid4())
    at = now_iso()
    with conn:
        conn.execute(
            """INSERT INTO change_request
               (request_id, op, db_table, pk, field, before_value, after_value, hard,
                status, origin, reason, source_entry_id, source_quote, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                request_id, op, db_table, pk_json(pk), field,
                _enc(before_value), _enc(after_value), 1 if hard else 0,
                status, origin, reason, source_entry_id, source_quote, by, at,
            ),
        )
        conn.execute(
            """INSERT INTO change_event (request_id, event, at, by, run_id, pre_image, post_image, note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (request_id, status, at, by, run_id, _enc(before_value), _enc(after_value), note),
        )
    return request_id


def add_event(
    conn: sqlite3.Connection,
    request_id: str,
    *,
    event: str,
    by: str,
    new_status: str | None = None,
    run_id: str | None = None,
    note: str | None = None,
) -> None:
    at = now_iso()
    with conn:
        if new_status:
            conn.execute(
                "UPDATE change_request SET status=?, reviewed_by=?, reviewed_at=? WHERE request_id=?",
                (new_status, by, at, request_id),
            )
        conn.execute(
            "INSERT INTO change_event (request_id, event, at, by, run_id, note) VALUES (?,?,?,?,?,?)",
            (request_id, event, at, by, run_id, note),
        )


def request_by_id(conn: sqlite3.Connection, request_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM change_request WHERE request_id=?", (request_id,)).fetchone()
    return dict(row) if row else None


def history_for_row(conn: sqlite3.Connection, db_table: str, pk: dict[str, Any]) -> list[dict[str, Any]]:
    """All operations on one DB row, newest first, each with its event trail."""
    requests = conn.execute(
        "SELECT * FROM change_request WHERE db_table=? AND pk=? ORDER BY created_at DESC",
        (db_table, pk_json(pk)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for req in requests:
        events = conn.execute(
            "SELECT event, at, by, note FROM change_event WHERE request_id=? ORDER BY event_id",
            (req["request_id"],),
        ).fetchall()
        item = dict(req)
        item["before_value"] = _dec(item.get("before_value"))
        item["after_value"] = _dec(item.get("after_value"))
        item["events"] = [dict(e) for e in events]
        out.append(item)
    return out


def is_row_hidden(conn: sqlite3.Connection, db_table: str, pk: dict[str, Any]) -> bool:
    """Latest applied delete/restore op for a row decides whether it is hidden."""
    row = conn.execute(
        """SELECT op FROM change_request
           WHERE db_table=? AND pk=? AND status='applied' AND op IN ('delete','restore')
           ORDER BY created_at DESC LIMIT 1""",
        (db_table, pk_json(pk)),
    ).fetchone()
    return bool(row) and row["op"] == "delete"


def created_row_ids(conn: sqlite3.Connection) -> set[str]:
    """``table:id`` keys of rows born via applied create ops (DB-native rows).

    These rows were added directly to the database after the Word corpus was
    frozen, so they have no Word summary by design: the matcher must not report
    them as unlinked DB rows, and coverage metrics must not count them in the
    frozen-corpus denominator. Composite-key tables are not addressable in the
    ``table:id`` scheme and are skipped.
    """
    out: set[str] = set()
    rows = conn.execute(
        "SELECT db_table, pk FROM change_request WHERE op='create' AND status='applied'"
    ).fetchall()
    for row in rows:
        key_cols = ALL_TABLE_PRIMARY_KEYS.get(row["db_table"])
        if not key_cols or len(key_cols) != 1:
            continue
        pk = json.loads(row["pk"])
        if key_cols[0] in pk:
            out.add(f"{row['db_table']}:{pk[key_cols[0]]}")
    return out


def applied_operations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Applied, non-reverted operations in chronological order, for replay."""
    rows = conn.execute(
        "SELECT * FROM change_request WHERE status='applied' ORDER BY created_at",
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["before_value"] = _dec(item.get("before_value"))
        item["after_value"] = _dec(item.get("after_value"))
        item["pk"] = json.loads(item["pk"])
        result.append(item)
    return result


def _enc(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _dec(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "{[":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
