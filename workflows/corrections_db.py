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

-- Interpretive reference and identity links. A reviewed judgement that two
-- lookup terms or entered person rows are the same, or explicitly NOT the same. This
-- is ADDITIVE annotation: it never mutates or deletes either term in main.db,
-- so it lives only here and survives a reseed. Reversible via status='revoked'.
CREATE TABLE IF NOT EXISTS reference_link (
  link_id     TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,        -- place | title | currency | economic_activity | person
  rel         TEXT NOT NULL CHECK (rel IN ('same_as','variant_of','distinct')),
  from_id     INTEGER NOT NULL,
  to_id       INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  reason      TEXT,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  revoked_by  TEXT,
  revoked_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_reflink_kind ON reference_link(kind, status);

-- Immutable history for person-identity review. `reference_link` is the active
-- projection; this records exactly what a reviewer did and what evidence they
-- saw, including deferrals and split flags that are not links.
CREATE TABLE IF NOT EXISTS person_review_event (
  event_id          TEXT PRIMARY KEY,
  action            TEXT NOT NULL CHECK (
    action IN ('same_as','distinct','defer','reopen','flag_split','revoke','bulk_rule_ack')
  ),
  case_id           TEXT,
  person_ids        TEXT NOT NULL,
  link_ids          TEXT,
  reason_code       TEXT,
  rationale         TEXT,
  source_tier       TEXT,
  model_sha256      TEXT,
  cache_run_id      TEXT,
  match_probability REAL,
  evidence_snapshot TEXT NOT NULL,
  created_by        TEXT NOT NULL,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_person_event_case ON person_review_event(case_id, created_at);
CREATE INDEX IF NOT EXISTS ix_person_event_action ON person_review_event(action, created_at);
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
    conn = sqlite3.connect(path, timeout=15.0)
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
    # Structured values are reserved for `create` snapshots. A dict/list on any
    # other op would be stringified by _enc and JSON-revived by the create-aware
    # decoder's counterpart — refuse loudly instead of storing an ambiguity
    # (docs/multi_user_safety.md A5).
    if op != "create":
        for name, value in (("before_value", before_value), ("after_value", after_value)):
            if isinstance(value, (dict, list)):
                raise ValueError(
                    f"{name} must be a scalar for op={op!r}; full-row snapshots belong to create ops only"
                )
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
        item["before_value"] = _dec_for(item["op"], item.get("before_value"))
        item["after_value"] = _dec_for(item["op"], item.get("after_value"))
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
        item["before_value"] = _dec_for(item["op"], item.get("before_value"))
        item["after_value"] = _dec_for(item["op"], item.get("after_value"))
        item["pk"] = json.loads(item["pk"])
        result.append(item)
    return result


# --- Reference links (interpretive vocabulary curation) ---------------------


def insert_reference_link(
    conn: sqlite3.Connection,
    *,
    kind: str,
    rel: str,
    from_id: int,
    to_id: int,
    by: str,
    reason: str | None = None,
    link_id: str | None = None,
) -> str:
    """Insert a link into the caller's transaction."""
    link_id = link_id or str(uuid.uuid4())
    conn.execute(
        """INSERT INTO reference_link
           (link_id, kind, rel, from_id, to_id, status, reason, created_by, created_at)
           VALUES (?,?,?,?,?, 'active', ?,?,?)""",
        (link_id, kind, rel, int(from_id), int(to_id), reason, by, now_iso()),
    )
    return link_id


def add_reference_link(
    conn: sqlite3.Connection,
    *,
    kind: str,
    rel: str,
    from_id: int,
    to_id: int,
    by: str,
    reason: str | None = None,
) -> str:
    """Record one active link (same_as / variant_of / distinct). Returns link_id.

    A pair is ordered so (a,b) and (b,a) collapse, except for same_as/variant_of
    where direction (variant -> canonical) is meaningful and preserved as given.
    """
    with conn:
        link_id = insert_reference_link(
            conn,
            kind=kind,
            rel=rel,
            from_id=from_id,
            to_id=to_id,
            by=by,
            reason=reason,
        )
    return link_id


def reference_link_by_id(conn: sqlite3.Connection, link_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM reference_link WHERE link_id=?", (link_id,)).fetchone()
    return dict(row) if row else None


def set_reference_link_revoked(
    conn: sqlite3.Connection,
    link_id: str,
    *,
    by: str,
    expected_kind: str | None = None,
) -> bool:
    """Revoke one active link inside the caller's transaction."""
    sql = (
        "UPDATE reference_link SET status='revoked', revoked_by=?, revoked_at=? "
        "WHERE link_id=? AND status='active'"
    )
    params: list[Any] = [by, now_iso(), link_id]
    if expected_kind is not None:
        sql += " AND kind=?"
        params.append(expected_kind)
    return conn.execute(sql, params).rowcount > 0


def revoke_reference_link(conn: sqlite3.Connection, link_id: str, by: str) -> bool:
    with conn:
        return set_reference_link_revoked(conn, link_id, by=by)


def active_reference_links(conn: sqlite3.Connection, kind: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM reference_link WHERE kind=? AND status='active' ORDER BY created_at DESC",
        (kind,),
    ).fetchall()
    return [dict(r) for r in rows]


def decided_pairs(conn: sqlite3.Connection, kind: str) -> set[frozenset[int]]:
    """Unordered term-id pairs that already carry an active link (any rel), so
    the duplicate finder can stop resurfacing them."""
    out: set[frozenset[int]] = set()
    for r in conn.execute(
        "SELECT from_id, to_id FROM reference_link WHERE kind=? AND status='active'",
        (kind,),
    ):
        out.add(frozenset((int(r["from_id"]), int(r["to_id"]))))
    return out


def append_person_review_event(
    conn: sqlite3.Connection,
    *,
    action: str,
    person_ids: list[int],
    by: str,
    evidence_snapshot: dict[str, Any],
    case_id: str | None = None,
    link_ids: list[str] | None = None,
    reason_code: str | None = None,
    rationale: str | None = None,
    source_tier: str | None = None,
    model_sha256: str | None = None,
    cache_run_id: str | None = None,
    match_probability: float | None = None,
    event_id: str | None = None,
) -> str:
    """Append one immutable person-review event in the caller's transaction."""
    event_id = event_id or str(uuid.uuid4())
    conn.execute(
        """INSERT INTO person_review_event
           (event_id, action, case_id, person_ids, link_ids, reason_code, rationale,
            source_tier, model_sha256, cache_run_id, match_probability,
            evidence_snapshot, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            action,
            case_id,
            json.dumps(sorted({int(value) for value in person_ids}), separators=(",", ":")),
            json.dumps(link_ids or [], separators=(",", ":")),
            reason_code,
            rationale,
            source_tier,
            model_sha256,
            cache_run_id,
            match_probability,
            json.dumps(evidence_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            by,
            now_iso(),
        ),
    )
    return event_id


def person_review_events(
    conn: sqlite3.Connection, *, case_id: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM person_review_event"
    params: tuple[Any, ...] = ()
    if case_id is not None:
        sql += " WHERE case_id=?"
        params = (case_id,)
    sql += " ORDER BY created_at DESC, event_id DESC"
    rows = conn.execute(sql, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["person_ids"] = json.loads(item["person_ids"])
        item["link_ids"] = json.loads(item["link_ids"] or "[]")
        item["evidence_snapshot"] = json.loads(item["evidence_snapshot"])
        result.append(item)
    return result


class PersonDecisionConflict(ValueError):
    """A proposed identity decision contradicts active reviewed links."""


def _person_link_state(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[int, int], list[tuple[int, int]]]:
    links = active_reference_links(conn, "person")
    parent: dict[int, int] = {}

    def find(value: int) -> int:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            low, high = sorted((a, b))
            parent[high] = low

    distinct: list[tuple[int, int]] = []
    for link in links:
        left, right = int(link["from_id"]), int(link["to_id"])
        if link["rel"] == "same_as":
            union(left, right)
        elif link["rel"] == "distinct":
            distinct.append(tuple(sorted((left, right))))
    for value in list(parent):
        parent[value] = find(value)
    return links, parent, distinct


def record_person_review(
    conn: sqlite3.Connection,
    *,
    action: str,
    person_ids: list[int],
    by: str,
    evidence_snapshot: dict[str, Any],
    case_id: str | None = None,
    reason_code: str | None = None,
    rationale: str | None = None,
    source_tier: str | None = None,
    model_sha256: str | None = None,
    cache_run_id: str | None = None,
    match_probability: float | None = None,
) -> dict[str, Any]:
    """Atomically update person-link state and append its immutable event."""
    ids = sorted({int(value) for value in person_ids})
    if not ids:
        raise ValueError("A person review decision needs at least one person id.")
    if action in {"same_as", "distinct"} and len(ids) < 2:
        raise ValueError(f"{action} needs at least two person ids.")
    if action not in {
        "same_as",
        "distinct",
        "defer",
        "reopen",
        "flag_split",
        "bulk_rule_ack",
    }:
        raise ValueError(f"Unknown person review action: {action}")

    link_ids: list[str] = []
    with conn:
        links, parent, distinct_pairs = _person_link_state(conn)

        def root(value: int) -> int:
            while parent.get(value, value) != value:
                value = parent[value]
            return value

        active_pair: dict[frozenset[int], dict[str, Any]] = {
            frozenset((int(link["from_id"]), int(link["to_id"]))): link
            for link in links
        }
        if action == "same_as":
            proposed_roots = {root(value) for value in ids}
            merged_members = {
                value
                for value in set(parent) | set(ids)
                if root(value) in proposed_roots
            }
            for left, right in distinct_pairs:
                if left in merged_members and right in merged_members:
                    raise PersonDecisionConflict(
                        f"Person {left} and person {right} are already reviewed as different."
                    )
            anchor = min(ids)
            for other in ids:
                if other == anchor or root(other) == root(anchor):
                    continue
                pair = frozenset((anchor, other))
                existing = active_pair.get(pair)
                if existing:
                    raise PersonDecisionConflict(
                        "These person records already have an active contradictory decision."
                    )
                low, high = sorted((anchor, other))
                link_ids.append(
                    insert_reference_link(
                        conn,
                        kind="person",
                        rel="same_as",
                        from_id=low,
                        to_id=high,
                        by=by,
                        reason=rationale,
                    )
                )
                parent[root(high)] = root(low)
        elif action == "distinct":
            import itertools

            reviewed_distinct = {
                frozenset((root(left), root(right))) for left, right in distinct_pairs
            }
            for left, right in itertools.combinations(ids, 2):
                if root(left) == root(right):
                    raise PersonDecisionConflict(
                        f"Person {left} and person {right} are already linked as the same person."
                    )
                if frozenset((root(left), root(right))) in reviewed_distinct:
                    continue
                pair = frozenset((left, right))
                existing = active_pair.get(pair)
                if existing:
                    if existing["rel"] == "distinct":
                        continue
                    raise PersonDecisionConflict(
                        "These person records already have an active contradictory decision."
                    )
                link_ids.append(
                    insert_reference_link(
                        conn,
                        kind="person",
                        rel="distinct",
                        from_id=left,
                        to_id=right,
                        by=by,
                        reason=rationale,
                    )
                )
        if action in {"same_as", "distinct"} and not link_ids:
            raise PersonDecisionConflict("This identity decision is already active.")
        event_id = append_person_review_event(
            conn,
            action=action,
            person_ids=ids,
            by=by,
            evidence_snapshot=evidence_snapshot,
            case_id=case_id,
            link_ids=link_ids,
            reason_code=reason_code,
            rationale=rationale,
            source_tier=source_tier,
            model_sha256=model_sha256,
            cache_run_id=cache_run_id,
            match_probability=match_probability,
        )
    return {"event_id": event_id, "link_ids": link_ids}


def revoke_person_reference_link(
    conn: sqlite3.Connection,
    *,
    link_id: str,
    by: str,
    rationale: str | None = None,
) -> dict[str, Any]:
    """Atomically revoke a person link and append exactly one immutable event."""
    with conn:
        link = reference_link_by_id(conn, link_id)
        if not link or link.get("kind") != "person" or link.get("status") != "active":
            return {}
        origin = next(
            (
                event
                for event in person_review_events(conn)
                if link_id in event.get("link_ids", [])
                and event.get("action") in {"same_as", "distinct"}
            ),
            None,
        )
        if not set_reference_link_revoked(conn, link_id, by=by, expected_kind="person"):
            return {}
        event_id = append_person_review_event(
            conn,
            action="revoke",
            person_ids=[int(link["from_id"]), int(link["to_id"])],
            by=by,
            evidence_snapshot={
                "revoked_link": link,
                "original_event_id": (origin or {}).get("event_id"),
            },
            case_id=(origin or {}).get("case_id"),
            link_ids=[link_id],
            rationale=rationale,
            source_tier=(origin or {}).get("source_tier"),
            model_sha256=(origin or {}).get("model_sha256"),
            cache_run_id=(origin or {}).get("cache_run_id"),
            match_probability=(origin or {}).get("match_probability"),
        )
    return {"event_id": event_id, "link": link}


def revoke_person_review_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    by: str,
    rationale: str | None = None,
) -> dict[str, Any]:
    """Atomically revoke every still-active link created by one review event."""
    origin = next(
        (event for event in person_review_events(conn) if event["event_id"] == event_id),
        None,
    )
    if not origin or origin.get("action") not in {"same_as", "distinct"}:
        return {}
    link_ids = list(origin.get("link_ids") or [])
    with conn:
        active_links = [
            link
            for link_id in link_ids
            if (link := reference_link_by_id(conn, link_id))
            and link.get("kind") == "person"
            and link.get("status") == "active"
        ]
        if not active_links:
            return {}
        for link in active_links:
            if not set_reference_link_revoked(
                conn, str(link["link_id"]), by=by, expected_kind="person"
            ):
                raise RuntimeError("Person decision changed while it was being undone.")
        revoke_event_id = append_person_review_event(
            conn,
            action="revoke",
            person_ids=origin["person_ids"],
            by=by,
            evidence_snapshot={
                "original_event_id": event_id,
                "revoked_links": active_links,
                "undo_scope": "whole_decision",
            },
            case_id=origin.get("case_id"),
            link_ids=[str(link["link_id"]) for link in active_links],
            rationale=rationale,
            source_tier=origin.get("source_tier"),
            model_sha256=origin.get("model_sha256"),
            cache_run_id=origin.get("cache_run_id"),
            match_probability=origin.get("match_probability"),
        )
    return {
        "event_id": revoke_event_id,
        "original_event_id": event_id,
        "revoked_link_ids": [str(link["link_id"]) for link in active_links],
    }


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


def _dec_for(op: str, value: Any) -> Any:
    """Op-aware decoding (docs/multi_user_safety.md A5): only `create` ops carry
    structured values (full-row snapshots) — update/delete/restore values are
    reviewer-typed or scalar TEXT and must round-trip verbatim. Without this
    gate a typed "[2]" (an editorial bracketed folio) revives as a Python list
    and crashes replay's parameter binding, blocking the whole rebuild."""
    return _dec(value) if op == "create" else value
