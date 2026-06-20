"""Derived FTS5 search index over the working database.

`search.db` is regenerable derived data — deliberately a SEPARATE file so the
sacred `main.db = seed + replay(corrections)` equation stays untouched. It is
built by `db_import` after every seed build and rebuilt on demand by the review
server whenever `main.db` has changed since (an applied correction, a hide, a
created row). A full rebuild takes ~0.5 s on this corpus, so "rebuild the whole
thing" IS the incremental strategy.

Scope: database rows only — contracts (narrative + activity phrase + linked
place names), sub-contracts (narrative), and people (all name parts plus
husband names, professions, and residences from their investor rows, which is
what lets a search for "battiloro" find the gold-beaters themselves).

The frozen Word summaries are deliberately NOT indexed (WH decision,
2026-06-12; see LOG.md): they mirror the DB `document` text for attached
entries, so their content is already reachable through the DB twin; word-only
acts are enumerated in the Reconcile Investigate worklist instead. Revisit only
if searching deleted readings or word-only acts becomes a real need.

Search-time matching is diacritic-insensitive (`unicode61 remove_diacritics 2`)
— a FINDING aid only; historical spelling variants (Pagolo/Paolo) are NOT
bridged, by design: that would be interpretation, and interpretation stays with
the human reviewer.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

SNIPPET_OPEN = "«"  # «
SNIPPET_CLOSE = "»"  # »

KIND_LABELS = {
    "contract": "Contracts",
    "sub_contract": "Acts (sub-contracts)",
    "person": "People",
}
KIND_ORDER = ["contract", "sub_contract", "person"]

# Most `document` values store LITERAL backslash escapes (\r\n) from the legacy
# export; normalize both those and real control characters to spaces for the
# index text (display cleaning elsewhere is unchanged — this never writes back).
_LINEBREAKS = re.compile(r"\\r\\n|\\n|\\r|[\r\n\t]+")


def default_path(main_db_path: Path) -> Path:
    raw = os.getenv("FLORACCO_SEARCH_DB_PATH")
    return Path(raw) if raw else main_db_path.parent / "search.db"


def _clean(text: Any) -> str:
    return _LINEBREAKS.sub(" ", str(text or "")).strip()


def build(main_db_path: Path, search_db_path: Path | None = None) -> dict[str, Any]:
    """(Re)build the whole index from main.db. Fast enough to be the only mode."""
    search_db_path = search_db_path or default_path(main_db_path)
    src = sqlite3.connect(f"file:{main_db_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    search_db_path.parent.mkdir(parents=True, exist_ok=True)
    idx = sqlite3.connect(search_db_path)
    try:
        idx.executescript(
            """
            DROP TABLE IF EXISTS search_index;
            DROP TABLE IF EXISTS search_meta;
            CREATE VIRTUAL TABLE search_index USING fts5(
              kind UNINDEXED, ref UNINDEXED, title, body, meta UNINDEXED,
              tokenize = "unicode61 remove_diacritics 2", prefix = '2 3'
            );
            CREATE TABLE search_meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        rows: list[tuple[str, str, str, str, str]] = []

        activities = {
            r["ec_activity_id"]: r["activity"] for r in src.execute("SELECT * FROM economic_activity")
        }
        contract_places: dict[int, list[str]] = {}
        for r in src.execute(
            "SELECT cp.contract_id, p.place_name FROM contract_place cp JOIN place p ON p.place_id = cp.place_id"
        ):
            contract_places.setdefault(r["contract_id"], []).append(r["place_name"])

        for r in src.execute("SELECT * FROM contract WHERE is_deleted = 0"):
            body = " ".join(
                filter(
                    None,
                    [
                        _clean(r["document"]),
                        activities.get(r["economic_sector"]),
                        " ".join(contract_places.get(r["contract_id"], [])),
                    ],
                )
            )
            date = str(r["registration_date"] or "")
            meta_parts = [
                date if date not in ("", "0000-00-00") else "no date",
                f"c. {str(r['folio'] or '').strip()}" if str(r["folio"] or "").strip() else "",
                f"reg. {str(r['folder'] or '').strip()}" if str(r["folder"] or "").strip() else "",
            ]
            rows.append(
                (
                    "contract",
                    str(r["contract_id"]),
                    (r["firm_name"] or "").strip(),
                    body,
                    " · ".join(p for p in meta_parts if p),
                )
            )

        for r in src.execute("SELECT * FROM sub_contract WHERE is_deleted = 0"):
            meta_parts = [
                str(r["sub_type"] or "").strip(),
                str(r["registration_date"] or ""),
                f"c. {str(r['folio'] or '').strip()}" if str(r["folio"] or "").strip() else "",
                f"reg. {str(r['folder'] or '').strip()}" if str(r["folder"] or "").strip() else "",
            ]
            rows.append(
                (
                    "sub_contract",
                    str(r["contract_id"]),
                    (r["sub_firm_name"] or "").strip(),
                    _clean(r["document"]),
                    " · ".join(p for p in meta_parts if p),
                )
            )

        person_extra: dict[int, str] = {}
        for r in src.execute(
            """SELECT i.person_id,
                      group_concat(DISTINCT i.profession) pr,
                      group_concat(DISTINCT i.husband_first_name || ' ' || i.husband_last_name) hb,
                      group_concat(DISTINCT p.place_name) pl
               FROM investor i LEFT JOIN place p ON p.place_id = i.place_of_residence
               WHERE i.is_deleted = 0
               GROUP BY i.person_id"""
        ):
            person_extra[r["person_id"]] = " ".join(filter(None, [r["pr"], r["hb"], r["pl"]]))
        person_contracts = {
            r["person_id"]: r["n"]
            for r in src.execute(
                "SELECT person_id, count(DISTINCT contract_id) n FROM investor WHERE is_deleted = 0 GROUP BY person_id"
            )
        }
        for r in src.execute("SELECT * FROM person WHERE is_deleted = 0"):
            name = " ".join(
                filter(
                    None,
                    [r["first_name"], r["father_mother"], r["grandfather"], r["last_name"], r["nickname"]],
                )
            ).strip()
            n = person_contracts.get(r["person_id"], 0)
            rows.append(
                (
                    "person",
                    str(r["person_id"]),
                    name,
                    f"{name} {person_extra.get(r['person_id'], '')}".strip(),
                    f"appears on {n} contract{'s' if n != 1 else ''}",
                )
            )

        with idx:
            idx.executemany(
                "INSERT INTO search_index (kind, ref, title, body, meta) VALUES (?,?,?,?,?)", rows
            )
            idx.execute(
                "INSERT INTO search_meta (key, value) VALUES ('main_mtime', ?)",
                (str(os.path.getmtime(main_db_path)),),
            )
            idx.execute("INSERT INTO search_meta (key, value) VALUES ('rows', ?)", (str(len(rows)),))
        return {"rows": len(rows), "path": str(search_db_path)}
    finally:
        idx.close()
        src.close()


