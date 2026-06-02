"""Unit tests for the tracked-changes revision parser.

Run with an ephemeral pytest (no project dependency added):

    uv run --with pytest pytest tests/test_revision_parser.py
"""

from __future__ import annotations

from workflows.review_server import build_word_entry_rich, parse_revision_segments


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
