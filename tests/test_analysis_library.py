"""Tests locking ANALYSIS_LIBRARY queries to their titles' semantics.

The library queries are read-only SQL shown to historians; nothing else
exercises them, which is how a title ("partners") and its SQL (investment
rows) drifted apart (code-review finding B1). Each test pulls the SQL from
the registry BY ID, so editing a query without keeping its meaning breaks
the suite.

    uv run --with pytest pytest tests/test_analysis_library.py
"""

from __future__ import annotations

import sqlite3

from workflows.review_server import ANALYSIS_LIBRARY

_SCHEMA = """
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, registration_date TEXT, total INTEGER,
    is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE person (person_id INTEGER PRIMARY KEY, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investment (investment_id INTEGER PRIMARY KEY, contract_id INTEGER, type TEXT,
    investment_cash INTEGER, investment_non_cash TEXT, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, person_id INTEGER, contract_id INTEGER,
    is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor_group (investor_id INTEGER, investment_id INTEGER,
    is_deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (investor_id, investment_id));
"""

# Contract 1 — the four traps the partner count must survive:
#   inv 10 (gp): a JOINT tranche shared by persons 1,2,3  → 3 partners, not 1
#   inv 11 (lp): person 4 ... and inv 12 (lp): person 4 again
#                (one person, two tranches)               → 1 partner, not 2
#   inv 13 (lp): UNATTACHED (no live holder)              → 0 partners, not 1
# Contract 2 — HIDDEN: its gp tranche must not count at all.
# Contract 3 — plain single-gp control.
_SEED = """
INSERT INTO contract VALUES (1,'1600-01-01',NULL,0),(2,'1600-01-01',NULL,1),(3,'1600-01-01',NULL,0);
INSERT INTO person VALUES (1,0),(2,0),(3,0),(4,0),(5,0);
INSERT INTO investment VALUES (10,1,'gp',0,NULL,0),(11,1,'lp',100,NULL,0),(12,1,'lp',200,NULL,0),(13,1,'lp',300,NULL,0);
INSERT INTO investor VALUES (100,1,1,0),(101,2,1,0),(102,3,1,0),(103,4,1,0),(104,4,1,0);
INSERT INTO investor_group VALUES (100,10,0),(101,10,0),(102,10,0),(103,11,0),(104,12,0);
INSERT INTO investment VALUES (20,2,'gp',0,NULL,0);
INSERT INTO investor VALUES (200,5,2,0);
INSERT INTO investor_group VALUES (200,20,0);
INSERT INTO investment VALUES (30,3,'gp',0,NULL,0);
INSERT INTO investor VALUES (300,5,3,0);
INSERT INTO investor_group VALUES (300,30,0);

-- Funding-itemization fixtures (one contract per bucket; investments carry
-- type NULL so the gp/lp partner counts above stay untouched):
--   c10 cash equals total, nothing noted        c11 equals + qualifier note
--   c12 short + non-cash recorded               c13 short + literal-'0' sentinel
--   c14 no cash + non-cash recorded             c15 no cash, nothing itemized
--   c16 HIDDEN contract — must not count at all
--   c17 cash ABOVE the total (stored total 10 beside 10,000 in cash, as on 2441)
--   c18 cash above the total with a non-cash note — still "exceeds", never "equals"
INSERT INTO contract VALUES (10,'1600-01-01',100,0),(11,'1600-01-01',100,0),
    (12,'1600-01-01',100,0),(13,'1600-01-01',100,0),(14,'1600-01-01',100,0),
    (15,'1600-01-01',100,0),(16,'1600-01-01',100,1),
    (17,'1600-01-01',10,0),(18,'1600-01-01',100,0);
INSERT INTO investment VALUES
    (110,10,NULL,100,'',0),
    (111,11,NULL,100,'parte stimata in panni',0),
    (112,12,NULL,60,'in drappi',0),
    (113,13,NULL,60,'0',0),
    (114,14,NULL,NULL,'bottega',0),
    (115,15,NULL,NULL,'',0),
    (116,16,NULL,100,'',0),
    (117,17,NULL,10000,'',0),
    (118,18,NULL,150,'in drappi',0);
"""


def _sql(query_id: str) -> str:
    return next(q["sql"] for q in ANALYSIS_LIBRARY if q["id"] == query_id)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA + _SEED)
    return conn


