"""Fences for the Analysis figures that were wrong without warning (review
2026-10-27, recommendation 1; docs/analysis/review_2026-10-27.md 4.1 and 4.2).

Each test fails on the code as it stood at df743e3:
- bar charts cut ranked results at 20 silently, scaled to the visible bars and
  closed up the periods with no contracts;
- builder lists wrote their own LIMIT 5000, so "(capped)" never showed, and the
  CSV held only what reached the screen; "Stated initial term" kept 15 of 75;
- the console built rows by column name, repeating the first of two same-named
  columns;
- "Joint" read the stored flag alone;
- "Registration year ≤" counted the undated contracts as early;
- "Title contains" could not reach the profession;
- "Possible relatives" paired one person entered twice with itself.

(The hidden-row sweep lives in tests/test_hidden_sweep.py; the Jewish
provenance column and the funding split in test_analysis_truth.py and
test_analysis_library.py.)

    uv run --with pytest pytest tests/test_analysis_silent_errors.py
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, review_server
from workflows.review_server import ANALYSIS_LIBRARY, build_analysis_sql, run_readonly_sql

ROOT = Path(__file__).resolve().parents[1]
_TABLES_SQL = ROOT / "queries" / "schema" / "tables.sql"


def _sql(query_id: str) -> str:
    return next(q["sql"] for q in ANALYSIS_LIBRARY if q["id"] == query_id)


def _make_db(path: Path, seed: str) -> Path:
    c = sqlite3.connect(path)
    c.executescript(_TABLES_SQL.read_text())
    for table in corrections_db.ALL_TABLE_PRIMARY_KEYS:
        c.execute(f"ALTER TABLE `{table}` ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    c.executescript(seed)
    c.commit()
    c.close()
    return path


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A fresh corpus file the read-only executor runs on; returns a seeder."""
    path = tmp_path / "main.db"
    monkeypatch.setattr(review_server, "db_path", lambda: path)

    def seed(sql: str) -> sqlite3.Connection:
        _make_db(path, sql)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return seed


# ---- 3. the console reads columns by position -------------------------------

def test_two_columns_with_one_name_keep_their_own_values(db):
    # A sub-contract beside its parent: the review's "3, 3" where the database holds 3 and 69.
    db("""
    INSERT INTO contract (contract_id, registration_date) VALUES (3,'1500-01-01');
    INSERT INTO sub_contract (contract_id, archive, series, folder, folio, main_contract_id, registration_date)
      VALUES (69,'ASF','Mercanzia','10831','2r',3,'1502-06-01');
    """)
    out = run_readonly_sql(
        "SELECT s.contract_id, c.contract_id, s.registration_date, c.registration_date "
        "FROM sub_contract s JOIN contract c ON c.contract_id = s.main_contract_id"
    )
    assert out["columns"] == ["contract_id", "contract_id", "registration_date", "registration_date"]
    assert out["rows"] == [[69, 3, "1502-06-01", "1500-01-01"]]


# ---- 2. caps are announced with the true total; downloads carry every row ----

def _download(response) -> str:
    async def read():
        chunks = [chunk async for chunk in response.body_iterator]
        return "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)

    return asyncio.run(read())


def _seed_contracts(db, n: int):
    db("INSERT INTO contract (contract_id, registration_date) VALUES "
       + ",".join(f"({k},'1600-01-01')" for k in range(1, n + 1)) + ";")


def test_capped_result_reports_the_true_total(db, monkeypatch):
    monkeypatch.setattr(review_server, "ANALYSIS_ROW_CAP", 3)
    _seed_contracts(db, 7)
    out = run_readonly_sql("SELECT contract_id FROM contract ORDER BY contract_id")
    assert out["truncated"] is True
    assert out["row_count"] == 3 and len(out["rows"]) == 3
    assert out["total_rows"] == 7 and out["total_more_than"] is None
    small = run_readonly_sql("SELECT contract_id FROM contract WHERE contract_id < 3")
    assert small["truncated"] is False and small["total_rows"] == 2


def test_counting_stops_just_past_the_download_ceiling(db, monkeypatch):
    # A big capped result no longer runs to the time limit to be counted.
    monkeypatch.setattr(review_server, "ANALYSIS_ROW_CAP", 3)
    monkeypatch.setattr(review_server, "ANALYSIS_EXPORT_CAP", 10)
    _seed_contracts(db, 7)
    out = run_readonly_sql("SELECT a.contract_id FROM contract a, contract b")  # 49 rows
    assert out["total_rows"] is None and out["total_more_than"] == 10


