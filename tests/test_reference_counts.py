"""What a Reference term's numbers count (UI review 2026-10-14, item 20).

Livorno showed 1,270 in the rail, 661 on its page and 578 in Analysis. They
measured different things: times recorded (place of activity + partners'
residences + origins), distinct contracts, places of activity. The rail also
counted links on hidden contracts and on contract numbers that do not exist.
Every Reference number now counts contracts (WH, 2026-10-26): these tests pin
that the rail, the term page and the phrase picker give the same number of
live contracts, that each part counts contracts too (a contract can fall under
several parts, so the parts can add up to more), and that only live contracts
count.

    uv run --with pytest pytest tests/test_reference_counts.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workflows import corrections_db, review_server

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"

# place 7 'Livorno': place of activity on live c1; residence of investors 10 and 11
# and origin of investor 10 on c1; residence of investor 30 on live c3; place of
# activity and a residence on HIDDEN c2; a place link on contract 999, which does
# not exist. Title 3 'signor': investor 10's own title and investor 11's
# father/mother title on c1, and investor 20's own title on hidden c2.
_SEED = """
INSERT INTO place (place_id, place_name) VALUES (7, 'Livorno');
INSERT INTO title (title_id, title_name) VALUES (3, 'signor');
INSERT INTO person (person_id, first_name, last_name) VALUES (1,'Piero','Uno'),(2,'Luca','Due'),(3,'Ugo','Tre');
INSERT INTO contract (contract_id, registration_date, firm_name, document) VALUES
  (1, '1600-01-01', 'Viva', 'compagni in Livorno'),
  (2, '1650-01-01', 'Nascosta', 'in Livorno'),
  (3, '1610-01-01', 'Terza', 'compagni');
UPDATE contract SET is_deleted = 1 WHERE contract_id = 2;
INSERT INTO sub_contract (contract_id, archive, series, folder, folio, registration_date, sub_type,
  main_contract_id, document) VALUES (90, 'ASF', 'M', '10831', '1r', '1601-01-01', 'termination', 1, 'mercante in Livorno');
INSERT INTO contract_place (place_id, contract_id) VALUES (7, 1), (7, 2), (7, 999);
INSERT INTO investor (investor_id, person_id, contract_id, place_of_residence, place_of_origin, title, title_father_mother)
  VALUES (10, 1, 1, 7, 7, 3, NULL), (11, 2, 1, 7, NULL, NULL, 3), (20, 3, 2, 7, NULL, 3, NULL),
  (30, 3, 3, 7, NULL, NULL, NULL);
"""


@pytest.fixture()
def conn(tmp_path, monkeypatch) -> sqlite3.Connection:
    main = str(tmp_path / "main.db")
    c = sqlite3.connect(main)
    c.row_factory = sqlite3.Row
    c.executescript(_TABLES_SQL.read_text())
    for table in corrections_db.ALL_TABLE_PRIMARY_KEYS:
        c.execute(f"ALTER TABLE `{table}` ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    c.executescript(_SEED)
    c.commit()

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(tmp_path / "corr.db"))
    yield c
    c.close()


def _rail_count(kind: str, term_id: int) -> int:
    out = review_server.db_reference(kind, q="", sort="usage", offset=0, limit=60, orphans_only=False)
    return next(t["count"] for t in out["terms"] if t["id"] == term_id)


def test_place_rail_counts_live_contracts_and_matches_term_page(conn) -> None:
    detail = review_server.db_reference_records("place", 7, offset=0, limit=120)
    # Each part counts contracts: c1's two residences count once; c3 adds one.
    assert detail["usage_parts"] == {"activity": 1, "residence": 2, "origin": 1}
    # c1 records Livorno three ways and counts once; with c3 that is 2 contracts.
    # Hidden c2's place link and residence, and the link on contract 999, are left out.
    assert _rail_count("place", 7) == 2 == detail["usage_total"] == detail["record_total"]


def test_title_rail_counts_contracts_across_every_title_field(conn) -> None:
    detail = review_server.db_reference_records("title", 3, offset=0, limit=120)
    assert detail["usage_parts"] == {"title": 1, "title_father_mother": 1}
    # Both uses are on c1, which counts once; hidden c2 is left out.
    assert _rail_count("title", 3) == 1 == detail["usage_total"] == detail["record_total"]


def test_narrative_count_keeps_contracts_and_sub_contracts_apart(conn) -> None:
    detail = review_server.db_reference_records("place", 7, offset=0, limit=120)
    # 'Livorno' in live c1 and in sub-contract 90; hidden c2 is left out.
    assert detail["narrative_contracts"] == 1
    assert detail["narrative_sub_contracts"] == 1
    assert detail["narrative_mentions"] == 2


def test_phrase_picker_counts_like_the_reference_page(conn) -> None:
    """The phrase picker's "used on N contracts" is the Reference rail's number."""
    assert review_server.db_lookup("place", q="Livorno")["exact"]["used"] == _rail_count("place", 7) == 2
    assert review_server.db_lookup("title", q="signor")["exact"]["used"] == _rail_count("title", 3) == 1