def is_stale(main_db_path: Path, search_db_path: Path | None = None) -> bool:
    """Stale when the index predates main.db's last write (any applied
    correction, hide, or create touches the file's mtime)."""
    search_db_path = search_db_path or default_path(main_db_path)
    if not search_db_path.exists():
        return True
    try:
        idx = sqlite3.connect(f"file:{search_db_path}?mode=ro", uri=True)
        try:
            row = idx.execute("SELECT value FROM search_meta WHERE key = 'main_mtime'").fetchone()
        finally:
            idx.close()
        return row is None or row[0] != str(os.path.getmtime(main_db_path))
    except sqlite3.Error:
        return True


def ensure_fresh(main_db_path: Path, search_db_path: Path | None = None) -> Path:
    search_db_path = search_db_path or default_path(main_db_path)
    if is_stale(main_db_path, search_db_path):
        build(main_db_path, search_db_path)
    return search_db_path


def fts_query(user_input: str) -> str:
    """User text → safe FTS5 MATCH expression.

    Quoted spans become exact phrases; every other token is double-quoted (so
    apostrophes, parentheses, and FTS keywords cannot break the parser, and AND
    is implicit); the final unquoted token gets a `*` so results refine while
    typing. Returns "" when nothing searchable remains.
    """
    phrases = re.findall(r'"([^"]+)"', user_input)
    rest = re.sub(r'"[^"]*"?', " ", user_input)
    tokens = [t for t in re.findall(r"[\wÀ-ɏ]+", rest) if t]
    parts = [f'"{p}"' for p in (phrase.strip() for phrase in phrases) if p]
    if tokens:
        parts.extend(f'"{t}"' for t in tokens[:-1])
        parts.append(f'"{tokens[-1]}"*')
    return " ".join(parts)


def search(
    search_db_path: Path,
    user_input: str,
    *,
    per_kind: int = 5,
    expand_kind: str | None = None,
) -> dict[str, Any]:
    """Grouped results + counts; per-term counts when an AND query finds nothing
    (an honest empty state: "seta: 1,200 · lucca: 82 · together: 0")."""
    match = fts_query(user_input)
    if not match:
        return {"query": "", "total": 0, "groups": [], "term_counts": None}
    idx = sqlite3.connect(f"file:{search_db_path}?mode=ro", uri=True)
    idx.row_factory = sqlite3.Row
    try:
        groups = []
        total = 0
        for kind in KIND_ORDER:
            count = idx.execute(
                "SELECT count(*) FROM search_index WHERE search_index MATCH ? AND kind = ?",
                (match, kind),
            ).fetchone()[0]
            total += count
            limit = 50 if expand_kind == kind else per_kind
            results = [
                dict(r)
                for r in idx.execute(
                    """SELECT kind, ref, title, meta,
                              snippet(search_index, 3, ?, ?, '…', 12) AS snippet
                       FROM search_index
                       WHERE search_index MATCH ? AND kind = ?
                       ORDER BY bm25(search_index, 0, 0, 5.0, 1.0)
                       LIMIT ?""",
                    (SNIPPET_OPEN, SNIPPET_CLOSE, match, kind, limit),
                )
            ]
            groups.append(
                {"kind": kind, "label": KIND_LABELS[kind], "total": count, "results": results}
            )
        term_counts = None
        terms = [t for t in re.findall(r"[\wÀ-ɏ]+", user_input) if len(t) > 1]
        if total == 0 and len(terms) > 1:
            term_counts = [
                {
                    "term": term,
                    "count": idx.execute(
                        "SELECT count(*) FROM search_index WHERE search_index MATCH ?",
                        (f'"{term}"',),
                    ).fetchone()[0],
                }
                for term in terms[:6]
            ]
        return {"query": match, "total": total, "groups": groups, "term_counts": term_counts}
    finally:
        idx.close()