def test_a_capped_screen_and_its_download_hold_every_one_of_5001_rows(db):
    # The real cap, no monkeypatching: 5,001 partner appearances, one over.
    rows = ["INSERT INTO contract (contract_id, registration_date) VALUES (1,'1600-01-01');"]
    rows += [f"INSERT INTO person (person_id, first_name, last_name) VALUES ({k},'N','Cognome{k:05d}');"
             f"INSERT INTO investor (investor_id, person_id, contract_id) VALUES ({k},{k},1);" for k in range(1, 5002)]
    db("BEGIN;\n" + "\n".join(rows) + "\nCOMMIT;")
    sql, params, _ = build_analysis_sql({"subject": "investors", "measure": "list"})
    assert "LIMIT" not in sql.upper()
    out = run_readonly_sql(sql, tuple(params))
    assert out["row_count"] == review_server.ANALYSIS_ROW_CAP == 5000
    assert out["truncated"] is True and out["total_rows"] == 5001
    lines = _download(review_server.export_readonly_csv(sql, tuple(params))).splitlines()
    assert lines[0] == "contract_id,year,first_name,last_name" and len(lines) == 1 + 5001
    assert lines[-1] == "1,1600,N,Cognome05001"


@pytest.fixture()
def builder_db(db, tmp_path, monkeypatch):
    """The builder endpoint opens the corpus and the change log itself."""
    def open_db():
        conn = sqlite3.connect(review_server.db_path())
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(tmp_path / "corr.db"))
    return db


def test_builder_download_holds_every_row_past_the_screen_cap(builder_db, monkeypatch):
    monkeypatch.setattr(review_server, "ANALYSIS_ROW_CAP", 2)
    builder_db("""
    INSERT INTO contract (contract_id, registration_date) VALUES (1,'1600-01-01');
    INSERT INTO person (person_id, first_name, last_name) VALUES (1,'A','Uno'),(2,'B','Due'),(3,'C','Tre');
    INSERT INTO investor (investor_id, person_id, contract_id) VALUES (10,1,1),(11,2,1),(12,3,1);
    """)
    spec = {"subject": "investors", "measure": "list", "filters": [], "group_by": ""}
    screen = review_server.analysis_build(dict(spec))
    assert screen["row_count"] == 2 and screen["total_rows"] == 3
    csv_text = _download(review_server.analysis_export(review_server.AnalysisExport(spec=spec)))
    assert csv_text.splitlines() == [
        "contract_id,year,first_name,last_name", "1,1600,B,Due", "1,1600,C,Tre", "1,1600,A,Uno",
    ]


def test_download_keeps_two_columns_with_one_name_apart(db):
    db("""
    INSERT INTO contract (contract_id, registration_date) VALUES (3,'1500-01-01');
    INSERT INTO sub_contract (contract_id, archive, series, folder, folio, main_contract_id, registration_date)
      VALUES (69,'ASF','Mercanzia','10831','2r',3,'1502-06-01');
    """)
    out = review_server.analysis_export(review_server.AnalysisExport(
        sql="SELECT s.contract_id, c.contract_id FROM sub_contract s JOIN contract c ON c.contract_id = s.main_contract_id"))
    assert _download(out).splitlines() == ["contract_id,contract_id", "69,3"]


def test_download_over_the_ceiling_is_refused_before_any_row_is_kept(db, monkeypatch):
    monkeypatch.setattr(review_server, "ANALYSIS_EXPORT_CAP", 10)
    _seed_contracts(db, 7)
    with pytest.raises(HTTPException) as e:
        review_server.analysis_export(review_server.AnalysisExport(
            sql="SELECT a.contract_id FROM contract a, contract b"))  # 49 rows > 10
    assert e.value.status_code == 400 and "too many to download" in e.value.detail


_SLOW = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 50000) SELECT x FROM n"


def test_download_that_runs_out_of_time_is_refused(db, monkeypatch):
    _seed_contracts(db, 1)
    monkeypatch.setattr(review_server, "ANALYSIS_TIMEOUT_S", 0.0)
    with pytest.raises(HTTPException) as e:
        review_server.analysis_export(review_server.AnalysisExport(sql=_SLOW))
    assert e.value.status_code == 400 and "timed out" in e.value.detail


def test_a_save_succeeds_while_a_download_is_read_slowly(db, tmp_path):
    # The file is written and the connection closed before the first byte goes
    # out, so a stalled download never holds the read lock that saves wait on.
    db("INSERT INTO contract (contract_id, registration_date) "
       "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 20000) "
       "SELECT x, '1600-01-01' FROM n;")
    response = review_server.analysis_export(
        review_server.AnalysisExport(sql="SELECT contract_id, registration_date FROM contract"))

    async def read_slowly():
        chunks = response.body_iterator
        first = await chunks.__anext__()
        writer = sqlite3.connect(review_server.db_path(), timeout=0)  # no waiting: a held lock fails at once
        writer.execute("UPDATE contract SET firm_name = 'salvato' WHERE contract_id = 1")
        writer.commit()
        writer.close()
        return first + b"".join([chunk async for chunk in chunks])

    body = asyncio.run(read_slowly()).decode()
    assert len(body.splitlines()) == 1 + 20000


