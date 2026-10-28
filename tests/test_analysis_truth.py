"""Fences for the analysis surfaces that would lie confidently if broken.

The Jewish partition lists (all-Jewish vs mixed), the stated-vs-editorial
provenance split (the platform's core epistemic distinction), and the one-line
guard that stops capital totals being multiplied per investor. None of these
error when wrong — they render wrong numbers with full confidence, which is why
each gets a semantic fixture here (canonical minimal schema, every expectation
hand-derived).

    uv run --with pytest pytest tests/test_analysis_truth.py
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from workflows.review_server import ANALYSIS_LIBRARY, build_analysis_sql


def _sql(query_id: str) -> str:
    return next(q["sql"] for q in ANALYSIS_LIBRARY if q["id"] == query_id)


_SCHEMA = """
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, registration_date TEXT,
    firm_name TEXT, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, contract_id INTEGER,
    is_jewish INTEGER DEFAULT 0, jewish_db INTEGER DEFAULT 0,
    is_joint INTEGER DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0,
    person_id INTEGER GENERATED ALWAYS AS (investor_id) VIRTUAL);
-- Every partner here is its own live person entry; the hidden-person rule is
-- fenced in tests/test_hidden_sweep.py.
CREATE VIEW person AS SELECT investor_id AS person_id, 0 AS is_deleted FROM investor;
"""


def _conn(seed: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA + seed)
    return conn


# ---------------------------------------------------------------------------
# jewish_all / jewish_mixed — the partition behind published claims
# ---------------------------------------------------------------------------

_PARTITION_SEED = """
-- c1 all-Jewish: one stated (is_jewish), one editorial (jewish_db) — both count
INSERT INTO contract VALUES (1,'1600-01-01','Tutta ebrea',0);
INSERT INTO investor VALUES (10,1,1,0,0,0),(11,1,0,1,0,0);
-- c2 mixed: one stated Jewish + one with neither flag
INSERT INTO contract VALUES (2,'1610-01-01','Mista',0);
INSERT INTO investor VALUES (20,2,1,0,0,0),(21,2,0,0,0,0);
-- c3 no Jewish investor at all → neither list
INSERT INTO contract VALUES (3,'1620-01-01','Nessuno',0);
INSERT INTO investor VALUES (30,3,0,0,0,0);
-- c4 all-Jewish ONLY because its non-Jewish partner is soft-deleted:
-- the is_deleted filter inside both subqueries is load-bearing
INSERT INTO contract VALUES (4,'1630-01-01','Tutta (dopo rimozione)',0);
INSERT INTO investor VALUES (40,4,0,1,0,0),(41,4,0,0,0,1);
-- c5 its ONLY Jewish partner is soft-deleted → in NEITHER list
INSERT INTO contract VALUES (5,'1640-01-01','Solo tramite nascosto',0);
INSERT INTO investor VALUES (50,5,1,0,0,1),(51,5,0,0,0,0);
-- c6 hidden contract, would be all-Jewish → excluded entirely
INSERT INTO contract VALUES (6,'1650-01-01','Nascosto',1);
INSERT INTO investor VALUES (60,6,1,0,0,0);
-- c7 all-Jewish, every partner stated in the act (one also attributed)
INSERT INTO contract VALUES (7,'1660-01-01','Tutta dichiarata',0);
INSERT INTO investor VALUES (70,7,1,0,0,0),(71,7,1,1,0,0);
-- c8 mixed, its Jewish side attributed only
INSERT INTO contract VALUES (8,'1670-01-01','Mista attribuita',0);
INSERT INTO investor VALUES (80,8,0,1,0,0),(81,8,0,0,0,0);
"""


def test_jewish_all_and_mixed_are_a_true_partition():
    conn = _conn(_PARTITION_SEED)
    all_ids = [r["contract_id"] for r in conn.execute(_sql("jewish_all"))]
    mixed_ids = [r["contract_id"] for r in conn.execute(_sql("jewish_mixed"))]
    # both provenances count toward "Jewish"; the soft-deleted partner rule cuts
    # both ways (c4 in, c5 out); hidden contracts never appear
    assert all_ids == [1, 4, 7]
    assert mixed_ids == [2, 8]
    assert set(all_ids).isdisjoint(mixed_ids)


def test_jewish_lists_say_how_each_contract_is_known():
    """Review 2026-10-27, Q4: 113 of 146 all-Jewish and 30 of 37 mixed contracts
    rest on attribution alone, and the lists did not say so."""
    conn = _conn(_PARTITION_SEED)
    known_all = {r["contract_id"]: r["provenance"] for r in conn.execute(_sql("jewish_all"))}
    known_mixed = {r["contract_id"]: r["provenance"] for r in conn.execute(_sql("jewish_mixed"))}
    assert known_all == {
        1: "partly stated, partly attributed",  # one stated, one attributed only
        4: "attributed only",                   # the removed partner is not counted
        7: "stated in the act",                 # a partner both stated and attributed counts as stated
    }
    assert known_mixed == {2: "stated in the act", 8: "attributed only"}


# ---------------------------------------------------------------------------
# the provenance split — HOW we know, by decade
# ---------------------------------------------------------------------------

def _provenance_seed() -> str:
    # decade 1600: exactly 30 investors on one contract — 3 stated-only,
    # 2 editorial-only, 1 BOTH (must count in both series), 24 neither.
    rows = []
    rows.append("INSERT INTO contract VALUES (1,'1605-06-01','Decennio pieno',0);")
    iid = 100
    for _ in range(3):
        rows.append(f"INSERT INTO investor VALUES ({iid},1,1,0,0,0);"); iid += 1
    for _ in range(2):
        rows.append(f"INSERT INTO investor VALUES ({iid},1,0,1,0,0);"); iid += 1
    rows.append(f"INSERT INTO investor VALUES ({iid},1,1,1,0,0);"); iid += 1
    for _ in range(24):
        rows.append(f"INSERT INTO investor VALUES ({iid},1,0,0,0,0);"); iid += 1
    # decade 1700: 29 investors — one short of the ≥30 gate → must NOT appear
    rows.append("INSERT INTO contract VALUES (2,'1705-06-01','Sotto soglia',0);")
    for _ in range(29):
        rows.append(f"INSERT INTO investor VALUES ({iid},2,1,0,0,0);"); iid += 1
    return "\n".join(rows)


def test_provenance_series_count_independently_and_gate_holds():
    conn = _conn(_provenance_seed())
    rows = conn.execute(_sql("jewish_provenance_over_time")).fetchall()
    assert len(rows) == 1 and rows[0]["decade"] == 1600  # the 29-investor decade is gated out
    # stated = (3 stated-only + 1 both) / 30; editorial = (2 editorial-only + 1 both) / 30
    assert rows[0]["pct_stated_in_act"] == pytest.approx(100 * 4 / 30, abs=0.01)
    assert rows[0]["pct_editorial_attribution"] == pytest.approx(100 * 3 / 30, abs=0.01)
    # and the combined either-provenance share = 6/30
    combined = conn.execute(_sql("jewish_over_time")).fetchall()
    assert len(combined) == 1
    assert combined[0]["pct_jewish"] == pytest.approx(100 * 6 / 30, abs=0.1)
    assert combined[0]["investors"] == 30


# ---------------------------------------------------------------------------
# the capital guard — one line between us and multiplied, mixed totals
# ---------------------------------------------------------------------------

def test_capital_measures_refuse_the_investors_subject():
    with pytest.raises(HTTPException) as e:
        build_analysis_sql({"subject": "investors", "measure": "sum_total"})
    assert e.value.status_code == 400
    assert "contracts only" in e.value.detail
    with pytest.raises(HTTPException):
        build_analysis_sql({"subject": "investors", "measure": "avg_total", "group_by": "decade"})
