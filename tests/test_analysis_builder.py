"""Tests for the guided builder's capital guard + link-aware currency counting
(code-review finding B2).

Capital amounts live in 315 different currency formulations, so the builder
must never add them across currencies: a capital measure is allowed only when
currency-scoped (filter or grouping); unscoped-with-no-grouping becomes the
honest per-currency table. Once humans confirm same_as links in the Reference
worklist, grouping folds spelling variants into their canonical phrase and the
"Currency is …" filter matches the whole family.

    uv run --with pytest pytest tests/test_analysis_builder.py
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from workflows.review_server import build_analysis_sql

# Two spellings of one unit (513/20 in the corpus; 2/1 here) + an unrelated one.
_LINKS = (
    {"scudi a lire 7 per scudo": "scudi di lire 7 per scudo"},
    {
        "scudi a lire 7 per scudo": ["scudi a lire 7 per scudo", "scudi di lire 7 per scudo"],
        "scudi di lire 7 per scudo": ["scudi a lire 7 per scudo", "scudi di lire 7 per scudo"],
    },
)

_SCHEMA_SEED = """
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, registration_date TEXT,
    total INTEGER, currency_id INTEGER, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE currency (currency_id INTEGER PRIMARY KEY, currency TEXT);
CREATE TABLE person (person_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT,
    is_woman INTEGER, is_deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, person_id INTEGER, contract_id INTEGER,
    is_deleted INTEGER NOT NULL DEFAULT 0, via_proxy INTEGER DEFAULT 0,
    is_jewish INTEGER DEFAULT 0, jewish_db INTEGER DEFAULT 0, title INTEGER);
INSERT INTO currency VALUES (1,'scudi di lire 7 per scudo'),(2,'scudi a lire 7 per scudo'),(3,'ducati');
INSERT INTO person VALUES (1,'Luca','Franceschi',0,0),(2,'Luca','Franceschi',0,0),(3,'Anna','Rossi',1,0);
INSERT INTO contract VALUES (10,'1600-01-01',100,1,0);
INSERT INTO contract VALUES (11,'1610-01-01',200,1,0);
INSERT INTO contract VALUES (12,'1620-01-01',400,2,0);
INSERT INTO contract VALUES (13,'1630-01-01',800,3,0);
INSERT INTO investor VALUES (101,1,10,0,0,0,0,NULL),(102,2,11,0,0,0,0,NULL),(103,3,12,0,0,0,0,NULL);
"""


def _run(sql, params):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SEED)
    return [dict(r) for r in conn.execute(sql, params)]


def test_capital_sum_grouped_by_year_without_currency_scope_is_refused():
    with pytest.raises(HTTPException) as e:
        build_analysis_sql({"subject": "contracts", "measure": "sum_total", "group_by": "decade"})
    assert "cannot be added together" in e.value.detail
    assert "Currency is" in e.value.detail


def test_capital_avg_gets_the_same_guard():
    with pytest.raises(HTTPException):
        build_analysis_sql({"subject": "contracts", "measure": "avg_total", "group_by": "reg_year"})


def test_capital_sum_unscoped_ungrouped_auto_groups_by_currency():
    sql, params, chart = build_analysis_sql({"subject": "contracts", "measure": "sum_total"})
    assert "cu.currency" in sql and "GROUP BY 1" in sql
    assert chart == "bar"
    rows = _run(sql, params)
    # without links: three per-phrase rows, never one cross-currency number
    assert {r["currency"]: r["total_capital"] for r in rows} == {
        "ducati": 800, "scudi a lire 7 per scudo": 400, "scudi di lire 7 per scudo": 300}


def test_capital_sum_with_currency_filter_allows_any_grouping():
    sql, params, chart = build_analysis_sql({
        "subject": "contracts", "measure": "sum_total", "group_by": "decade",
        "filters": [{"field": "currency_is", "value": "ducati"}],
    })
    rows = _run(sql, params)
    assert rows == [{"decade": 1630, "contracts": 1, "total_capital": 800}]


def test_count_measure_is_unaffected_by_the_guard():
    sql, params, _ = build_analysis_sql({"subject": "contracts", "measure": "count", "group_by": "decade"})
    assert len(_run(sql, params)) == 4


def test_confirmed_links_fold_variants_into_the_canonical_phrase():
    sql, params, _ = build_analysis_sql(
        {"subject": "contracts", "measure": "sum_total"}, currency_links=_LINKS)
    assert "CASE cu.currency" in sql
    rows = {r["currency"]: (r["contracts"], r["total_capital"]) for r in _run(sql, params)}
    # 100+200 (di) + 400 (a) fold into one canonical row of 3 contracts / 700
    assert rows == {"scudi di lire 7 per scudo": (3, 700), "ducati": (1, 800)}


def test_currency_filter_matches_the_whole_confirmed_family():
    sql, params, _ = build_analysis_sql(
        {"subject": "contracts", "measure": "count",
         "filters": [{"field": "currency_is", "value": "scudi a lire 7 per scudo"}]},
        currency_links=_LINKS)
    assert _run(sql, params) == [{"count": 3}]  # both spellings, either value typed


def test_no_links_means_exact_phrase_matching_as_before():
    sql, params, _ = build_analysis_sql(
        {"subject": "contracts", "measure": "count",
         "filters": [{"field": "currency_is", "value": "scudi a lire 7 per scudo"}]})
    assert _run(sql, params) == [{"count": 1}]


def test_reviewed_person_measure_folds_only_confirmed_identity_links():
    raw_sql, raw_params, _ = build_analysis_sql({
        "subject": "investors", "measure": "distinct_people_entered", "group_by": ""})
    reviewed_sql, reviewed_params, _ = build_analysis_sql(
        {"subject": "investors", "measure": "distinct_people_reviewed", "group_by": ""},
        person_links={2: 1},
    )
    assert _run(raw_sql, raw_params) == [{"people": 3}]
    assert _run(reviewed_sql, reviewed_params) == [{"people": 2}]


def test_reviewed_identity_measure_refuses_unsettled_person_attributes():
    with pytest.raises(HTTPException) as exc:
        build_analysis_sql(
            {
                "subject": "investors",
                "measure": "distinct_people_reviewed",
                "group_by": "gender",
            },
            person_links={2: 1},
        )
    assert "cannot yet be combined" in exc.value.detail


def test_distinct_people_excludes_rows_classified_as_non_people():
    sql, params, _ = build_analysis_sql(
        {
            "subject": "investors",
            "measure": "distinct_people_entered",
            "group_by": "",
        },
        excluded_person_ids=[3],
    )
    assert _run(sql, params) == [{"people": 2}]
