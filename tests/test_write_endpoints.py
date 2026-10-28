"""Write-endpoint tests — the doors that had none (code-review rebuild batch).

Two families, each tested the same three-part way (does the button work; did it
write the right op-log entries; does the result survive a fresh seed + replay):

 * the generic inline editor — apply_correction / revert_correction, whose own
   docstring names the danger ("without this, a main.db rebuild would silently
   drop the edit"): the live UPDATE, the logged before/after, and the replay
   pre-image check must agree three ways;
 * the create doors — contract / sub-act / investor. These rows exist ONLY in
   the op-log (no Word source, no archive dump behind them): a malformed create
   op does not mangle them on rebuild, it makes them vanish.

    uv run --with pytest pytest tests/test_write_endpoints.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, db_import, review_server
from workflows.review_server import (
    ContractCreate,
    CorrectionAction,
    CorrectionCreate,
    InvestorCreate,
    NewPerson,
    SubContractCreate,
)

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"

_SEED = """
INSERT INTO contract (contract_id, archive, series, folder, folio, registration_date,
                      firm_name, total, document) VALUES
 (1,'ASF','Mercanzia','10831','10r','1601-05-10','Compagnia Vecchia',1500,'Accomandita in Firenze.');
INSERT INTO person (person_id, first_name, last_name) VALUES (10,'Baccio','Martelli'),(11,'Lena','Rucellai');
INSERT INTO title (title_id, title_name) VALUES (2,'messer');
INSERT INTO investment (investment_id, contract_id, type, investment_cash) VALUES (200,1,'gp',1000);
INSERT INTO investor (investor_id, person_id, contract_id, is_joint) VALUES (100,10,1,0);
INSERT INTO investor_group (investor_id, investment_id) VALUES (100,200);
"""


def _make_db(path: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(_TABLES_SQL.read_text() + _SEED)
    db_import.add_soft_delete_columns(c)
    c.commit()
    c.close()


def _wire(tmp_path, monkeypatch):
    main = str(tmp_path / "main.db")
    corr = str(tmp_path / "corr.db")
    _make_db(main)

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(Path(corr)))
    # apply/revert open main.db via db_path(), and the proposal store lives in JSONL files
    monkeypatch.setattr(review_server, "db_path", lambda: Path(main))
    store = tmp_path / "corrstore"
    monkeypatch.setattr(review_server, "CORRECTIONS_DIR", store)
    monkeypatch.setattr(review_server, "PROPOSALS_PATH", store / "proposals.jsonl")
    monkeypatch.setattr(review_server, "EVENTS_PATH", store / "events.jsonl")

    def rebuild() -> tuple[sqlite3.Connection, dict]:
        fresh = str(tmp_path / f"fresh-{len(list(tmp_path.iterdir()))}.db")
        _make_db(fresh)
        monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", corr)
        fc = sqlite3.connect(fresh)
        stats = db_import.replay_corrections(fc)
        fc.commit()
        return fc, stats

    return open_db, corr, rebuild


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
        return [
            dict(r) for r in clog.execute(
                "SELECT op, db_table, field, before_value, after_value FROM change_request ORDER BY created_at"
            )
        ]
    finally:
        clog.close()


def _proposal(field, value, *, row="contract:1", change_type="correct", rationale="checked the act"):
    created = review_server.create_correction(CorrectionCreate(
        reviewer="WH", db_row_id=row, field=field,
        change_type=change_type, proposed_value=value, rationale=rationale,
    ))
    pid = created["proposal"]["proposal_id"]
    review_server.approve_correction(pid, CorrectionAction(reviewer="FT"))
    return pid


# ---------------------------------------------------------------------------
# The generic inline editor: apply / revert
# ---------------------------------------------------------------------------

def test_apply_writes_logs_and_survives_rebuild(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    pid = _proposal("folio", "99v")
    review_server.apply_correction(pid, CorrectionAction(reviewer="WH"))
    assert _val(open_db, "SELECT folio FROM contract WHERE contract_id=1") == "99v"
    # the three-way agreement: live write ↔ logged op ↔ replay pre-image
    op = _ops(corr)[0]
    assert (op["op"], op["field"], op["before_value"], op["after_value"]) == ("update", "folio", "10r", "99v")
    fc, stats = rebuild()
    assert stats == {"applied": 1, "conflicts": 0, "skipped": 0}
    assert fc.execute("SELECT folio FROM contract WHERE contract_id=1").fetchone()[0] == "99v"
    fc.close()


def test_apply_refuses_when_db_drifted_and_logs_nothing(tmp_path, monkeypatch):
    open_db, corr, _ = _wire(tmp_path, monkeypatch)
    pid = _proposal("firm_name", "Compagnia Nuova")
    x = open_db()  # someone edits the row while the proposal waits
    with x:
        x.execute("UPDATE contract SET firm_name='Renamed meanwhile' WHERE contract_id=1")
    x.close()
    with pytest.raises(HTTPException) as e:
        review_server.apply_correction(pid, CorrectionAction(reviewer="WH"))
    assert e.value.status_code == 409
    assert _val(open_db, "SELECT firm_name FROM contract WHERE contract_id=1") == "Renamed meanwhile"
    assert _ops(corr) == []  # a refused apply must leave no op behind


def test_apply_clear_sets_null_and_survives_rebuild(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    pid = _proposal("firm_name", "", change_type="clear")
    review_server.apply_correction(pid, CorrectionAction(reviewer="WH"))
    assert _val(open_db, "SELECT firm_name FROM contract WHERE contract_id=1") is None
    fc, stats = rebuild()
    assert stats["conflicts"] == 0
    assert fc.execute("SELECT firm_name FROM contract WHERE contract_id=1").fetchone()[0] is None
    fc.close()


def test_apply_coerces_bool_and_number_to_int(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    review_server.apply_correction(
        _proposal("is_jewish", "1", row="investor:100"), CorrectionAction(reviewer="WH"))
    review_server.apply_correction(
        _proposal("total", "2000"), CorrectionAction(reviewer="WH"))
    x = open_db()
    try:  # stored as ints, not the strings the editor sends
        assert x.execute("SELECT is_jewish FROM investor WHERE investor_id=100").fetchone()[0] == 1
        assert x.execute("SELECT total FROM contract WHERE contract_id=1").fetchone()[0] == 2000
    finally:
        x.close()
    fc, stats = rebuild()
    assert stats["conflicts"] == 0
    assert fc.execute("SELECT is_jewish FROM investor WHERE investor_id=100").fetchone()[0] == 1
    assert fc.execute("SELECT total FROM contract WHERE contract_id=1").fetchone()[0] == 2000
    fc.close()


def test_revert_refuses_after_a_later_edit(tmp_path, monkeypatch):
    # The drift guard (finding A4/D4): revert may only undo the value THIS
    # proposal applied — a later edit to the same field must never be clobbered.
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    pid = _proposal("folio", "99v")
    review_server.apply_correction(pid, CorrectionAction(reviewer="WH"))
    x = open_db()  # a colleague improves the field afterwards
    with x:
        x.execute("UPDATE contract SET folio='100r' WHERE contract_id=1")
    x.close()
    with pytest.raises(HTTPException) as e:
        review_server.revert_correction(pid, CorrectionAction(reviewer="WH"))
    assert e.value.status_code == 409 and "edited since" in e.value.detail
    assert _val(open_db, "SELECT folio FROM contract WHERE contract_id=1") == "100r"  # newer work intact
    assert len(_ops(corr)) == 1  # only the apply's op — the refused revert logged nothing
    assert review_server.proposal_by_id(pid)["status"] == "applied"


def test_revert_restores_and_replay_nets_out(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    pid = _proposal("folio", "99v")
    review_server.apply_correction(pid, CorrectionAction(reviewer="WH"))
    review_server.revert_correction(pid, CorrectionAction(reviewer="WH"))
    assert _val(open_db, "SELECT folio FROM contract WHERE contract_id=1") == "10r"
    ops = _ops(corr)  # a revert is itself a logged, compensating update
    assert [(o["before_value"], o["after_value"]) for o in ops] == [("10r", "99v"), ("99v", "10r")]
    fc, stats = rebuild()
    assert stats == {"applied": 2, "conflicts": 0, "skipped": 0}
    assert fc.execute("SELECT folio FROM contract WHERE contract_id=1").fetchone()[0] == "10r"
    fc.close()


# ---------------------------------------------------------------------------
# The create doors: rows whose only survival mechanism is the logged snapshot
# ---------------------------------------------------------------------------

def test_create_contract_logged_and_survives_rebuild(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    res = review_server.create_contract(ContractCreate(
        reviewer="WH", source="ASF Mercanzia 10832, c. 40r", folder="10832", folio="40r",
        registration_date="1610-02-11", firm_name="Nuova compagnia",
        economic_activity="arte della seta", total=2000,
        document="Accomandita nuova registrata in Mercanzia.",
    ))
    new_id = int(res["id"])
    kinds = [(o["op"], o["db_table"]) for o in _ops(corr)]
    assert ("create", "economic_activity") in kinds and ("create", "contract") in kinds
    fc, stats = rebuild()
    assert stats["conflicts"] == 0 and stats["skipped"] == 0
    row = fc.execute(
        "SELECT folder, folio, registration_date, firm_name, total, document, is_deleted "
        "FROM contract WHERE contract_id=?", (new_id,)).fetchone()
    assert row == ("10832", "40r", "1610-02-11", "Nuova compagnia", 2000,
                   "Accomandita nuova registrata in Mercanzia.", 0)
    # the minted lookup came back too, and the FK still points at it
    sector = fc.execute("SELECT economic_sector FROM contract WHERE contract_id=?", (new_id,)).fetchone()[0]
    assert fc.execute("SELECT activity FROM economic_activity WHERE ec_activity_id=?", (sector,)).fetchone()[0] == "arte della seta"
    fc.close()
    # DB-native rows are recognisable as such (matcher/coverage exclusion)
    clog = corrections_db.connect(Path(corr))
    try:
        assert f"contract:{new_id}" in corrections_db.created_row_ids(clog)
    finally:
        clog.close()


def test_create_contract_register_number_collision_409(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        review_server.create_contract(ContractCreate(
            reviewer="WH", source="ASF Mercanzia", folder="10832", folio="40r",
            registration_date="1610-02-11", register_number=1,
            document="Should be a sub-contract instead.",
        ))
    assert e.value.status_code == 409


def test_create_sub_contract_logged_and_survives_rebuild(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    res = review_server.create_sub_contract(SubContractCreate(
        reviewer="WH", source="ASF Mercanzia 10831, c. 44r", main_contract_id=1,
        sub_type="termination", folder="10831", folio="44r",
        registration_date="1620-06-01", document="Cessazione della accomandita.",
    ))
    new_id = int(res["id"])
    assert ("create", "sub_contract") in [(o["op"], o["db_table"]) for o in _ops(corr)]
    fc, stats = rebuild()
    assert stats["conflicts"] == 0
    row = fc.execute(
        "SELECT main_contract_id, sub_type, folio, registration_date, is_deleted "
        "FROM sub_contract WHERE contract_id=?", (new_id,)).fetchone()
    assert row == (1, "termination", "44r", "1620-06-01", 0)
    fc.close()


def test_create_investor_full_cascade_survives_rebuild(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    res = review_server.create_investor(InvestorCreate(
        reviewer="WH", contract_id=1,
        new_person=NewPerson(first_name="Piero", last_name="Capponi"),
        role="lp", investment_cash=300, title="ser", residence="Pisa",
    ))
    # every write in the cascade has its paired create op
    kinds = [(o["op"], o["db_table"]) for o in _ops(corr)]
    for expected in [("create", "person"), ("create", "title"), ("create", "place"),
                     ("create", "investment"), ("create", "investor"), ("create", "investor_group")]:
        assert expected in kinds, expected
    inv_id, person_id = int(res["investor_id"]), int(res["person_id"])
    fc, stats = rebuild()
    assert stats["conflicts"] == 0 and stats["skipped"] == 0
    assert fc.execute("SELECT first_name, last_name FROM person WHERE person_id=?", (person_id,)).fetchone() == ("Piero", "Capponi")
    row = fc.execute(
        "SELECT person_id, contract_id, is_joint, is_deleted FROM investor WHERE investor_id=?", (inv_id,)).fetchone()
    assert row == (person_id, 1, 0, 0)
    inv = fc.execute(
        "SELECT type, investment_cash FROM investment WHERE investment_id = "
        "(SELECT investment_id FROM investor_group WHERE investor_id=?)", (inv_id,)).fetchone()
    assert inv == ("lp", 300)
    assert fc.execute(
        "SELECT title_name FROM title WHERE title_id=(SELECT title FROM investor WHERE investor_id=?)",
        (inv_id,)).fetchone()[0] == "ser"
    fc.close()


def test_create_investor_join_derives_sibling_is_joint(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    res = review_server.create_investor(InvestorCreate(
        reviewer="WH", contract_id=1, person_id=11, join_investment_id=200,
    ))
    inv_id = int(res["investor_id"])
    assert res["joined_existing"] is True
    # the structural derivation: joiner born joint, lone sibling flipped — and
    # the sibling flip is its own audited update op (else it dies on rebuild)
    assert _val(open_db, "SELECT is_joint FROM investor WHERE investor_id=?", inv_id) == 1
    assert _val(open_db, "SELECT is_joint FROM investor WHERE investor_id=100") == 1
    sib = [o for o in _ops(corr) if o["op"] == "update" and o["field"] == "is_joint"]
    assert len(sib) == 1 and (sib[0]["before_value"], sib[0]["after_value"]) == ("0", "1")
    fc, stats = rebuild()
    assert stats["conflicts"] == 0 and stats["skipped"] == 0
    assert fc.execute("SELECT is_joint FROM investor WHERE investor_id=100").fetchone()[0] == 1
    assert fc.execute("SELECT is_joint FROM investor WHERE investor_id=?", (inv_id,)).fetchone()[0] == 1
    fc.close()


def test_placeholder_text_is_refused_at_every_door(tmp_path, monkeypatch):
    """"NULL", "none", a dash: never a value (LOG 2026-09-10, multi_user_safety D8).
    The inline editor and all three create forms refuse it before any write."""
    open_db, corr, _ = _wire(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        _proposal("firm_name", "NULL")
    assert e.value.status_code == 400 and "placeholder" in e.value.detail
    with pytest.raises(HTTPException) as e:
        review_server.create_contract(review_server.ContractCreate(
            reviewer="WH", source="the act", folder="10831", folio="7r", registration_date="1445-04-19",
            firm_name="-", document="a narrative long enough"))
    assert e.value.status_code == 400 and "Firm name" in e.value.detail
    with pytest.raises(HTTPException) as e:
        review_server.create_sub_contract(review_server.SubContractCreate(
            reviewer="WH", source="the act", main_contract_id=1, sub_type="termination", folder="10831",
            folio="none", registration_date="1450-01-01", document="a narrative long enough"))
    assert e.value.status_code == 400 and "Folio" in e.value.detail
    with pytest.raises(HTTPException) as e:
        review_server.create_investor(InvestorCreate(
            reviewer="WH", contract_id=1, role="lp", investment_cash=100,
            new_person=review_server.NewPerson(first_name="Piero", last_name="null")))
    assert e.value.status_code == 400 and "Last name" in e.value.detail
    assert _ops(corr) == []  # nothing reached the op-log


def test_create_investor_guards(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:  # neither an existing nor a new person
        review_server.create_investor(InvestorCreate(reviewer="WH", contract_id=1, role="gp"))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:  # own tranche needs a real role
        review_server.create_investor(InvestorCreate(reviewer="WH", contract_id=1, person_id=10, role="owner"))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:  # joining a tranche of another contract
        review_server.create_investor(InvestorCreate(
            reviewer="WH", contract_id=1, person_id=10, join_investment_id=999))
    assert e.value.status_code == 404
