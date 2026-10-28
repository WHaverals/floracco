"""Phase-A multi-user safety battery (docs/multi_user_safety.md §0).

Real threads driving the real endpoints against one scratch database:

* the process-wide write lock — no id-mint collisions, no duplicate verbatim
  lookup mints, no drift-check TOCTOU, no lost ledger rows;
* the cross-process flock — an external holder yields a clean 503;
* compensation-lite — an op-log failure restores the main.db value, so no
  unlogged write can survive into a rebuild;
* the E4 op-log poison — reviewer-typed JSON-looking values ("[2]") round-trip
  as the strings they are, all the way through a rebuild.

    uv run --with pytest pytest tests/test_concurrency.py
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, db_import, locks, review_server
from workflows.review_server import (
    CorrectionAction,
    CorrectionCreate,
    InvestorCreate,
    NewPerson,
    PlaceAdd,
    RecordAction,
)

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"

_SEED = """
INSERT INTO contract (contract_id, archive, series, folder, folio, registration_date,
                      firm_name, total, document) VALUES
 (1,'ASF','Mercanzia','10831','10r','1601-05-10','Compagnia Vecchia',1500,'Accomandita in Firenze.'),
 (2,'ASF','Mercanzia','10831','12v','1602-03-01','Seconda compagnia',NULL,'Seconda accomandita.');
INSERT INTO person (person_id, first_name, last_name) VALUES (10,'Baccio','Martelli'),(11,'Lena','Rucellai');
INSERT INTO place (place_id, place_name) VALUES (5,'Firenze');
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


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    main = str(tmp_path / "main.db")
    corr = str(tmp_path / "corr.db")
    _make_db(main)

    def open_db():
        x = sqlite3.connect(main, timeout=15.0)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(Path(corr)))
    monkeypatch.setattr(review_server, "db_path", lambda: Path(main))
    store = tmp_path / "corrstore"
    monkeypatch.setattr(review_server, "CORRECTIONS_DIR", store)
    monkeypatch.setattr(review_server, "PROPOSALS_PATH", store / "proposals.jsonl")
    monkeypatch.setattr(review_server, "EVENTS_PATH", store / "events.jsonl")

    def rebuild():
        fresh = str(tmp_path / f"fresh-{len(list(tmp_path.iterdir()))}.db")
        _make_db(fresh)
        monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", corr)
        fc = sqlite3.connect(fresh)
        stats = db_import.replay_corrections(fc)
        fc.commit()
        return fc, stats

    return open_db, corr, rebuild


