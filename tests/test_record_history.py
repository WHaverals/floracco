"""A record page's change history is complete (LOG 2026-10-27).

The history used to list only changes to the record's own row, so on the live
site 30 of 43 logged changes showed on no page (contract 2826: 14 changes, no
history). It now gathers what hangs below the record, reads one user action as
one line, and words each line for a reader. These tests pin the scope, the
grouping (action ids, and the two-second rule for older operations), the
wording, the person-entry window, the sub-contract id overlap, the Needs-review
lines and the provenance banner's source.

    uv run --with pytest pytest tests/test_record_history.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from workflows import corrections_db, db_import, review_server
from workflows.review_server import ContractCreate, InvestorCreate, NewPerson, RecordAction

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"

# Contract 1 with one general partner (Baccio Martelli, stake 200 of 1000); a
# sub-contract 90 of contract 1, and a separate contract 90 (ids overlap, as
# 3,266 do in the real data).
_SEED = """
INSERT INTO contract (contract_id, archive, series, folder, folio, registration_date, firm_name, total, document) VALUES
 (1,'ASF','Mercanzia','10831','10r','1601-05-10','Compagnia Vecchia',1500,'Accomandita in Firenze.'),
 (90,'ASF','Mercanzia','10831','90r','1620-01-01','Novanta e compagni',100,'Altra.');
INSERT INTO sub_contract (contract_id, archive, series, folder, folio, registration_date, sub_type, main_contract_id)
 VALUES (90,'ASF','Mercanzia','10831','11r','1602-01-01','termination',1);
INSERT INTO person (person_id, first_name, last_name) VALUES (10,'Baccio','Martelli'),(11,'Lena','Rucellai');
INSERT INTO title (title_id, title_name) VALUES (2,'messer');
INSERT INTO investment (investment_id, contract_id, type, investment_cash) VALUES (200,1,'gp',1000);
INSERT INTO investor (investor_id, person_id, contract_id, is_joint) VALUES (100,10,1,0);
INSERT INTO investor_group (investor_id, investment_id) VALUES (100,200);
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    main = str(tmp_path / "main.db")
    corr = tmp_path / "corr.db"
    c = sqlite3.connect(main)
    c.executescript(_TABLES_SQL.read_text() + _SEED)
    db_import.add_soft_delete_columns(c)
    c.commit()
    c.close()

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(corr))
    monkeypatch.setattr(review_server, "FLAG_DISMISSALS_PATH", tmp_path / "flag_dismissals.jsonl")

    def history(table: str, record_id: str) -> dict:
        x, clog = open_db(), corrections_db.connect(corr)
        try:
            return review_server.record_history(x, clog, table, record_id)
        finally:
            x.close()
            clog.close()

    def log(at: str, by: str = "FT", **op) -> None:
        """An older operation, logged before action ids: no run_id, a fixed time."""
        clog = corrections_db.connect(corr)
        try:
            rid = corrections_db.record_operation(clog, by=by, **op)
            clog.execute("UPDATE change_request SET created_at=? WHERE request_id=?", (at, rid))
            clog.execute("UPDATE change_event SET at=?, run_id=NULL WHERE request_id=?", (at, rid))
            clog.commit()
        finally:
            clog.close()

    return {"history": history, "log": log, "corr": corr, "open_db": open_db, "tmp": tmp_path}


def _lines(h: dict) -> list[str]:
    out = []
    for e in h["entries"]:
        change = e["text"] or ""
        if e["field"]:
            values = e["chain"] or [e["before"], e["after"]]
            change += ("; " if change else "") + f"{e['field']}: " + " → ".join(v or "(empty)" for v in values)
        out.append(f"[{e['tag']}] {e['subject'] or ''} · {change}".replace("]  · ", "] "))
    return out


def test_one_request_is_one_action(env) -> None:
    review_server.create_investor(InvestorCreate(
        reviewer="WH", contract_id=1, new_person=NewPerson(first_name="Piero", last_name="Capponi"),
        role="lp", investment_cash=300, title="ser", residence="Pisa",
    ))
    review_server.remove_partner("1", "100", RecordAction(reviewer="WH", reason="entered twice"))
    clog = corrections_db.connect(env["corr"])
    runs = [r[0] for r in clog.execute(
        "SELECT e.run_id FROM change_request r JOIN change_event e USING (request_id) ORDER BY e.event_id")]
    clog.close()
    # person, two new phrases, stake, partner, link: one action; the removal: another
    assert len(set(runs[:6])) == 1 and runs[0].startswith("action-")
    assert runs[6].startswith("action-") and runs[6] != runs[0]
    # an explicit run_id (the inline editor's apply-…) still wins inside a request
    with corrections_db.action_scope():
        clog = corrections_db.connect(env["corr"])
        rid = corrections_db.record_operation(clog, op="update", db_table="contract", pk={"contract_id": 1},
                                              field="folio", before_value="10r", after_value="10v", by="WH",
                                              run_id="apply-2026-10-27")
        assert clog.execute("SELECT run_id FROM change_event WHERE request_id=?", (rid,)).fetchone()[0] == "apply-2026-10-27"
        clog.close()


