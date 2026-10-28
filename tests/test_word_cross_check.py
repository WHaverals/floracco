"""Tests for the curated Word↔DB registration-date cross-check (read-only).

Each gate is exercised on a seeded fixture: reliable-link, real-difference (live DB),
apparatus-suppression, calendar normalization, tier assignment, and self-healing.

    uv run --with pytest pytest tests/test_word_cross_check.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from workflows import word_cross_check as wcc


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _fixture(tmp_path: Path):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, registration_date TEXT,
            is_deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE sub_contract (contract_id INTEGER PRIMARY KEY, registration_date TEXT,
            is_deleted INTEGER NOT NULL DEFAULT 0);
        INSERT INTO contract (contract_id, registration_date) VALUES
            (184,'1534-01-19'), (1083,'1583-06-16'), (401,'1467-02-11'),
            (900,'1745-03-30'), (901,'1512-04-06'), (902,'1526-08-09');
        INSERT INTO sub_contract (contract_id, registration_date) VALUES (500,'1520-03-25');
        """
    )
    conn.commit()

    def L(rid, foc, word, eid, folio, score=100):
        return {"db_row_id": rid, "db_table": rid.split(":")[0], "conflicts": ["registration_date_differs"],
                "field_overlap_count": foc, "entry_registration_date_raw": word, "source_entry_id": eid,
                "entry_folio_start": folio, "score": score}
    links = [
        L("contract:184", 3, "20 marzo 1533/34", "E184", "8r"),       # T1 tracked-change
        L("contract:1083", 4, "11 giugno 1583", "E1083", "137r"),     # T2 clear (gap 5)
        L("contract:401", 2, "12 febbraio 1466/67", "E401", "63r"),   # T3 one-day (gap 1) -> held
        L("contract:900", 3, "30 marzo 1744", "E900", "47r"),         # apparatus (sic, ma 1745) -> drop
        L("contract:901", 1, "5 aprile 1512", "E901", "12r"),         # weak link (1 name) -> drop
        L("contract:902", 3, "9 agosto 1526", "E902", "20r"),         # self-healed (DB==Word) -> drop
        L("sub_contract:500", 2, "28 marzo 1520", "E500", "178v"),    # T2 clear, verso, sub_contract
    ]
    def E(eid, head, folio, rev=None):
        return {"source_entry_id": eid, "head_text": head, "folio_raw": folio,
                "has_revisions": bool(rev), "revision_aware_text": rev or ""}
    entries = [
        E("E184", "Al nome di dio adì 20 di marzo 1533", "8r",
          'adì <DEL author="Matteo">19 gennaio</DEL> <INS author="Matteo">20 marzo</INS> 1533/34'),
        E("E1083", "Addì 11 di giugno 1583", "137r"),
        E("E401", "adì XII di febbraio 1466", "63r"),
        E("E900", "30 marzo 1744 (sic, ma 1745)", "47r"),
        E("E901", "adì 5 di aprile 1512", "12r"),
        E("E902", "adì 9 di agosto 1526", "20r"),
        E("E500", "adì 28 di marzo 1520", "178v"),
    ]
    images = [{"source_entry_id": "E184", "image_path": str(tmp_path / "img_008.jpg")}]
    _write(tmp_path / "links.jsonl", links)
    _write(tmp_path / "entries.jsonl", entries)
    _write(tmp_path / "images.jsonl", images)
    return conn, dict(links_path=tmp_path / "links.jsonl",
                      entries_path=tmp_path / "entries.jsonl",
                      images_path=tmp_path / "images.jsonl")


def test_gates_select_the_right_rows(tmp_path):
    conn, paths = _fixture(tmp_path)
    checks = wcc.build_checks(conn, **paths)
    # kept: 184, 1083, 401, sub:500 ; dropped: 900 (apparatus), 901 (weak link), 902 (self-healed)
    assert set(checks) == {"contract:184", "contract:1083", "contract:401", "sub_contract:500"}


def test_tiers_and_surfacing(tmp_path):
    conn, paths = _fixture(tmp_path)
    checks = wcc.build_checks(conn, **paths)
    assert checks["contract:184"]["tier"] == "tracked_change"
    assert checks["contract:1083"]["tier"] == "clear"
    assert checks["contract:401"]["tier"] == "one_day"
    # T3 computed but not surfaced
    assert checks["contract:401"]["surfaced"] is False
    surfaced = wcc.surfaced_checks(checks)
    assert set(surfaced) == {"contract:184", "contract:1083", "sub_contract:500"}


def test_tracked_change_evidence(tmp_path):
    conn, paths = _fixture(tmp_path)
    rev = wcc.build_checks(conn, **paths)["contract:184"]["revision"]
    assert rev is not None
    assert any("19 gennaio" in s for s in rev["removed"])
    assert any("20 marzo" in s for s in rev["added"])
    assert rev["author"] == "Matteo"


def test_calendar_normalization_and_display(tmp_path):
    conn, paths = _fixture(tmp_path)
    c = wcc.build_checks(conn, **paths)["contract:184"]
    assert c["word_iso"] == "1534-03-20"          # stile fiorentino 1533/34 -> 1534
    assert c["word_display"] == "20 Mar 1534"
    assert c["db_display"] == "19 Jan 1534"
    c2 = wcc.build_checks(conn, **paths)["contract:1083"]
    assert c2["word_display"] == "11 Jun 1583" and c2["db_display"] == "16 Jun 1583"
    assert c2["gap_days"] == 5


def test_page_side_and_table(tmp_path):
    conn, paths = _fixture(tmp_path)
    checks = wcc.build_checks(conn, **paths)
    assert checks["contract:184"]["page_side"] == "recto"
    assert checks["sub_contract:500"]["page_side"] == "verso"
    assert checks["sub_contract:500"]["table"] == "sub_contract"


def test_self_heals_when_db_already_matches(tmp_path):
    conn, paths = _fixture(tmp_path)
    # fix 1083 in the DB to the Word reading → the conflict must disappear on recompute
    conn.execute("UPDATE contract SET registration_date='1583-06-11' WHERE contract_id=1083")
    conn.commit()
    assert "contract:1083" not in wcc.build_checks(conn, **paths)


def test_impossible_stored_date_skips_only_its_row(tmp_path):
    """UI review 2026-10-26: a typo such as 1583-02-30 saved on a linked contract
    made build_checks raise, and Needs review then silently dropped the whole
    'Word summary gives another date' group. Now only that row is skipped."""
    conn, paths = _fixture(tmp_path)
    conn.execute("UPDATE contract SET registration_date = '1583-02-30' WHERE contract_id = 1083")
    checks = wcc.build_checks(conn, **paths)
    assert "contract:1083" not in checks
    assert {"contract:184", "contract:401", "sub_contract:500"} <= set(checks)
