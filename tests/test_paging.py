"""Long result lists reach their end (UI review 2026-10-14, items 4 and 7).

Explore's "Show all N" used to stop at 50 hits with the button still on
screen; a Reference term listed its first 120 contracts with no way to the
rest. Both now page in a stable order, so a longer page always starts with
the shorter one and nothing repeats or goes missing at a page boundary.

    uv run --with pytest pytest tests/test_paging.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workflows import corrections_db, review_server, search_index

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"

_SEARCH_SCHEMA = """
    CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, archive TEXT, series TEXT,
      folder TEXT, folio TEXT, registration_date TEXT, firm_name TEXT, economic_sector INTEGER,
      document TEXT, is_deleted INTEGER DEFAULT 0);
    CREATE TABLE sub_contract (contract_id INTEGER PRIMARY KEY, main_contract_id INTEGER,
      sub_type TEXT, folder TEXT, folio TEXT, registration_date TEXT, sub_firm_name TEXT,
      document TEXT, is_deleted INTEGER DEFAULT 0);
    CREATE TABLE person (person_id INTEGER PRIMARY KEY, first_name TEXT, father_mother TEXT,
      grandfather TEXT, last_name TEXT, nickname TEXT, is_deleted INTEGER DEFAULT 0);
    CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, person_id INTEGER, contract_id INTEGER,
      profession TEXT, husband_first_name TEXT, husband_last_name TEXT, place_of_residence INTEGER,
      is_deleted INTEGER DEFAULT 0);
    CREATE TABLE investment (investment_id INTEGER PRIMARY KEY);
    CREATE TABLE contract_place (place_id INTEGER, contract_id INTEGER, is_deleted INTEGER DEFAULT 0);
    CREATE TABLE place (place_id INTEGER PRIMARY KEY, place_name TEXT);
    CREATE TABLE economic_activity (ec_activity_id INTEGER PRIMARY KEY, activity TEXT);
"""


@pytest.fixture()
def search_db(tmp_path) -> Path:
    """60 contracts sharing one word (all tied on score), plus 2 people."""
    main = tmp_path / "main.db"
    con = sqlite3.connect(main)
    con.executescript(_SEARCH_SCHEMA)
    con.executemany(
        "INSERT INTO contract (contract_id, folder, folio, registration_date, firm_name, document) "
        "VALUES (?, '10831', ?, '1600-01-01', ?, 'ricevuto in accomandita zibellini')",
        [(1000 + i, f"{i}r", f"Compagnia {i}") for i in range(60)],
    )
    con.executemany(
        "INSERT INTO person (person_id, first_name, last_name) VALUES (?, ?, 'Zibellini')",
        [(1, "Piero"), (2, "Luca")],
    )
    con.commit()
    con.close()
    search_index.build(main)
    return main


def _group(out: dict, kind: str) -> dict:
    return next(g for g in out["groups"] if g["kind"] == kind)


def test_search_expand_limit_pages_past_fifty(search_db) -> None:
    sp = search_index.default_path(search_db)

    collapsed = _group(search_index.search(sp, "zibellini"), "contract")
    assert collapsed["total"] == 60 and len(collapsed["results"]) == 5

    first = _group(search_index.search(sp, "zibellini", expand_kind="contract"), "contract")
    assert len(first["results"]) == 50  # the old default is unchanged

    more = search_index.search(sp, "zibellini", expand_kind="contract", expand_limit=55)
    longer = _group(more, "contract")
    assert len(longer["results"]) == 55
    # Tied scores keep their order: the longer list starts with the shorter one.
    assert [r["ref"] for r in longer["results"][:50]] == [r["ref"] for r in first["results"]]
    assert _group(more, "person")["results"] and len(_group(more, "person")["results"]) <= 5

    everything = _group(search_index.search(sp, "zibellini", expand_kind="contract", expand_limit=10_000), "contract")
    assert len(everything["results"]) == 60 == everything["total"]
    assert len({r["ref"] for r in everything["results"]}) == 60

    # A nonsense limit falls back to the collapsed size instead of reaching SQLite.
    silly = _group(search_index.search(sp, "zibellini", expand_kind="contract", expand_limit=-3), "contract")
    assert len(silly["results"]) == 5


def test_global_search_passes_limit_through(search_db, monkeypatch) -> None:
    monkeypatch.setattr(review_server, "db_path", lambda: search_db)
    assert len(_group(review_server.global_search(q="zibellini", expand="contract", limit=55), "contract")["results"]) == 55
    assert len(_group(review_server.global_search(q="zibellini", expand="contract"), "contract")["results"]) == 50
    assert len(_group(review_server.global_search(q="zibellini", expand="bogus", limit=55), "contract")["results"]) == 5


# --- Reference: a term's contracts, page by page --------------------------------

_REF_SEED = """
INSERT INTO place (place_id, place_name) VALUES (7, 'Livorno');
INSERT INTO contract (contract_id, registration_date, firm_name) VALUES
  (1, '1600-01-01', 'Prima'),
  (2, '1600-01-01', 'Seconda'),
  (3, '1590-05-05', 'Terza'),
  (4, '0000-00-00', 'Senza data'),
  (5, '', 'Senza data due'),
  (6, '1610-02-02', 'Nascosta');
