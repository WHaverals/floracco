"""Tests for FK relink — re-pointing title/place/currency/economic_sector to a
lookup row (reuse existing / create verbatim / clear), audited as create?+update.

    uv run --with pytest pytest tests/test_relink.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, review_server
from workflows.review_server import RelinkAction

_SCHEMA = """
CREATE TABLE economic_activity (ec_activity_id INTEGER PRIMARY KEY, activity TEXT);
CREATE TABLE currency (currency_id INTEGER PRIMARY KEY, currency TEXT, start_date TEXT, end_date TEXT, ratio INTEGER);
CREATE TABLE title (title_id INTEGER PRIMARY KEY, title_name TEXT);
CREATE TABLE place (place_id INTEGER PRIMARY KEY, place_name TEXT);
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, economic_sector INTEGER, currency_id INTEGER,
    is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, contract_id INTEGER, title INTEGER DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0);
"""


def _db(tmp_path, monkeypatch):
    main = str(tmp_path / "main.db")
    corr = str(tmp_path / "corr.db")
    c = sqlite3.connect(main)
    c.executescript(
        _SCHEMA
        + """
        INSERT INTO economic_activity VALUES (10,'arte di lana'),(11,'arte di seta');
        INSERT INTO currency VALUES (1,'fiorini',NULL,NULL,0);
        INSERT INTO title VALUES (5,'messer');
        INSERT INTO contract (contract_id, economic_sector, currency_id) VALUES (1, 10, 1);
        INSERT INTO investor (investor_id, contract_id, title) VALUES (100, 1, 0);
        """
    )
    c.commit()
    c.close()

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(Path(corr)))
    return open_db, corr


def _val(open_db, sql, *params):
    x = open_db()
    try:
        return x.execute(sql, params).fetchone()[0]
    finally:
        x.close()


def _ops(corr):
    clog = corrections_db.connect(Path(corr))
    try:
        return clog.execute("SELECT op, db_table, field FROM change_request ORDER BY created_at").fetchall()
    finally:
        clog.close()


def test_relink_existing_activity_is_update_only(tmp_path, monkeypatch):
    open_db, corr = _db(tmp_path, monkeypatch)
    res = review_server.relink_record("contract", "1", RelinkAction(reviewer="WH", field="economic_sector", value="arte di seta"))
    assert res["value"] == "arte di seta"
    assert _val(open_db, "SELECT economic_sector FROM contract WHERE contract_id=1") == 11
    kinds = [(o[0], o[1], o[2]) for o in _ops(corr)]
    assert kinds == [("update", "contract", "economic_sector")]      # no create — phrase reused


def test_relink_new_phrase_creates_then_points(tmp_path, monkeypatch):
    open_db, corr = _db(tmp_path, monkeypatch)
    review_server.relink_record("contract", "1", RelinkAction(reviewer="WH", field="economic_sector", value="negozio di spezieria"))
    # a new activity row was minted verbatim, and the FK now points at it
    new_id = _val(open_db, "SELECT ec_activity_id FROM economic_activity WHERE activity='negozio di spezieria'")
    assert _val(open_db, "SELECT economic_sector FROM contract WHERE contract_id=1") == new_id
    kinds = [(o[0], o[1]) for o in _ops(corr)]
    assert ("create", "economic_activity") in kinds
    assert ("update", "contract") in kinds


def test_relink_reuses_exact_phrase_no_duplicate(tmp_path, monkeypatch):
    open_db, corr = _db(tmp_path, monkeypatch)
    review_server.relink_record("contract", "1", RelinkAction(reviewer="WH", field="economic_sector", value="arte di seta"))
    # same phrase again on the investor side would reuse, not duplicate
    review_server.relink_record("investor", "100", RelinkAction(reviewer="WH", field="title", value="messer"))
    assert _val(open_db, "SELECT COUNT(*) FROM title WHERE title_name='messer'") == 1
    assert _val(open_db, "SELECT title FROM investor WHERE investor_id=100") == 5


def test_relink_to_none_clears_fk(tmp_path, monkeypatch):
    open_db, _ = _db(tmp_path, monkeypatch)
    review_server.relink_record("contract", "1", RelinkAction(reviewer="WH", field="economic_sector", value=""))
    assert _val(open_db, "SELECT economic_sector FROM contract WHERE contract_id=1") is None


def test_relink_rejects_unregistered_field(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        review_server.relink_record("contract", "1", RelinkAction(reviewer="WH", field="firm_name", value="x"))


def test_relink_no_op_same_value_rejected(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        review_server.relink_record("contract", "1", RelinkAction(reviewer="WH", field="economic_sector", value="arte di lana"))


def test_relink_survives_rebuild(tmp_path, monkeypatch):
    open_db, corr = _db(tmp_path, monkeypatch)
    review_server.relink_record("contract", "1", RelinkAction(reviewer="WH", field="economic_sector", value="negozio di spezieria"))
    # rebuild: a FRESH seed copy + replay the op-log → the new phrase + re-point survive
    main2 = str(tmp_path / "fresh.db")
    src = open_db()
    try:
        # fresh seed = original schema + original rows (no relink applied)
        s = sqlite3.connect(main2)
        s.executescript(_SCHEMA + """
            INSERT INTO economic_activity VALUES (10,'arte di lana'),(11,'arte di seta');
            INSERT INTO currency VALUES (1,'fiorini',NULL,NULL,0);
            INSERT INTO title VALUES (5,'messer');
            INSERT INTO contract (contract_id, economic_sector, currency_id) VALUES (1, 10, 1);
            INSERT INTO investor (investor_id, contract_id, title) VALUES (100, 1, 0);
        """)
        s.commit()
    finally:
        src.close()
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", corr)
    from workflows import db_import
    fc = sqlite3.connect(main2)
    db_import.replay_corrections(fc)
    fc.commit()
    fc.row_factory = sqlite3.Row
    new_id = fc.execute("SELECT ec_activity_id FROM economic_activity WHERE activity='negozio di spezieria'").fetchone()
    sector = fc.execute("SELECT economic_sector FROM contract WHERE contract_id=1").fetchone()[0]
    fc.close()
    assert new_id is not None and sector == new_id["ec_activity_id"]