def _client():
    from fastapi.testclient import TestClient

    return TestClient(review_server.app)


def test_download_carries_its_length(db):
    _seed_contracts(db, 7)
    reply = _client().post("/api/analysis/export", json={"sql": "SELECT contract_id FROM contract"})
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/csv")
    assert int(reply.headers["content-length"]) == len(reply.content)
    assert reply.text.splitlines() == ["contract_id", *map(str, range(1, 8))]


def test_a_time_out_while_reading_is_a_400_with_no_file(db, monkeypatch):
    _seed_contracts(db, 1)
    monkeypatch.setattr(review_server, "ANALYSIS_TIMEOUT_S", 0.0)
    reply = _client().post("/api/analysis/export", json={"sql": _SLOW})
    assert reply.status_code == 400 and reply.headers["content-type"].startswith("application/json")
    assert "timed out" in reply.json()["detail"]


@pytest.mark.parametrize("sql", [
    "", "SELECT 1; SELECT 2", "PRAGMA table_info(contract)", "UPDATE contract SET firm_name = 'x'",
    "SELECT 1 WHERE 'x' LIKE '%create%'", "SELECT no_such_column FROM contract",
])
def test_download_refuses_what_the_screen_refuses(db, sql):
    _seed_contracts(db, 1)
    with pytest.raises(HTTPException) as screen:
        run_readonly_sql(sql)
    with pytest.raises(HTTPException) as download:
        review_server.analysis_export(review_server.AnalysisExport(sql=sql))
    assert (download.value.status_code, download.value.detail) == (screen.value.status_code, screen.value.detail)


def _numbers(n: int) -> str:
    return f"WITH RECURSIVE k(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM k WHERE x < {n}) SELECT x FROM k"


def test_the_download_ceiling_is_exactly_100000(db):
    _seed_contracts(db, 1)
    at = run_readonly_sql(_numbers(100_000))
    assert at["total_rows"] == 100_000 and at["total_more_than"] is None
    over = run_readonly_sql(_numbers(100_001))
    assert over["total_rows"] is None and over["total_more_than"] == 100_000
    assert len(_download(review_server.analysis_export(review_server.AnalysisExport(sql=_numbers(100_000))))
               .splitlines()) == 1 + 100_000
    with pytest.raises(HTTPException) as e:
        review_server.analysis_export(review_server.AnalysisExport(sql=_numbers(100_001)))
    assert "too many to download" in e.value.detail


def test_an_endless_query_stops_at_the_ceiling(db):
    # Counting and the download stop at 100,001 rows, long before the time limit.
    _seed_contracts(db, 1)
    endless = "WITH RECURSIVE k(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM k) SELECT x FROM k"
    started = time.monotonic()
    out = run_readonly_sql(endless)
    assert out["total_more_than"] == 100_000
    with pytest.raises(HTTPException) as e:
        review_server.analysis_export(review_server.AnalysisExport(sql=endless))
    assert "too many to download" in e.value.detail
    assert time.monotonic() - started < review_server.ANALYSIS_TIMEOUT_S


@pytest.fixture()
def spools(monkeypatch):
    """Every temporary file the download makes, rolled to disk at once when asked."""
    made, real = [], tempfile.SpooledTemporaryFile
    state = {"max_size": None}

    def spool(max_size=0, *args, **kwargs):
        made.append(real(state["max_size"] if state["max_size"] is not None else max_size, *args, **kwargs))
        return made[-1]

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", spool)
    return made, state


def test_the_temporary_file_closes_when_the_download_is_sent(db, spools):
    made, _ = spools
    _seed_contracts(db, 3)
    reply = _client().post("/api/analysis/export", json={"sql": "SELECT contract_id FROM contract"})
    assert reply.status_code == 200 and len(made) == 1 and made[0].closed


def test_the_temporary_file_closes_when_the_download_is_refused(db, spools, monkeypatch):
    made, _ = spools
    monkeypatch.setattr(review_server, "ANALYSIS_EXPORT_CAP", 2)
    _seed_contracts(db, 3)
    with pytest.raises(HTTPException):
        review_server.analysis_export(review_server.AnalysisExport(sql="SELECT contract_id FROM contract"))
    assert len(made) == 1 and made[0].closed