def test_gp_lp_per_contract_counts_people_not_tranches():
    rows = {r["role"]: dict(r) for r in _conn().execute(_sql("gp_lp_per_contract"))}
    # gp: contract 1's joint tranche = 3 people; contract 3 = 1 person; the
    # hidden contract 2 contributes nothing. min 1 · max 3 · 2 contracts.
    assert rows["gp"]["min"] == 1
    assert rows["gp"]["max"] == 3
    assert rows["gp"]["contracts"] == 2
    assert rows["gp"]["avg"] == 2.0
    # lp: person 4 holds TWO tranches on contract 1 → counted once; the
    # unattached tranche 13 adds nobody. Exactly one lp partner, one contract.
    assert rows["lp"]["min"] == 1
    assert rows["lp"]["max"] == 1
    assert rows["lp"]["contracts"] == 1


def test_funding_itemization_buckets_are_arithmetic_plus_recorded_noncash():
    rows = list(_conn().execute(_sql("funding_composition")))
    got = {r["itemization"]: r["contracts"] for r in rows}
    assert got == {
        "cash equals the stated total": 1,                      # c10
        "cash equals the total — non-cash component noted": 1,  # c11 (qualifier note)
        "cash exceeds the stated total": 2,                     # c17, c18: never filed as "equals"
        "cash short of the total — non-cash recorded": 1,       # c12
        "cash short of the total — remainder unitemized": 1,    # c13 — the literal-'0'
        "no cash recorded — non-cash recorded": 1,              # c14   sentinel is NOT
        "no cash recorded — nothing itemized": 1,               # c15   "recorded" text
    }
    # hidden contract 16 counted nowhere, and rows arrive in logical bucket order
    assert sum(got.values()) == 8
    assert [r["itemization"] for r in rows][:3] == [
        "cash equals the stated total",
        "cash equals the total — non-cash component noted",
        "cash exceeds the stated total",
    ]


def test_gp_lp_per_contract_ignores_soft_deleted_layers():
    conn = _conn()
    # soft-delete one co-holder's LINK: the joint gp stake drops 3 → 2
    conn.execute("UPDATE investor_group SET is_deleted=1 WHERE investor_id=102")
    rows = {r["role"]: dict(r) for r in conn.execute(_sql("gp_lp_per_contract"))}
    assert rows["gp"]["max"] == 2
    # soft-delete an INVESTOR row: 2 → 1
    conn.execute("UPDATE investor SET is_deleted=1 WHERE investor_id=101")
    rows = {r["role"]: dict(r) for r in conn.execute(_sql("gp_lp_per_contract"))}
    assert rows["gp"]["max"] == 1


# --- UI review 2026-10-14: the library speaks the reviewer's words ---------------

import re  # noqa: E402

from workflows import review_server  # noqa: E402


def test_library_text_names_no_columns_or_codes():
    """Titles and descriptions never show column names, bare role codes or the
    retired "investor"/"investment"/"later acts" vocabulary. "External investor"
    is the role's name (WH, 2026-10-26), so only a bare "investor" is banned."""
    banned = re.compile(
        r"\b(is_jewish|jewish_db|gp|lp|later_acts|(?<!external )investors?|investments?|tranches?)\b", re.I
    )
    for q in ANALYSIS_LIBRARY:
        text = f"{q['title']} {q['description']}"
        assert not banned.search(text), (q["id"], text)


def test_sub_contract_aliases_use_the_reviewers_word():
    by_id = {q["id"]: q["sql"] for q in ANALYSIS_LIBRARY}
    assert "AS sub_contracts" in by_id["acts_per_contract"]
    assert "later_acts" not in by_id["acts_per_contract"]
    assert "AS act_type" not in by_id["later_acts_by_type"] and "AS acts" not in by_id["later_acts_by_type"]


def test_average_capital_is_named_for_what_it_holds():
    scoped = [{"field": "currency_is", "value": "ducati"}]
    sql, _, _ = review_server.build_analysis_sql({"subject": "contracts", "measure": "avg_total", "filters": scoped})
    assert "AS average_capital" in sql and "total_capital" not in sql
    sql, _, _ = review_server.build_analysis_sql({"subject": "contracts", "measure": "sum_total", "filters": scoped})
    assert "AS total_capital" in sql