def _run_threads(fns):
    """Start all fns behind one barrier; return each thread's result or exception."""
    barrier = threading.Barrier(len(fns))
    results: list = [None] * len(fns)

    def runner(i, fn):
        barrier.wait()
        try:
            results[i] = ("ok", fn())
        except Exception as exc:  # noqa: BLE001 — the exception IS the assertion target
            results[i] = ("err", exc)

    threads = [threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _val(open_db, sql, *p):
    x = open_db()
    try:
        row = x.execute(sql, p).fetchone()
        return row[0] if row else None
    finally:
        x.close()


# ---------------------------------------------------------------------------
# The write lock: races that were possible before Phase A
# ---------------------------------------------------------------------------

def test_parallel_investor_creates_no_collisions(wired):
    open_db, corr, rebuild = wired
    mk = lambda name: lambda: review_server.create_investor(InvestorCreate(
        reviewer="WH", contract_id=1,
        new_person=NewPerson(first_name=name, last_name="Capponi"),
        role="lp", investment_cash=100,
    ))
    results = _run_threads([mk("Piero"), mk("Antonio")])
    assert all(kind == "ok" for kind, _ in results), results
    ids = {r["investor_id"] for _, r in results}
    assert len(ids) == 2  # no max()+1 collision — distinct ids for everything
    fc, stats = rebuild()
    assert stats["conflicts"] == 0 and stats["skipped"] == 0
    names = {r[0] for r in fc.execute(
        "SELECT first_name FROM person WHERE person_id > 11").fetchall()}
    assert names == {"Piero", "Antonio"}  # both cascades fully logged + replayed
    fc.close()


def test_parallel_same_phrase_add_place_single_mint(wired):
    open_db, corr, rebuild = wired
    results = _run_threads([
        lambda: review_server.add_place("1", PlaceAdd(reviewer="WH", place="Livorno")),
        lambda: review_server.add_place("2", PlaceAdd(reviewer="FT", place="Livorno")),
    ])
    assert all(kind == "ok" for kind, _ in results), results
    # THE silent race of the old code: two threads minting the same verbatim
    # phrase under two ids. Under the lock: exactly one lookup row, two links.
    assert _val(open_db, "SELECT COUNT(*) FROM place WHERE trim(place_name)='Livorno'") == 1
    assert _val(open_db, "SELECT COUNT(*) FROM contract_place WHERE is_deleted=0") == 2


def test_parallel_apply_same_field_exactly_one_wins(wired):
    open_db, corr, rebuild = wired

    def proposal(value):
        created = review_server.create_correction(CorrectionCreate(
            reviewer="WH", db_row_id="contract:1", field="folio",
            proposed_value=value, rationale="checked",
        ))
        pid = created["proposal"]["proposal_id"]
        review_server.approve_correction(pid, CorrectionAction(reviewer="FT"))
        return pid

    a, b = proposal("77r"), proposal("88r")
    results = _run_threads([
        lambda: review_server.apply_correction(a, CorrectionAction(reviewer="WH")),
        lambda: review_server.apply_correction(b, CorrectionAction(reviewer="FT")),
    ])
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1 and len(errs) == 1
    assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code == 409
    # exactly one update op logged; the loser's drift 409 left no trace
    clog = corrections_db.connect(Path(corr))
    try:
        assert clog.execute("SELECT COUNT(*) FROM change_request").fetchone()[0] == 1
    finally:
        clog.close()
    assert _val(open_db, "SELECT folio FROM contract WHERE contract_id=1") in ("77r", "88r")


def test_parallel_proposal_saves_lose_nothing(wired):
    open_db, corr, rebuild = wired
    combos = [(row, field, value)
              for row in (1, 2)
              for field, value in [("folio", "9r"), ("firm_name", "Nuova"),
                                   ("total", "123"), ("document", "testo nuovo del contratto")]]
    mk = lambda i: lambda: review_server.create_correction(CorrectionCreate(
        reviewer=f"R{i}", db_row_id=f"contract:{combos[i][0]}", field=combos[i][1],
        proposed_value=f"{combos[i][2]}", rationale="load",
    ))
    results = _run_threads([mk(i) for i in range(8)])
    assert all(kind == "ok" for kind, _ in results), results
    # the whole-file-rewrite ledger lost nothing: all 8 proposals present
    assert len(review_server.load_proposals()) == 8


def test_hide_restore_interleave_matches_log(wired):
    open_db, corr, rebuild = wired
    results = _run_threads([
        lambda: review_server.hide_record("contract", "2", RecordAction(reviewer="WH", reason="dup")),
        lambda: review_server.restore_record("contract", "2", RecordAction(reviewer="FT")),
    ])
    # initial state is visible: hide always works; restore either 409s (ran
    # first, nothing hidden) or succeeds (ran second). Whatever interleaving
    # happened, the rebuilt state must equal the live state.
    live = _val(open_db, "SELECT is_deleted FROM contract WHERE contract_id=2")
    fc, stats = rebuild()
    assert stats["conflicts"] == 0
    assert fc.execute("SELECT is_deleted FROM contract WHERE contract_id=2").fetchone()[0] == live
    fc.close()


# ---------------------------------------------------------------------------
# The cross-process flock
# ---------------------------------------------------------------------------

def test_external_flock_holder_yields_503_then_recovers(wired, monkeypatch):
    open_db, corr, rebuild = wired
    monkeypatch.setattr(locks, "FLOCK_ATTEMPTS", 2)  # keep the test fast (~0.25 s)
    path = locks.write_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)  # an external maintenance process
    try:
        with pytest.raises(HTTPException) as e:
            review_server.hide_record("contract", "2", RecordAction(reviewer="WH", reason="x"))
        assert e.value.status_code == 503
        assert "Maintenance" in e.value.detail
        assert _val(open_db, "SELECT is_deleted FROM contract WHERE contract_id=2") == 0
    finally:
        os.close(fd)  # release
    review_server.hide_record("contract", "2", RecordAction(reviewer="WH", reason="x"))
    assert _val(open_db, "SELECT is_deleted FROM contract WHERE contract_id=2") == 1


def test_maintenance_lock_refuses_while_server_lock_held(wired):
    with locks.write_lock():
        with pytest.raises(locks.WriteLockBusy):
            with locks.maintenance_lock():
                pass


# ---------------------------------------------------------------------------
# Compensation-lite: an op-log failure must leave NO unlogged main.db write
# ---------------------------------------------------------------------------