def test_the_temporary_file_closes_after_a_time_out_on_disk(db, spools, monkeypatch):
    made, state = spools
    state["max_size"] = 1  # rolled over to disk on the first write
    _seed_contracts(db, 1)
    monkeypatch.setattr(review_server, "ANALYSIS_TIMEOUT_S", 0.0)
    wide = ("WITH RECURSIVE k(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM k WHERE x < 1000000) "
            "SELECT x, printf('%.*c', 200, 'x') FROM k")
    with pytest.raises(HTTPException) as e:
        review_server.analysis_export(review_server.AnalysisExport(sql=wide))
    assert "timed out" in e.value.detail
    assert len(made) == 1 and made[0]._rolled and made[0].closed


@pytest.mark.parametrize("asgi", ["2.4", "2.3"])
def test_the_temporary_file_closes_when_the_client_goes_away(db, spools, asgi):
    # ASGI 2.4 servers report a vanished client as an error on send; older ones
    # as a disconnect message. Either way the file is closed and deleted.
    made, _ = spools
    _seed_contracts(db, 3)
    response = review_server.analysis_export(review_server.AnalysisExport(sql="SELECT contract_id FROM contract"))

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("client gone")

    async def serve():
        try:
            await response({"type": "http", "asgi": {"spec_version": asgi}}, receive, send)
        except Exception:
            pass  # the server's concern; the file is ours

    asyncio.run(serve())
    assert len(made) == 1 and made[0].closed


def test_a_huge_computed_value_is_refused_or_emptied(db):
    # One SQLite step that builds a huge value outruns the time check; values are
    # capped at a million characters (the longest stored one has 8,321).
    _seed_contracts(db, 1)
    doubling = ("WITH RECURSIVE k(s) AS (SELECT 'x' UNION ALL SELECT s || s FROM k WHERE length(s) < 2000000) "
                "SELECT length(s) FROM k")
    for call in (run_readonly_sql, review_server.export_readonly_csv):
        with pytest.raises(HTTPException) as e:
            call(doubling)
        assert e.value.status_code == 400 and e.value.detail == "SQL error: string or blob too big"
    # printf past the cap gives an empty value instead (the review measured 160
    # rows of 5 MB at 10.6 s and 800 MB written before).
    many = "WITH RECURSIVE k(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM k WHERE x < 160) SELECT printf('%.*c', 5000000, 'x') FROM k"
    assert run_readonly_sql(many)["rows"][0] == [None]


def test_no_library_query_cuts_its_own_rows():
    # "Stated initial term" kept 15 of its 75 values with LIMIT 15.
    for query in ANALYSIS_LIBRARY:
        assert "LIMIT" not in query["sql"].upper(), query["id"]


# ---- 5. undated contracts are left out of year filters as of groupings -------

_DATED_SEED = """
INSERT INTO contract (contract_id, registration_date) VALUES
  (1,'1440-03-01'),(2,'1450-07-01'),(3,'1460-01-01'),(4,'0000-00-00'),(5,'0000-00-00');
"""


def _count(conn, spec):
    sql, params = build_analysis_sql(spec)[:2]
    return [dict(r) for r in conn.execute(sql, params)]


def test_year_filters_and_groupings_agree_on_undated_contracts(db):
    conn = db(_DATED_SEED)
    to_1450 = _count(conn, {"subject": "contracts", "measure": "count",
                            "filters": [{"field": "reg_year_to", "value": "1450"}]})
    assert to_1450 == [{"count": 2}]  # was 4: both undated contracts passed "≤ 1450"
    from_1450 = _count(conn, {"subject": "contracts", "measure": "count",
                              "filters": [{"field": "reg_year_from", "value": "1450"}]})
    assert from_1450 == [{"count": 2}]
    # one question, one total: the widest year range equals the sum of the decades
    everything = _count(conn, {"subject": "contracts", "measure": "count",
                               "filters": [{"field": "reg_year_from", "value": "1"},
                                           {"field": "reg_year_to", "value": "9999"}]})
    by_decade = _count(conn, {"subject": "contracts", "measure": "count", "group_by": "decade"})
    assert everything[0]["count"] == sum(r["n"] for r in by_decade) == 3


def test_years_compare_as_numbers(db):
    # As text, "1450" sorts before "999"; as a year it comes after.
    conn = db(_DATED_SEED)
    assert _count(conn, {"subject": "contracts", "measure": "count",
                         "filters": [{"field": "reg_year_to", "value": "999"}]}) == [{"count": 0}]


@pytest.mark.parametrize("filters", [["reg_year_to"], [None], "reg_year_to", {"field": "reg_year_to"}])
def test_a_filter_that_is_not_an_object_is_refused(filters):
    with pytest.raises(HTTPException) as e:
        build_analysis_sql({"subject": "contracts", "measure": "count", "filters": filters})
    assert e.value.status_code == 400 and e.value.detail.startswith("Unknown filter")