UPDATE contract SET is_deleted = 1 WHERE contract_id = 6;
INSERT INTO contract_place (place_id, contract_id) VALUES (7,1),(7,2),(7,3),(7,4),(7,5),(7,6);
"""


@pytest.fixture()
def ref_conn(tmp_path, monkeypatch) -> sqlite3.Connection:
    main = str(tmp_path / "main.db")
    c = sqlite3.connect(main)
    c.row_factory = sqlite3.Row
    c.executescript(_TABLES_SQL.read_text())
    for table in corrections_db.ALL_TABLE_PRIMARY_KEYS:
        c.execute(f"ALTER TABLE `{table}` ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    c.executescript(_REF_SEED)
    c.commit()

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    yield c
    c.close()


def test_reference_records_pages_in_date_order(ref_conn) -> None:
    whole = review_server.db_reference_records("place", 7, offset=0, limit=500)
    # Dated first by date, a tie broken by number, placeholder dates last; hidden 6 never shows.
    ids = [r["id"] for r in whole["records"]]
    assert ids[:3] == ["3", "1", "2"]
    assert set(ids[3:]) == {"4", "5"}
    assert whole["record_total"] == 5

    pages = []
    for offset in (0, 2, 4):
        page = review_server.db_reference_records("place", 7, offset=offset, limit=2)
        assert page["record_total"] == whole["record_total"]
        assert page["by_decade"] == whole["by_decade"]
        assert page["offset"] == offset
        pages.extend(r["id"] for r in page["records"])
    assert pages == [r["id"] for r in whole["records"]]

    past_end = review_server.db_reference_records("place", 7, offset=5, limit=2)
    assert past_end["records"] == [] and past_end["record_total"] == 5


def test_undated_sub_contract_reads_no_date_and_old_index_rebuilds(tmp_path) -> None:
    """UI review 2026-10-14, item 16: an undated sub-contract's search line read
    "termination · 0000-00-00 · …". It now reads "no date", like contracts, and
    an index written by an older version is rebuilt once (format key)."""
    import sqlite3

    main = tmp_path / "main.db"
    con = sqlite3.connect(main)
    con.executescript(_SEARCH_SCHEMA)
    con.execute(
        "INSERT INTO sub_contract (contract_id, sub_type, folder, folio, registration_date, sub_firm_name, document) "
        "VALUES (3667, 'termination', '10831', '5r', '0000-00-00', '', 'disdetta della compagnia')"
    )
    con.commit()
    con.close()
    search_index.build(main)
    sp = search_index.default_path(main)
    hit = _group(search_index.search(sp, "disdetta"), "sub_contract")["results"][0]
    assert "no date" in hit["meta"] and "0000" not in hit["meta"]
    assert not search_index.is_stale(main, sp)

    idx = sqlite3.connect(sp)
    idx.execute("DELETE FROM search_meta WHERE key = 'format'")  # as built before the format key
    idx.commit()
    idx.close()
    assert search_index.is_stale(main, sp)


def test_separately_counts_the_last_word_as_a_prefix(tmp_path) -> None:
    """UI review item 6, word case: "salviati battil" finds nothing together, and the
    "Separately" line must count "battil" the way the search does (as a prefix)."""
    import sqlite3

    main = tmp_path / "main.db"
    con = sqlite3.connect(main)
    con.executescript(_SEARCH_SCHEMA)
    con.execute(
        "INSERT INTO contract (contract_id, folder, folio, registration_date, firm_name, document) "
        "VALUES (100, '10831', '21r', '1451-03-05', 'Niccolò Salviati e compagni', 'fiorini 400')"
    )
    con.execute("INSERT INTO person (person_id, first_name, last_name) VALUES (7, 'Antonio', 'Sangallo')")
    con.execute("INSERT INTO investor (investor_id, person_id, contract_id, profession) VALUES (1, 7, 999, 'battiloro')")
    con.commit()
    con.close()
    search_index.build(main)
    sp = search_index.default_path(main)
    out = search_index.search(sp, "salviati battil")
    assert out["total"] == 0
    assert {t["term"]: t["count"] for t in out["term_counts"]} == {"salviati": 1, "battil": 1}
    # A quoted last word stays exact, as in the search itself.
    out = search_index.search(sp, 'salviati "battil"')
    assert {t["term"]: t["count"] for t in out["term_counts"]}["battil"] == 0
    # The prefix belongs to the last unquoted word even when it is too short to be
    # listed: in "salviati battil x" the search takes "battil" exactly, so does the count.
    out = search_index.search(sp, "salviati battil x")
    assert out["total"] == 0
    assert {t["term"]: t["count"] for t in out["term_counts"]} == {"salviati": 1, "battil": 0}
