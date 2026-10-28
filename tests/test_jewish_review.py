"""Tests for the Jewish-attribution review lanes + the audited refute action.

    uv run --with pytest pytest tests/test_jewish_review.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, db_import, jewish_review, review_server
from workflows.review_server import AttributionRefute

_SCHEMA = """
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, firm_name TEXT, document TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sub_contract (contract_id INTEGER PRIMARY KEY, main_contract_id INTEGER,
    document TEXT, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE person (person_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, person_id INTEGER, contract_id INTEGER,
    is_jewish INTEGER DEFAULT 0, jewish_db INTEGER DEFAULT 0, is_convert INTEGER DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0);
"""

# c1: narrative attests, investor 100 carries only the editorial flag  → Lane A
# c2: attests via a LATER ACT's narrative, investor 200 editorial-only → Lane A
# c3: stated flag, but no “ebre” anywhere                              → stated-unattested
# c4: attested + stated                                                → consistent, no flag
# c5/c6: person 5 flagged on one appearance, unflagged on the other    → person drift
# c7: soft-deleted investor on an attesting contract                   → must NOT flag
_SEED = """
INSERT INTO contract VALUES (1,'A','Samuel Pesaro, ebreo commorante a Firenze',0);
INSERT INTO contract VALUES (2,'B','nessuna menzione qui',0);
INSERT INTO sub_contract VALUES (90,2,'disdetta: Giacob Saccuto ebreo',0);
INSERT INTO contract VALUES (3,'C','contratto ordinario senza menzioni',0);
INSERT INTO contract VALUES (4,'D','Vita Funaro, ebreo di Livorno',0);
INSERT INTO contract VALUES (5,'E','primo contratto',0);
INSERT INTO contract VALUES (6,'F','secondo contratto',0);
INSERT INTO contract VALUES (7,'G','Isach Orvieto, ebreo',0);
INSERT INTO person VALUES (1,'Samuel','Pesaro',0),(2,'Giacob','Saccuto',0),(3,'Isach','Moreno',0),
    (4,'Vita','Funaro',0),(5,'Leone','Fano',0),(6,'Rimosso','Investitore',0);
INSERT INTO investor VALUES (100,1,1,0,1,0,0);
INSERT INTO investor VALUES (200,2,2,0,1,0,0);
INSERT INTO investor VALUES (300,3,3,1,0,0,0);
INSERT INTO investor VALUES (400,4,4,1,0,0,0);
INSERT INTO investor VALUES (500,5,5,0,1,0,0);
INSERT INTO investor VALUES (501,5,6,0,0,0,0);
INSERT INTO investor VALUES (600,6,7,0,1,0,1);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA + _SEED)
    return conn


def _keys(flags, group):
    return sorted(f["key"] for f in flags if f["group"] == group)


def test_text_attests_lane_contract_and_subact():
    flags = jewish_review.flags(_conn())
    keys = _keys(flags, "jewish_text_attests")
    # investor 100 (contract narrative) and 200 (later act's narrative); the
    # soft-deleted 600 is excluded even though contract 7 attests
    assert keys == ["contract:1:jewish_text_attests:100", "contract:2:jewish_text_attests:200"]
    f = next(x for x in flags if x["key"].endswith(":100"))
    assert f["fix"] == {"kind": "partner_field", "field": "is_jewish", "investor_id": "100"}
    assert "Samuel Pesaro" in f["title"]


def test_stated_unattested_lane():
    flags = jewish_review.flags(_conn())
    keys = _keys(flags, "jewish_stated_unattested")
    assert keys == ["contract:3:jewish_stated_unattested:300"]  # c4 attests → not flagged


def test_person_drift_lane():
    flags = jewish_review.flags(_conn())
    drift = [f for f in flags if f["group"] == "jewish_person_drift"]
    assert [f["pk"] for f in drift] == ["5"]
    assert "1 of 2" in drift[0]["title"]


def test_confirmed_both_flags_is_not_drift():
    conn = _conn()
    # person 5's unflagged appearance gets confirmed later: stated on one row,
    # editorial on the other row becomes stated+editorial → still drift (split);
    # but a person whose EVERY appearance is flagged (either layer) is clean.
    conn.execute("UPDATE investor SET is_jewish=1, jewish_db=1 WHERE investor_id=501")
    drift = [f for f in jewish_review.flags(conn) if f["group"] == "jewish_person_drift"]
    assert [f["pk"] for f in drift] == []


def test_split_provenance_drift_fires_even_when_every_appearance_is_flagged():
    conn = _conn()
    # person 5: appearance 500 is editorial-only; make 501 STATED-only. Every
    # appearance now carries a flag (first disjunct false), but the provenances
    # are split between the two layers — the OR clause's second disjunct is the
    # ONLY thing that keeps this person in the reconciliation queue. Deleting it
    # would silently drop the exact case the queue exists to reconcile.
    conn.execute("UPDATE investor SET is_jewish=1, jewish_db=0 WHERE investor_id=501")
    drift = [f for f in jewish_review.flags(conn) if f["group"] == "jewish_person_drift"]
    assert [f["pk"] for f in drift] == ["5"]
    assert "2 of 2" in drift[0]["title"]


def _curated(conn: sqlite3.Connection) -> None:
    """The real curated rows (jewish_review.SUSPECT_ATTRIBUTIONS etc. name these
    exact ids) — absent from the base seed, so the lane guards never fired in
    any test until now."""
    conn.executescript("""
        INSERT INTO contract VALUES (2646,'H','contratto con case straniere',0);
        INSERT INTO contract VALUES (4850,'I','sei soci con attribuzione editoriale',0);
        INSERT INTO person VALUES (80,'Giovanni David','Ahrenz',0),(81,'Sahadun','',0),
            (3255,'Jacob Francesco','de Miranda',0);
        INSERT INTO investor VALUES (8052,80,2646,0,1,0,0);
        INSERT INTO investor VALUES (18098,81,4850,0,1,1,0);
    """)