@pytest.mark.parametrize("value", ["the 1450s", "-5", "10000", "99999999999999999999"])
def test_year_filter_wants_a_year(value):
    # The last would pass int() but overflow SQLite's integer, a 500 before.
    with pytest.raises(HTTPException) as e:
        build_analysis_sql({"subject": "contracts", "measure": "count",
                            "filters": [{"field": "reg_year_to", "value": value}]})
    assert e.value.status_code == 400 and e.value.detail == "Enter a year as a number, such as 1600."


# ---- 6. title, profession, or either; never a relative's title ---------------

_TITLE_SEED = """
INSERT INTO title (title_id, title_name) VALUES (1,'nobile'),(2,'senatore');
INSERT INTO contract (contract_id, registration_date) VALUES (1,'1600-01-01');
INSERT INTO person (person_id, first_name, last_name) VALUES (1,'A','Uno'),(2,'B','Due'),(3,'C','Tre'),(4,'D','Quattro');
INSERT INTO investor (investor_id, person_id, contract_id, title, profession, title_husband, title_father_mother, title_grandfather)
  VALUES (10,1,1,1,'',NULL,NULL,NULL),               -- title only
         (11,2,1,NULL,'nobile fiorentino',NULL,NULL,NULL), -- profession only
         (12,3,1,1,'Nobile',NULL,NULL,NULL),         -- both: counted once
         (13,4,1,NULL,'mercante',1,1,1);             -- only her husband, father, grandfather are noble
"""


@pytest.mark.parametrize(
    ("field", "expected"),
    [("title_contains", 2), ("profession_contains", 2), ("title_or_profession_contains", 3)],
)
def test_title_and_profession_are_separate_labelled_choices(db, field, expected):
    conn = db(_TITLE_SEED)
    rows = _count(conn, {"subject": "investors", "measure": "count",
                         "filters": [{"field": field, "value": "nobil"}]})
    assert rows == [{"count": expected}]  # partner 13 never matches


def test_men_say_they_include_institutions_and_placeholders(db):
    # Until register §10c decides, partner appearances keep non-people among
    # "men" as entered, and the group says how many (non_person_rows.md).
    conn = db("""
    INSERT INTO contract (contract_id, registration_date) VALUES (1,'1600-01-01');
    INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES
      (1,'A','Uno',0),(2,'','MOLTEPLICI AZIONARI',0),(3,'B','Due',1);
    INSERT INTO investor (investor_id, person_id, contract_id) VALUES (10,1,1),(11,2,1),(12,3,1);
    """)
    sql, params, _ = build_analysis_sql({"subject": "investors", "measure": "count", "group_by": "gender"},
                                        excluded_person_ids=[2])
    assert [tuple(r) for r in conn.execute(sql, params)] == [
        ("men, including 1 appearance of institutions, estates, shareholder groups and placeholders", 2),
        ("women", 1),
    ]


def test_men_label_counts_every_non_person_appearance(db):
    conn = db("""
    INSERT INTO contract (contract_id, registration_date) VALUES (1,'1600-01-01'),(2,'1610-01-01');
    INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES (1,'A','Uno',0),(2,'','MOLTEPLICI AZIONARI',0);
    INSERT INTO investor (investor_id, person_id, contract_id) VALUES (10,1,1),(11,2,1),(12,2,2);
    """)
    sql, params, _ = build_analysis_sql({"subject": "investors", "measure": "count", "group_by": "gender"},
                                        excluded_person_ids=[2])
    assert [tuple(r) for r in conn.execute(sql, params)] == [
        ("men, including 2 appearances of institutions, estates, shareholder groups and placeholders", 3)]


def test_the_builder_finds_the_non_person_rows_itself(builder_db):
    # The endpoint classifies the rows (non_person_ids); nothing is passed in.
    builder_db("""
    INSERT INTO contract (contract_id, registration_date) VALUES (1,'1600-01-01');
    INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES
      (1,'A','Uno',0),(2,'','MOLTEPLICI AZIONARI',0),(3,'B','Due',1);
    INSERT INTO investor (investor_id, person_id, contract_id) VALUES (10,1,1),(11,2,1),(12,3,1);
    """)
    by_gender = review_server.analysis_build({"subject": "investors", "measure": "count", "group_by": "gender",
                                              "filters": []})
    assert by_gender["rows"] == [
        ["men, including 1 appearance of institutions, estates, shareholder groups and placeholders", 2],
        ["women", 1],
    ]
    people = review_server.analysis_build({"subject": "investors", "measure": "distinct_people_entered",
                                           "group_by": "", "filters": []})
    assert people["rows"] == [[2]]  # the shareholders' entry is no person


def test_relatives_titles_are_never_searched():
    for key, (_subjects, fragment, *_rest) in review_server.ANALYSIS_FILTERS.items():
        assert "title_husband" not in fragment and "title_father" not in fragment, key
        assert "title_grandfather" not in fragment, key


