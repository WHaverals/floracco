"""Unit tests for the tracked-changes revision parser.

Run with an ephemeral pytest (no project dependency added):

    uv run --with pytest pytest tests/test_revision_parser.py
"""

from __future__ import annotations

from workflows.review_server import build_word_entry_rich, group_word_entry_images, parse_revision_segments
from workflows.word_pipeline import (
    classify_match,
    event_components_for_text,
    event_label_guesses,
    has_matched_multiple,
    is_dual_contract_sub_combined_act,
    segment_register,
    split_compound_event_label_inner,
    trim_trailing_blank_paragraphs,
)


def _dual_act_candidates_entry_00042() -> list[dict]:
    """Top candidates for Camera_di_Commercio_1262_entry_00042 (disdetta + nuova)."""
    return [
        {
            "score": 146.25,
            "db_table": "contract",
            "db_contract_id": 2682,
            "db_main_contract_id": None,
            "narrative_similarity_ratio": 0.9624,
            "signals": ["component_contract_id_exact", "registration_date_exact", "folio_exact"],
            "conflicts": [],
        },
        {
            "score": 140.78,
            "db_table": "sub_contract",
            "db_contract_id": 1667,
            "db_main_contract_id": 2658,
            "narrative_similarity_ratio": 0.939,
            "signals": ["main_contract_id_referenced", "registration_date_exact", "folio_exact"],
            "conflicts": [],
        },
        {
            "score": 32.91,
            "db_table": "sub_contract",
            "db_contract_id": 1674,
            "db_main_contract_id": 2656,
            "narrative_similarity_ratio": 0.0362,
            "signals": [],
            "conflicts": ["registration_date_differs"],
        },
    ]


def test_is_dual_contract_sub_combined_act_positive() -> None:
    candidates = _dual_act_candidates_entry_00042()
    assert is_dual_contract_sub_combined_act(candidates) is True
    assert classify_match(candidates) == "matched_high_confidence"
    assert classify_match(candidates) != "ambiguous"


def test_is_dual_contract_sub_combined_act_rejects_two_sub_contracts() -> None:
    candidates = [
        {
            "score": 140.0,
            "db_table": "sub_contract",
            "narrative_similarity_ratio": 0.95,
            "signals": ["main_contract_id_referenced"],
            "conflicts": [],
        },
        {
            "score": 135.0,
            "db_table": "sub_contract",
            "narrative_similarity_ratio": 0.94,
            "signals": ["main_contract_id_referenced"],
            "conflicts": [],
        },
        {"score": 30.0, "db_table": "contract", "narrative_similarity_ratio": 0.05, "signals": [], "conflicts": []},
    ]
    assert is_dual_contract_sub_combined_act(candidates) is False


def test_is_dual_contract_sub_combined_act_rejects_third_candidate_too_close() -> None:
    base = _dual_act_candidates_entry_00042()
    candidates = [base[0], base[1], {**base[1], "score": 138.0, "db_contract_id": 9999}]
    assert is_dual_contract_sub_combined_act(candidates) is False


def test_is_dual_contract_sub_combined_act_rejects_low_text_similarity() -> None:
    candidates = _dual_act_candidates_entry_00042()
    candidates[1]["narrative_similarity_ratio"] = 0.70
    assert is_dual_contract_sub_combined_act(candidates) is False


def test_has_matched_multiple_regression() -> None:
    candidates = [
        {
            "score": 120.0,
            "db_table": "sub_contract",
            "db_main_contract_id": 100,
            "db_registration_date": "1641-04-19",
            "db_folio_start": 12,
            "db_folio_end": 12,
            "narrative_similarity_ratio": 0.95,
            "signals": ["main_contract_id_referenced"],
            "conflicts": [],
        },
        {
            "score": 118.0,
            "db_table": "sub_contract",
            "db_main_contract_id": 100,
            "db_registration_date": "1641-04-19",
            "db_folio_start": 12,
            "db_folio_end": 12,
            "narrative_similarity_ratio": 0.94,
            "signals": ["main_contract_id_referenced"],
            "conflicts": [],
        },
    ]
    assert has_matched_multiple(candidates) is True
    assert classify_match(candidates) == "matched_multiple"