def _break_logging(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated corrections.db failure")
    monkeypatch.setattr(corrections_db, "record_operation", boom)


def test_log_failure_undoes_hide(wired, monkeypatch):
    open_db, corr, rebuild = wired
    _break_logging(monkeypatch)
    with pytest.raises(RuntimeError):
        review_server.hide_record("contract", "2", RecordAction(reviewer="WH", reason="dup"))
    assert _val(open_db, "SELECT is_deleted FROM contract WHERE contract_id=2") == 0  # restored
    clog = corrections_db.connect(Path(corr))
    try:
        assert clog.execute("SELECT COUNT(*) FROM change_request").fetchone()[0] == 0
    finally:
        clog.close()


def test_log_failure_undoes_apply_and_retry_succeeds(wired, monkeypatch):
    open_db, corr, rebuild = wired
    created = review_server.create_correction(CorrectionCreate(
        reviewer="WH", db_row_id="contract:1", field="folio",
        proposed_value="99v", rationale="checked",
    ))
    pid = created["proposal"]["proposal_id"]
    review_server.approve_correction(pid, CorrectionAction(reviewer="FT"))

    real = corrections_db.record_operation
    with monkeypatch.context() as mp:
        _break_logging(mp)
        with pytest.raises(RuntimeError):
            review_server.apply_correction(pid, CorrectionAction(reviewer="WH"))
    # the committed UPDATE was rolled back by compensation; proposal still approved
    assert _val(open_db, "SELECT folio FROM contract WHERE contract_id=1") == "10r"
    assert review_server.proposal_by_id(pid)["status"] == "approved"
    # retry with logging healed: clean success, value + op both present
    review_server.apply_correction(pid, CorrectionAction(reviewer="WH"))
    assert _val(open_db, "SELECT folio FROM contract WHERE contract_id=1") == "99v"
    fc, stats = rebuild()
    assert stats == {"applied": 1, "conflicts": 0, "skipped": 0}
    assert fc.execute("SELECT folio FROM contract WHERE contract_id=1").fetchone()[0] == "99v"
    fc.close()
    assert corrections_db.record_operation is real  # context exited cleanly


def test_log_failure_undoes_refute(wired, monkeypatch):
    open_db, corr, rebuild = wired
    x = open_db()
    with x:
        x.execute("UPDATE investor SET jewish_db = 1 WHERE investor_id = 100")
    x.close()
    _break_logging(monkeypatch)
    with pytest.raises(RuntimeError):
        review_server.refute_jewish_attribution(
            "100", review_server.AttributionRefute(reviewer="WH", reason="no evidence"))
    # the refute did NOT stand unlogged — jewish_db restored to 1
    assert _val(open_db, "SELECT jewish_db FROM investor WHERE investor_id=100") == 1


# ---------------------------------------------------------------------------
# E4: reviewer-typed JSON-looking values stay the strings they are
# ---------------------------------------------------------------------------

def test_bracket_values_replay_verbatim(wired):
    open_db, corr, rebuild = wired
    cases = [("folio", "10r", "[2]"), ("folder", "10831", "{}"),
             ("series", "Mercanzia", "[1600]"), ("firm_name", "Compagnia Vecchia", '{"a":1}')]
    clog = corrections_db.connect(Path(corr))
    try:
        for field, before, after in cases:
            corrections_db.record_operation(
                clog, op="update", db_table="contract", pk={"contract_id": 1},
                field=field, before_value=before, after_value=after, by="WH",
            )
    finally:
        clog.close()
    fc, stats = rebuild()
    assert stats == {"applied": 4, "conflicts": 0, "skipped": 0}
    row = fc.execute("SELECT folio, folder, series, firm_name FROM contract WHERE contract_id=1").fetchone()
    # the literal typed strings — never a revived list/dict, never a crash
    assert row == ("[2]", "{}", "[1600]", '{"a":1}')
    fc.close()


def test_bracket_value_end_to_end_through_apply(wired):
    open_db, corr, rebuild = wired
    created = review_server.create_correction(CorrectionCreate(
        reviewer="WH", db_row_id="contract:1", field="folio",
        proposed_value="[2]", rationale="editorial bracketed folio",
    ))
    pid = created["proposal"]["proposal_id"]
    review_server.approve_correction(pid, CorrectionAction(reviewer="FT"))
    review_server.apply_correction(pid, CorrectionAction(reviewer="WH"))
    assert _val(open_db, "SELECT folio FROM contract WHERE contract_id=1") == "[2]"
    # the audit trail shows the raw typed string, not a Python list
    clog = corrections_db.connect(Path(corr))
    try:
        hist = corrections_db.history_for_row(clog, "contract", {"contract_id": 1})
    finally:
        clog.close()
    assert hist[0]["after_value"] == "[2]" and isinstance(hist[0]["after_value"], str)
    fc, stats = rebuild()
    assert stats["conflicts"] == 0
    assert fc.execute("SELECT folio FROM contract WHERE contract_id=1").fetchone()[0] == "[2]"
    fc.close()


def test_structured_values_refused_outside_create():
    conn = corrections_db.connect(Path(os.devnull).parent / "unused.db") if False else None
    with pytest.raises(ValueError):
        corrections_db.record_operation(
            sqlite3.connect(":memory:"), op="update", db_table="contract",
            pk={"contract_id": 1}, field="folio", before_value="x", after_value=[2], by="WH",
        )
