"""Tests for contract-place editing — add / remove / restore / edit-address.
Composite-PK rows, audited + replay-safe; re-pointing a place = remove + add.

    uv run --with pytest pytest tests/test_places.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, db_import, review_server
from workflows.review_server import PlaceAdd, PlaceAddress, RecordAction

_SCHEMA = """
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE place (place_id INTEGER PRIMARY KEY, place_name TEXT);
CREATE TABLE contract_place (place_id INTEGER, contract_id INTEGER, address TEXT,
    place_db INTEGER NOT NULL DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (place_id, contract_id));
"""
_SEED = """
INSERT INTO contract VALUES (1,0);
INSERT INTO place VALUES (5,'Firenze');
INSERT INTO contract_place VALUES (5,1,'via Larga',0,0);
"""


def _wire(tmp_path, monkeypatch):
    main = str(tmp_path / "main.db")
    corr = str(tmp_path / "corr.db")
    c = sqlite3.connect(main)
    c.executescript(_SCHEMA + _SEED)
    c.commit()
    c.close()

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(Path(corr)))
    return open_db, corr


def _val(open_db, sql, *p):
    x = open_db()
    try:
        row = x.execute(sql, p).fetchone()
        return row[0] if row else None
    finally:
        x.close()


def _ops(corr):
    clog = corrections_db.connect(Path(corr))
    try:
        return [(o[0], o[1]) for o in clog.execute("SELECT op, db_table FROM change_request ORDER BY created_at")]
    finally:
        clog.close()


def test_add_new_place_creates_lookup_and_link(tmp_path, monkeypatch):
    open_db, corr = _wire(tmp_path, monkeypatch)
    review_server.add_place("1", PlaceAdd(reviewer="WH", place="Pisa", address="centro"))
    pid = _val(open_db, "SELECT place_id FROM place WHERE place_name='Pisa'")
    assert pid is not None
    assert _val(open_db, "SELECT address FROM contract_place WHERE contract_id=1 AND place_id=?", pid) == "centro"
    kinds = _ops(corr)
    assert ("create", "place") in kinds and ("create", "contract_place") in kinds


def test_add_duplicate_is_rejected(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        review_server.add_place("1", PlaceAdd(reviewer="WH", place="Firenze"))  # (5,1) already linked


def test_remove_then_readd_restores_not_duplicates(tmp_path, monkeypatch):
    open_db, corr = _wire(tmp_path, monkeypatch)
    review_server.remove_place("1", "5", RecordAction(reviewer="WH", reason="wrong place"))
    assert _val(open_db, "SELECT is_deleted FROM contract_place WHERE contract_id=1 AND place_id=5") == 1
    # re-adding the same place restores the soft-deleted row (no PK duplicate)
    review_server.add_place("1", PlaceAdd(reviewer="WH", place="Firenze", address="new addr"))
    assert _val(open_db, "SELECT is_deleted FROM contract_place WHERE contract_id=1 AND place_id=5") == 0
    assert _val(open_db, "SELECT COUNT(*) FROM contract_place WHERE contract_id=1 AND place_id=5") == 1


def test_edit_address(tmp_path, monkeypatch):
    open_db, corr = _wire(tmp_path, monkeypatch)
    review_server.edit_place_address("1", "5", PlaceAddress(reviewer="WH", address="Piazza del Grano"))
    assert _val(open_db, "SELECT address FROM contract_place WHERE contract_id=1 AND place_id=5") == "Piazza del Grano"
    assert ("update", "contract_place") in _ops(corr)
    with pytest.raises(HTTPException):  # unchanged → 409
        review_server.edit_place_address("1", "5", PlaceAddress(reviewer="WH", address="Piazza del Grano"))


def test_remove_requires_reason(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        review_server.remove_place("1", "5", RecordAction(reviewer="WH"))


def test_readd_with_blank_address_keeps_existing(tmp_path, monkeypatch):
    open_db, corr = _wire(tmp_path, monkeypatch)
    review_server.remove_place("1", "5", RecordAction(reviewer="WH", reason="wrong place"))
    # blank address on re-add = "restore as it was", never an implicit clear
    review_server.add_place("1", PlaceAdd(reviewer="WH", place="Firenze"))
    assert _val(open_db, "SELECT address FROM contract_place WHERE contract_id=1 AND place_id=5") == "via Larga"
    # same phrase, same address typed out → restore only, no spurious update op
    review_server.remove_place("1", "5", RecordAction(reviewer="WH", reason="again"))
    review_server.add_place("1", PlaceAdd(reviewer="WH", place="Firenze", address="via Larga"))
    assert _ops(corr).count(("update", "contract_place")) == 0


def test_readd_with_new_address_is_logged_and_survives_rebuild(tmp_path, monkeypatch):
    open_db, corr = _wire(tmp_path, monkeypatch)
    review_server.remove_place("1", "5", RecordAction(reviewer="WH", reason="wrong place"))
    review_server.add_place("1", PlaceAdd(reviewer="WH", place="Firenze", address="Borgo San Lorenzo"))
    assert _val(open_db, "SELECT address FROM contract_place WHERE contract_id=1 AND place_id=5") == "Borgo San Lorenzo"
    # the address change is its own audited op, not a side effect of the restore
    assert ("update", "contract_place") in _ops(corr)
    # rebuild: fresh seed (address = 'via Larga') + replay must keep the typed address
    fresh = str(tmp_path / "fresh.db")
    f = sqlite3.connect(fresh); f.executescript(_SCHEMA + _SEED); f.commit(); f.close()
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", corr)
    fc = sqlite3.connect(fresh)
    db_import.replay_corrections(fc)
    fc.commit()
    row = fc.execute("SELECT is_deleted, address FROM contract_place WHERE contract_id=1 AND place_id=5").fetchone()
    assert row == (0, "Borgo San Lorenzo")
    fc.close()


def test_place_edits_survive_rebuild(tmp_path, monkeypatch):
    open_db, corr = _wire(tmp_path, monkeypatch)
    review_server.add_place("1", PlaceAdd(reviewer="WH", place="Pisa", address="centro"))
    review_server.edit_place_address("1", "5", PlaceAddress(reviewer="WH", address="Piazza del Grano"))
    pid = _val(open_db, "SELECT place_id FROM place WHERE place_name='Pisa'")
    # rebuild: fresh seed + replay
    fresh = str(tmp_path / "fresh.db")
    f = sqlite3.connect(fresh); f.executescript(_SCHEMA + _SEED); f.commit(); f.close()
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", corr)
    fc = sqlite3.connect(fresh)
    db_import.replay_corrections(fc)
    fc.commit()
    assert fc.execute("SELECT place_name FROM place WHERE place_id=?", (pid,)).fetchone()[0] == "Pisa"
    assert fc.execute("SELECT address FROM contract_place WHERE contract_id=1 AND place_id=5").fetchone()[0] == "Piazza del Grano"
    assert fc.execute("SELECT address FROM contract_place WHERE contract_id=1 AND place_id=?", (pid,)).fetchone()[0] == "centro"
    fc.close()
