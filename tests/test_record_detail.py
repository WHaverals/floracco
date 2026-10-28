"""Tests for record-detail display honesty (code-review B5 + B6).

Runs contract_detail / sub_contract_detail directly on the CANONICAL schema
(queries/schema/tables.sql + the is_deleted columns db_import adds), with the
file-reading helpers stubbed out.

    uv run --with pytest pytest tests/test_record_detail.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workflows import corrections_db, review_server

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"


@pytest.fixture()
def conn(monkeypatch) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_TABLES_SQL.read_text())
    for table in corrections_db.ALL_TABLE_PRIMARY_KEYS:
        c.execute(f"ALTER TABLE `{table}` ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    # stub the helpers that read pipeline files / the proposals store
    monkeypatch.setattr(review_server, "proposals_for_row", lambda *_: {})
    monkeypatch.setattr(review_server, "_attach_word_check", lambda *_a, **_k: None)
    monkeypatch.setattr(review_server, "manuscript_images_for", lambda *_a, **_k: [])
    monkeypatch.setattr(review_server, "linked_word_sources", lambda *_a, **_k: [])
    c.executescript("""
        INSERT INTO currency (currency_id, currency) VALUES (7, 'scudi');
        -- B6: whitespace-only firm name; B5: currency_id=0 sentinel with a real total
        INSERT INTO contract (contract_id, registration_date, firm_name, currency_id, total)
            VALUES (1, '1600-01-01', ' ', 0, 4500);
        -- control: real name, real currency
        INSERT INTO contract (contract_id, registration_date, firm_name, currency_id, total)
            VALUES (2, '1600-01-01', 'Compagnia Reale', 7, 100);
        -- currency sentinel with NO total at all
        INSERT INTO contract (contract_id, registration_date, firm_name, currency_id, total)
            VALUES (3, '1600-01-01', 'Terza', 0, NULL);
        INSERT INTO sub_contract (contract_id, archive, series, folder, folio,
            registration_date, sub_firm_name, sub_type, main_contract_id)
            VALUES (90, 'ASF', 'Mercanzia', '10831', '1r', '1601-01-01', ' ', 'termination', 2);
    """)
    return c


def _total(detail):
    return next(f["value"] for f in detail["fields"] if f["label"] == "Total capital")


def test_whitespace_firm_name_falls_back_to_contract_number(conn):
    assert review_server.contract_detail(conn, "1")["title"] == "Contract 1"
    assert review_server.contract_detail(conn, "2")["title"] == "Compagnia Reale"


def test_whitespace_sub_firm_name_falls_back_too(conn):
    assert review_server.sub_contract_detail(conn, "90")["title"] == "Sub-contract 90"


def test_missing_currency_sentinel_never_renders_none(conn):
    assert _total(review_server.contract_detail(conn, "1")) == "4500 · currency not recorded"
    assert _total(review_server.contract_detail(conn, "2")) == "100 scudi"
    assert _total(review_server.contract_detail(conn, "3")) == "—"


# --- UI review 2026-10-14: labels, placeholder dates, readable change history ---


def test_economic_activity_label(conn):
    """The contract's activity reads "Economic activity" (the column is called
    economic_sector, but it holds one activity phrase) and stays distinct from
    "Economic activity at discretion"; React keys the fields by label."""
    labels = [f["label"] for f in review_server.contract_detail(conn, "2")["fields"]]
    assert "Economic activity" in labels
    assert "Economic activity at discretion" in labels
    assert "Economic sector" not in labels
    assert len(labels) == len(set(labels))


def test_placeholder_date_reads_not_recorded(conn):
    conn.executescript("""
        INSERT INTO contract (contract_id, registration_date, start_date, firm_name, currency_id)
            VALUES (4, '0000-00-00', '0000-00-00', 'Senza data', 7);
        INSERT INTO sub_contract (contract_id, archive, series, folder, folio, registration_date,
            end_date, sub_firm_name, sub_type, main_contract_id)
            VALUES (91, 'ASF', 'Mercanzia', '10831', '2r', '0000-00-00', '0000-00-00', '', 'termination', 4);
    """)
    detail = review_server.contract_detail(conn, "4")
    fields = {f["label"]: f for f in detail["fields"]}
    for label in ("Registration date", "Start date"):
        assert fields[label]["value"] == "not recorded"
        assert fields[label]["current"] == "0000-00-00"  # the editor still starts from the stored value
    subs = next(s for s in detail["sections"] if s["title"].startswith("Sub-contracts"))
    assert subs["rows"][0]["cells"][1] == "not recorded"
    sub = review_server.sub_contract_detail(conn, "91")
    sub_fields = {f["label"]: f["value"] for f in sub["fields"]}
    assert sub_fields["Registration date"] == "not recorded"
    assert sub_fields["End date"] == "not recorded"
    # a real date still reads verbatim
    assert {f["label"]: f["value"] for f in review_server.contract_detail(conn, "2")["fields"]}[
        "Registration date"
    ] == "1600-01-01"


# --- Find's contract drawer (review 2026-10-27, 12.1): read-only facts on the record ---


def test_record_carries_marks_register_and_both_jewish_layers(conn, monkeypatch):
    """The drawer reads the data-entry team's "inferred" mark on places and on the
    activity, the register as the filters name it, and the two Jewish layers apart;
    removed partners and removed places stay out, as on the record page."""
    monkeypatch.setattr(review_server, "proposals_by_row", lambda: {})
    conn.executescript("""
        INSERT INTO place (place_id, place_name) VALUES (25, 'Livorno'), (26, 'Pisa'), (27, 'Lucca');
        INSERT INTO contract (contract_id, archive, series, folder, folio, registration_date,
            firm_name, currency_id, total, economic_sector_db)
            VALUES (5, 'ASF', 'Mercanzia', ' 10838 ', '1r', '1700-01-01', 'Ditta', 7, 900, 1);
        INSERT INTO contract_place (place_id, contract_id, address, place_db) VALUES (25, 5, '', 0);
        INSERT INTO contract_place (place_id, contract_id, address, place_db) VALUES (26, 5, '', 1);
        INSERT INTO contract_place (place_id, contract_id, address, place_db, is_deleted) VALUES (27, 5, '', 1, 1);
        INSERT INTO person (person_id, first_name, last_name) VALUES (1, 'Abram', 'Franco'),
            (2, 'Isach', 'Nunes'), (3, 'Piero', 'Neri'), (4, 'Tolto', 'Via');
        INSERT INTO investor (investor_id, person_id, contract_id, is_jewish, jewish_db) VALUES
            (11, 1, 5, 1, 0), (12, 2, 5, 0, 1), (13, 3, 5, 0, 0);
        INSERT INTO investor (investor_id, person_id, contract_id, is_jewish, jewish_db, is_deleted)
            VALUES (14, 4, 5, 1, 1, 1);
        INSERT INTO investment (investment_id, contract_id, type, investment_cash) VALUES
            (21, 5, 'gp', 500), (22, 5, 'lp', 400), (23, 5, 'lp', 0);
        INSERT INTO investor_group (investor_id, investment_id) VALUES (11, 21), (12, 22), (13, 23);
        -- the default contract has no register and no marks
        INSERT INTO contract (contract_id, registration_date, firm_name) VALUES (6, '1700-01-01', 'Altra');
    """)
    detail = review_server.contract_detail(conn, "5")
    assert detail["register"] == "Mercanzia 10838"
    assert detail["activity_inferred"] is True
    assert [(p["place"], p["inferred"]) for p in detail["places"]["rows"]] == [("Livorno", False), ("Pisa", True)]
    layers = {r["person"]["name"]: r["jewish"] for r in detail["partners"]["rows"]}
    assert layers == {
        "Abram Franco": {"stated": True, "attributed": False},
        "Isach Nunes": {"stated": False, "attributed": True},
        "Piero Neri": {"stated": False, "attributed": False},
    }
    plain = review_server.contract_detail(conn, "6")
    assert plain["register"] == ""
    assert plain["activity_inferred"] is False
    # removed partners come back only on request, without the layers
    removed = review_server.build_partners(conn, "5", include_hidden=True)["rows"]
    assert [r["jewish"] for r in removed if r["removed"]] == [None]