def test_split_compound_event_label_inner_plus_and_slash() -> None:
    assert split_compound_event_label_inner("nuovo + variazione") == ["nuovo", "variazione"]
    assert split_compound_event_label_inner("Disdetta/Rinnovo") == ["Disdetta", "Rinnovo"]
    assert split_compound_event_label_inner("bilancio+rinnovo+modifica") == [
        "bilancio",
        "rinnovo",
        "modifica",
    ]


def test_split_compound_event_label_inner_e_conjunction() -> None:
    assert split_compound_event_label_inner("Bilancio e modifica") == ["Bilancio", "modifica"]


def test_split_compound_event_label_inner_skips_editorial_brackets() -> None:
    editorial = "Verifica compiuta in ASF: il contratto registrato fuori ordine cronologico"
    assert split_compound_event_label_inner(editorial) == [editorial]


def test_event_label_guesses_compound_bracket() -> None:
    assert set(event_label_guesses("[Bilancio e modifica]")) == {"modification", "balance"}
    assert event_label_guesses("[Disdetta/Rinnovo]") == ["termination", "renewal"]


def test_event_components_for_text_splits_single_compound_bracket() -> None:
    text = "[nuovo / disdetta] 4018 +  [nuovo] 5097\n\tNarrative body."
    components = event_components_for_text(text)
    raw_labels = [component["raw_label"] for component in components]
    assert raw_labels.count("[nuovo]") >= 2
    assert "[disdetta]" in raw_labels
    guesses = {component["label_guess"] for component in components if component["raw_label"] == "[nuovo]"}
    assert "new_contract" in guesses


def _text_tokens(parsed: dict) -> list[dict]:
    return [token for token in parsed["tokens"] if token["type"] == "text"]


def test_plain_text_has_no_revisions() -> None:
    parsed = parse_revision_segments("Compagnia di Lione, c. 12r.")
    assert parsed["has_revisions"] is False
    assert parsed["summary"] == {
        "insertions": 0,
        "deletions": 0,
        "moves": 0,
        "comments": 0,
        "notes": 0,
    }
    texts = _text_tokens(parsed)
    assert len(texts) == 1
    assert texts[0]["text"] == "Compagnia di Lione, c. 12r."
    assert texts[0]["changes"] == []
    assert texts[0]["comment_ids"] == []


def test_empty_input_is_safe() -> None:
    parsed = parse_revision_segments("")
    assert parsed["tokens"] == []
    assert parsed["has_revisions"] is False


def test_insertion_marks_change_on_text() -> None:
    parsed = parse_revision_segments('before <INS id="3" author="FT" date="2024">nuovo</INS> after')
    assert parsed["has_revisions"] is True
    assert parsed["summary"]["insertions"] == 1
    by_text = {token["text"]: token for token in _text_tokens(parsed)}
    assert by_text["before "]["changes"] == []
    inserted = by_text["nuovo"]
    assert len(inserted["changes"]) == 1
    assert inserted["changes"][0]["kind"] == "insertion"
    assert inserted["changes"][0]["author"] == "FT"
    assert inserted["changes"][0]["date"] == "2024"
    assert by_text[" after"]["changes"] == []


def test_deletion_kind() -> None:
    parsed = parse_revision_segments('keep <DEL id="9">vecchio</DEL> end')
    assert parsed["summary"]["deletions"] == 1
    deleted = next(token for token in _text_tokens(parsed) if token["text"] == "vecchio")
    assert deleted["changes"][0]["kind"] == "deletion"