def test_contract_page_reads_each_action_as_one_line(env) -> None:
    review_server.create_investor(InvestorCreate(
        reviewer="WH", contract_id=1, new_person=NewPerson(first_name="Piero", last_name="Capponi"),
        role="lp", investment_cash=300, title="ser", residence="Pisa", note="seen in the act",
    ))
    review_server.create_investor(InvestorCreate(reviewer="WH", contract_id=1, person_id=11, join_investment_id=200))
    review_server.remove_partner("1", "100", RecordAction(reviewer="WH", reason="entered twice"))
    h = env["history"]("contract", "1")
    assert _lines(h) == [
        "[Partner] Baccio Martelli · removed from the contract; the stake stayed",
        "[Partner] Lena Rucellai · added, sharing the stake of Baccio Martelli (general partner, 1000)",
        "[Partner] Piero Capponi · added as external investor (accomandante), 300",
    ]
    removal, joined, added = h["entries"]
    assert removal["kind"] == "removed" and removal["reason"] == "entered twice"
    assert added["detail"] == "new person entry; new to the list: “ser”, “Pisa”"
    assert added["reason"] == "seen in the act"  # the typed note, not "added partner on contract 1"
    assert joined["reason"] is None
    assert added["link"] == {"kind": "person", "id": added["link"]["id"]}
    # a partner added to a seed contract does not make it "added to the database"
    assert h["created"] is None


def test_older_operations_group_by_author_within_two_seconds(env) -> None:
    log = env["log"]
    # an add-partner logged before action ids: three creates 14 ms apart
    log("2026-09-16T02:46:09.701662+00:00", op="create", db_table="investment", pk={"investment_id": 201},
        after_value={"investment_id": 201, "contract_id": 1, "type": "lp", "investment_cash": 6000},
        reason="added investor on contract 1")
    log("2026-09-16T02:46:09.716072+00:00", op="create", db_table="investor", pk={"investor_id": 101},
        after_value={"investor_id": 101, "person_id": 11, "contract_id": 1}, reason="added investor on contract 1")
    log("2026-09-16T02:46:09.730563+00:00", op="create", db_table="investor_group",
        pk={"investor_id": 101, "investment_id": 201}, after_value={"investor_id": 101, "investment_id": 201},
        reason="added investor on contract 1")
    # the stake removed 53 s later, and another author's edit within a second of that
    log("2026-09-16T02:47:02.000000+00:00", op="delete", db_table="investment", pk={"investment_id": 201},
        reason="input error in earlier phase")
    log("2026-09-16T02:47:02.500000+00:00", by="WH", op="update", db_table="contract", pk={"contract_id": 1},
        field="pl_discretion", before_value="0", after_value="1")
    with sqlite3.connect(str(env["tmp"] / "main.db")) as x:
        x.execute("INSERT INTO investment (investment_id, contract_id, type, investment_cash, is_deleted) VALUES (201,1,'lp',6000,1)")
        x.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (101,11,1)")
        x.execute("INSERT INTO investor_group (investor_id, investment_id) VALUES (101,201)")
    h = env["history"]("contract", "1")
    assert _lines(h) == [
        "[Contract] Place at discretion: No → Yes",
        "[Stake] External investor (accomandante), 6000 · removed; it had been held by Lena Rucellai",
        "[Partner] Lena Rucellai · added as external investor (accomandante), 6000",
    ]
    assert h["entries"][1]["reason"] == "input error in earlier phase"
    assert h["entries"][2]["reason"] is None  # "added investor on contract 1" is the server's own text


