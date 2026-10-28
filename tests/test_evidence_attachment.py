"""enrich_family2 — the only code that machine-attaches manuscript evidence
and pre-filled dates onto correction-queue candidates.

A keying bug here attaches document X's tracked change (and X's pre-filled
date) to record Y's candidate: a wrong suggestion one click from a logged DB
write. These tests pin the (db_row_id, reason_code) keying, the date/folio
gating, the safe-prefill rule (dates only, never folios), and the
don't-overwrite rule.

    uv run --with pytest pytest tests/test_evidence_attachment.py
"""

from __future__ import annotations

from workflows.correction_candidates import enrich_family2


def _entry(eid: str, revision_text: str) -> dict:
    return {
        "source_entry_id": eid,
        "has_revisions": True,
        "revision_aware_text": revision_text,
    }


DATE_EDIT = (
    'registrato <DEL author="FT" date="2013-05-01">26 gennaio 1600</DEL>'
    '<INS author="FT" date="2013-05-01">26 gennaio 1699/700</INS> in Mercanzia'
)
FOLIO_EDIT = (
    'a <DEL author="BB" date="2013-06-01">c. 45r</DEL>'
    '<INS author="BB" date="2013-06-01">c. 46v</INS> del registro'
)
PROSE_EDIT = (  # a tracked change touching NEITHER a date nor a folio
    '<DEL author="FT">chompagnia</DEL><INS author="FT">compagnia</INS>'
)


def _candidates():
    """Family-1 conflicts for two rows: a date conflict on contract:10 and BOTH
    a folio and a date conflict on contract:20."""
    a = {"word_value": "26 gennaio 1699/700"}                   # date conflict, row 10
    b = {"word_value": "46v"}                                   # folio conflict, row 20
    c = {"word_value": "12 marzo 1650"}                         # date conflict, row 20 (decoy)
    index = {
        ("contract:10", "registration_date_differs"): a,
        ("contract:20", "folio_differs"): b,
        ("contract:20", "registration_date_differs"): c,
    }
    return a, b, c, index


def test_evidence_lands_on_the_right_row_and_field():
    a, b, c, index = _candidates()
    entries = [_entry("E1", DATE_EDIT), _entry("E2", FOLIO_EDIT)]
    ent2db = {"E1": [{"db_row_id": "contract:10"}], "E2": [{"db_row_id": "contract:20"}]}
    enriched = enrich_family2(entries, ent2db, index)
    assert enriched == 2
    # E1's date edit → row 10's DATE candidate, with the safe pre-fill: the
    # entry's own double-dated reading, resolved to the modern calendar
    assert a["revision_evidence"]["insertions"] == ["26 gennaio 1699/700"]
    assert a["revision_evidence"]["author"] == "FT"
    assert a["suggested_value"] == "1700-01-26"
    # E2's folio edit → row 20's FOLIO candidate; folios NEVER get a pre-fill
    assert b["revision_evidence"]["insertions"] == ["c. 46v"]
    assert "suggested_value" not in b
    # the decoy: row 20 has a date conflict too, but E2 edited only a folio —
    # cross-attaching here is exactly the silent lie this fence exists to stop
    assert "revision_evidence" not in c


def test_prose_only_revisions_attach_nothing():
    a, b, c, index = _candidates()
    entries = [_entry("E1", PROSE_EDIT)]
    ent2db = {"E1": [{"db_row_id": "contract:10"}]}
    assert enrich_family2(entries, ent2db, index) == 0
    assert "revision_evidence" not in a and "suggested_value" not in a


def test_unlinked_entries_and_missing_conflicts_attach_nothing():
    a, b, c, index = _candidates()
    entries = [_entry("E1", DATE_EDIT), _entry("E3", DATE_EDIT)]
    # E1 linked to a row with NO family-1 conflict; E3 not linked at all
    ent2db = {"E1": [{"db_row_id": "contract:99"}]}
    assert enrich_family2(entries, ent2db, index) == 0


def test_existing_evidence_is_never_overwritten():
    a, b, c, index = _candidates()
    a["revision_evidence"] = {"insertions": ["prior"], "deletions": [], "author": "X", "date": None}
    a["suggested_value"] = "1650-01-01"
    entries = [_entry("E1", DATE_EDIT)]
    ent2db = {"E1": [{"db_row_id": "contract:10"}]}
    assert enrich_family2(entries, ent2db, index) == 0
    assert a["revision_evidence"]["author"] == "X" and a["suggested_value"] == "1650-01-01"


def test_ambiguous_florentine_date_prefills_nothing():
    # "26 gennaio 1600" (no double-date suffix, pre-1750, Jan) is ambiguous
    # stile fiorentino — evidence attaches, but NO machine pre-fill
    a, b, c, index = _candidates()
    a["word_value"] = "26 gennaio 1600"
    entries = [_entry("E1", DATE_EDIT)]
    ent2db = {"E1": [{"db_row_id": "contract:10"}]}
    assert enrich_family2(entries, ent2db, index) == 1
    assert "revision_evidence" in a
    assert "suggested_value" not in a
