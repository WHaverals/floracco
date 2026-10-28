"""Rebuild-integrity suite — the seed+replay constitution itself.

main.db is disposable by design: it must always equal seed (the archive dump)
+ replay (corrections.db, the authoritative op-log). The per-feature tests each
spot-check one field surviving a rebuild; this file locks the properties none
of them assert:

 * a stale pre-image flags `conflict` and leaves the seed value untouched —
   and a flagged op permanently leaves the replay stream (both failure
   directions are silent: overwrite newer upstream data, or drop a legitimate
   correction from every future rebuild);
 * a hidden record is STILL hidden after a rebuild (every other rebuild test
   happens to end visible, so a delete replay that became a no-op would pass
   the entire suite);
 * whole-database equivalence: one of every kind of write the site can do,
   through the real endpoints, then fresh seed + replay must reproduce the
   live database table by table, row by row, value by value — types included;
 * the op-log path resolution (a wrong path is auto-created as an EMPTY log,
   and replay on a missing file reports success — so a resolution regression
   would silently rebuild with zero corrections).

    uv run --with pytest pytest tests/test_rebuild_integrity.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, db_import, review_server
from workflows.review_server import (
    AttributionRefute,
    ContractCreate,
    InvestorCreate,
    NewPerson,
    PlaceAdd,
    PlaceAddress,
    RecordAction,
    RelinkAction,
    SubContractCreate,
)

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"

# One small corpus slice on the CANONICAL schema: two contracts, a sub-act,
# people/investors (100 carries a 2010s editorial attribution for the refute),
# a tranche with a lone member (for the join → is_joint derivation), lookups.
_SEED = """
INSERT INTO contract (contract_id, archive, series, folder, folio, registration_date,
                      firm_name, economic_sector, total, currency_id, document) VALUES
 (1,'ASF','Mercanzia','10831','10r','1601-05-10','Compagnia Vecchia',NULL,1500,7,'Accomandita in Firenze.'),
 (2,'ASF','Mercanzia','10831','12v','1602-03-01','Seconda compagnia',NULL,NULL,NULL,NULL);
INSERT INTO sub_contract (contract_id, archive, series, folder, folio, registration_date,
                          sub_type, main_contract_id, document) VALUES
 (90,'ASF','Mercanzia','10831','13r','1603-01-01','renewal',1,'Rinnovazione della accomandita.');
INSERT INTO person (person_id, first_name, last_name) VALUES (10,'Baccio','Martelli'),(11,'Lena','Rucellai');
INSERT INTO place (place_id, place_name) VALUES (5,'Firenze');
INSERT INTO contract_place (place_id, contract_id, address, place_db) VALUES (5,1,'via Larga',0);
INSERT INTO currency (currency_id, currency) VALUES (7,'scudi');
INSERT INTO economic_activity (ec_activity_id, activity) VALUES (3,'lana');
INSERT INTO title (title_id, title_name) VALUES (2,'messer');
INSERT INTO investment (investment_id, contract_id, type, investment_cash) VALUES
 (200,1,'gp',1000),(201,1,'lp',500);
INSERT INTO investor (investor_id, person_id, contract_id, is_jewish, jewish_db, is_joint) VALUES
 (100,10,1,0,1,0),
 (101,11,1,0,0,0);
INSERT INTO investor_group (investor_id, investment_id) VALUES (100,200),(101,201);
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

    def rebuild() -> tuple[sqlite3.Connection, dict]:
        """Fresh seed + replay of the op-log = the actual reseed procedure."""
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


# ---------------------------------------------------------------------------
# 1. The staleness guard: a stale pre-image must conflict, never overwrite —
#    and a flagged op must permanently leave the replay stream.
# ---------------------------------------------------------------------------

def _log_update(corr, table, pk, field, before, after):
    clog = corrections_db.connect(Path(corr))
    try:
        corrections_db.record_operation(
            clog, op="update", db_table=table, pk=pk, by="WH",
            field=field, before_value=before, after_value=after,
        )
    finally:
        clog.close()


