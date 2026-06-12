"""Convert the live IAS MySQL export into the working SQLite database.

Reads ``data/sqlite/projects_at.sql`` (phpMyAdmin / MySQL 8 dump from production)
and writes ``data/sqlite/main.db`` with the 11 research tables only — no ``admin``
(plaintext passwords), no MySQL views.

Usage:
    export UV_PROJECT_ENVIRONMENT=.floracco
    uv run python workflows/db_import.py build

Environment:
    FLORACCO_DB_DUMP_PATH  — MySQL dump (default: data/sqlite/projects_at.sql)
    FLORACCO_DB_PATH       — SQLite output (default: data/sqlite/main.db)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

try:  # importable both as a script (workflows/ on path) and as a module
    from workflows import corrections_db
except ModuleNotFoundError:
    import corrections_db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_DUMP_PATH = PROJECT_ROOT / "data/sqlite/projects_at.sql"
DEFAULT_DB_PATH = PROJECT_ROOT / "data/sqlite/main.db"
SCHEMA_PATH = PROJECT_ROOT / "queries/schema/tables.sql"

# Research tables only — matches queries/schema/tables.sql minus admin.
IMPORT_TABLES = (
    "contract",
    "sub_contract",
    "person",
    "investor",
    "investment",
    "investor_group",
    "contract_place",
    "place",
    "title",
    "currency",
    "economic_activity",
)

DUMP_TIME_RE = re.compile(r"Generation Time:\s*(.+)", re.IGNORECASE)
CREATE_TABLE_RE = re.compile(
    r"CREATE TABLE `(?P<name>\w+)`\s*\(", re.IGNORECASE | re.MULTILINE
)


def dump_path() -> Path:
    raw = os.getenv("FLORACCO_DB_DUMP_PATH", DEFAULT_DUMP_PATH)
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def db_path() -> Path:
    raw = os.getenv("FLORACCO_DB_PATH", DEFAULT_DB_PATH)
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def parse_dump_timestamp(text: str) -> str | None:
    match = DUMP_TIME_RE.search(text[:2000])
    return match.group(1).strip() if match else None


def schema_ddl() -> str:
    """SQLite DDL for IMPORT_TABLES from queries/schema/tables.sql."""
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    parts: list[str] = []
    for match in CREATE_TABLE_RE.finditer(raw):
        name = match.group("name")
        if name not in IMPORT_TABLES:
            continue
        start = match.start()
        end = raw.find(";", match.end())
        if end == -1:
            raise ValueError(f"Unterminated CREATE TABLE for {name}")
        stmt = raw[start : end + 1]
        # Strip MySQL-style commented KEY lines inside CREATE TABLE.
        stmt = re.sub(r"/\*.*?\*/", "", stmt, flags=re.DOTALL)
        parts.append(stmt)
    missing = set(IMPORT_TABLES) - {m.group("name") for m in CREATE_TABLE_RE.finditer(raw)}
    if missing:
        raise ValueError(f"Schema missing tables: {sorted(missing)}")
    return "\n\n".join(parts)


def extract_insert_statements(dump_text: str, table: str) -> list[str]:
    """Pull complete INSERT INTO `table` ... ; statements from the dump."""
    marker = f"-- Dumping data for table `{table}`"
    pos = dump_text.find(marker)
    if pos == -1:
        return []
    chunk = dump_text[pos:]
    # Stop at next table structure / view section after the inserts.
    stop = re.search(r"\n-- -+\n\n--\n-- (?:Table structure|Stand-in structure)", chunk[100:])
    if stop:
        chunk = chunk[: 100 + stop.start()]

    prefix = f"INSERT INTO `{table}`"
    statements: list[str] = []
    i = 0
    while True:
        start = chunk.find(prefix, i)
        if start == -1:
            break
        end = _find_statement_end(chunk, start)
        if end is None:
            raise ValueError(f"Unterminated INSERT for `{table}` at offset {start}")
        statements.append(chunk[start : end + 1].strip())
        i = end + 1
    return statements


def _find_statement_end(text: str, start: int) -> int | None:
    """Find the semicolon that terminates an INSERT, respecting string literals."""
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == "'":
                in_string = False
            continue
        if ch == "'":
            in_string = True
        elif ch == ";":
            return idx
    return None


def mysql_insert_to_sqlite(sql: str) -> str:
    """Convert MySQL string escapes inside INSERT literals for SQLite."""
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch != "'":
            out.append(ch)
            i += 1
            continue
        out.append("'")
        i += 1
        while i < n:
            ch = sql[i]
            if ch == "\\" and i + 1 < n:
                nxt = sql[i + 1]
                if nxt == "'":
                    out.append("''")
                elif nxt == '"':
                    out.append('"')
                elif nxt == "n":
                    out.append("\n")
                elif nxt == "r":
                    out.append("\r")
                elif nxt == "t":
                    out.append("\t")
                elif nxt == "\\":
                    out.append("\\")
                elif nxt == "0":
                    out.append("\0")
                else:
                    out.append(nxt)
                i += 2
                continue
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("''")
                    i += 2
                    continue
                out.append("'")
                i += 1
                break
            out.append(ch)
            i += 1
    return "".join(out)


def import_table(connection: sqlite3.Connection, dump_text: str, table: str) -> int:
    statements = extract_insert_statements(dump_text, table)
    rows = 0
    for stmt in statements:
        connection.executescript(mysql_insert_to_sqlite(stmt))
        # Count tuple opens after VALUES — rough row count per statement.
        values_idx = stmt.upper().find("VALUES")
        if values_idx != -1:
            body = stmt[values_idx + 6 : -1]
            rows += body.count("),(") + 1
    return rows


def count_rows(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM `{table}`").fetchone()[0]


def max_id(connection: sqlite3.Connection, table: str, column: str) -> int | None:
    try:
        row = connection.execute(f"SELECT MAX(`{column}`) FROM `{table}`").fetchone()
        return row[0] if row and row[0] is not None else None
    except sqlite3.OperationalError:
        return None


def validate_counts(counts: dict[str, int]) -> list[str]:
    """Return human-readable validation errors (empty if OK)."""
    errors: list[str] = []
    minimums = {
        "contract": 4800,
        "sub_contract": 3400,
        "person": 11000,
        "investor": 17000,
        "investment": 15000,
    }
    for table, floor in minimums.items():
        if counts[table] < floor:
            errors.append(f"{table}: {counts[table]} rows (expected >= {floor})")
    return errors


def add_soft_delete_columns(connection: sqlite3.Connection) -> None:
    """Add `is_deleted` to each editable table (idempotent)."""
    for table in corrections_db.SOFT_DELETE_TABLES:
        cols = {row[1] for row in connection.execute(f"PRAGMA table_info(`{table}`)")}
        if corrections_db.IS_DELETED_COLUMN not in cols:
            connection.execute(
                f"ALTER TABLE `{table}` ADD COLUMN {corrections_db.IS_DELETED_COLUMN} INTEGER NOT NULL DEFAULT 0"
            )


def _norm(value: object) -> str:
    return "" if value is None else str(value).strip()


def replay_corrections(connection: sqlite3.Connection) -> dict:
    """Re-apply the authoritative human-change log onto the fresh seed.

    Idempotent and staleness-aware: an `update` whose recorded pre-image no longer
    matches the seed value is left unapplied and flagged `conflict` for re-review,
    rather than silently overwriting an upstream change. A `create` (a DB-native
    row born on the platform after the Word-corpus freeze) is re-INSERTed from its
    full-row snapshot — unless the seed meanwhile contains a row with that id, in
    which case it is flagged `conflict` instead of duplicated. (hard-delete /
    relink land in phase 2 and are skipped here.)
    """
    cpath = corrections_db.default_path()
    if not cpath.exists():
        return {"applied": 0, "conflicts": 0, "skipped": 0}
    run_id = f"replay-{datetime.now(timezone.utc).isoformat()[:19]}"
    clog = corrections_db.connect(cpath)
    applied = conflicts = skipped = 0
    try:
        for op in corrections_db.applied_operations(clog):
            table, pk = op["db_table"], op["pk"]
            key_cols = corrections_db.ALL_TABLE_PRIMARY_KEYS.get(table)
            if not key_cols or not all(c in pk for c in key_cols):
                skipped += 1
                continue
            where = " AND ".join(f"`{c}`=?" for c in key_cols)
            params = [pk[c] for c in key_cols]
            if op["op"] == "update":
                field = op["field"]
                cols = {row[1] for row in connection.execute(f"PRAGMA table_info(`{table}`)")}
                if field not in cols:
                    skipped += 1
                    continue
                current = connection.execute(f"SELECT `{field}` FROM `{table}` WHERE {where}", params).fetchone()
                if current is None or _norm(current[0]) != _norm(op["before_value"]):
                    corrections_db.add_event(
                        clog, op["request_id"], event="conflict_flagged", by="db_import",
                        new_status="conflict", run_id=run_id,
                        note="Seed value differs from the recorded pre-image; re-review before re-applying.",
                    )
                    conflicts += 1
                    continue
                connection.execute(f"UPDATE `{table}` SET `{field}`=? WHERE {where}", [op["after_value"], *params])
                applied += 1
            elif op["op"] == "delete" and not op["hard"]:
                connection.execute(f"UPDATE `{table}` SET {corrections_db.IS_DELETED_COLUMN}=1 WHERE {where}", params)
                applied += 1
            elif op["op"] == "restore":
                connection.execute(f"UPDATE `{table}` SET {corrections_db.IS_DELETED_COLUMN}=0 WHERE {where}", params)
                applied += 1
            elif op["op"] == "create":
                after = op["after_value"]
                if not isinstance(after, dict):
                    skipped += 1
                    continue
                if connection.execute(f"SELECT 1 FROM `{table}` WHERE {where}", params).fetchone():
                    corrections_db.add_event(
                        clog, op["request_id"], event="conflict_flagged", by="db_import",
                        new_status="conflict", run_id=run_id,
                        note="Seed now contains a row with this id; the platform-created row was not re-inserted.",
                    )
                    conflicts += 1
                    continue
                cols = {row[1] for row in connection.execute(f"PRAGMA table_info(`{table}`)")}
                data = {k: v for k, v in after.items() if k in cols}
                for c in key_cols:
                    data.setdefault(c, pk[c])
                names = ", ".join(f"`{c}`" for c in data)
                marks = ", ".join("?" for _ in data)
                connection.execute(f"INSERT INTO `{table}` ({names}) VALUES ({marks})", list(data.values()))
                applied += 1
            else:
                skipped += 1  # hard delete / relink → phase 2
    finally:
        clog.close()
    return {"applied": applied, "conflicts": conflicts, "skipped": skipped}


def build(*, backup: bool = True) -> dict:
    dump = dump_path()
    out = db_path()
    if not dump.is_file():
        raise FileNotFoundError(f"MySQL dump not found: {dump}")

    dump_text = dump.read_text(encoding="utf-8", errors="replace")
    dump_time = parse_dump_timestamp(dump_text)

    out.parent.mkdir(parents=True, exist_ok=True)
    if backup and out.exists():
        bak = out.with_suffix(out.suffix + ".bak")
        shutil.copy2(out, bak)

    if out.exists():
        out.unlink()

    connection = sqlite3.connect(out)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(schema_ddl())

        insert_counts: dict[str, int] = {}
        for table in IMPORT_TABLES:
            insert_counts[table] = import_table(connection, dump_text, table)

        connection.commit()

        counts = {table: count_rows(connection, table) for table in IMPORT_TABLES}
        validation_errors = validate_counts(counts)
    finally:
        connection.close()

    if validation_errors:
        raise RuntimeError("Import validation failed:\n  " + "\n  ".join(validation_errors))

    # Working-DB layer on top of the seed: soft-delete flag + replay of the
    # authoritative human-change log so corrections survive a reseed.
    connection = sqlite3.connect(out)
    try:
        add_soft_delete_columns(connection)
        replay_stats = replay_corrections(connection)
        connection.commit()
    finally:
        connection.close()

    # Derived FTS index (separate search.db; regenerable; never part of the
    # seed+replay equation). Built last so it indexes the replayed state.
    try:
        from workflows import search_index
    except ImportError:  # run as a script from workflows/
        import search_index  # type: ignore[no-redef]
    search_stats = search_index.build(out)

    pk_hints = {
        "contract": "contract_id",
        "sub_contract": "contract_id",
        "person": "person_id",
        "investor": "investor_id",
        "investment": "investment_id",
    }
    max_ids = {}
    connection = sqlite3.connect(out)
    try:
        for table, col in pk_hints.items():
            max_ids[table] = max_id(connection, table, col)
    finally:
        connection.close()

    return {
        "dump_path": str(dump.relative_to(PROJECT_ROOT)),
        "db_path": str(out.relative_to(PROJECT_ROOT)),
        "dump_timestamp": dump_time,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "row_counts": counts,
        "insert_statements_rows_est": insert_counts,
        "max_ids": max_ids,
        "corrections_replay": replay_stats,
        "search_index": search_stats,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--no-backup", action="store_true", help="do not copy existing main.db to .bak")
    args = parser.parse_args()
    if args.command == "build":
        summary = build(backup=not args.no_backup)
        print(f"Wrote {summary['db_path']} from {summary['dump_path']}")
        if summary["dump_timestamp"]:
            print(f"Dump timestamp: {summary['dump_timestamp']}")
        print("Row counts:")
        for table in IMPORT_TABLES:
            est = summary["insert_statements_rows_est"].get(table, 0)
            actual = summary["row_counts"][table]
            extra = f" (insert blocks ~{est} rows)" if est != actual else ""
            print(f"  {table:20s} {actual:6d}{extra}")
        if summary["max_ids"]:
            print("Max IDs:", ", ".join(f"{k}={v}" for k, v in summary["max_ids"].items()))


if __name__ == "__main__":
    main()
