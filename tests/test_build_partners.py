"""Unit tests for the Partners block builder — especially the joint-display fix.

`build_partners` must mark a stake "joint" under EITHER encoding the corpus uses:
  (1) one investment shared by several investors (derivable → "joint · N"), and
  (2) parallel investments marked only by the per-investor `is_joint` flag
      (not derivable → bare "joint").
…and it must NEVER put "joint" in the Status column (that's personal standing
only: widow/guardian). See docs/data_quality/is_joint.md.

Run with an ephemeral pytest (no project dependency added):

    uv run --with pytest pytest tests/test_build_partners.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workflows import corrections_db, review_server
from workflows.review_server import RecordAction

_SCHEMA = """
CREATE TABLE place (place_id INTEGER PRIMARY KEY, place_name TEXT);
CREATE TABLE title (title_id INTEGER PRIMARY KEY, title_name TEXT);
CREATE TABLE person (person_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT,
    nickname TEXT, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investment (investment_id INTEGER PRIMARY KEY, contract_id INTEGER, type TEXT,
    investment_cash INTEGER, investment_non_cash TEXT, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, person_id INTEGER, contract_id INTEGER,
    profession TEXT, place_of_residence INTEGER, place_of_origin INTEGER DEFAULT 0,
    is_widow INTEGER DEFAULT 0, is_guardian INTEGER DEFAULT 0, is_joint INTEGER DEFAULT 0,
    citizen_florence INTEGER DEFAULT 0, via_proxy INTEGER DEFAULT 0, is_jewish INTEGER DEFAULT 0,
    jewish_db INTEGER DEFAULT 0,
    is_convert INTEGER DEFAULT 0, heirs INTEGER DEFAULT 0, heirs_of INTEGER DEFAULT 0, and_c INTEGER DEFAULT 0,
    husband_first_name TEXT, husband_last_name TEXT, guardian_of TEXT,
    title INTEGER DEFAULT 0, title_husband INTEGER DEFAULT 0, title_grandfather INTEGER DEFAULT 0,
    title_father_mother INTEGER DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor_group (investor_id INTEGER, investment_id INTEGER,
    is_deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (investor_id, investment_id));
"""

# Brief named-column insert for the basic fixtures (so the schema can grow without
# breaking positional inserts).
_IC = "(investor_id,person_id,contract_id,profession,place_of_residence,is_widow,is_guardian,is_joint,is_deleted)"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        _SCHEMA
        + f"""
        INSERT INTO place VALUES (1, 'Firenze'), (2, 'Pisa');
        INSERT INTO title VALUES (1, 'monna');
        INSERT INTO person (person_id, first_name, last_name) VALUES
            (10,'Antonio','Corbinelli'),(11,'Antonio','Lenzi'),
            (20,'Benedetto','Machiavelli'),(21,'Taddeo',NULL),
            (30,'Orphan','Investor'),(40,'Caterina','Vedova'),(50,'Solo','Investore'),
            (60,'Margherita','Strozzi');

        -- Contract 1: encoding (1) — ONE shared investment, two investors.
        INSERT INTO investment VALUES (100, 1, 'lp', 1500, NULL, 0);
        INSERT INTO investor {_IC} VALUES (1000,10,1,'mercante',1,0,0,1,0);
        INSERT INTO investor {_IC} VALUES (1001,11,1,'mercante',1,0,0,0,0);
        INSERT INTO investor_group VALUES (1000,100,0),(1001,100,0);

        -- Contract 2: encoding (2) — parallel investments, flag-only joint.
        INSERT INTO investment VALUES (200, 2, 'gp', 7000, NULL, 0);
        INSERT INTO investment VALUES (201, 2, 'gp', 7000, NULL, 0);
        INSERT INTO investor {_IC} VALUES (2000,20,2,NULL,NULL,0,0,1,0);
        INSERT INTO investor {_IC} VALUES (2001,21,2,NULL,NULL,0,0,1,0);
        INSERT INTO investor_group VALUES (2000,200,0),(2001,201,0);

        -- Contract 3: orphan investor, a widow, a solo, an orphan investment.
        INSERT INTO investor {_IC} VALUES (3000,30,3,NULL,NULL,0,0,1,0);
        INSERT INTO investment VALUES (300, 3, 'lp', 500, NULL, 0);
        INSERT INTO investor {_IC} VALUES (3001,40,3,NULL,2,1,0,0,0);
        INSERT INTO investor_group VALUES (3001,300,0);
        INSERT INTO investment VALUES (302, 3, 'lp', 200, NULL, 0);
        INSERT INTO investor {_IC} VALUES (3002,50,3,NULL,1,0,0,0,0);
        INSERT INTO investor_group VALUES (3002,302,0);
        INSERT INTO investment VALUES (301, 3, 'gp', 9000, NULL, 0);

        -- Contract 4: a soft-deleted investor must NOT appear (is_deleted audit).
        INSERT INTO investment VALUES (400, 4, 'gp', 1000, NULL, 0);
        INSERT INTO investor {_IC} VALUES (4000,10,4,NULL,NULL,0,0,0,0);
        INSERT INTO investor_group VALUES (4000,400,0);
        INSERT INTO investment VALUES (401, 4, 'lp', 500, NULL, 0);
        INSERT INTO investor {_IC} VALUES (4001,11,4,NULL,NULL,0,0,0,1);
        INSERT INTO investor_group VALUES (4001,401,0);

        -- Contract 5: a rich-attribute investor for the detail panel (Phase 4b).
        INSERT INTO investment VALUES (500, 5, 'lp', 300, NULL, 0);
        INSERT INTO investor (investor_id,person_id,contract_id,is_widow,husband_first_name,
                              husband_last_name,via_proxy,is_jewish,citizen_florence,title,place_of_origin)
            VALUES (5000,60,5,1,'Giovanni','Rossi',1,1,1,1,2);
        INSERT INTO investor_group VALUES (5000,500,0);

        -- Contract 6: the SAME person (10) double-linked to ONE investment — a
        -- dup_partner data fault. joint_count must count DISTINCT people (1), not
        -- links (2), so the stake is NOT falsely shown joint.
        INSERT INTO investment VALUES (600, 6, 'lp', 4000, NULL, 0);
        INSERT INTO investor {_IC} VALUES (6000,10,6,NULL,NULL,0,0,0,0);
        INSERT INTO investor {_IC} VALUES (6001,10,6,NULL,NULL,0,0,0,0);
        INSERT INTO investor_group VALUES (6000,600,0),(6001,600,0);
        """
    )
    return conn


@pytest.fixture(autouse=True)
def _no_proposals(monkeypatch):
    # Partners cells read correction chips from the proposal store; keep it empty.
    monkeypatch.setattr(review_server, "load_proposals", lambda: [])


def _by_person(result: dict, name_startswith: str) -> dict:
    for row in result["rows"]:
        if row["person"] and row["person"]["name"].startswith(name_startswith):
            return row
    raise AssertionError(f"no partner row starting {name_startswith!r}")


def test_encoding1_shared_tranche_marks_both_joint_with_count():
    res = review_server.build_partners(_conn(), 1)
    assert res["count"] == 2
    a, b = _by_person(res, "Antonio Corbinelli"), _by_person(res, "Antonio Lenzi")
    # Both joint from the DERIVED count — even the stale is_joint=0 sibling.
    for row in (a, b):
        assert row["cash"]["joint"] is True
        assert row["cash"]["joint_count"] == 2          # → UI shows "joint · 2"
        assert "joint" not in row["status"]             # never in Status


def test_encoding2_parallel_flag_marks_joint_without_count():
    res = review_server.build_partners(_conn(), 2)
    assert res["count"] == 2
    for row in res["rows"]:
        assert row["cash"]["joint"] is True             # from the stored flag
        assert row["cash"]["joint_count"] == 1          # → UI shows bare "joint"
        assert "joint" not in row["status"]


def test_same_person_double_linked_counts_distinct_people_not_links():
    # Regression: a person double-linked to one investment must not inflate the joint
    # badge. joint_count = distinct people (1); with no is_joint flag, not joint at all.
    res = review_server.build_partners(_conn(), 6)
    for row in res["rows"]:
        assert row["cash"]["joint_count"] == 1
        assert row["cash"]["joint"] is False


def test_orphan_investor_suppresses_badge_and_status_joint():
    res = review_server.build_partners(_conn(), 3)
    orphan = _by_person(res, "Orphan")
    # is_joint=1 but no investment → no stake to be joint with.
    assert orphan["role"] is None
    assert orphan["cash"]["joint"] is False
    assert orphan["status"] == "—"                      # joint dropped from status


def test_status_carries_personal_standing_only():
    res = review_server.build_partners(_conn(), 3)
    widow = _by_person(res, "Caterina")
    solo = _by_person(res, "Solo")
    assert widow["status"] == "widow"
    assert widow["cash"]["joint"] is False
    assert solo["status"] == "—"
    assert solo["cash"]["joint"] is False


def test_orphan_investment_surfaces_as_unattached():
    res = review_server.build_partners(_conn(), 3)
    unattached = [r for r in res["rows"] if r["person"] is None]
    assert len(unattached) == 1
    row = unattached[0]
    # the state is a flag the UI puts into words ("No partner attached" + Attach /
    # Remove); Status keeps meaning the partner's personal standing, so it is blank
    assert row["unattached"] is True
    assert row["investment_id"] == "301"
    assert row["status"] == "—"
    assert row["cash"]["joint"] is False
    assert row["key"] == "investment:301"
    assert row["removed"] is False


def test_joint_never_appears_in_status_anywhere():
    for cid in (1, 2, 3):
        for row in review_server.build_partners(_conn(), cid)["rows"]:
            assert "joint" not in row["status"], (cid, row)


def test_soft_deleted_investor_excluded_and_investment_unattached():
    # The is_deleted audit: a removed partner must vanish from the Partners view,
    # and the stake they alone held surfaces as 'unattached' (Phase-3 decision 1).
    res = review_server.build_partners(_conn(), 4)
    people = [r["person"]["name"] for r in res["rows"] if r["person"]]
    assert "Antonio Corbinelli" in people          # live
    assert "Antonio Lenzi" not in people           # soft-deleted → gone
    assert any(r["person"] is None and r["key"] == "investment:401" for r in res["rows"])


# --- remove / restore cascade -------------------------------------------------

def _cascade_db(tmp_path, monkeypatch):
    """File-backed main.db + corrections.db wired into the server's openers."""
    main = str(tmp_path / "main.db")
    corr = str(tmp_path / "corrections.db")
    c = sqlite3.connect(main)
    ic = _IC
    c.executescript(
        _SCHEMA
        + f"""
        INSERT INTO person (person_id, first_name, last_name) VALUES
            (10,'Antonio','Corbinelli'),(11,'Antonio','Lenzi'),(12,'Solo','Gp');
        -- joint tranche: investment 100 shared by 1000 & 1001, both flagged joint
        INSERT INTO investment VALUES (100,1,'lp',1500,NULL,0);
        INSERT INTO investor {ic} VALUES (1000,10,1,NULL,NULL,0,0,1,0);
        INSERT INTO investor {ic} VALUES (1001,11,1,NULL,NULL,0,0,1,0);
        INSERT INTO investor_group VALUES (1000,100,0),(1001,100,0);
        -- solo partner: investment 101 held only by 1002
        INSERT INTO investment VALUES (101,1,'gp',5000,NULL,0);
        INSERT INTO investor {ic} VALUES (1002,12,1,NULL,NULL,0,0,0,0);
        INSERT INTO investor_group VALUES (1002,101,0);
        """
    )
    c.commit()
    c.close()

    def open_db():
        conn = sqlite3.connect(main)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(Path(corr)))
    return open_db


def _val(open_db, sql, *params):
    c = open_db()
    try:
        return c.execute(sql, params).fetchone()[0]
    finally:
        c.close()


def test_remove_one_of_joint_pair_rederives_survivor(tmp_path, monkeypatch):
    open_db = _cascade_db(tmp_path, monkeypatch)
    res = review_server.remove_partner("1", "1001", RecordAction(reviewer="WH", reason="duplicate"))
    assert res["ok"] and res["left_unattached"] is False
    # removed investor + its link soft-deleted; the lone survivor is no longer joint
    assert _val(open_db, "SELECT is_deleted FROM investor WHERE investor_id=1001") == 1
    assert _val(open_db, "SELECT is_deleted FROM investor_group WHERE investor_id=1001") == 1
    assert _val(open_db, "SELECT is_joint FROM investor WHERE investor_id=1000") == 0   # re-derived
    # investment 100 stays (survivor still holds it) — not unattached
    assert _val(open_db, "SELECT is_deleted FROM investment WHERE investment_id=100") == 0
    c = open_db()
    try:
        assert review_server.build_partners(c, "1")["count"] == 2           # survivor + solo
        assert review_server.build_partners(c, "1", include_hidden=True)["removed_count"] == 1
    finally:
        c.close()


def test_remove_solo_leaves_investment_unattached(tmp_path, monkeypatch):
    open_db = _cascade_db(tmp_path, monkeypatch)
    res = review_server.remove_partner("1", "1002", RecordAction(reviewer="WH", reason="error"))
    assert res["left_unattached"] is True
    assert _val(open_db, "SELECT is_deleted FROM investor WHERE investor_id=1002") == 1
    assert _val(open_db, "SELECT is_deleted FROM investment WHERE investment_id=101") == 0  # left in place
    c = open_db()
    try:
        rows = review_server.build_partners(c, "1")["rows"]
        assert any(r["person"] is None and r["key"] == "investment:101" for r in rows)  # unattached
    finally:
        c.close()


def test_restore_partner_round_trip_restores_joint(tmp_path, monkeypatch):
    open_db = _cascade_db(tmp_path, monkeypatch)
    review_server.remove_partner("1", "1001", RecordAction(reviewer="WH", reason="dup"))
    review_server.restore_partner("1", "1001", RecordAction(reviewer="WH"))
    assert _val(open_db, "SELECT is_deleted FROM investor WHERE investor_id=1001") == 0
    assert _val(open_db, "SELECT is_deleted FROM investor_group WHERE investor_id=1001") == 0
    # tranche is shared again → both flagged joint
    assert _val(open_db, "SELECT is_joint FROM investor WHERE investor_id=1000") == 1
    assert _val(open_db, "SELECT is_joint FROM investor WHERE investor_id=1001") == 1


def test_remove_requires_reason_and_rejects_double_remove(tmp_path, monkeypatch):
    from fastapi import HTTPException

    _cascade_db(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        review_server.remove_partner("1", "1002", RecordAction(reviewer="WH"))  # no reason
    review_server.remove_partner("1", "1002", RecordAction(reviewer="WH", reason="x"))
    with pytest.raises(HTTPException):
        review_server.remove_partner("1", "1002", RecordAction(reviewer="WH", reason="again"))  # already removed


def test_remove_logs_audit_ops(tmp_path, monkeypatch):
    _cascade_db(tmp_path, monkeypatch)
    corr = str(tmp_path / "corrections.db")
    review_server.remove_partner("1", "1001", RecordAction(reviewer="WH", reason="dup"))
    clog = corrections_db.connect(Path(corr))
    try:
        ops = clog.execute(
            "SELECT op, db_table FROM change_request ORDER BY created_at"
        ).fetchall()
    finally:
        clog.close()
    kinds = {(o[0], o[1]) for o in ops}
    assert ("delete", "investor") in kinds
    assert ("delete", "investor_group") in kinds
    assert ("update", "investor") in kinds   # the is_joint re-derive on the survivor


# --- per-partner detail panel (Phase 4b) --------------------------------------

def _attr(row: dict, label: str) -> dict:
    for g in row["attributes"]["groups"]:
        for f in g["fields"]:
            if f["label"] == label:
                return f
    raise AssertionError(f"no attribute field {label!r}")


def test_partner_attributes_grouping_and_notable_count():
    res = review_server.build_partners(_conn(), 5)
    row = _by_person(res, "Margherita")
    attrs = row["attributes"]
    # citizen + via_proxy + widow + jewish + husband + origin = 6
    assert attrs["notable"] == 6
    assert [g["label"] for g in attrs["groups"]] == [
        "Origin & citizenship", "Religion", "Capacity — how they participate", "Titles",
    ]
    # bool field → editable cell, canonical current
    jewish = _attr(row, "Jewish — stated in the act")
    assert jewish["cell"]["input_type"] == "bool"
    assert jewish["cell"]["current"] == "1"
    assert jewish["cell"]["db_row_id"] == "investor:5000"
    # the legacy editorial layer is display-only: value + provenance note, no
    # cell, and no refute action when the flag is 0
    editorial = _attr(row, "Jewish — editorial attribution")
    assert "cell" not in editorial
    assert editorial["value"] == "No"
    assert "data-entry team" in editorial["note"]
    assert editorial["refute"] is None
    # text field surfaced (was editable since Phase 1, now shown)
    assert _attr(row, "Husband — first name")["cell"]["current"] == "Giovanni"
    # FK title is now a relink field (re-point to a lookup phrase), not a cell
    title = _attr(row, "Title")
    assert "cell" not in title
    assert title["value"] == "monna"
    assert title["relink"]["kind"] == "title"
    assert title["relink"]["field"] == "title"
    assert title["relink"]["current"] == "monna"


def test_editorial_attribution_display_and_refute_marker():
    conn = _conn()
    # an attributed-only investor: jewish_db=1, is_jewish=0 — the exact B4 case
    conn.executescript(
        """
        INSERT INTO person (person_id, first_name, last_name) VALUES (70,'Leone','Tedesco');
        INSERT INTO investment VALUES (700, 7, 'lp', 800, NULL, 0);
        INSERT INTO investor (investor_id,person_id,contract_id,jewish_db) VALUES (7000,70,7,1);
        INSERT INTO investor_group VALUES (7000,700,0);
        """
    )
    res = review_server.build_partners(conn, 7)
    row = _by_person(res, "Leone")
    attrs = row["attributes"]
    stated = _attr(row, "Jewish — stated in the act")
    assert stated["cell"]["current"] == "0"          # the record page must NOT say plain "No" alone…
    editorial = _attr(row, "Jewish — editorial attribution")
    assert editorial["value"] == "Yes"               # …while the analyses count this investor as Jewish
    assert editorial["refute"] == {"investor_id": "7000"}
    # the attribution is a notable attr (drives the expand cue)
    assert attrs["notable"] == 1


def test_bare_investor_has_zero_notable():
    res = review_server.build_partners(_conn(), 1)
    row = _by_person(res, "Antonio Corbinelli")   # only profession + residence
    assert row["attributes"]["notable"] == 0


def test_removed_partner_carries_no_attributes():
    res = review_server.build_partners(_conn(), 4, include_hidden=True)
    removed = [r for r in res["rows"] if r["removed"]]
    assert removed and all(r["attributes"] is None for r in removed)
