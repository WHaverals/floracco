"""Unit tests for the `bool` input type (Phase 4a).

Booleans in this corpus are strictly 0/1 with no NULLs, so the editor is a plain
Yes/No mapped to 1/0; validation accepts only "0"/"1" and the value is written as
an int (same path as `number`). See docs/data_quality + the Phase-4 analysis.

    uv run --with pytest pytest tests/test_bool_fields.py
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from workflows import review_server as rs


def test_bool_fields_are_registered():
    assert rs.CORRECTABLE_FIELDS["person"]["is_woman"]["input_type"] == "bool"
    for col in ("automatic_renewal", "clauses", "pl_discretion", "ec_discretion",
                "administrators", "additional_docs"):
        assert rs.CORRECTABLE_FIELDS["contract"][col]["input_type"] == "bool", col
    # the renewal-months companion stays a number
    assert rs.CORRECTABLE_FIELDS["contract"]["automatic_renewal_months"]["input_type"] == "number"


def test_protected_investor_fields_stay_uncorrectable():
    # jewish_db: the platform never mints or edits editorial Jewish attributions.
    # The ONLY sanctioned write is the audited refute endpoint (1→0, reason
    # required); the generic editor gates purely on registry membership, so this
    # ABSENCE is the entire enforcement of the house rule. Keep it absent.
    assert "jewish_db" not in rs.CORRECTABLE_FIELDS["investor"]
    # is_joint is derived from the tranche's group structure, never edited directly.
    assert "is_joint" not in rs.CORRECTABLE_FIELDS["investor"]
    for field in ("jewish_db", "is_joint"):
        with pytest.raises(HTTPException):
            rs.validate_correction("investor", field, "correct", "1")


def test_validate_bool_accepts_only_0_or_1():
    assert rs.validate_correction("person", "is_woman", "correct", "1") == "1"
    assert rs.validate_correction("person", "is_woman", "correct", "0") == "0"
    for bad in ("2", "Yes", "true", "-1", "x"):
        with pytest.raises(HTTPException):
            rs.validate_correction("person", "is_woman", "correct", bad)


def test_record_field_bool_is_editable_with_canonical_current():
    corrections: dict = {}
    field = rs.record_field("Recorded as woman", "person", "is_woman", 1, rs.yes_no(1), corrections)
    assert field["editable"] is True
    assert field["input_type"] == "bool"
    assert field["current"] == "1"          # editor sends canonical 1/0
    assert field["value"] == "Yes"          # display via yes_no()

    field0 = rs.record_field("Special clauses", "contract", "clauses", 0, rs.yes_no(0), corrections)
    assert field0["current"] == "0"
    assert field0["value"] == "No"
