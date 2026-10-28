"""Audited, reversible person identity decisions in corrections.db."""

from __future__ import annotations

import sqlite3

import pytest

from workflows import corrections_db, review_server


def decide(conn, action: str, ids: list[int]):
    return corrections_db.record_person_review(
        conn,
        action=action,
        person_ids=ids,
        by="reviewer@example.org",
        evidence_snapshot={"case": "fixture", "ids": ids},
        case_id="pair:" + ":".join(map(str, sorted(ids))),
        rationale="reviewed fixture",
        source_tier="review",
        model_sha256="abc",
        cache_run_id="run-1",
        match_probability=0.91,
    )


def test_person_same_as_and_revoke_are_audited(tmp_path):
    path = tmp_path / "corrections.db"
    conn = corrections_db.connect(path)
    result = decide(conn, "same_as", [20, 10])

    links = corrections_db.active_reference_links(conn, "person")
    assert [(row["from_id"], row["to_id"]) for row in links] == [(10, 20)]
    events = corrections_db.person_review_events(conn)
    assert events[0]["action"] == "same_as"
    assert events[0]["person_ids"] == [10, 20]
    assert events[0]["link_ids"] == result["link_ids"]

    revoked = corrections_db.revoke_person_reference_link(
        conn, link_id=result["link_ids"][0], by="reviewer@example.org"
    )
    assert revoked
    assert corrections_db.active_reference_links(conn, "person") == []
    events = corrections_db.person_review_events(conn)
    assert [row["action"] for row in events] == ["revoke", "same_as"]
    assert events[0]["case_id"] == "pair:10:20"
    conn.close()


def test_person_decisions_reject_transitive_contradictions(tmp_path):
    conn = corrections_db.connect(tmp_path / "corrections.db")
    decide(conn, "same_as", [1, 2])
    decide(conn, "distinct", [1, 3])

    with pytest.raises(corrections_db.PersonDecisionConflict):
        decide(conn, "same_as", [2, 3])
    assert len(corrections_db.active_reference_links(conn, "person")) == 2
    assert len(corrections_db.person_review_events(conn)) == 2
    conn.close()


def test_group_distinct_is_atomic_and_pair_complete(tmp_path):
    conn = corrections_db.connect(tmp_path / "corrections.db")
    result = decide(conn, "distinct", [3, 1, 2])
    assert len(result["link_ids"]) == 3
    assert {
        frozenset((row["from_id"], row["to_id"]))
        for row in corrections_db.active_reference_links(conn, "person")
    } == {frozenset((1, 2)), frozenset((1, 3)), frozenset((2, 3))}
    assert len(corrections_db.person_review_events(conn)) == 1
    conn.close()


def test_whole_group_decision_undo_is_atomic(tmp_path):
    conn = corrections_db.connect(tmp_path / "corrections.db")
    decision = decide(conn, "distinct", [3, 1, 2])
    undone = corrections_db.revoke_person_review_event(
        conn,
        event_id=decision["event_id"],
        by="FT",
        rationale="Re-open the whole group.",
    )
    assert set(undone["revoked_link_ids"]) == set(decision["link_ids"])
    assert corrections_db.active_reference_links(conn, "person") == []
    events = corrections_db.person_review_events(conn)
    assert [event["action"] for event in events] == ["revoke", "distinct"]
    assert events[0]["evidence_snapshot"]["undo_scope"] == "whole_decision"
    assert events[0]["evidence_snapshot"]["original_event_id"] == decision["event_id"]
    conn.close()


def test_distinctness_applies_to_the_reviewed_identity_family(tmp_path):
    conn = corrections_db.connect(tmp_path / "corrections.db")
    decide(conn, "same_as", [1, 2])
    decide(conn, "distinct", [1, 3])
    with pytest.raises(corrections_db.PersonDecisionConflict, match="already active"):
        decide(conn, "distinct", [2, 3])
    assert len(corrections_db.active_reference_links(conn, "person")) == 2
    assert len(corrections_db.person_review_events(conn)) == 2
    conn.close()


def test_defer_is_event_only(tmp_path):
    conn = corrections_db.connect(tmp_path / "corrections.db")
    decide(conn, "defer", [1, 2])
    assert corrections_db.active_reference_links(conn, "person") == []
    assert corrections_db.person_review_events(conn)[0]["action"] == "defer"
    conn.close()


def test_repeating_active_decision_is_not_logged_as_new_work(tmp_path):
    conn = corrections_db.connect(tmp_path / "corrections.db")
    decide(conn, "same_as", [1, 2])
    with pytest.raises(corrections_db.PersonDecisionConflict, match="already active"):
        decide(conn, "same_as", [1, 2])
    assert len(corrections_db.active_reference_links(conn, "person")) == 1
    assert len(corrections_db.person_review_events(conn)) == 1
    conn.close()


def test_generic_reference_revoke_cannot_bypass_person_event(tmp_path, monkeypatch):
    path = tmp_path / "corrections.db"
    conn = corrections_db.connect(path)
    result = decide(conn, "same_as", [1, 2])
    conn.close()

    monkeypatch.setattr(
        review_server, "open_corrections", lambda: corrections_db.connect(path)
    )
    with pytest.raises(review_server.HTTPException) as exc:
        review_server.db_reference_link_revoke.__wrapped__(
            result["link_ids"][0], review_server.ReviewerOnly(reviewer="WH")
        )
    assert exc.value.status_code == 409

    conn = corrections_db.connect(path)
    assert len(corrections_db.active_reference_links(conn, "person")) == 1
    assert [row["action"] for row in corrections_db.person_review_events(conn)] == [
        "same_as"
    ]
    conn.close()


def test_double_revoke_writes_exactly_one_event(tmp_path):
    path = tmp_path / "corrections.db"
    conn = corrections_db.connect(path)
    result = decide(conn, "same_as", [1, 2])
    link_id = result["link_ids"][0]
    first = corrections_db.revoke_person_reference_link(conn, link_id=link_id, by="FT")
    second = corrections_db.revoke_person_reference_link(conn, link_id=link_id, by="FT")
    assert first
    assert second == {}
    assert [event["action"] for event in corrections_db.person_review_events(conn)] == [
        "revoke",
        "same_as",
    ]
    conn.close()


def test_flag_split_and_reopen_are_event_only(tmp_path):
    conn = corrections_db.connect(tmp_path / "corrections.db")
    decide(conn, "flag_split", [7])
    assert corrections_db.active_reference_links(conn, "person") == []
    assert corrections_db.person_review_events(conn)[0]["action"] == "flag_split"
    decide(conn, "reopen", [7])
    assert [event["action"] for event in corrections_db.person_review_events(conn)] == [
        "reopen",
        "flag_split",
    ]
    conn.close()


def test_scoped_pair_distinct_in_a_three_person_group(tmp_path):
    conn = corrections_db.connect(tmp_path / "corrections.db")
    decide(conn, "distinct", [1, 2])
    decide(conn, "same_as", [2, 3])
    assert {
        (row["rel"], row["from_id"], row["to_id"])
        for row in corrections_db.active_reference_links(conn, "person")
    } == {("distinct", 1, 2), ("same_as", 2, 3)}
    conn.close()