# ---- 7. relatives: her own rule ("SQL relatives new"), person entries ------------

_RELATIVES_SEED = """
INSERT INTO contract (contract_id, registration_date) VALUES
  (1,'1600-01-01'),(2,'1610-01-01'),(3,'1620-01-01'),(4,'1630-01-01'),(5,'1640-01-01'),(6,'1650-01-01');
INSERT INTO person (person_id, first_name, last_name, father_mother, grandfather) VALUES
  (1,'Piero','Doppio','Luca',''),                          -- c1: entered twice, alone
  (2,'Maria','Celato','',''),(3,'Anna','Celato','',''),     -- c2: one surname, two people
  (4,'Antonio','Mattei','Piero',''),(5,'Bernardo','Rucellai','Piero',''),  -- c3: fathers only
  (6,'Cosimo',NULL,'Giovanni',''),(7,'Lapo','Bardi','Giovanni',''),        -- c4: no last name, same father
  (8,'Ugo','Alfa','','Nero'),(9,'Ivo','Beta','','Nero'),   -- c5: grandfathers only
  (10,'Lisa','Rossi','',''),(11,'Gino','Verdi','',''),     -- c6: wife and husband by name
  (12,'Nina','','',''),(13,'Tina','','','');               -- c6: two empty names never match
INSERT INTO investor (investor_id, person_id, contract_id, husband_last_name) VALUES
  (10,1,1,''),(11,1,1,''),(20,2,2,''),(21,3,2,''),(30,4,3,''),(31,5,3,''),
  (40,6,4,''),(41,7,4,''),(50,8,5,''),(51,9,5,''),(60,10,6,'Verdi'),(61,11,6,''),(62,12,6,''),(63,13,6,'');
"""


def test_relatives_follow_her_own_matching_rule(db):
    conn = db(_RELATIVES_SEED)
    listed = {(r["contract_id"], r["first_name"]) for r in conn.execute(_sql("relatives_in_contract"))}
    assert listed == {
        (2, "Maria"), (2, "Anna"),     # one last name
        (4, "Cosimo"), (4, "Lapo"),    # a father's name, where one has no last name
        (6, "Lisa"), (6, "Gino"),      # her husband's last name is his
    }
    # c1: one person entered twice is no pair; c3: the fathers' names alone (the
    # review's Mattei and Rucellai, both sons of a Piero); c5: grandfathers are
    # never matched; Nina and Tina share only empty names.
    cursor = conn.execute(_sql("relatives_in_contract") + "")
    assert [d[0] for d in cursor.description] == [
        "contract_id", "person_id", "last_name", "father_mother", "grandfather", "first_name", "husband_last_name"]
    lisa = next(r for r in cursor if r[5] == "Lisa")
    assert lisa[6] == "Verdi"


def test_two_entries_with_one_name_stay_two_rows(db):
    # Contract 3786 has two "Fabio Orlandini" entries; DISTINCT on names merged them.
    conn = db("""
    INSERT INTO contract (contract_id, registration_date) VALUES (1,'1600-01-01');
    INSERT INTO person (person_id, first_name, last_name) VALUES (1,'Fabio','Orlandini'),(2,'Fabio','Orlandini');
    INSERT INTO investor (investor_id, person_id, contract_id) VALUES (10,1,1),(11,2,1);
    """)
    assert [r["person_id"] for r in conn.execute(_sql("relatives_in_contract"))] == [1, 2]


# ---- 4. joint per docs/data_quality/is_joint.md ------------------------------