def test_person_entry_edits_show_while_a_partner(env) -> None:
    log = env["log"]
    # Lena's entry is edited before she is added to contract 1 on the platform,
    # then after: only the later edit belongs on contract 1. Baccio is a seed
    # partner: his entry's edits always show there.
    log("2026-09-01T10:00:00+00:00", op="update", db_table="person", pk={"person_id": 11},
        field="last_name", before_value="Rucelai", after_value="Rucellai")
    log("2026-09-02T10:00:00+00:00", op="create", db_table="investor", pk={"investor_id": 101},
        after_value={"investor_id": 101, "person_id": 11, "contract_id": 1})
    log("2026-09-03T10:00:00+00:00", op="update", db_table="person", pk={"person_id": 11},
        field="first_name", before_value="Lena", after_value="Maddalena")
    log("2026-09-04T10:00:00+00:00", op="update", db_table="person", pk={"person_id": 10},
        field="father_mother", before_value=None, after_value="Piero")
    with sqlite3.connect(str(env["tmp"] / "main.db")) as x:
        x.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (101,11,1)")
    lines = _lines(env["history"]("contract", "1"))
    assert "[Person entry] Baccio Martelli · Father / mother: (empty) → Piero" in lines
    assert "[Person entry] Lena Rucellai · First name: Lena → Maddalena" in lines
    assert not any("Last name" in line for line in lines)
    # on her own page every edit shows, without a subject
    person = _lines(env["history"]("person", "11"))
    assert "[Person entry] Last name: Rucelai → Rucellai" in person


def test_sub_contracts_on_the_parent_and_the_id_overlap(env) -> None:
    log = env["log"]
    log("2026-09-05T10:00:00+00:00", op="update", db_table="sub_contract", pk={"contract_id": 90},
        field="end_date", before_value="1785-02-22", after_value="1785-02-28")
    log("2026-09-05T11:00:00+00:00", op="delete", db_table="sub_contract", pk={"contract_id": 90}, reason="duplicate act")
    assert _lines(env["history"]("contract", "1")) == ["[Sub-contract] Sub-contract 90 (termination) · hidden"]
    assert _lines(env["history"]("sub_contract", "90")) == [
        "[Sub-contract] hidden",
        "[Sub-contract] End date: 1785-02-22 → 1785-02-28",
    ]
    # contract 90 is a different record: none of sub-contract 90's changes
    assert env["history"]("contract", "90")["entries"] == []


def test_repeated_saves_of_one_field_read_as_one_line(env) -> None:
    log = env["log"]
    for at, before, after in (("02:37:11", None, "Alessandro"), ("02:37:21", "Alessandro", "Alessandro`"),
                              ("02:37:32", "Alessandro`", "Alessandro")):
        log(f"2026-09-16T{at}+00:00", op="update", db_table="person", pk={"person_id": 10},
            field="father_mother", before_value=before, after_value=after,
            reason="Edited directly on the record page.")
    entries = env["history"]("person", "10")["entries"]
    assert len(entries) == 1
    assert entries[0]["chain"] == [None, "Alessandro", "Alessandro`", "Alessandro"]
    assert entries[0]["edits"] == 3 and entries[0]["reason"] is None


