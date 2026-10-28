"""Tests for the Tier-2 structural integrity report (read-only).

Each check must fire on a seeded broken row and NOT on clean structure.

    uv run --with pytest pytest tests/test_integrity_report.py
"""

from __future__ import annotations

import sqlite3

from workflows import integrity_report

_SCHEMA = """
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, firm_name TEXT, folio TEXT, document TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, contract_id INTEGER, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investment (investment_id INTEGER PRIMARY KEY, contract_id INTEGER, type TEXT,
    investment_cash INTEGER, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor_group (investor_id INTEGER, investment_id INTEGER, is_deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (investor_id, investment_id));
CREATE TABLE sub_contract (contract_id INTEGER PRIMARY KEY, main_contract_id INTEGER, sub_type TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE contract_place (place_id INTEGER, contract_id INTEGER,
    is_deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (place_id, contract_id));
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.executescript(
        """
        -- c1, c2 have partners + a narrative; c3 is an empty stub (no partners, no narrative)
        INSERT INTO contract VALUES (1,'Firm A','1r','a narrative',0),
                                    (2,'Firm B','2r','a narrative',0),
                                    (3,'Stub firm','9r','',0);
        INSERT INTO investor VALUES (10,1,0),(11,2,0);
        INSERT INTO investment VALUES (100,1,'lp',500,0),(200,2,'gp',0,0),(300,999,'lp',7,0);
        -- clean: investor 10 (c1) -> investment 100 (c1) — trips nothing
        INSERT INTO investor_group VALUES (10,100,0);
        -- cross-contract: investor 11 (c2) -> investment 100 (c1)
        INSERT INTO investor_group VALUES (11,100,0);
        -- junction to a missing investor (888)
        INSERT INTO investor_group VALUES (888,100,0);
        -- junction to a missing investment (777)
        INSERT INTO investor_group VALUES (10,777,0);
        -- sub-contract with a missing parent
        INSERT INTO sub_contract VALUES (50,999,'termination',0);
        -- place-link on a missing contract
        INSERT INTO contract_place VALUES (5,999,0);
        """
    )
    c.commit()
    return c


def _counts():
    return {r["key"]: r["count"] for r in integrity_report.collect(_conn())}


def test_every_check_fires_once():
    counts = _counts()
    for key in (
        "cross_contract_link",
        "subcontract_missing_parent",
        "orphan_link_missing_investor",
        "orphan_link_missing_investment",
        "investment_missing_contract",
        "contract_place_missing_contract",
        "incomplete_contract",
    ):
        assert counts[key] == 1, (key, counts[key])


def test_clean_link_trips_nothing_extra():
    # the clean (10,100) link must not appear as cross-contract or orphan
    c = _counts()
    assert c["cross_contract_link"] == 1  # only the (11,100) one, not (10,100)


def test_render_is_readonly_text():
    results = integrity_report.collect(_conn(), examples=2)
    text = integrity_report.render(results)
    assert "TOTAL structural faults: 7" in text
    assert "cross_contract_link" in text
    assert "incomplete_contract" in text