def _joint_seed() -> str:
    # One decade, 30 partner appearances (the ≥30 gate):
    #   A, B share stake 1, neither flagged         → joint (the flag alone missed them)
    #   C flagged, solo stake 2 (parallel entry)    → joint (the structure alone misses it)
    #   D entered twice, both rows on stake 3       → not joint (one person, distinct count 1)
    #   E, F on stake 4, F's link removed           → not joint
    #   G marked joint, holds no stake (orphan)     → not joint (is_joint.md: the flag is wrong)
    #   H, I share stake 5, I a hidden person       → not joint; I is no appearance at all
    #   21 others, solo, unflagged                  → not joint
    rows = [
        "INSERT INTO contract (contract_id, registration_date) VALUES (1,'1605-01-01');",
        "INSERT INTO person (person_id, first_name, last_name) VALUES (1,'A','A'),(2,'B','B'),(3,'C','C'),"
        "(4,'D','D'),(5,'E','E'),(6,'F','F'),(7,'G','G'),(8,'H','H'),(9,'I','I');",
        "UPDATE person SET is_deleted=1 WHERE person_id=9;",
        "INSERT INTO investment (investment_id, contract_id, type) VALUES "
        "(1,1,'lp'),(2,1,'lp'),(3,1,'lp'),(4,1,'lp'),(5,1,'lp');",
        "INSERT INTO investor (investor_id, person_id, contract_id, is_joint) VALUES "
        "(1,1,1,0),(2,2,1,0),(3,3,1,1),(4,4,1,0),(5,4,1,0),(6,5,1,0),(7,6,1,0),(8,7,1,1),(9,8,1,0),(10,9,1,0);",
        "INSERT INTO investor_group (investor_id, investment_id, is_deleted) VALUES "
        "(1,1,0),(2,1,0),(3,2,0),(4,3,0),(5,3,0),(6,4,0),(7,4,1),(9,5,0),(10,5,0);",
    ]
    for n in range(21):
        rows.append(f"INSERT INTO person (person_id, first_name, last_name) VALUES ({100 + n},'X','Altro{n}');")
        rows.append(f"INSERT INTO investment (investment_id, contract_id, type) VALUES ({100 + n},1,'lp');")
        rows.append(f"INSERT INTO investor (investor_id, person_id, contract_id) VALUES ({100 + n},{100 + n},1);")
        rows.append(f"INSERT INTO investor_group (investor_id, investment_id) VALUES ({100 + n},{100 + n});")
    return "\n".join(rows)


def test_joint_reads_the_shared_stake_or_the_flag(db):
    conn = db(_joint_seed())
    rows = [dict(r) for r in conn.execute(_sql("joint_over_time"))]
    # 3 of 30 (A, B, C). The flag alone gave 2 of 31 (C, G); counting G's orphan
    # flag or I's hidden share gives 4 of 30.
    assert rows == [{"decade": 1600, "investors": 30, "pct_joint": 10.0}]


def test_joint_title_names_the_unit_it_counts():
    q = next(q for q in ANALYSIS_LIBRARY if q["id"] == "joint_over_time")
    assert q["title"].startswith("Partners") and "appearances" in q["description"]


# ---- 1. bar charts: every period; the top 20 said so; the scale from all rows -

_NODE = shutil.which("node")
_BAR_DATA = ROOT / "apps/review/src/utils/barData.ts"