def test_stale_pre_image_conflicts_and_never_replays_again(tmp_path, monkeypatch):
    _, corr, rebuild = _wire(tmp_path, monkeypatch)
    # op A: pre-image matches the seed → applies
    _log_update(corr, "contract", {"contract_id": 1}, "folio", "10r", "11r")
    # op B: the upstream dump changed underneath (seed says 12v, op recorded 99v)
    _log_update(corr, "contract", {"contract_id": 2}, "folio", "99v", "98v")
    # op C: the row vanished from the seed entirely
    _log_update(corr, "contract", {"contract_id": 777}, "folio", "1r", "2r")

    fc, stats = rebuild()
    assert stats == {"applied": 1, "conflicts": 2, "skipped": 0}
    assert fc.execute("SELECT folio FROM contract WHERE contract_id=1").fetchone()[0] == "11r"
    # the conflicting op did NOT overwrite the newer seed value
    assert fc.execute("SELECT folio FROM contract WHERE contract_id=2").fetchone()[0] == "12v"
    fc.close()

    clog = corrections_db.connect(Path(corr))
    try:
        statuses = dict(clog.execute("SELECT after_value, status FROM change_request"))
        assert statuses == {"11r": "applied", "98v": "conflict", "2r": "conflict"}
        events = [r[0] for r in clog.execute("SELECT event FROM change_event")]
        assert events.count("conflict_flagged") == 2
    finally:
        clog.close()

    # a flagged op is OUT of every future replay: only op A runs again
    fc2, stats2 = rebuild()
    assert stats2 == {"applied": 1, "conflicts": 0, "skipped": 0}
    assert fc2.execute("SELECT folio FROM contract WHERE contract_id=2").fetchone()[0] == "12v"
    fc2.close()


def test_pre_image_match_tolerates_null_vs_empty_and_whitespace(tmp_path, monkeypatch):
    # _norm's tolerance IS the false-conflict guard: a false flag permanently
    # drops a legitimate correction, so NULL≡'' and stray whitespace must match.
    _, corr, rebuild = _wire(tmp_path, monkeypatch)
    _log_update(corr, "contract", {"contract_id": 1}, "start_date", None, "1601-06-01")  # seed NULL, logged None
    _log_update(corr, "contract", {"contract_id": 2}, "firm_name", " Seconda compagnia ", "Terza")  # padded pre-image
    fc, stats = rebuild()
    assert stats == {"applied": 2, "conflicts": 0, "skipped": 0}
    assert fc.execute("SELECT start_date FROM contract WHERE contract_id=1").fetchone()[0] == "1601-06-01"
    assert fc.execute("SELECT firm_name FROM contract WHERE contract_id=2").fetchone()[0] == "Terza"
    fc.close()


# ---------------------------------------------------------------------------
# 2. Hidden stays hidden — the delete replay asserted in its FINAL state.
# ---------------------------------------------------------------------------

