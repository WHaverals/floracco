"""Tests for the DB-intrinsic 'Needs review' flags (Tier 1).

Each check must fire on a seeded bad row and NOT on a clean one; soft-deleted
rows are excluded. Contracts are built 'clean' (dated, valid sector, gp+lp) so
each test contract trips exactly one check.

    uv run --with pytest pytest tests/test_data_quality.py
"""

from __future__ import annotations

import sqlite3

import pytest

from workflows import data_quality

_SCHEMA = """
CREATE TABLE economic_activity (ec_activity_id INTEGER PRIMARY KEY, activity TEXT);
CREATE TABLE place (place_id INTEGER PRIMARY KEY, place_name TEXT);
CREATE TABLE person (person_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, is_woman INTEGER DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, firm_name TEXT, registration_date TEXT,
    economic_sector INTEGER, document TEXT, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sub_contract (contract_id INTEGER PRIMARY KEY, sub_firm_name TEXT, sub_type TEXT,
    main_contract_id INTEGER DEFAULT 0, registration_date TEXT, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, person_id INTEGER, contract_id INTEGER,
    place_of_residence INTEGER DEFAULT 0, place_of_origin INTEGER DEFAULT 0,
    is_guardian INTEGER DEFAULT 0, guardian_of TEXT, is_widow INTEGER DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investment (investment_id INTEGER PRIMARY KEY, contract_id INTEGER, type TEXT,
    investment_cash INTEGER, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor_group (investor_id INTEGER, investment_id INTEGER, is_deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (investor_id, investment_id));
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.executescript(
        """
        INSERT INTO economic_activity VALUES (10,'arte di lana');
        INSERT INTO place VALUES (5,'Firenze');
        INSERT INTO person (person_id, first_name, last_name) VALUES
            (1,'Mario','Rossi'),(2,'Carla','Bianchi'),(100,'Dup','Licato'),(200,'Gp','Person'),(300,NULL,NULL);
        """
    )
    iid = [1000]
    invid = [5000]

    def clean_partners(cid):
        # one gp + one lp so a contract trips no 'no_partners'/'no_gp'/dup checks
        for role, pid in (("gp", 1), ("lp", 2)):
            c.execute("INSERT INTO investment VALUES (?,?,?,?,0)", (invid[0], cid, role, 100))
            c.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (?,?,?)", (iid[0], pid, cid))
            c.execute("INSERT INTO investor_group VALUES (?,?,0)", (iid[0], invid[0]))
            iid[0] += 1
            invid[0] += 1

    def contract(cid, sector=0, reg="1500-01-01", deleted=0, firm="Firm", document="a recorded narrative"):
        c.execute("INSERT INTO contract VALUES (?,?,?,?,?,?)", (cid, firm, reg, sector, document, deleted))

    # C1 broken economic sector (999 missing) — otherwise clean
    contract(1, sector=999); clean_partners(1)
    # C2 missing registration date
    contract(2, reg="0000-00-00"); clean_partners(2)
    # C3 no partners
    contract(3)
    # C4 duplicate partner (person 100 twice as lp, same investment+cash) + a gp so no_gp doesn't fire
    contract(4)
    c.execute("INSERT INTO investment VALUES (8001,4,'lp',300,0)")
    c.execute("INSERT INTO investment VALUES (8002,4,'gp',0,0)")
    c.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (8101,100,4),(8102,100,4),(8103,200,4)")
    c.execute("INSERT INTO investor_group VALUES (8101,8001,0),(8102,8001,0),(8103,8002,0)")
    # C5 unnamed person (300) referenced by a live investor on a clean contract
    contract(5); clean_partners(5)
    c.execute("INSERT INTO investment VALUES (8200,5,'lp',50,0)")
    c.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (8201,300,5)")
    c.execute("INSERT INTO investor_group VALUES (8201,8200,0)")
    # C6 broken place (residence 999 missing)
    contract(6); clean_partners(6)
    c.execute("UPDATE investor SET place_of_residence=999 WHERE investor_id=(SELECT MIN(investor_id) FROM investor WHERE contract_id=6)")
    # C7 guardian, no ward
    contract(7); clean_partners(7)
    c.execute("UPDATE investor SET is_guardian=1, guardian_of='' WHERE investor_id=(SELECT MIN(investor_id) FROM investor WHERE contract_id=7)")
    # C8 no general partner (only lp)
    contract(8)
    c.execute("INSERT INTO investment VALUES (8300,8,'lp',100,0)")
    c.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (8301,1,8)")
    c.execute("INSERT INTO investor_group VALUES (8301,8300,0)")
    # C9 sub_contract missing type (real sub — has firm + parent + date → reviewer task)
    c.execute("INSERT INTO sub_contract VALUES (9001,'Sub','',1,'1600-01-01',0)")
    # C9b a fully-blank PLACEHOLDER sub (no parent, no date, no firm) → must NOT flag
    #   missing_sub_type (it's cruft for the maintainer integrity report, not a reviewer task)
    c.execute("INSERT INTO sub_contract VALUES (9002,'','',0,'0000-00-00',0)")
    # C12 empty STUB contract: no partners AND no narrative → must NOT flag no_partners or
    #   missing_reg_date (goes to the integrity report's incomplete_contract, not the queue)
    contract(12, reg="0000-00-00", document="")
    # C11 widow (person 400) not marked a woman, on a clean contract
    c.execute("INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES (400,'Laura','Verzelli',0)")
    contract(11); clean_partners(11)
    c.execute("INSERT INTO investment VALUES (8400,11,'lp',60,0)")
    c.execute("INSERT INTO investor (investor_id, person_id, contract_id, is_widow) VALUES (8401,400,11,1)")
    c.execute("INSERT INTO investor_group VALUES (8401,8400,0)")
    # C10 soft-deleted broken-sector contract → must NOT flag
    contract(10, sector=999, deleted=1)
    # C13 placeholder text saved as a firm name (the archive export's one 'NULL')
    contract(13, firm="NULL"); clean_partners(13)
    # C14 soft-deleted placeholder → must NOT flag
    contract(14, firm="null", deleted=1)
    # C15 a stake nobody is linked to (investment without any investor_group row)
    contract(15); clean_partners(15)
    c.execute("INSERT INTO investment VALUES (8500,15,'lp',1000,0)")
    # C16 the same, but the stake was removed (soft-deleted) → must NOT flag
    contract(16); clean_partners(16)
    c.execute("INSERT INTO investment VALUES (8501,16,'lp',500,1)")
    c.commit()
    return c


def _groups(conn):
    g = {}
    for f in data_quality.flags(conn):
        g.setdefault(f["group"], []).append(f)
    return g


def test_each_tier1_check_fires_once():
    g = _groups(_conn())
    expect = {
        "broken_economic_sector", "missing_reg_date", "no_partners", "dup_partner",
        "person_no_name", "broken_place", "guardian_no_ward", "no_gp", "missing_sub_type",
        "widow_not_woman", "placeholder_text", "orphan_stake",
    }
    assert expect.issubset(set(g)), f"missing groups: {expect - set(g)}"
    for name in expect:
        assert len(g[name]) == 1, (name, g[name])


def test_orphan_stake_flag_points_at_the_partners_block():
    flags = _groups(_conn())["orphan_stake"]
    assert len(flags) == 1
    f = flags[0]
    assert (f["table"], f["pk"]) == ("contract", "15")
    assert f["fix"] == {"kind": "review_partners", "field": None}
    assert f["key"] == "contract:15:orphan_stake:8500"
    assert f["title"].endswith("External investor (accomandante) · 1000")


def test_placeholder_text_helper():
    for bad in ("NULL", "null", " Null ", "none", "N/A", "-", "—", "–"):
        assert data_quality.is_placeholder_text(bad), bad
    for ok in (None, "", "Nullo Nulli e compagni", "n.n", "-- see act", "0"):
        assert not data_quality.is_placeholder_text(ok), ok


def test_placeholder_text_flag_opens_the_field_editor():
    flags = _groups(_conn())["placeholder_text"]
    assert len(flags) == 1
    f = flags[0]
    assert (f["table"], f["pk"]) == ("contract", "13")
    assert f["fix"] == {"kind": "edit", "field": "firm_name"}
    assert f["key"] == "contract:13:placeholder_text:firm_name"
    assert "NULL" in f["title"]
    # columns the fixture schema lacks (folio) are skipped, not errors
    assert "folio" not in {x["fix"]["field"] for x in flags}


def test_soft_deleted_contract_is_not_flagged():
    g = _groups(_conn())
    # only C1 (not the deleted C10) should appear in broken_economic_sector
    pks = {f["pk"] for f in g["broken_economic_sector"]}
    assert pks == {"1"}


def test_clean_contract_has_no_flags():
    # C1's clean siblings (gp+lp, dated, sector 0) must not trip no_partners/no_gp/dup
    g = _groups(_conn())
    no_partner_pks = {f["pk"] for f in g.get("no_partners", [])}
    no_gp_pks = {f["pk"] for f in g.get("no_gp", [])}
    assert no_partner_pks == {"3"}   # C3: no partners but HAS a narrative → reviewer can add them
    assert no_gp_pks == {"8"}        # only the lp-only one


def test_empty_stubs_and_placeholders_are_excluded():
    # Recategorization: empty-stub contracts + fully-blank placeholder subs are NOT reviewer
    # tasks (they go to the maintainer integrity report), so the queue must not flag them.
    g = _groups(_conn())
    assert "12" not in {f["pk"] for f in g.get("no_partners", [])}       # C12 stub: no partners, no narrative
    assert "12" not in {f["pk"] for f in g.get("missing_reg_date", [])}  # C12 stub: not a date task either
    assert "9002" not in {f["pk"] for f in g.get("missing_sub_type", [])}  # C9b: blank placeholder sub


def test_fix_descriptors_and_keys():
    g = _groups(_conn())
    sec = g["broken_economic_sector"][0]
    assert sec["fix"] == {"kind": "relink", "field": "economic_sector"}
    assert sec["key"] == "contract:1:broken_economic_sector"
    assert sec["severity"] == "high"
    guardian = g["guardian_no_ward"][0]
    assert guardian["fix"]["kind"] == "partner_field" and guardian["fix"]["field"] == "guardian_of"
    assert "investor_id" in guardian["fix"]
    place = g["broken_place"][0]
    assert place["fix"]["kind"] == "partner_field" and place["fix"]["field"] in ("place_of_residence", "place_of_origin")


def test_explanations_speak_of_the_source_not_the_act():
    """UI review 2026-10-14, item 17: "the act" read as the old name for a
    sub-contract; the explanations now point to the source."""
    import re

    from workflows import jewish_review

    for meta in (data_quality.GROUP_META, jewish_review.GROUP_META):
        for gid, m in meta.items():
            text = re.sub(r"stated in the act", "", m["explanation"], flags=re.I)  # the PI's own label stays
            assert not re.search(r"\bact\b|\blater acts?\b", text, re.I), (gid, text)
    assert "0000" not in data_quality.GROUP_META["missing_reg_date"]["explanation"]


def test_unnamed_person_title_names_the_entry():
    flags = _groups(_conn())["person_no_name"]
    assert flags and all(f["title"].startswith("Person entry #") for f in flags)
