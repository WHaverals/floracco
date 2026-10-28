"""Links people send keep working (UI review 2026-10-14, #44; LOG 2026-10-27).

Analysis queries are linked as /analysis/<query id> and Reference terms as
/reference/<vocabulary>/<term id>. A renamed query id would silently break links
already sent, and a term that is gone must say so instead of showing a blank
heading.

    uv run --with pytest pytest tests/test_shareable_links.py
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, review_server

# The ids shipped when links were introduced (2026-10-27). Adding queries is fine;
# renaming or removing one breaks links already sent, so it must be a decision.
SHIPPED_QUERY_IDS = {
    "corpus_totals", "contracts_per_year", "currency_frequency", "currency_totals", "location_frequency",
    "investors_per_contract", "acts_per_contract", "investments_by_role", "women_investors", "ec_discretion_yes",
    "no_place", "no_investors", "no_economic_activity", "jewish_all", "jewish_mixed", "gp_lp_per_contract",
    "relatives_in_contract", "pct_via_proxy", "via_proxy_over_time", "jewish_over_time",
    "jewish_provenance_over_time", "joint_over_time", "funding_composition", "initial_term", "later_acts_by_type",
}


def test_library_query_ids_are_link_safe_and_stable() -> None:
    ids = [q["id"] for q in review_server.ANALYSIS_LIBRARY]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9_]+", i) for i in ids)
    # /analysis/build, /analysis/console, /analysis/find and /analysis/library are
    # the builder, the console, Find and the library's own page
    assert not {"build", "console", "find", "library"} & set(ids)
    assert SHIPPED_QUERY_IDS <= set(ids), SHIPPED_QUERY_IDS - set(ids)


_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    main = str(tmp_path / "main.db")
    c = sqlite3.connect(main)
    c.executescript(_TABLES_SQL.read_text())
    for table in corrections_db.ALL_TABLE_PRIMARY_KEYS:
        c.execute(f"ALTER TABLE `{table}` ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    c.executescript("INSERT INTO title (title_id, title_name) VALUES (235, 'quondam'), (371, '  ');")
    c.commit()

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    yield c
    c.close()


def test_a_linked_term_that_is_gone_is_not_found(conn) -> None:
    assert review_server.db_reference_records("title", 235, offset=0, limit=120)["value"] == "quondam"
    for missing in (9999, 371):  # no such row; a blank row
        with pytest.raises(HTTPException) as err:
            review_server.db_reference_records("title", missing, offset=0, limit=120)
        assert err.value.status_code == 404