def _plan(columns, rows, **options):
    script = (
        f"import {{ planBars }} from {json.dumps(_BAR_DATA.as_uri())};\n"
        f"console.log(JSON.stringify(planBars({json.dumps(columns)}, {json.dumps(rows)}, {json.dumps(options)})));\n"
    )
    out = subprocess.run(
        [_NODE, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return json.loads(out.stdout)


def _node_version() -> tuple[int, ...]:
    if _NODE is None:
        return ()
    out = subprocess.run([_NODE, "--version"], capture_output=True, text=True, check=False).stdout
    return tuple(int(part) for part in re.findall(r"\d+", out)[:2])


# --experimental-strip-types (running the .ts helper directly) arrived in Node 22.6.
needs_node = pytest.mark.skipif(
    _node_version() < (22, 6),
    reason="needs Node 22.6 or newer to run apps/review/src/utils/barData.ts directly",
)


@needs_node
def test_time_groupings_draw_every_period_scaled_to_all():
    # 37 decades; the largest is the last, which the old chart never drew.
    rows = [[1440 + 10 * k, k + 1] for k in range(37)]
    plan = _plan(["decade", "n"], rows)
    assert [b["label"] for b in plan["bars"]] == [str(r[0]) for r in rows]
    assert plan["max"] == 37 and plan["note"] == ""


@needs_node
def test_ranked_categories_show_the_top_20_and_add_up_the_rest():
    rows = [[f"currency {k}", 400 - k] for k in range(315)]
    plan = _plan(["currency", "contracts"], rows)
    bars = plan["bars"]
    assert len(bars) == 21 and bars[0]["label"] == "currency 0"
    assert bars[-1]["label"] == "All others (295)"
    assert bars[-1]["value"] == sum(400 - k for k in range(20, 315))
    assert plan["max"] == max(bars[-1]["value"], 400)
    assert plan["note"] == "Top 20 of 315."


@needs_node
def test_ranked_picks_the_largest_even_when_rows_arrive_unsorted():
    rows = [[f"c{k}", k] for k in range(25)]  # ascending: the top 20 are the last 20
    plan = _plan(["currency", "contracts"], rows)
    assert [b["value"] for b in plan["bars"][:20]] == list(range(24, 4, -1))
    assert plan["bars"][-1]["value"] == 0 + 1 + 2 + 3 + 4


@needs_node
def test_capital_and_averages_are_never_added_across_rows():
    rows = [[f"currency {k}", 3, 1000 - k] for k in range(30)]
    for label in ("currency", "register"):
        for alias in ("total_capital", "average_capital", "pct_joint"):
            plan = _plan([label, "contracts", alias], rows)
            assert len(plan["bars"]) == 20 and not any("All others" in b["label"] for b in plan["bars"])
            assert plan["note"] == "Top 20 of 30; the rest are in the table below."


@needs_node
def test_short_ranked_results_draw_whole_without_a_note():
    plan = _plan(["role", "investments"], [["gp", 6715], ["lp", 8674]])
    assert [b["value"] for b in plan["bars"]] == [6715, 8674] and plan["note"] == ""
    assert plan["max"] == 8674


@needs_node
def test_decade_groupings_keep_decades_with_none_in_place():
    # Livorno-like decades with gaps; a count gap is a 0 bar, an amount gap the
    # marker "none" (the decade may hold other contracts, just none of these).
    plan = _plan(["decade", "n"], [[1500, 3], [1530, 5], [1550, 1]])
    assert [(b["label"], b["value"]) for b in plan["bars"]] == [
        ("1500", 3), ("1510", 0), ("1520", 0), ("1530", 5), ("1540", 0), ("1550", 1)]
    assert not any("text" in b for b in plan["bars"])
    capital = _plan(["decade", "contracts", "total_capital"], [[1550, 2, 900], [1570, 1, 300]])
    gap = capital["bars"][1]
    assert gap["label"] == "1560" and gap["value"] == 0 and gap["text"] == "none"


@needs_node
def test_only_decades_on_the_grid_are_filled():
    # Years are never filled (no chart groups by year); decade 0 stays outside the
    # fill instead of dragging in 144 empty decades; an off-grid value or a span
    # over 60 decades falls back to the rows as they come.
    years = _plan(["year", "n"], [[1600, 1], [1603, 2]])
    assert [b["label"] for b in years["bars"]] == ["1600", "1603"]
    undated = _plan(["decade", "n"], [[0, 4], [1440, 1], [1460, 2]])
    assert [b["label"] for b in undated["bars"]] == ["0", "1440", "1450", "1460"]
    off_grid = _plan(["decade", "n"], [[1440, 1], [1455, 2]])
    assert [b["label"] for b in off_grid["bars"]] == ["1440", "1455"]
    too_wide = _plan(["decade", "n"], [[1000, 1], [1800, 2]])
    assert [b["label"] for b in too_wide["bars"]] == ["1000", "1800"]


@needs_node
def test_the_fill_stops_at_60_decades():
    assert len(_plan(["decade", "n"], [[1200, 1], [1790, 1]])["bars"]) == 60
    assert len(_plan(["decade", "n"], [[1200, 1], [1800, 1]])["bars"]) == 2
    assert len(_plan(["year", "n"], [[1600, 1], [1610, 2]])["bars"]) == 2


@needs_node
def test_overlapping_categories_are_listed_and_never_summed():
    rows = [[f"place {k}", 100 - k] for k in range(30)]
    plan = _plan(["place_name", "contracts"], rows, overlapping="One contract can name several places.")
    assert len(plan["bars"]) == 20 and not any("All others" in b["label"] for b in plan["bars"])
    assert plan["note"] == "Top 20 of 30; the rest are in the table below. One contract can name several places."


def test_place_counts_overlap_and_the_library_says_so(db):
    # Contract 1 names two places: the place counts add up to 3 for 2 contracts.
    conn = db("""
    INSERT INTO place (place_id, place_name) VALUES (1,'Firenze'),(2,'Livorno');
    INSERT INTO contract (contract_id, registration_date) VALUES (1,'1600-01-01'),(2,'1601-01-01');
    INSERT INTO contract_place (place_id, contract_id) VALUES (1,1),(2,1),(2,2);
    """)
    counts = [r["contracts"] for r in conn.execute(_sql("location_frequency"))]
    assert sum(counts) == 3 and conn.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 2
    entry = next(q for q in ANALYSIS_LIBRARY if q["id"] == "location_frequency")
    assert entry.get("overlapping") == "One contract can name several places."


def test_bar_chart_draws_the_plan_whole():
    # The chart draws what planBars returns; a slice in the component would bring
    # back the silent 20-bar cut.
    source = (ROOT / "apps/review/src/pages/Analysis.tsx").read_text()
    chart = source[source.index("function BarChart("):source.index("const SERIES_COLORS")]
    assert "planBars(result.columns, result.rows," in chart and "plan.bars.map(" in chart
    assert ".slice(" not in chart
    assert re.search(r"planBars\([^)]*\boverlapping,", chart, re.S)  # the library's overlap sentence reaches it
    assert 'd.text ? "" : d.value.toLocaleString()' in chart  # a decade with none shows no amount
    assert "overlapping={selected.overlapping}" in source  # the library entry passes it on
    assert "idCols.length === 1" in source  # two contract_id columns link neither
