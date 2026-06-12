"""Unit tests for the tracked-changes revision parser.

Run with an ephemeral pytest (no project dependency added):

    uv run --with pytest pytest tests/test_revision_parser.py
"""

from __future__ import annotations

from workflows.review_server import build_word_entry_rich, group_word_entry_images, parse_revision_segments
from workflows.word_pipeline import (
    act_components_for_review,
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


def test_act_components_for_review_dual_act() -> None:
    entry = {
        "current_text": (
            "30 ottobre 1778\n"
            "[disdetta] di 2658 + [nuova] 2682\n"
            "Il signor Francesco Cornacchi dice che i signori Tommaso Guarducci..."
        ),
        "event_label_raw": "[disdetta]",
        "event_label_guess": "termination",
        "event_number_raw": "2658",
    }
    links = [
        {
            "db_row_id": "contract:2682",
            "db_table": "contract",
            "db_contract_id": 2682,
            "db_main_contract_id": None,
            "component_label": "contract",
            "score": 146.25,
        },
        {
            "db_row_id": "sub_contract:1667",
            "db_table": "sub_contract",
            "db_contract_id": 1667,
            "db_main_contract_id": 2658,
            "component_label": "termination",
            "score": 140.78,
        },
    ]
    rows = act_components_for_review(entry, links)
    assert len(rows) == 2
    by_label = {row["raw_label"]: row for row in rows}
    assert by_label["[disdetta]"]["suggested_db_row_id"] == "sub_contract:1667"
    assert by_label["[disdetta]"]["mapping_confidence"] == "exact"
    assert by_label["[nuova]"]["suggested_db_row_id"] == "contract:2682"
    assert by_label["[nuova]"]["mapping_confidence"] == "exact"


def test_act_components_for_review_compound_heuristic() -> None:
    entry = {
        "current_text": "[bilancio+modifica] 2948\nBody text.",
        "event_label_raw": "[bilancio+modifica]",
        "event_label_guess": "balance",
        "event_number_raw": "2948",
    }
    links = [
        {
            "db_row_id": "sub_contract:2114",
            "db_table": "sub_contract",
            "db_contract_id": 2114,
            "db_main_contract_id": 2948,
            "db_sub_type": "balance",
            "component_label": "balance",
            "score": 149.5,
        },
        {
            "db_row_id": "sub_contract:2115",
            "db_table": "sub_contract",
            "db_contract_id": 2115,
            "db_main_contract_id": 2948,
            "db_sub_type": "variation",
            "component_label": "variation",
            "score": 149.5,
        },
    ]
    rows = act_components_for_review(entry, links)
    assert len(rows) == 2
    mapped_ids = {row["suggested_db_row_id"] for row in rows}
    assert mapped_ids == {"sub_contract:2114", "sub_contract:2115"}
    assert all(row["mapping_confidence"] == "exact" for row in rows)


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


# ---------------------------------------------------------------------------
# Margin notes, head-block date harvesting, date-signal split, type relations
# (LOG 2026-06-11)
# ---------------------------------------------------------------------------

from workflows.word_pipeline import (  # noqa: E402
    event_type_relation,
    is_date_context_paragraph,
    parse_folio_heading,
    parse_italian_date,
    score_match,
)


def test_margin_note_is_not_date_context() -> None:
    margin = {
        "current_text": "A margine: disdetta in data 1 luglio 1782 a carta 48",
        "date_candidates": ["1 luglio 1782"],
        "bracket_labels": [],
    }
    assert not is_date_context_paragraph(margin)
    # Variants and a leading parenthesis are also margin notes.
    for text in ("Nel margine: vedi c. 12", "(A margine: finita)", "In margine: tronca"):
        assert not is_date_context_paragraph(
            {"current_text": text + " 1 luglio 1782", "date_candidates": ["1 luglio 1782"], "bracket_labels": []}
        )
    # A plain short date line still is date context.
    plain = {"current_text": "24 aprile 1778", "date_candidates": ["24 aprile 1778"], "bracket_labels": []}
    assert is_date_context_paragraph(plain)


def test_segment_register_keeps_margin_note_with_previous_entry() -> None:
    """The transcribers append the margin note at the END of the act it
    annotates; the backward extension must not pull it into the next entry
    (where its date would hijack the registration date)."""
    register = {
        "register_id": "Test_Register",
        "source_file": "test.docx",
        "normalized_path": "test.docx",
        "front_matter_paragraph_count": 0,
    }
    paragraphs = [
        {"paragraph_index": 0, "current_text": "c. 1r", "folio_heading": parse_folio_heading("c. 1r")},
        {"paragraph_index": 1, "current_text": "1 gennaio 1600", "date_candidates": ["1 gennaio 1600"]},
        {"paragraph_index": 2, "current_text": "[Nuova] 100"},
        {"paragraph_index": 3, "current_text": "Primo atto."},
        {
            "paragraph_index": 4,
            "current_text": "A margine: disdetta in data 1 luglio 1782 a carta 48",
            "date_candidates": ["1 luglio 1782"],
        },
        {"paragraph_index": 5, "current_text": ""},
        {"paragraph_index": 6, "current_text": "c. 2r", "folio_heading": parse_folio_heading("c. 2r")},
        {"paragraph_index": 7, "current_text": "24 aprile 1778", "date_candidates": ["24 aprile 1778"]},
        {"paragraph_index": 8, "current_text": "[nuovo] 101"},
        {"paragraph_index": 9, "current_text": "Secondo atto."},
    ]
    entries, entry_paragraphs, _unsegmented, _issues = segment_register(register, paragraphs)
    assert len(entries) == 2
    # Margin note stays in the first entry's tail …
    assert entries[0]["end_paragraph_index"] == 4
    roles = {row["paragraph_index"]: row["paragraph_role"] for row in entry_paragraphs}
    assert roles[4] == "margin_note"
    # … and the second entry's date is its own, not the margin note's.
    assert entries[1]["registration_date_raw"] == "24 aprile 1778"
    assert entries[1]["registration_date_precision"] == "day"
    assert "1 luglio 1782" not in entries[1]["date_candidates"]
    assert entries[1]["date_candidates_source"] == "head"


def test_head_block_finds_date_written_after_the_label() -> None:
    """Some registers write folio → label → date (e.g. Mercanzia 10856). The
    head block is order-independent, and a bare number on the label line is an
    act number, never a year."""
    register = {
        "register_id": "Test_Register",
        "source_file": "test.docx",
        "normalized_path": "test.docx",
        "front_matter_paragraph_count": 0,
    }
    paragraphs = [
        {"paragraph_index": 0, "current_text": "c. 43r", "folio_heading": parse_folio_heading("c. 43r")},
        {"paragraph_index": 1, "current_text": "[nuova] 1775", "date_candidates": ["1775"]},
        {"paragraph_index": 2, "current_text": "18 novembre 1734", "date_candidates": ["18 novembre 1734"]},
        {"paragraph_index": 3, "current_text": "Il signor Pietro Gaetano di Tommaso."},
    ]
    entries, _mapping, _unsegmented, _issues = segment_register(register, paragraphs)
    assert len(entries) == 1
    assert entries[0]["registration_date_raw"] == "18 novembre 1734"
    assert entries[0]["registration_date_precision"] == "day"
    assert "1775" not in entries[0]["date_candidates"]


def test_year_only_dating_is_kept_with_year_precision() -> None:
    register = {
        "register_id": "Test_Register",
        "source_file": "test.docx",
        "normalized_path": "test.docx",
        "front_matter_paragraph_count": 0,
    }
    paragraphs = [
        {"paragraph_index": 0, "current_text": "1522", "date_candidates": ["1522"]},
        {"paragraph_index": 1, "current_text": "[Nuova] 200"},
        {"paragraph_index": 2, "current_text": "Giovanni di Francesco."},
    ]
    entries, _mapping, _unsegmented, _issues = segment_register(register, paragraphs)
    assert entries[0]["registration_date_raw"] == "1522"
    assert entries[0]["registration_date_precision"] == "year"


def test_parse_italian_date_accepts_historical_month_variants() -> None:
    assert parse_italian_date("4 gennaro 1691") == "1691-01-04"
    assert parse_italian_date("10 febbraro 1550") == "1550-02-10"
    assert parse_italian_date("2 giungo 1733") == "1733-06-02"
    assert parse_italian_date("7 otobre 1801") == "1801-10-07"


def _date_entry(**overrides: object) -> dict:
    entry = {
        "register_id": "R",
        "event_label_guess": "termination",
        "event_label_raw": "[Disdetta]",
        "event_number_raw": None,
        "referenced_event_number_raw": None,
        "registration_date_raw": "1 gennaio 1600",
        "date_candidates": ["1 gennaio 1600"],
        "date_candidates_source": "head",
        "current_text": "1 gennaio 1600\n[Disdetta]\ncome da scritta del 2 gennaio 1600.",
        "folio_start": None,
        "folio_end": None,
    }
    entry.update(overrides)
    return entry


def _date_db_row(registration_date: str) -> dict:
    return {
        "db_row_id": "sub_contract:1",
        "db_table": "sub_contract",
        "contract_id": 1,
        "main_contract_id": 9,
        "register_id": "R",
        "registration_date": registration_date,
        "sub_type": "termination",
        "_match_text": "",
    }


def test_score_match_head_date_match_is_exact() -> None:
    details = score_match(_date_entry(), _date_db_row("1600-01-01"), "")
    assert "registration_date_exact" in details["signals"]
    assert "registration_date_differs" not in details["conflicts"]


def test_score_match_body_date_never_silences_a_head_date_conflict() -> None:
    """A date mentioned in the narrative body (here a scritta date) must not
    satisfy the exact-date signal nor suppress the conflict against the act's
    own head date."""
    details = score_match(_date_entry(), _date_db_row("1600-01-02"), "")
    assert "registration_date_exact" not in details["signals"]
    assert "registration_date_differs" in details["conflicts"]
    assert "date_in_narrative" in details["signals"]


def test_score_match_year_only_dating_matches_softly() -> None:
    entry = _date_entry(
        registration_date_raw="1522",
        date_candidates=["1522"],
        current_text="1522\n[Disdetta]\nGiovanni.",
    )
    details = score_match(entry, _date_db_row("1522-05-05"), "")
    assert "registration_year_match" in details["signals"]
    assert "registration_date_differs" not in details["conflicts"]


def test_event_type_relation_states() -> None:
    termination = {"event_label_guess": "termination", "event_label_raw": "[Disdetta]", "current_text": ""}
    assert event_type_relation(termination, "sub_contract", "termination") == "exact"
    assert event_type_relation(termination, "contract", None) == "mismatch"
    cessione = {"event_label_guess": "assignment", "event_label_raw": "[Cessione]", "current_text": ""}
    assert event_type_relation(cessione, "sub_contract", "variation") == "interpretive"
    assert event_type_relation(cessione, "sub_contract", "balance") == "mismatch"
    assert event_type_relation(cessione, "sub_contract", "") == "unknown"
    # Combined acts are judged per component: the contract row of a
    # [disdetta] + [nuova] entry is exact, not a mismatch against "termination".
    combined = {
        "event_label_guess": "termination",
        "event_label_raw": "[disdetta]",
        "current_text": "[disdetta] di 2658 + [nuova] 2682\nnarrative",
    }
    assert event_type_relation(combined, "contract", None) == "exact"
    assert event_type_relation(combined, "sub_contract", "termination") == "exact"


def test_double_dated_head_keeps_suffix_and_matches_modern_year() -> None:
    """"19 febbraio 1694/95" states the modern year in the suffix: the raw date
    keeps it, and the head-date set contains the modern year so the DB date
    matches exactly (not via a stile shift)."""
    register = {
        "register_id": "Test_Register",
        "source_file": "test.docx",
        "normalized_path": "test.docx",
        "front_matter_paragraph_count": 0,
    }
    paragraphs = [
        {"paragraph_index": 0, "current_text": "19 febbraio 1694/95", "date_candidates": ["19 febbraio 1694"]},
        {"paragraph_index": 1, "current_text": "[Disdetta] di 4485"},
        {"paragraph_index": 2, "current_text": "Compare il signor Francesco Berzini."},
    ]
    entries, _mapping, _unsegmented, _issues = segment_register(register, paragraphs)
    entry = entries[0]
    assert entry["registration_date_raw"] == "19 febbraio 1694/95"
    db_row = _date_db_row("1695-02-19")
    details = score_match(entry, db_row, "")
    assert "registration_date_exact" in details["signals"]
    assert "registration_date_differs" not in details["conflicts"]


def test_modern_registration_iso_prefill_rules() -> None:
    from workflows.correction_candidates import modern_registration_iso

    # Double-dated: the suffix is the modern year.
    assert modern_registration_iso("19 febbraio 1694/95") == "1695-02-19"
    assert modern_registration_iso("4 gennaro 1691/1692") == "1692-01-04"
    # Single date in the Florentine window, pre-1750: ambiguous — no pre-fill.
    assert modern_registration_iso("22 febbraio 1499") is None
    assert modern_registration_iso("24 marzo 1700") is None
    # Outside the window, or after the 1750 reform: literal.
    assert modern_registration_iso("25 marzo 1700") == "1700-03-25"
    assert modern_registration_iso("8 maggio 1778") == "1778-05-08"
    assert modern_registration_iso("10 gennaio 1782") == "1782-01-10"


def test_create_op_replays_onto_a_fresh_seed(tmp_path, monkeypatch) -> None:
    """A DB-native row (applied `create` op) must survive a reseed: replay
    re-INSERTs it from its snapshot, and flags a conflict instead of duplicating
    when the seed already carries the id."""
    import sqlite3

    from workflows import corrections_db, db_import

    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", str(tmp_path / "corrections.db"))
    clog = corrections_db.connect(tmp_path / "corrections.db")
    corrections_db.record_operation(
        clog,
        op="create",
        db_table="contract",
        pk={"contract_id": 9999},
        by="WH-test",
        after_value={"contract_id": 9999, "folder": "10831", "folio": "1r",
                     "registration_date": "1500-01-01", "document": "Test regest.",
                     "temp": 1, "is_deleted": 0, "not_a_column": "dropped"},
        reason="source: unit test",
    )
    assert corrections_db.created_row_ids(clog) == {"contract:9999"}
    clog.close()

    seed = sqlite3.connect(":memory:")
    seed.execute(
        "CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, folder TEXT, folio TEXT,"
        " registration_date TEXT, document TEXT, temp INTEGER, is_deleted INTEGER DEFAULT 0)"
    )
    stats = db_import.replay_corrections(seed)
    assert stats["applied"] == 1 and stats["conflicts"] == 0
    row = seed.execute("SELECT folder, folio, document FROM contract WHERE contract_id=9999").fetchone()
    assert row == ("10831", "1r", "Test regest.")

    # Second replay onto the SAME db: the id is taken → conflict, never a duplicate.
    stats2 = db_import.replay_corrections(seed)
    assert stats2["applied"] == 0 and stats2["conflicts"] == 1
    count = seed.execute("SELECT count(*) FROM contract WHERE contract_id=9999").fetchone()[0]
    assert count == 1


def test_search_index_build_query_and_staleness(tmp_path) -> None:
    import os
    import sqlite3
    import time

    from workflows import search_index

    main = tmp_path / "main.db"
    con = sqlite3.connect(main)
    con.executescript("""
        CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, archive TEXT, series TEXT,
          folder TEXT, folio TEXT, registration_date TEXT, firm_name TEXT, economic_sector INTEGER,
          document TEXT, is_deleted INTEGER DEFAULT 0);
        CREATE TABLE sub_contract (contract_id INTEGER PRIMARY KEY, main_contract_id INTEGER,
          sub_type TEXT, folder TEXT, folio TEXT, registration_date TEXT, sub_firm_name TEXT,
          document TEXT, is_deleted INTEGER DEFAULT 0);
        CREATE TABLE person (person_id INTEGER PRIMARY KEY, first_name TEXT, father_mother TEXT,
          grandfather TEXT, last_name TEXT, nickname TEXT, is_deleted INTEGER DEFAULT 0);
        CREATE TABLE investor (investor_id INTEGER PRIMARY KEY, person_id INTEGER, contract_id INTEGER,
          profession TEXT, husband_first_name TEXT, husband_last_name TEXT, place_of_residence INTEGER);
        CREATE TABLE investment (investment_id INTEGER PRIMARY KEY);
        CREATE TABLE contract_place (place_id INTEGER, contract_id INTEGER);
        CREATE TABLE place (place_id INTEGER PRIMARY KEY, place_name TEXT);
        CREATE TABLE economic_activity (ec_activity_id INTEGER PRIMARY KEY, activity TEXT);
        INSERT INTO economic_activity VALUES (1, 'arte di seta');
        INSERT INTO contract VALUES (100, 'ASF', 'Mercanzia', '10831', '21r', '1451-03-05',
          'Niccolò Salviati e compagni', 1, 'Giovanni dichiara di aver ricevuto\\r\\nin accomandita fiorini 400.', 0);
        INSERT INTO person VALUES (7, 'Antonio', 'di Pagolo', NULL, 'Sangallo', NULL, 0);
        INSERT INTO investor VALUES (1, 7, 100, 'battiloro', NULL, NULL, NULL);
    """)
    con.commit(); con.close()

    stats = search_index.build(main)
    assert stats["rows"] == 2
    sp = search_index.default_path(main)

    # Diacritic-free query finds Niccolò; the title carries the snippet markers.
    out = search_index.search(sp, "niccolo salviati")
    contracts = next(g for g in out["groups"] if g["kind"] == "contract")
    assert contracts["total"] == 1
    # Profession (from the investor row) reaches the person.
    out = search_index.search(sp, "battiloro")
    people = next(g for g in out["groups"] if g["kind"] == "person")
    assert people["total"] == 1 and "Sangallo" in people["results"][0]["title"]
    # Literal \r\n escapes were normalized for the index body.
    out = search_index.search(sp, "accomandita fiorini")
    assert out["total"] == 1
    # Honest empty state: AND of two terms that never co-occur → per-term counts.
    out = search_index.search(sp, "salviati battiloro")
    assert out["total"] == 0 and {t["term"]: t["count"] for t in out["term_counts"]} == {
        "salviati": 1, "battiloro": 1}
    # Hostile input must not crash the FTS parser.
    assert search_index.search(sp, 'dell’arte AND ("')["total"] >= 0

    # Staleness: untouched → fresh; main.db modified → stale.
    assert not search_index.is_stale(main, sp)
    time.sleep(0.01)
    con = sqlite3.connect(main); con.execute("UPDATE contract SET folio='22r'"); con.commit(); con.close()
    os.utime(main)
    assert search_index.is_stale(main, sp)