def test_nested_changes_stack_outermost_first() -> None:
    parsed = parse_revision_segments('<INS id="1">outer <DEL id="2">inner</DEL> tail</INS>')
    inner = next(token for token in _text_tokens(parsed) if token["text"] == "inner")
    kinds = [change["kind"] for change in inner["changes"]]
    assert kinds == ["insertion", "deletion"]
    tail = next(token for token in _text_tokens(parsed) if token["text"] == " tail")
    assert [change["kind"] for change in tail["changes"]] == ["insertion"]


def test_move_markers_counted_as_moves() -> None:
    parsed = parse_revision_segments(
        '<MOVEFROM id="1">phrase</MOVEFROM> ... <MOVETO id="1">phrase</MOVETO>'
    )
    assert parsed["summary"]["moves"] == 2
    kinds = {token["changes"][0]["kind"] for token in _text_tokens(parsed) if token["changes"]}
    assert kinds == {"move_from", "move_to"}


def test_comment_range_attaches_ids_to_enclosed_text() -> None:
    parsed = parse_revision_segments('a <COMMENT_START id="7">flagged span<COMMENT_END id="7"> b')
    assert parsed["summary"]["comments"] == 1
    flagged = next(token for token in _text_tokens(parsed) if token["text"] == "flagged span")
    assert flagged["comment_ids"] == ["7"]
    trailing = next(token for token in _text_tokens(parsed) if token["text"] == " b")
    assert trailing["comment_ids"] == []


def test_point_markers_emit_ref_tokens() -> None:
    parsed = parse_revision_segments('text<FOOTNOTE_REF id="5">more<COMMENT_REF id="2">')
    types = [token["type"] for token in parsed["tokens"]]
    assert "note_ref" in types
    assert "comment_ref" in types
    note = next(token for token in parsed["tokens"] if token["type"] == "note_ref")
    assert note["kind"] == "footnote"
    assert note["id"] == "5"
    assert parsed["summary"]["notes"] == 1
    assert parsed["summary"]["comments"] == 1


def test_newlines_and_tabs_become_structural_tokens() -> None:
    parsed = parse_revision_segments("line one\n\tindented")
    types = [token["type"] for token in parsed["tokens"]]
    assert "break" in types
    assert "tab" in types


def test_orphan_close_tag_is_tolerated() -> None:
    parsed = parse_revision_segments("plain </INS> still here")
    texts = "".join(token["text"] for token in _text_tokens(parsed))
    assert "plain " in texts
    assert "still here" in texts
    assert all(token["changes"] == [] for token in _text_tokens(parsed))


def test_unbalanced_open_does_not_drop_text() -> None:
    parsed = parse_revision_segments('start <INS id="1">never closed and more text')
    texts = "".join(token["text"] for token in _text_tokens(parsed))
    assert "start " in texts
    assert "never closed and more text" in texts


def test_build_word_entry_rich_embeds_bodies() -> None:
    row = {
        "word_entry_revision_text": 'x <COMMENT_START id="7">y<COMMENT_END id="7"> '
        '<INS id="1">z</INS><FOOTNOTE_REF id="5">',
        "word_entry_has_revisions": True,
        "word_entry_text": "x y z",
        "word_entry_comments": [
            {"id": "7", "author": "FT", "date": "2024", "initials": "FT", "text": "check this"}
        ],
        "word_entry_notes": [{"id": "5", "kind": "footnote", "text": "ASF, Notarile."}],
    }
    rich = build_word_entry_rich(row)
    assert rich["has_revisions"] is True
    assert rich["clean_text"] == "x y z"
    assert rich["comments"][0]["text"] == "check this"
    assert rich["notes"][0]["text"] == "ASF, Notarile."
    flagged = next(
        token for token in rich["tokens"] if token["type"] == "text" and token["text"] == "y"
    )
    assert flagged["comment_ids"] == ["7"]