def test_hidden_record_stays_hidden_after_rebuild(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    review_server.hide_record("contract", "2", RecordAction(reviewer="WH", reason="duplicate entry"))
    review_server.hide_record("person", "11", RecordAction(reviewer="WH", reason="merged elsewhere"))
    assert _val(open_db, "SELECT is_deleted FROM contract WHERE contract_id=2") == 1
    assert _val(open_db, "SELECT is_deleted FROM person WHERE person_id=11") == 1

    fc, stats = rebuild()
    assert stats["conflicts"] == 0 and stats["skipped"] == 0
    assert fc.execute("SELECT is_deleted FROM contract WHERE contract_id=2").fetchone()[0] == 1
    assert fc.execute("SELECT is_deleted FROM person WHERE person_id=11").fetchone()[0] == 1
    fc.close()


def test_hide_then_restore_rebuilds_visible_with_both_ops_logged(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)
    review_server.hide_record("contract", "2", RecordAction(reviewer="WH", reason="looked duplicate"))
    review_server.restore_record("contract", "2", RecordAction(reviewer="WH"))
    clog = corrections_db.connect(Path(corr))
    try:
        ops = [r[0] for r in clog.execute("SELECT op FROM change_request ORDER BY created_at")]
        assert ops == ["delete", "restore"]
    finally:
        clog.close()
    fc, _ = rebuild()
    assert fc.execute("SELECT is_deleted FROM contract WHERE contract_id=2").fetchone()[0] == 0
    fc.close()


def test_hide_guards(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:  # a hide without a reason is refused
        review_server.hide_record("contract", "2", RecordAction(reviewer="WH"))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        review_server.hide_record("contract", "777", RecordAction(reviewer="WH", reason="x"))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:  # investment is not a browsable record door
        review_server.hide_record("investment", "200", RecordAction(reviewer="WH", reason="x"))
    assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# 3. Whole-database equivalence: live writes through every endpoint family,
#    then fresh seed + replay must reproduce main.db exactly.
# ---------------------------------------------------------------------------

def _dump(conn: sqlite3.Connection, table: str):
    """All rows as ((type_name, value), ...) tuples, order-independent —
    typed, so an int stored on one side and its string twin on the other
    (op-log values are stringified) cannot silently pass as equal."""
    rows = conn.execute(f"SELECT * FROM `{table}`").fetchall()
    return sorted(
        [tuple((type(v).__name__, v) for v in row) for row in rows],
        key=lambda r: [(t, str(v)) for t, v in r],
    )


def test_full_restore_equivalence(tmp_path, monkeypatch):
    open_db, corr, rebuild = _wire(tmp_path, monkeypatch)

    # One of every write the site can do, through the real endpoints:
    review_server.edit_place_address("1", "5", PlaceAddress(reviewer="WH", address="Borgo Santi Apostoli"))
    review_server.add_place("2", PlaceAdd(reviewer="WH", place="Livorno"))          # lookup mint + link
    review_server.hide_record("sub_contract", "90", RecordAction(reviewer="WH", reason="void act"))
    review_server.hide_record("contract", "2", RecordAction(reviewer="WH", reason="checking"))
    review_server.restore_record("contract", "2", RecordAction(reviewer="WH"))      # net-visible pair
    review_server.create_contract(ContractCreate(
        reviewer="WH", source="ASF Mercanzia 10832, c. 40r", folder="10832", folio="40r",
        registration_date="1610-02-11", firm_name="Nuova compagnia",
        economic_activity="arte della seta", total=2000,
        document="Accomandita nuova registrata in Mercanzia.",
    ))                                                                              # → contract 3 + activity mint
    review_server.create_sub_contract(SubContractCreate(
        reviewer="WH", source="ASF Mercanzia 10832, c. 41r", main_contract_id=3,
        sub_type="renewal", folder="10832", folio="41r", registration_date="1615-02-11",
        document="Rinnovazione della nuova compagnia.",
    ))                                                                              # → sub 91
    review_server.create_investor(InvestorCreate(
        reviewer="WH", contract_id=1,
        new_person=NewPerson(first_name="Piero", last_name="Capponi"),
        role="lp", investment_cash=300, title="ser", residence="Pisa",
    ))                                                                              # person+2 lookups+investment+investor+group
    review_server.create_investor(InvestorCreate(
        reviewer="WH", contract_id=1, person_id=11, join_investment_id=200,
        title="messer",                                                             # exact reuse, no mint
    ))                                                                              # join → sibling 100 is_joint 0→1
    review_server.relink_record("contract", "1", RelinkAction(reviewer="WH", field="economic_sector", value="lana"))
    review_server.refute_jewish_attribution("100", AttributionRefute(reviewer="WH", reason="no evidence in the act"))
    review_server.remove_partner("1", "101", RecordAction(reviewer="WH", reason="duplicate appearance"))

    fc, stats = rebuild()
    # nothing the endpoints log may be un-replayable or conflicting
    assert stats["conflicts"] == 0
    assert stats["skipped"] == 0
    clog = corrections_db.connect(Path(corr))
    try:
        logged = clog.execute("SELECT COUNT(*) FROM change_request").fetchone()[0]
    finally:
        clog.close()
    assert stats["applied"] == logged  # every logged op replayed

    live = open_db()
    try:
        tables = [r[0] for r in live.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        assert "contract" in tables and "investor_group" in tables
        for table in tables:
            assert _dump(fc, table) == _dump(live, table), (
                f"rebuilt `{table}` diverges from the live database — "
                f"the op-log is not a complete constitution for this write path"
            )
    finally:
        live.close()
        fc.close()


# ---------------------------------------------------------------------------
# 4. Op-log path resolution — a wrong path silently becomes an empty log.
# ---------------------------------------------------------------------------

def test_corrections_path_resolution(tmp_path, monkeypatch):
    root = Path(corrections_db.__file__).resolve().parents[1]
    monkeypatch.delenv("FLORACCO_CORRECTIONS_DB_PATH", raising=False)
    monkeypatch.delenv("FLORACCO_DATA_DIR", raising=False)
    assert corrections_db.default_path() == root / "data/sqlite/corrections.db"

    # the deployed layout: everything under one relocatable data root
    monkeypatch.setenv("FLORACCO_DATA_DIR", str(tmp_path / "datadir"))
    assert corrections_db.default_path() == (tmp_path / "datadir").resolve() / "sqlite/corrections.db"

    # an explicit override wins over the data root; relative resolves under the repo
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", str(tmp_path / "x.db"))
    assert corrections_db.default_path() == tmp_path / "x.db"
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", "rel/corr.db")
    assert corrections_db.default_path() == root / "rel/corr.db"


def test_replay_on_missing_log_applies_nothing_but_says_so(tmp_path, monkeypatch):
    # A missing op-log is a fresh install, not an error — but the caller must be
    # able to SEE that nothing replayed (this return shape is the only signal).
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", str(tmp_path / "nowhere.db"))
    main = str(tmp_path / "main.db")
    _make_db(main)
    c = sqlite3.connect(main)
    assert db_import.replay_corrections(c) == {"applied": 0, "conflicts": 0, "skipped": 0}
    c.close()
    assert not (tmp_path / "nowhere.db").exists()  # replay never mints an empty log
