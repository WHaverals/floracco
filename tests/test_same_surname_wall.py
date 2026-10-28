"""The same-surname wall states its true size (UI review 2026-10-14, item 5).

Before a new person is created, the add-partner panel lists existing persons
with the same surname. The server capped that list at 12 and gave no total,
so for a surname shared by 86 persons the panel said "12 existing persons".
It now returns the uncapped total and lists persons sharing the typed first
name first, so the right person is more likely to be among those shown.

    uv run --with pytest pytest tests/test_same_surname_wall.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workflows import corrections_db, review_server

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"


@pytest.fixture()
def conn(tmp_path, monkeypatch) -> sqlite3.Connection:
    main = str(tmp_path / "main.db")
    c = sqlite3.connect(main)
    c.row_factory = sqlite3.Row
    c.executescript(_TABLES_SQL.read_text())
    for table in corrections_db.ALL_TABLE_PRIMARY_KEYS:
        c.execute(f"ALTER TABLE `{table}` ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    # 14 live persons named Franceschi (two of them Luca), one hidden one, one Rossi.
    people = [(100 + i, f"Nome{i}", "Franceschi") for i in range(12)]
    people += [(200, "Luca", "Franceschi"), (201, "Lúca", "FRANCESCHI"), (300, "Luca", "Franceschi"), (400, "Luca", "Rossi")]
    c.executemany("INSERT INTO person (person_id, first_name, last_name) VALUES (?, ?, ?)", people)
    c.execute("UPDATE person SET is_deleted = 1 WHERE person_id = 300")
    c.execute("INSERT INTO contract (contract_id, registration_date, firm_name) VALUES (1, '1600-01-01', 'Viva')")
    # Person 100 appears on a contract, so it ranks first when no first name is given.
    c.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (1, 100, 1)")
    c.commit()

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "_person_hits_with_identity", lambda hits: hits)
    yield c
    c.close()


def test_total_is_uncapped_and_hidden_persons_are_left_out(conn) -> None:
    out = review_server.same_surname(last_name="Franceschi")
    assert out["total"] == 14
    assert len(out["results"]) == 12
    assert "300" not in {h["person_id"] for h in out["results"]}
    # Without a first name the order is unchanged: most contracts first.
    assert out["results"][0]["person_id"] == "100"


def test_typed_first_name_comes_first(conn) -> None:
    out = review_server.same_surname(last_name="franceschi", first_name="LUCA")
    # Case- and accent-insensitive, like the surname match.
    assert {h["person_id"] for h in out["results"][:2]} == {"200", "201"}
    assert out["total"] == 14


def test_empty_surname(conn) -> None:
    assert review_server.same_surname(last_name="") == {"results": [], "total": 0}