def test_build_word_entry_rich_handles_missing_fields() -> None:
    rich = build_word_entry_rich({})
    assert rich["has_revisions"] is False
    assert rich["tokens"] == []
    assert rich["comments"] == []
    assert rich["notes"] == []


def test_group_word_entry_images_merges_opening_spread() -> None:
    rows = [
        {
            "image_path": "Mercanzia/122.jpg",
            "image_file": "122.jpg",
            "image_role": "folio_opening",
            "matched_folio": "122v",
            "page_position": "left",
            "entry_folio_role": "start",
            "needs_review": False,
        },
        {
            "image_path": "Mercanzia/122.jpg",
            "image_file": "122.jpg",
            "image_role": "folio_opening",
            "matched_folio": "123r",
            "page_position": "right",
            "entry_folio_role": "end",
            "needs_review": True,
        },
    ]
    grouped = group_word_entry_images(rows)
    assert len(grouped) == 1
    assert grouped[0]["path"] == "Mercanzia/122.jpg"
    assert grouped[0]["needs_review"] is True
    assert [f["folio"] for f in grouped[0]["folios"]] == ["122v", "123r"]


def test_trim_trailing_blank_paragraphs() -> None:
    paragraphs = [
        {"paragraph_index": 0, "current_text": "body"},
        {"paragraph_index": 1, "current_text": ""},
        {"paragraph_index": 2, "current_text": "   "},
    ]
    trimmed = trim_trailing_blank_paragraphs(paragraphs)
    assert [p["paragraph_index"] for p in trimmed] == [0]
    assert trim_trailing_blank_paragraphs([{"paragraph_index": 0, "current_text": ""}]) == []


def test_segment_register_trims_trailing_blank_paragraphs() -> None:
    register = {
        "register_id": "Test_Register",
        "source_file": "test.docx",
        "normalized_path": "test.docx",
        "front_matter_paragraph_count": 0,
    }
    paragraphs = [
        {"paragraph_index": 0, "current_text": "1 gennaio 1600", "date_candidates": ["1 gennaio 1600"]},
        {"paragraph_index": 1, "current_text": "[Nuova] 100"},
        {"paragraph_index": 2, "current_text": "Primo att."},
        {"paragraph_index": 3, "current_text": ""},
        {"paragraph_index": 4, "current_text": ""},
        {"paragraph_index": 5, "current_text": "2 gennaio 1600", "date_candidates": ["2 gennaio 1600"]},
        {"paragraph_index": 6, "current_text": "[Nuova] 101"},
        {"paragraph_index": 7, "current_text": "Secondo att."},
        {"paragraph_index": 8, "current_text": ""},
        {"paragraph_index": 9, "current_text": ""},
        {"paragraph_index": 10, "current_text": ""},
    ]
    entries, entry_paragraphs, unsegmented, _issues = segment_register(register, paragraphs)
    assert len(entries) == 2
    assert entries[0]["paragraph_count"] == 3
    assert entries[0]["end_paragraph_index"] == 2
    # Blanks before the next label are absorbed as leading context for entry 2.
    assert entries[1]["paragraph_count"] == 5
    assert entries[1]["end_paragraph_index"] == 7
    blank_unassigned = [
        row for row in unsegmented if row["classification"] == "blank_unassigned"
    ]
    assert [row["paragraph_index"] for row in blank_unassigned] == [8, 9, 10]
    assert len(entry_paragraphs) == 8


def test_group_word_entry_images_keeps_distinct_files() -> None:
    rows = [
        {
            "image_path": "Mercanzia/122.jpg",
            "matched_folio": "122v",
            "page_position": "left",
            "needs_review": False,
        },
        {
            "image_path": "Mercanzia/124.jpg",
            "matched_folio": "124v",
            "page_position": "left",
            "needs_review": False,
        },
    ]
    grouped = group_word_entry_images(rows)
    assert len(grouped) == 2
    assert [item["path"] for item in grouped] == ["Mercanzia/122.jpg", "Mercanzia/124.jpg"]