def test_curated_suspect_lanes_fire_while_their_condition_stands():
    conn = _conn()
    _curated(conn)
    flags = jewish_review.flags(conn)
    assert _keys(flags, "jewish_suspect_attribution") == ["contract:2646:jewish_suspect_attribution:8052"]
    assert _keys(flags, "jewish_convert_suspect") == ["contract:4850:jewish_convert_suspect:18098"]
    norm = [f for f in flags if f["group"] == "jewish_name_normalization"]
    assert [f["pk"] for f in norm] == ["3255"]
    assert norm[0]["fix"] == {"kind": "edit", "field": "first_name"}


def test_curated_lanes_self_clean_once_resolved():
    conn = _conn()
    _curated(conn)
    # the sanctioned resolutions: refute the attribution, clear the convert
    # flag, normalize the name — each lane must vanish on its own
    conn.execute("UPDATE investor SET jewish_db=0 WHERE investor_id=8052")
    conn.execute("UPDATE investor SET is_convert=0 WHERE investor_id=18098")
    conn.execute("UPDATE person SET first_name='Jacob' WHERE person_id=3255")
    flags = jewish_review.flags(conn)
    for group in ("jewish_suspect_attribution", "jewish_convert_suspect", "jewish_name_normalization"):
        assert _keys(flags, group) == [], group


def test_curated_lanes_ignore_soft_deleted_rows():
    conn = _conn()
    _curated(conn)
    conn.execute("UPDATE investor SET is_deleted=1 WHERE investor_id IN (8052, 18098)")
    conn.execute("UPDATE person SET is_deleted=1 WHERE person_id=3255")
    flags = jewish_review.flags(conn)
    for group in ("jewish_suspect_attribution", "jewish_convert_suspect", "jewish_name_normalization"):
        assert _keys(flags, group) == [], group


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


def test_refute_writes_audited_op_and_409s_on_repeat(tmp_path, monkeypatch):
    open_db, corr = _wire(tmp_path, monkeypatch)
    review_server.refute_jewish_attribution("100", AttributionRefute(reviewer="WH", reason="Vienna merchant house"))
    x = open_db()
    assert x.execute("SELECT jewish_db FROM investor WHERE investor_id=100").fetchone()[0] == 0
    x.close()
    clog = corrections_db.connect(Path(corr))
    op = clog.execute(
        "SELECT op, field, before_value, after_value, reason FROM change_request WHERE db_table='investor'"
    ).fetchone()
    clog.close()
    assert (op[0], op[1]) == ("update", "jewish_db")
    assert op[4] == "Vienna merchant house"
    with pytest.raises(HTTPException):  # already refuted → 409, never 0→…
        review_server.refute_jewish_attribution("100", AttributionRefute(reviewer="WH", reason="again"))
    with pytest.raises(HTTPException):  # unknown investor → 404
        review_server.refute_jewish_attribution("999", AttributionRefute(reviewer="WH", reason="x"))


def test_refutation_survives_rebuild(tmp_path, monkeypatch):
    open_db, corr = _wire(tmp_path, monkeypatch)
    review_server.refute_jewish_attribution("100", AttributionRefute(reviewer="WH", reason="erroneous"))
    fresh = str(tmp_path / "fresh.db")
    f = sqlite3.connect(fresh); f.executescript(_SCHEMA + _SEED); f.commit(); f.close()
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", corr)
    fc = sqlite3.connect(fresh)
    db_import.replay_corrections(fc)
    fc.commit()
    assert fc.execute("SELECT jewish_db FROM investor WHERE investor_id=100").fetchone()[0] == 0
    fc.close()


def test_conjecture_lane_enabled_with_guards():
    # Enabled on WH's instruction 2026-08-27 (see LOG + decisions register).
    assert jewish_review.CONJECTURE_ENABLED is True
    conn = _conn()
    # seed one real conjecture id (20006 on contract 5103, Samuel Vita Servi)
    conn.executescript("""
        INSERT INTO contract VALUES (5103,'K','scritta senza menzione religiosa',0);
        INSERT INTO person VALUES (90,'Samuel Vita','Servi',0);
        INSERT INTO investor VALUES (20006,90,5103,0,0,0,0);
    """)
    flags = jewish_review.flags(conn)
    keys = _keys(flags, "jewish_conjecture")
    assert keys == ["contract:5103:jewish_conjecture:20006"]
    f = next(x for x in flags if x["group"] == "jewish_conjecture")
    assert f["fix"] == {"kind": "partner_field", "field": "is_jewish", "investor_id": "20006"}
    assert "editorial flag" in f["title"]  # each item carries its written basis


def test_conjecture_lane_self_cleans_on_either_flag_or_delete():
    for resolution in (
        "UPDATE investor SET is_jewish=1 WHERE investor_id=20006",   # attested → stated
        "UPDATE investor SET jewish_db=1 WHERE investor_id=20006",   # (hypothetical) editorial
        "UPDATE investor SET is_deleted=1 WHERE investor_id=20006",  # removed row
    ):
        conn = _conn()
        conn.executescript("""
            INSERT INTO contract VALUES (5103,'K','scritta senza menzione religiosa',0);
            INSERT INTO person VALUES (90,'Samuel Vita','Servi',0);
            INSERT INTO investor VALUES (20006,90,5103,0,0,0,0);
        """)
        conn.execute(resolution)
        assert _keys(jewish_review.flags(conn), "jewish_conjecture") == [], resolution