def test_needs_review_decisions_show_and_undo_marks_them(env) -> None:
    path = env["tmp"] / "flag_dismissals.jsonl"
    rows = [
        {"key": "contract:1:missing_reg_date", "action": "dismiss", "reviewer": "FT", "reason": "the act has no date",
         "at": "2026-10-20T10:00:00+00:00"},
        {"key": "contract:1:no_place", "action": "dismiss", "reviewer": "FT", "reason": "", "at": "2026-10-21T10:00:00+00:00"},
        {"key": "contract:1:no_place", "action": "restore", "reviewer": "FT", "at": "2026-10-21T10:00:05+00:00"},
        {"key": "contract:10:no_place", "action": "dismiss", "reviewer": "FT", "reason": "", "at": "2026-10-21T10:00:00+00:00"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    entries = env["history"]("contract", "1")["entries"]
    assert [e["kind"] for e in entries] == ["dismissed", "dismissed"]  # contract 10's is not here
    label = review_server.flag_group_meta()["missing_reg_date"]["label"]
    assert entries[1]["text"] == f"“{label}” marked not an issue"
    assert entries[1]["reason"] == "the act has no date" and entries[1]["undone"] is None
    assert entries[0]["undone"] == {"by": "FT", "at": "2026-10-21T10:00:05+00:00"}


def test_a_contract_created_on_the_platform_keeps_its_source(env) -> None:
    out = review_server.create_contract(ContractCreate(
        reviewer="WH", source="ASF Mercanzia 10832, c. 3r (photo 12)", folder="10832", folio="3r",
        registration_date="1610-02-03", firm_name="Nuova compagnia", document="Accomandita in Firenze.",
    ))
    h = env["history"]("contract", out["id"])
    assert h["created"]["source"] == "ASF Mercanzia 10832, c. 3r (photo 12)"
    assert _lines(h) == ["[Contract] added to the database"]
    assert h["entries"][0]["source"] == "ASF Mercanzia 10832, c. 3r (photo 12)"


def test_reading_the_history_writes_nothing(env) -> None:
    review_server.remove_partner("1", "100", RecordAction(reviewer="WH", reason="entered twice"))
    clog = corrections_db.connect(env["corr"])
    before = clog.execute("SELECT COUNT(*) FROM change_request").fetchone()[0], clog.execute(
        "SELECT COUNT(*) FROM change_event").fetchone()[0]
    clog.close()
    env["history"]("contract", "1")
    env["history"]("person", "10")
    clog = corrections_db.connect(env["corr"])
    after = clog.execute("SELECT COUNT(*) FROM change_request").fetchone()[0], clog.execute(
        "SELECT COUNT(*) FROM change_event").fetchone()[0]
    clog.close()
    assert before == after


def test_values_read_as_the_page_shows_them(env) -> None:
    x = env["open_db"]()
    x.execute("INSERT INTO economic_activity (ec_activity_id, activity) VALUES (11, 'arte di seta')")
    try:
        value = review_server._history_value
        assert value(x, "contract", "economic_sector", "1725") == "not in the list"  # an id with no phrase
        assert value(x, "contract", "economic_sector", "11") == "arte di seta"
        assert value(x, "contract", "currency_id", "0") is None
        assert value(x, "contract", "pl_discretion", "1") == "Yes"
        assert value(x, "investor", "jewish_db", "0") == "No"
        assert value(x, "investment", "type", "lp") == "External investor (accomandante)"
        assert value(x, "contract", "registration_date", "0000-00-00") == "not recorded"
        assert value(x, "contract", "firm_name", "") is None
    finally:
        x.close()


def test_joining_a_stake_names_only_who_held_it_then(env) -> None:
    review_server.remove_partner("1", "100", RecordAction(reviewer="WH", reason="entered twice"))
    review_server.create_investor(InvestorCreate(reviewer="WH", contract_id=1, person_id=11, join_investment_id=200))
    review_server.create_investor(InvestorCreate(
        reviewer="WH", contract_id=1, new_person=NewPerson(first_name="Neri", last_name="Strozzi"), join_investment_id=200,
    ))
    lines = _lines(env["history"]("contract", "1"))
    # Baccio had been removed before Lena joined; Neri joined after her
    assert "[Partner] Lena Rucellai · added, holding an existing stake (general partner, 1000)" in lines
    assert "[Partner] Neri Strozzi · added, sharing the stake of Lena Rucellai (general partner, 1000)" in lines


def test_inline_saves_are_never_merged_by_time(env) -> None:
    log = env["log"]
    for at, iid in (("2026-09-16T02:46:09.100000+00:00", 100), ("2026-09-16T02:46:09.900000+00:00", 101)):
        log(at, op="update", db_table="investor", pk={"investor_id": iid}, field="citizen_florence",
            before_value="0", after_value="1")
    clog = corrections_db.connect(env["corr"])
    clog.execute("UPDATE change_event SET run_id='apply-2026-09-16'")
    clog.commit()
    clog.close()
    with sqlite3.connect(str(env["tmp"] / "main.db")) as x:
        x.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (101,11,1)")
    lines = _lines(env["history"]("contract", "1"))
    assert lines == [
        "[Partner] Lena Rucellai · Citizen of Florence: No → Yes",
        "[Partner] Baccio Martelli · Citizen of Florence: No → Yes",
    ]


def test_repeated_saves_merge_only_on_the_same_row(env) -> None:
    log = env["log"]
    with sqlite3.connect(str(env["tmp"] / "main.db")) as x:
        x.execute("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (101,10,1)")
    log("2026-09-16T03:27:00+00:00", op="update", db_table="investor", pk={"investor_id": 100},
        field="profession", before_value=None, after_value="setaiolo")
    log("2026-09-16T03:28:00+00:00", op="update", db_table="investor", pk={"investor_id": 101},
        field="profession", before_value="lanaiolo", after_value="setaiolo")
    entries = env["history"]("person", "10")["entries"]
    assert len(entries) == 2 and all(e["edits"] == 1 for e in entries)


def test_hiding_a_partners_person_entry_shows_on_the_contract(env) -> None:
    log = env["log"]
    log("2026-09-20T10:00:00+00:00", op="delete", db_table="person", pk={"person_id": 10}, reason="duplicate entry")
    log("2026-09-20T10:05:00+00:00", op="restore", db_table="person", pk={"person_id": 10})
    assert _lines(env["history"]("contract", "1")) == [
        "[Person entry] Baccio Martelli · restored from hidden",
        "[Person entry] Baccio Martelli · hidden",
    ]
