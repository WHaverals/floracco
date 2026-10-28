"""Person worklist API and audited decision integration."""

from __future__ import annotations

import hashlib
import sqlite3
import threading

import pytest
from fastapi import HTTPException

from workflows import corrections_db, db_import, person_cache, review_server


def _main_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE person (
          person_id INTEGER PRIMARY KEY, first_name TEXT, father_mother TEXT,
          grandfather TEXT, last_name TEXT, nickname TEXT, is_woman INTEGER,
          is_deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE contract (
          contract_id INTEGER PRIMARY KEY, registration_date TEXT, firm_name TEXT,
          folder TEXT, folio TEXT, document TEXT, economic_sector INTEGER,
          is_deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE investor (
          investor_id INTEGER PRIMARY KEY, person_id INTEGER, contract_id INTEGER,
          profession TEXT, heirs_of INTEGER DEFAULT 0, place_of_residence INTEGER,
          place_of_origin INTEGER, title INTEGER, title_father_mother INTEGER,
          title_grandfather INTEGER, title_husband INTEGER,
          is_deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE investor_group (
          investor_id INTEGER, investment_id INTEGER, is_deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE investment (
          investment_id INTEGER PRIMARY KEY, type TEXT, is_deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE place (place_id INTEGER PRIMARY KEY, place_name TEXT);
        CREATE TABLE title (title_id INTEGER PRIMARY KEY, title_name TEXT);
        CREATE TABLE economic_activity (ec_activity_id INTEGER PRIMARY KEY, activity TEXT);
        INSERT INTO person VALUES
          (1,'Luca','Carlo','Lorenzo','Franceschi',NULL,0,0),
          (2,'Luca','Carlo',NULL,'Franceschi',NULL,0,0),
          (3,'Baccio',NULL,NULL,'Ulivi',NULL,0,0);
        INSERT INTO contract VALUES
          (10,'1623-01-01','Pandolfini e compagni','1','1r','One Luca appears.',NULL,0),
          (11,'1635-01-01','Pandolfini e compagni','1','2r','Another Luca appears.',NULL,0);
        INSERT INTO investor VALUES
          (101,1,10,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,0),
          (102,2,11,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,0),
          (103,3,10,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,0),
          (104,3,11,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,0);
        INSERT INTO investment VALUES (201,'lp',0),(202,'lp',0),(203,'gp',0),(204,'gp',0);
        INSERT INTO investor_group VALUES (101,201,0),(102,202,0),(103,203,0),(104,204,0);
        """
    )
    conn.commit()
    conn.close()


def _pair_suggestion(person_id_l=1, person_id_r=2, **overrides):
    left, right = sorted((int(person_id_l), int(person_id_r)))
    row = {
        "pair_key": f"{left}:{right}",
        "person_id_l": left,
        "person_id_r": right,
        "deterministic_tier": "review",
        "precedence_verdict": "review",
        "source": "rule",
        "match_probability": 0.95,
        "match_weight": 4.2,
        "score_band": "very_high",
        "blocking_lanes_json": ["exact_first_and_surname"],
        "reasons_json": ["careers overlap"],
        "evidence_json": {
            "names": {
                "left": {"first_name": "Luca", "father_mother": "Carlo", "last_name": "Franceschi"},
                "right": {"first_name": "Luca", "father_mother": "Carlo", "last_name": "Franceschi"},
            },
            "name_review": {"same_first": True},
            "career": {"combined_span_years": 12},
            "roles": {"left": "lp", "right": "lp"},
            "appearance_counts": {"left": 1, "right": 1},
            "shared": {"firms": ["pandolfini e compagni"]},
            "context_for_review": {},
            "network_diagnostics": {"partner_degree": 3},
            "firm_token_diagnostics": {"rarest_shared_token": "pandolfini"},
        },
        "shared_contract_ids_json": [],
        "shared_firms_json": ["pandolfini e compagni"],
        "shared_firm_words_json": ["pandolfini"],
        "shared_partner_ids_json": [3],
        "roles_json": {"left": "lp", "right": "lp"},
        "career_span_json": {
            "left": {"first_year": 1623, "last_year": 1623},
            "right": {"first_year": 1635, "last_year": 1635},
            "combined_span_years": 12,
        },
        "contract_pointers_json": {
            "left": {"person_id": left, "contract_ids": [10]},
            "right": {"person_id": right, "contract_ids": [11]},
        },
        "source_pointers_json": [],
        "waterfall_contributions_json": [],
        "run_id": "run-1",
    }
    row.update(overrides)
    left, right = sorted((int(row["person_id_l"]), int(row["person_id_r"])))
    row["person_id_l"] = left
    row["person_id_r"] = right
    row["pair_key"] = f"{left}:{right}"
    return row


def _group_projection(person_ids=(1, 2), edges=("1:2",), group_key="g1"):
    ids = [int(value) for value in person_ids]
    return {
        "group_key": group_key,
        "person_ids_json": ids,
        "edge_pair_keys_json": list(edges),
        "pair_matrix_json": {},
        "size": len(ids),
        "first_year": 1623,
        "last_year": 1635,
        "implied_career_years": 12,
        "career_guard": 0,
        "guard_status": "clear",
        "is_likely_same": 1,
        "reasons_json": ["test group"],
        "run_id": "run-1",
    }


def _cache(path, extra_pairs=None, groups=None):
    run = {
        "run_id": "run-1",
        "run_timestamp": "2026-08-28T12:00:00+00:00",
        "model_sha256": "a" * 64,
        "model_training_timestamp": "2026-08-28T11:00:00+00:00",
        "recall": 0.5,
        "model_seed": 7,
        "model_max_pairs": 1.0,
        "source_input_fingerprint": "sha256:" + "b" * 64,
        "source_pointer_fingerprint": "",
        "comparison_rule_version": person_cache.COMPARISON_RULE_VERSION,
        "deterministic_rule_version": person_cache.DETERMINISTIC_RULE_VERSION,
        "group_threshold": 0.9,
        "blocking_lane_count": 5,
    }
    pairs = [_pair_suggestion()]
    if extra_pairs:
        pairs.extend(extra_pairs)
    person_cache.write_cache_atomic(path, run, pairs, groups or [])


def test_case_decision_is_audited_without_mutating_main(tmp_path, monkeypatch):
    main = tmp_path / "main.db"
    cache = tmp_path / "person_cache.db"
    corr = tmp_path / "corrections.db"
    model = tmp_path / "person_model.json"
    _main_db(main)
    _cache(cache)
    model.write_text("{}")

    def open_db():
        conn = sqlite3.connect(main)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(review_server, "PERSON_CACHE_PATH", cache)
    monkeypatch.setattr(review_server, "PERSON_MODEL_PATH", model)
    monkeypatch.setattr(review_server, "db_path", lambda: main)
    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(
        review_server, "open_corrections", lambda: corrections_db.connect(corr)
    )
    before = hashlib.sha256(main.read_bytes()).hexdigest()
    detail = review_server.person_linkage_case("pair:1:2")
    assert [person["display_name"] for person in detail["persons"]] == [
        "Luca Franceschi",
        "Luca Franceschi",
    ]
    assert detail["business_context"]["exact_firms"][0]["display_name"] == (
        "Pandolfini e compagni"
    )
    shared_partner = detail["business_context"]["shared_partners"][0]
    assert shared_partner["display_name"] == "Baccio Ulivi"
    assert {
        side: [item["contract_id"] for item in contracts]
        for side, contracts in shared_partner["connections_by_person"].items()
    } == {"1": [10], "2": [11]}

    result = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="same_as",
            person_ids=[1, 2],
            rationale="The reviewed acts identify one person.",
            citations=[
                {
                    "source_entry_id": "entry-10",
                    "source_quote": "One Luca appears.",
                }
            ],
            expected_status="open",
            expected_event_id=None,
        ),
    )
    assert result["ok"] is True
    assert hashlib.sha256(main.read_bytes()).hexdigest() == before

    conn = corrections_db.connect(corr)
    assert len(corrections_db.active_reference_links(conn, "person")) == 1
    events = corrections_db.person_review_events(conn)
    assert events[0]["action"] == "same_as"
    assert events[0]["evidence_snapshot"]["persons"][0]["fields"]["first_name"] == "Luca"
    citation = events[0]["evidence_snapshot"]["citations"][0]
    assert citation["source_entry_id"] == "entry-10"
    assert citation["source_quote_sha256"] == hashlib.sha256(
        b"One Luca appears."
    ).hexdigest()
    conn.close()


def test_person_linkage_requires_explicit_enablement_when_serving_static(monkeypatch):
    monkeypatch.setenv("FLORACCO_SERVE_STATIC", "1")
    monkeypatch.delenv("FLORACCO_ENABLE_PERSON_LINKAGE", raising=False)
    assert review_server.person_linkage_enabled() is False
    monkeypatch.setenv("FLORACCO_ENABLE_PERSON_LINKAGE", "1")
    assert review_server.person_linkage_enabled() is True


def _wire(tmp_path, monkeypatch, extra_sql="", extra_pairs=None, groups=None):
    main = tmp_path / "main.db"
    cache = tmp_path / "person_cache.db"
    corr = tmp_path / "corrections.db"
    model = tmp_path / "person_model.json"
    _main_db(main)
    if extra_sql:
        conn = sqlite3.connect(main)
        conn.executescript(extra_sql)
        conn.commit()
        conn.close()
    _cache(cache, extra_pairs=extra_pairs, groups=groups)
    model.write_text("{}")

    def open_db():
        conn = sqlite3.connect(main)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(review_server, "PERSON_CACHE_PATH", cache)
    monkeypatch.setattr(review_server, "PERSON_MODEL_PATH", model)
    monkeypatch.setattr(review_server, "PERSON_LABELING_PACKET_PATH", tmp_path / "no_packet.jsonl")
    monkeypatch.setattr(review_server, "db_path", lambda: main)
    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(
        review_server, "open_corrections", lambda: corrections_db.connect(corr)
    )
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", str(corr))
    return main, cache, corr


def test_worklist_filters_and_name_search(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    page = review_server.person_linkage_cases(lane="other_matches", q="Luca", offset=0, limit=100)
    assert page["total"] == 1
    assert page["cases"][0]["case_id"] == "pair:1:2"
    empty = review_server.person_linkage_cases(lane="other_matches", q="Capponi", offset=0, limit=100)
    assert empty["total"] == 0


def test_all_lanes_search_finds_a_case_wherever_it_sits(tmp_path, monkeypatch):
    """lane="all" is the search box: a name or number finds its cases in every
    lane, each result names its lane, decided cases come back under Decided,
    and an empty query searches nothing (the lanes are the browse view)."""
    _wire(tmp_path, monkeypatch)
    page = review_server.person_linkage_cases(lane="all", q="Luca", offset=0, limit=100)
    assert page["total"] == 1
    assert page["cases"][0]["case_id"] == "pair:1:2"
    assert page["cases"][0]["lane"] == "other_matches"
    assert page["lane_counts"] == {"other_matches": 1}
    by_number = review_server.person_linkage_cases(lane="all", q="2", offset=0, limit=100)
    assert {case["case_id"] for case in by_number["cases"]} == {"pair:1:2"}
    assert review_server.person_linkage_cases(lane="all", q="", offset=0, limit=100)["total"] == 0
    assert review_server.person_linkage_cases(lane="all", q="Capponi", offset=0, limit=100)["total"] == 0

    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT", action="same_as", person_ids=[1, 2],
            rationale="The reviewed acts identify one person.",
            citations=[{"source_entry_id": "entry-10", "source_quote": "One Luca appears."}],
            expected_status="open", expected_event_id=None,
        ),
    )
    decided = review_server.person_linkage_cases(lane="all", q="Luca", offset=0, limit=100)
    assert [(case["case_id"], case["lane"], case["status"]) for case in decided["cases"]] == [
        ("pair:1:2", "decided", "same_as")
    ]


def _labeling_packet(tmp_path, monkeypatch, run_id="run-1", pair_key="1:2", pair_keys=None):
    packet = tmp_path / "labeling_packet.jsonl"
    rows = list(pair_keys) if pair_keys else [pair_key]
    packet.write_text(
        "".join(
            '{"pair_key":"%s","labeling_stratum":"audit_anchor","run_id":"%s",'
            '"stratum_population":4,"stratum_sample_size":4,'
            '"inclusion_probability":1.0,"sampling_weight":1.0}\n' % (key, run_id)
            for key in rows
        )
    )
    packet.with_name("labeling_packet.manifest.json").write_text(
        '{"algorithm_version":"person-labeling-v3","packet_sha256":"abc123"}'
    )
    monkeypatch.setattr(review_server, "PERSON_LABELING_PACKET_PATH", packet)
    return packet


# The contract both server and frontend implement: exactly these keys and no
# more, so any future field addition breaks the test instead of the blinding.
BLIND_DETAIL_KEYS = [
    "business_context",
    "case_id",
    "history",
    "kind",
    "lane",
    "latest_event_id",
    "pairs",
    "person_ids",
    "persons",
    "shared_contracts",
    "status",
]
# History events in the blind payload keep only reviewer-authored content:
# score-at-decision provenance and the labeling metadata (whose stratum name
# encodes the sampling cell) must not resurface when a case is reopened.
BLIND_HISTORY_EVENT_KEYS = [
    "action",
    "created_at",
    "created_by",
    "event_id",
    "evidence_snapshot",
    "link_ids",
    "person_ids",
    "rationale",
]
BLIND_PAIR_KEYS = [
    "career_span_json",
    "contract_pointers_json",
    "evidence_json",
    "pair_key",
    "person_id_l",
    "person_id_r",
    "roles_json",
    "shared_contract_ids_json",
    "shared_firm_words_json",
    "shared_firms_json",
    "shared_partner_ids_json",
    "source_pointers_json",
]
BLIND_PREVIEW_KEYS = [
    "career",
    "case_id",
    "is_reviewed",
    "lane",
    "names",
    "person_ids",
    "status",
]


def test_frozen_labeling_round_hides_model_routing(tmp_path, monkeypatch):
    main, cache, corr = _wire(tmp_path, monkeypatch)
    packet = _labeling_packet(tmp_path, monkeypatch)
    page = review_server.person_linkage_cases(
        lane="labeling_round", priority_band="All", offset=0, limit=100
    )
    assert page["blind_labeling"] is True
    assert page["total"] == 1
    preview = page["cases"][0]
    assert sorted(preview) == BLIND_PREVIEW_KEYS
    assert preview["lane"] == "labeling_round"
    assert preview["names"] == ["Luca Franceschi", "Luca Franceschi"]
    assert preview["person_ids"] == [1, 2]
    assert preview["career"]["combined_span_years"] == 12
    assert preview["is_reviewed"] is False
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="defer",
            person_ids=[1, 2],
            rationale="Source review needed.",
            expected_status="open",
            expected_event_id=None,
            review_mode="blind_labeling",
        ),
    )
    clog = corrections_db.connect(corr)
    labeling = corrections_db.person_review_events(clog)[0]["evidence_snapshot"]["labeling"]
    assert labeling["packet_id"] == "person-labeling-v3:abc123"
    assert labeling["sampling_weight"] == 1.0
    clog.close()
    packet.write_text('{"pair_key":"1:2","run_id":"older-cache"}\n')
    with pytest.raises(review_server.HTTPException) as exc:
        review_server.person_linkage_cases(
            lane="labeling_round", priority_band="All", offset=0, limit=100
        )
    assert exc.value.status_code == 409


def test_blind_detail_payload_is_a_strict_whitelist(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _labeling_packet(tmp_path, monkeypatch)
    detail = review_server.person_linkage_case("pair:1:2")
    assert sorted(detail) == BLIND_DETAIL_KEYS
    assert detail["lane"] == "labeling_round"
    assert detail["status"] == "open"
    # The decision dock reads these unconditionally — a blind payload without
    # them crashes every labeling case before the first click.
    assert detail["kind"] == "pair"
    assert detail["person_ids"] == [1, 2]
    # business_context is main.db record evidence (resolved partner names,
    # dated firm co-appearances) — admitted so the shared-firm/partner drawer
    # still works for labelers; it must never grow a model field.
    assert set(detail["business_context"]) <= {
        "exact_firms",
        "shared_partners",
        "shared_firm_words",
        "firm_word_evidence",
    }
    pair = detail["pairs"][0]
    assert sorted(pair) == BLIND_PAIR_KEYS
    evidence = pair["evidence_json"]
    assert "network_diagnostics" not in evidence
    assert "firm_token_diagnostics" not in evidence
    assert evidence["names"]["left"]["first_name"] == "Luca"
    assert evidence["career"] == {"combined_span_years": 12}
    assert evidence["shared"] == {"firms": ["pandolfini e compagni"]}
    for person in detail["persons"]:
        assert "identity_status" not in person
        assert person["context_profile"]["titles"] == []


def test_standard_decisions_are_rejected_while_the_round_is_open(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _labeling_packet(tmp_path, monkeypatch)

    def standard(action, expected_status="open", rationale=""):
        return review_server.PersonLinkageDecision(
            reviewer="FT",
            action=action,
            person_ids=[1, 2],
            rationale=rationale,
            expected_status=expected_status,
            expected_event_id=None,
        )

    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__("pair:1:2", standard("same_as"))
    assert exc.value.status_code == 409
    assert "This case is waiting under Judge blind" in exc.value.detail

    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="defer",
            person_ids=[1, 2],
            rationale="Source review needed.",
            expected_status="open",
            expected_event_id=None,
            review_mode="blind_labeling",
        ),
    )
    # Decided (deferred) cases leave the round: the full payload returns and
    # standard actions work again.
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["status"] == "defer"
    assert "match_probability" in detail
    assert "evidence_basis" in detail
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="reopen",
            person_ids=[1, 2],
            expected_status="defer",
            expected_event_id=detail["latest_event_id"],
        ),
    )
    # Reopening puts the case back into the round, blinded once more.
    detail = review_server.person_linkage_case("pair:1:2")
    assert sorted(detail) == BLIND_DETAIL_KEYS
    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__("pair:1:2", standard("same_as"))
    assert exc.value.status_code == 409
    # The reopened case's history holds its own prior blind decision — the
    # events must be whitelisted (no probability, no stratum-bearing labeling
    # metadata), and the bare latest_event_id must still be present so the
    # client's optimistic-concurrency check lets the case be decided AGAIN.
    for event in detail["history"]:
        assert sorted(event) == BLIND_HISTORY_EVENT_KEYS
        assert set(event["evidence_snapshot"]) <= {"citations"}
    assert detail["latest_event_id"] == detail["history"][0]["event_id"]
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="defer",
            person_ids=[1, 2],
            rationale="Second look, still unsure.",
            expected_status="open",
            expected_event_id=detail["latest_event_id"],
            review_mode="blind_labeling",
        ),
    )
    assert review_server.person_linkage_case("pair:1:2")["status"] == "defer"


def test_open_round_pairs_are_hidden_from_their_natural_lane(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    page = review_server.person_linkage_cases(lane="other_matches", offset=0, limit=100)
    assert page["total"] == 1
    _labeling_packet(tmp_path, monkeypatch)
    page = review_server.person_linkage_cases(
        lane="other_matches", status="All", offset=0, limit=100
    )
    assert page["total"] == 0
    # The listing must say WHY it is empty: the case is frozen in the round.
    assert page["reserved_for_labeling"] == 1
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="distinct",
            person_ids=[1, 2],
            expected_status="open",
            expected_event_id=None,
            review_mode="blind_labeling",
        ),
    )
    page = review_server.person_linkage_cases(
        lane="other_matches", status="distinct", offset=0, limit=100
    )
    assert [case["case_id"] for case in page["cases"]] == ["pair:1:2"]
    assert page["reserved_for_labeling"] == 0


def test_summary_reports_packet_availability_and_staleness(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    missing = tmp_path / "no_packet.jsonl"
    monkeypatch.setattr(review_server, "PERSON_LABELING_PACKET_PATH", missing)
    summary = review_server.person_linkage_summary()
    assert summary["labeling_packet_available"] is False
    assert summary["labeling_packet_stale"] is False
    assert summary["lanes"]["labeling_round"] == 0

    packet = _labeling_packet(tmp_path, monkeypatch)
    summary = review_server.person_linkage_summary()
    assert summary["labeling_packet_available"] is True
    assert summary["labeling_packet_stale"] is False
    assert summary["lanes"]["labeling_round"] == 1

    packet.write_text('{"pair_key":"1:2","run_id":"older-cache"}\n')
    summary = review_server.person_linkage_summary()
    assert summary["labeling_packet_available"] is True
    assert summary["labeling_packet_stale"] is True
    assert summary["lanes"]["labeling_round"] == 0


def test_decision_rejects_missing_and_non_person_ids(tmp_path, monkeypatch):
    ghost = {
        "pair_key": "1:99",
        "person_id_l": 1,
        "person_id_r": 99,
        "deterministic_tier": "review",
        "precedence_verdict": "review",
        "source": "rule",
        "match_probability": 0.5,
        "match_weight": 0.0,
        "score_band": "possible",
        "blocking_lanes_json": ["exact_first_and_surname"],
        "reasons_json": [],
        "evidence_json": {},
        "shared_contract_ids_json": [],
        "shared_firms_json": [],
        "shared_firm_words_json": [],
        "shared_partner_ids_json": [],
        "roles_json": {},
        "career_span_json": {},
        "contract_pointers_json": {},
        "source_pointers_json": [],
        "waterfall_contributions_json": [],
        "run_id": "run-1",
    }
    institution = dict(ghost, pair_key="1:9", person_id_r=9)
    _wire(
        tmp_path,
        monkeypatch,
        extra_sql="INSERT INTO person VALUES (9,'Arte della Lana',NULL,NULL,NULL,NULL,0,0);",
        extra_pairs=[ghost, institution],
    )
    missing = review_server.PersonLinkageDecision(
        reviewer="FT",
        action="same_as",
        person_ids=[1, 99],
        expected_status="open",
        expected_event_id=None,
    )
    try:
        review_server.decide_person_linkage.__wrapped__("pair:1:99", missing)
        raise AssertionError("missing person should 409")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "no longer in the database" in exc.detail

    non_person = review_server.PersonLinkageDecision(
        reviewer="FT",
        action="same_as",
        person_ids=[1, 9],
        expected_status="open",
        expected_event_id=None,
    )
    try:
        review_server.decide_person_linkage.__wrapped__("pair:1:9", non_person)
        raise AssertionError("institution should 409")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "does not name a person" in exc.detail


def test_generic_reference_cannot_create_or_revoke_person_links(tmp_path, monkeypatch):
    main, cache, corr = _wire(tmp_path, monkeypatch)
    try:
        review_server.db_reference_link.__wrapped__(
            "person",
            review_server.ReferenceLink(
                reviewer="FT", rel="same_as", from_id=1, to_id=2
            ),
        )
        raise AssertionError("generic create should reject person kind")
    except HTTPException as exc:
        assert exc.status_code == 400

    result = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="same_as",
            person_ids=[1, 2],
            expected_status="open",
            expected_event_id=None,
        ),
    )
    try:
        review_server.db_reference_link_revoke.__wrapped__(
            result["link_ids"][0], review_server.ReviewerOnly(reviewer="FT")
        )
        raise AssertionError("generic revoke should 409")
    except HTTPException as exc:
        assert exc.status_code == 409
    conn = corrections_db.connect(corr)
    assert len(corrections_db.active_reference_links(conn, "person")) == 1
    conn.close()


def test_evidence_change_marks_needs_recheck(tmp_path, monkeypatch):
    main, cache, corr = _wire(tmp_path, monkeypatch)
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="same_as",
            person_ids=[1, 2],
            expected_status="open",
            expected_event_id=None,
        ),
    )
    conn = sqlite3.connect(main)
    conn.execute("UPDATE person SET first_name='Lucas' WHERE person_id=1")
    conn.commit()
    conn.close()
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["needs_recheck"] is True
    assert detail["status"] == "same_as"


def test_stale_cache_is_flagged_when_model_changes(tmp_path, monkeypatch):
    main, cache, corr = _wire(tmp_path, monkeypatch)
    other = tmp_path / "other_model.json"
    other.write_text('{"different": true}')
    monkeypatch.setattr(review_server, "PERSON_MODEL_PATH", other)
    summary = review_server.person_linkage_summary()
    assert summary["stale"]["stale"] is True
    assert summary["stale"]["model_changed"] is True


def test_hidden_person_stays_in_history_as_unavailable(tmp_path, monkeypatch):
    main, cache, corr = _wire(tmp_path, monkeypatch)
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="same_as",
            person_ids=[1, 2],
            expected_status="open",
            expected_event_id=None,
        ),
    )
    conn = sqlite3.connect(main)
    conn.execute("UPDATE person SET is_deleted=1 WHERE person_id=2")
    conn.commit()
    conn.close()
    detail = review_server.person_linkage_case("pair:1:2")
    hidden = [person for person in detail["persons"] if person["person_id"] == 2][0]
    assert hidden["unavailable"] is True
    assert detail["status"] == "same_as"


def test_identity_decisions_survive_reseed_without_touching_main(tmp_path, monkeypatch):
    main, cache, corr = _wire(tmp_path, monkeypatch)
    before = hashlib.sha256(main.read_bytes()).hexdigest()
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="same_as",
            person_ids=[1, 2],
            expected_status="open",
            expected_event_id=None,
        ),
    )
    assert hashlib.sha256(main.read_bytes()).hexdigest() == before
    seed = tmp_path / "seed.db"
    seed.write_bytes(main.read_bytes())
    fresh = sqlite3.connect(seed)
    stats = db_import.replay_corrections(fresh)
    fresh.commit()
    fresh.close()
    assert stats["applied"] == 0
    assert hashlib.sha256(seed.read_bytes()).hexdigest() == hashlib.sha256(main.read_bytes()).hexdigest()
    conn = corrections_db.connect(corr)
    assert len(corrections_db.active_reference_links(conn, "person")) == 1
    assert corrections_db.person_review_events(conn)[0]["action"] == "same_as"
    conn.close()


def test_concurrent_same_pair_one_success_one_conflict(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def worker(index: int):
        barrier.wait()
        try:
            results[index] = (
                "ok",
                review_server.decide_person_linkage(
                    "pair:1:2",
                    review_server.PersonLinkageDecision(
                        reviewer="FT",
                        action="same_as",
                        person_ids=[1, 2],
                        expected_status="open",
                        expected_event_id=None,
                    ),
                ),
            )
        except HTTPException as exc:
            results[index] = ("err", exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    kinds = [item[0] for item in results]
    assert kinds.count("ok") == 1
    assert kinds.count("err") == 1
    err = next(item[1] for item in results if item[0] == "err")
    assert err.status_code == 409


def test_bulk_rule_ack_does_not_close_the_case(tmp_path, monkeypatch):
    main, cache, corr = _wire(tmp_path, monkeypatch)
    result = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="bulk_rule_ack",
            person_ids=[1, 2],
            expected_status="open",
            expected_event_id=None,
        ),
    )
    assert result["ok"] is True
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["status"] == "open"
    conn = corrections_db.connect(corr)
    events = corrections_db.person_review_events(conn)
    assert events[0]["action"] == "bulk_rule_ack"
    assert events[0]["case_id"].startswith("rule:")
    assert corrections_db.active_reference_links(conn, "person") == []
    conn.close()


def test_same_person_override_of_rule_exclusion_requires_rationale(tmp_path, monkeypatch):
    main, cache, corr = _wire(tmp_path, monkeypatch)
    connection = sqlite3.connect(cache)
    connection.execute(
        "UPDATE person_pair_suggestion "
        "SET deterministic_tier='distinct_strong', precedence_verdict='distinct_strong' "
        "WHERE pair_key='1:2'"
    )
    connection.commit()
    connection.close()
    action = review_server.PersonLinkageDecision(
        reviewer="FT",
        action="same_as",
        person_ids=[1, 2],
        expected_status="open",
        expected_event_id=None,
    )
    with pytest.raises(review_server.HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__("pair:1:2", action)
    assert exc.value.status_code == 400
    assert "override the rule" in exc.value.detail


def test_pair_detail_surfaces_separate_split_flag(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    review_server.decide_person_linkage.__wrapped__(
        "split:1",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="flag_split",
            person_ids=[1],
            rationale="Appearances may belong to more than one generation.",
            expected_status="open",
            expected_event_id=None,
        ),
    )
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["status"] == "open"
    assert detail["split_flagged_person_ids"] == [1]


def test_identity_status_hides_open_labeling_pairs(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    visible = review_server.person_identity_status(1)
    assert visible["open_cases"] == ["pair:1:2"]
    assert visible["open_count"] == 1
    assert visible["split_flags"] == []
    assert "match_probability" not in visible

    _labeling_packet(tmp_path, monkeypatch)
    hidden = review_server.person_identity_status(1)
    assert hidden["open_cases"] == []
    assert hidden["open_count"] == 0

    page = review_server.db_search("person", q="Luca", limit=60, offset=0)
    hints = {item["id"]: item["identity_hint"] for item in page["results"]}
    assert hints["1"]["open_count"] == 0
    assert hints["1"]["split_flagged"] is False

    monkeypatch.setattr(review_server, "PERSON_LABELING_PACKET_PATH", tmp_path / "gone.jsonl")
    restored = review_server.person_identity_status(1)
    assert restored["open_cases"] == ["pair:1:2"]
    assert restored["open_count"] == 1


def test_identity_status_keeps_unrelated_open_cases_during_the_round(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_pairs=[_pair_suggestion(1, 3)])
    _labeling_packet(tmp_path, monkeypatch)
    status = review_server.person_identity_status(1)
    assert status["open_cases"] == ["pair:1:3"]
    assert "pair:1:2" not in status["open_cases"]
    other = review_server.person_identity_status(2)
    assert other["open_cases"] == []
    assert other["open_count"] == 0


def test_identity_status_hides_groups_that_contain_open_packet_edges(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, groups=[_group_projection()])
    before = review_server.person_identity_status(1)
    assert before["open_cases"] == ["group:g1"]
    assert "pair:1:2" not in before["open_cases"]

    _labeling_packet(tmp_path, monkeypatch)
    hidden = review_server.person_identity_status(1)
    assert hidden["open_cases"] == []
    assert hidden["open_count"] == 0

    monkeypatch.setattr(review_server, "PERSON_LABELING_PACKET_PATH", tmp_path / "gone.jsonl")
    restored = review_server.person_identity_status(1)
    assert restored["open_cases"] == ["group:g1"]


def test_identity_status_surfaces_split_flags(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    review_server.decide_person_linkage.__wrapped__(
        "split:1",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="flag_split",
            person_ids=[1],
            rationale="Appearances may belong to more than one generation.",
            expected_status="open",
            expected_event_id=None,
        ),
    )
    status = review_server.person_identity_status(1)
    assert status["split_flags"] == [
        {
            "person_id": 1,
            "case_id": "split:1",
            "event_id": status["split_flags"][0]["event_id"],
            "created_by": "FT",
            "created_at": status["split_flags"][0]["created_at"],
            "rationale": "Appearances may belong to more than one generation.",
            "root_event_id": status["split_flags"][0]["event_id"],
            "changed_by": None,
            "changed_at": None,
            "change_note": None,
            "marks": None,
        }
    ]
    assert status["split_flags"][0]["created_at"]
    assert status["split_flags"][0]["event_id"]
    quiet = review_server.person_identity_status(2)
    assert quiet["split_flags"] == []

    page = review_server.db_search("person", q="Luca", limit=60, offset=0)
    hints = {item["id"]: item["identity_hint"] for item in page["results"]}
    assert hints["1"]["split_flagged"] is True
    assert hints["1"]["case_id"] == "pair:1:2"
    assert hints["2"]["split_flagged"] is False

    persons = review_server.contract_persons("10")
    picker = {row["person_id"]: row["identity_hint"] for row in persons["persons"]}
    assert picker["1"]["split_flagged"] is True
    assert picker["1"]["open_count"] == 1


def test_identity_status_reopened_non_packet_cases_return_to_the_hints(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    saved = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="defer",
            person_ids=[1, 2],
            rationale="Source review needed.",
            expected_status="open",
            expected_event_id=None,
        ),
    )
    assert review_server.person_identity_status(1)["open_count"] == 0
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="reopen",
            person_ids=[1, 2],
            expected_status="defer",
            expected_event_id=saved["event_id"],
        ),
    )
    restored = review_server.person_identity_status(1)
    assert restored["open_cases"] == ["pair:1:2"]
    assert restored["open_count"] == 1


def test_identity_status_reopened_packet_pairs_stay_suppressed(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _labeling_packet(tmp_path, monkeypatch)
    saved = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="defer",
            person_ids=[1, 2],
            rationale="Source review needed.",
            expected_status="open",
            expected_event_id=None,
            review_mode="blind_labeling",
        ),
    )
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="reopen",
            person_ids=[1, 2],
            expected_status="defer",
            expected_event_id=saved["event_id"],
        ),
    )
    assert review_server.person_identity_status(1)["open_count"] == 0
    monkeypatch.setattr(review_server, "PERSON_LABELING_PACKET_PATH", tmp_path / "gone.jsonl")
    restored = review_server.person_identity_status(1)
    assert restored["open_cases"] == ["pair:1:2"]


def test_identity_status_split_flag_from_pair_case_clears_on_reopen(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    saved = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="flag_split",
            person_ids=[1],
            rationale="Appearances may belong to more than one generation.",
            expected_status="open",
            expected_event_id=None,
        ),
    )
    status = review_server.person_identity_status(1)
    assert status["split_flags"][0]["case_id"] == "pair:1:2"
    assert status["split_flags"][0]["rationale"] == (
        "Appearances may belong to more than one generation."
    )
    assert status["open_count"] == 0
    assert review_server.person_identity_status(2)["split_flags"] == []
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["split_flagged_person_ids"] == [1]

    search = review_server.person_search(q="Luca", limit=12)
    search_hints = {row["person_id"]: row["identity_hint"] for row in search["results"]}
    assert search_hints["1"]["split_flagged"] is True
    assert search_hints["2"]["split_flagged"] is False
    wall = review_server.same_surname(last_name="Franceschi")
    wall_hints = {row["person_id"]: row["identity_hint"] for row in wall["results"]}
    assert wall_hints["1"]["split_flagged"] is True

    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="reopen",
            person_ids=[1, 2],
            expected_status="flag_split",
            expected_event_id=saved["event_id"],
        ),
    )
    cleared = review_server.person_identity_status(1)
    assert cleared["split_flags"] == []
    assert cleared["open_cases"] == ["pair:1:2"]
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["split_flagged_person_ids"] == []


def test_a_standing_split_flag_blocks_decisions_and_survives_the_attempts(
    tmp_path, monkeypatch
):
    """A pair-cased flag_split makes the case undecidable until reopened.

    Pins the semantics the scan-based flag relies on: while the flag is the
    case's latest event, no substantive decision — even from an honest client
    sending the correct expected_status and event id — may land on that case,
    so nothing can ever become "latest" over the flag except an explicit
    reopen. Without this pin, a refactor could quietly change "blocked" into
    "clears the flag" and the whole suite would stay green.
    """
    _wire(tmp_path, monkeypatch)
    saved = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="flag_split",
            person_ids=[1],
            rationale="Appearances may belong to more than one generation.",
            expected_status="open",
            expected_event_id=None,
        ),
    )
    for action in ("same_as", "distinct", "defer"):
        with pytest.raises(HTTPException) as exc:
            review_server.decide_person_linkage.__wrapped__(
                "pair:1:2",
                review_server.PersonLinkageDecision(
                    reviewer="FT",
                    action=action,
                    person_ids=[1, 2],
                    rationale="Trying to decide over a standing split flag.",
                    expected_status="flag_split",
                    expected_event_id=saved["event_id"],
                ),
            )
        assert exc.value.status_code == 409
        assert "reopen" in exc.value.detail
        # The refused attempt must leave no trace: the flag still stands and
        # the flag event is still the latest on the case.
        status = review_server.person_identity_status(1)
        assert status["split_flags"][0]["case_id"] == "pair:1:2"
        assert (
            review_server.person_linkage_case("pair:1:2")["latest_event_id"]
            == saved["event_id"]
        )





def _stranded_evidence(
    *,
    left_count=0,
    right_count=3,
    given=True,
    father=True,
    surname=True,
    grandfather=False,
):
    """Full evidence_json override for stranded-facet and hint-gate tests.

    The server reads appearance_counts and the name_review exact flags via
    json_extract, so the override must be a complete evidence blob — a partial
    dict would silently drop the fields the default fixture provides.
    """

    def flag(exact):
        return {"both_recorded": True, "exact": bool(exact), "edit_distance": 0 if exact else 2}

    return {
        "names": {
            "left": {"first_name": "Luca", "father_mother": "Carlo", "last_name": "Franceschi"},
            "right": {"first_name": "Luca", "father_mother": "Carlo", "last_name": "Franceschi"},
        },
        "name_review": {
            "given_name": flag(given),
            "surname": flag(surname),
            "father": flag(father),
            "grandfather": flag(grandfather),
        },
        "career": {"combined_span_years": 12},
        "roles": {"left": "lp", "right": "lp"},
        "appearance_counts": {"left": left_count, "right": right_count},
        "shared": {"firms": []},
        "context_for_review": {},
    }


def _stranded_pairs():
    """One pair per stranded population: XOR/rank-0, XOR/rank-2, both-zero, model-only."""
    return [
        # Rank 0 (given+father+surname exact) with the LOWEST review score:
        # only class-first ordering can put it above the rank-2 pair.
        _pair_suggestion(
            4, 5, review_score=0.10, match_probability=0.28, score_band="possible",
            evidence_json=_stranded_evidence(),
        ),
        # Rank 2: father+grandfather exact, given and surname drifted.
        _pair_suggestion(
            6, 7, review_score=0.90, match_probability=0.30, score_band="possible",
            evidence_json=_stranded_evidence(
                given=False, surname=False, grandfather=True
            ),
        ),
        # Both sides zero: stranded on both ends, review tier.
        _pair_suggestion(
            8, 9, review_score=0.50, match_probability=0.30, score_band="possible",
            evidence_json=_stranded_evidence(right_count=0),
        ),
        # Model-only XOR pair (no deterministic tier at all).
        _pair_suggestion(
            10, 11, review_score=0.40, match_probability=0.30, score_band="possible",
            deterministic_tier=None, precedence_verdict="review", source="splink",
            evidence_json=_stranded_evidence(),
        ),
    ]


def test_stranded_facet_selects_the_review_tier_xor_population(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_pairs=_stranded_pairs())
    page = review_server.person_linkage_cases(
        lane="other_matches", stranded=1, offset=0, limit=100
    )
    # Only the review-tier XOR pairs: the fully-appearing 1:2 pair, the
    # both-zero pair and the tier-NULL pair are all outside the default facet —
    # and the rank-0 pair outranks the rank-2 pair despite a far lower score.
    assert [case["case_id"] for case in page["cases"]] == ["pair:4:5", "pair:6:7"]
    assert page["total"] == 2


def test_stranded_scope_all_admits_both_zero_and_model_only_pairs(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_pairs=_stranded_pairs())
    page = review_server.person_linkage_cases(
        lane="other_matches", stranded=1, stranded_scope="all", offset=0, limit=100
    )
    assert sorted(case["case_id"] for case in page["cases"]) == [
        "pair:10:11",
        "pair:4:5",
        "pair:6:7",
        "pair:8:9",
    ]
    assert page["total"] == 4


def test_stranded_facet_composes_with_open_round_suppression(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_pairs=_stranded_pairs())
    _labeling_packet(tmp_path, monkeypatch, pair_key="4:5")
    page = review_server.person_linkage_cases(
        lane="other_matches", stranded=1, offset=0, limit=100
    )
    # The packet pair is frozen in the blind round, so the facet must not leak it.
    assert [case["case_id"] for case in page["cases"]] == ["pair:6:7"]


def test_stranded_exact_chain_pair_hints_on_the_person_record(tmp_path, monkeypatch):
    _wire(
        tmp_path,
        monkeypatch,
        extra_pairs=[
            # p=0.28-style stranded pair, given/father/surname all exact: hints.
            _pair_suggestion(
                4, 5, match_probability=0.28, score_band="possible",
                evidence_json=_stranded_evidence(),
            ),
            # Same shape but the given name drifted: no hint.
            _pair_suggestion(
                6, 7, match_probability=0.28, score_band="possible",
                evidence_json=_stranded_evidence(given=False),
            ),
            # First+father sludge (surname does not match): no hint.
            _pair_suggestion(
                8, 9, match_probability=0.28, score_band="possible",
                evidence_json=_stranded_evidence(surname=False),
            ),
            # Clear tier stays out of the pair arm even with a perfect chain.
            _pair_suggestion(
                10, 11, match_probability=0.99,
                deterministic_tier="batch_ghost", precedence_verdict="batch_ghost",
                evidence_json=_stranded_evidence(),
            ),
        ],
    )
    hinted = review_server.person_identity_status(4)
    assert hinted["open_cases"] == ["pair:4:5"]
    assert hinted["open_count"] == 1
    assert review_server.person_identity_status(6)["open_cases"] == []
    assert review_server.person_identity_status(8)["open_cases"] == []
    assert review_server.person_identity_status(10)["open_cases"] == []


def test_clear_tier_group_edge_still_hints_without_a_packet(tmp_path, monkeypatch):
    """A3 pin: the GROUP arm must keep hinting for clear-tier duplicate groups.

    The hint-gate extension touches only the pair arm; if this pin breaks, a
    gate change leaked into the group arm and must be reported, not patched.
    """
    _wire(
        tmp_path,
        monkeypatch,
        extra_pairs=[
            _pair_suggestion(
                1, 3, deterministic_tier="batch_ghost", precedence_verdict="batch_ghost"
            )
        ],
        groups=[_group_projection(person_ids=(1, 3), edges=("1:3",), group_key="g2")],
    )
    status = review_server.person_identity_status(3)
    assert "group:g2" in status["open_cases"]
    assert status["open_count"] == 1
    page = review_server.db_search("person", q="Baccio", limit=60, offset=0)
    hints = {item["id"]: item["identity_hint"] for item in page["results"]}
    assert hints["3"]["open_count"] == 1
    assert hints["3"]["case_id"] == "group:g2"


def test_person_browse_meta_counts_live_contracts(tmp_path, monkeypatch):
    _wire(
        tmp_path,
        monkeypatch,
        extra_sql="""
        INSERT INTO person VALUES (4,'Piero',NULL,NULL,'Strozzi',NULL,0,0);
        -- A deleted appearance must not rescue a stranded entry from 'no contracts'.
        INSERT INTO investor VALUES (105,4,10,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,1);
        -- A second live investor row on the SAME contract must not double-count.
        INSERT INTO investor VALUES (106,3,10,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,0);
        """,
    )
    page = review_server.db_search("person", q="", limit=60, offset=0)
    metas = {item["id"]: item["meta"] for item in page["results"]}
    assert metas["4"] == "Entry #4 · no contracts"
    assert metas["1"] == "Entry #1 · 1 contract"
    assert metas["3"] == "Entry #3 · 2 contracts"


def test_a_lane_frozen_by_the_round_reports_its_reserved_count(tmp_path, monkeypatch):
    """Badge and listing must not silently disagree during the blind round.

    The likely-duplicates lane is the worst case: every deterministic pair is
    in the packet, so the whole lane empties while its badge still counts the
    groups. The listing therefore reports how many cases the round froze, and
    the badge keeps the true lane size — the empty state explains itself
    instead of claiming "no open cases".
    """
    _wire(
        tmp_path,
        monkeypatch,
        extra_pairs=[
            _pair_suggestion(
                1, 3, deterministic_tier="batch_ghost", precedence_verdict="batch_ghost"
            )
        ],
        groups=[_group_projection(person_ids=(1, 3), edges=("1:3",), group_key="g2")],
    )
    _labeling_packet(tmp_path, monkeypatch, pair_key="1:3")
    page = review_server.person_linkage_cases(
        lane="likely_duplicates", status="All", offset=0, limit=100
    )
    assert page["total"] == 0
    assert page["reserved_for_labeling"] == 1
    # The badge keeps the true lane size: the group exists, it is merely frozen.
    assert review_server.person_linkage_summary()["lanes"]["likely_duplicates"] == 1
    # Deciding the packet pair releases the group back to its lane.
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:3",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="defer",
            person_ids=[1, 3],
            rationale="Blind judgment recorded.",
            expected_status="open",
            expected_event_id=None,
            review_mode="blind_labeling",
        ),
    )
    page = review_server.person_linkage_cases(
        lane="likely_duplicates", status="All", offset=0, limit=100
    )
    assert page["reserved_for_labeling"] == 0
    assert [case["case_id"] for case in page["cases"]] == ["group:g2"]


def test_sibling_pairs_freeze_with_the_round_and_thaw_per_person(tmp_path, monkeypatch):
    """Co-minted ghost siblings are frozen while their family is in the round.

    The Corradini shape: star pairs against the row with the appearance go
    into the packet; the ghost-to-ghost sibling pair does not, yet it shows
    the same family with a visible score. A pair whose BOTH persons belong
    to open packet cases must vanish from lanes and hints, blind its detail,
    and refuse decisions — then thaw as the packet cases are decided.
    Fixture: packet = pairs 1:3 and 2:3 (the star); sibling = 1:2.
    """
    _wire(
        tmp_path,
        monkeypatch,
        extra_pairs=[_pair_suggestion(1, 3), _pair_suggestion(2, 3)],
    )
    _labeling_packet(tmp_path, monkeypatch, pair_keys=["1:3", "2:3"])

    # Lane: the sibling is hidden and counted as reserved alongside the stars.
    page = review_server.person_linkage_cases(
        lane="other_matches", status="All", offset=0, limit=100
    )
    assert page["total"] == 0
    assert page["reserved_for_labeling"] == 3

    # Hints: person 1's only cases involve round members, so nothing shows.
    assert review_server.person_identity_status(1)["open_cases"] == []

    # Detail: the sibling gets the blind whitelist, same as a packet case.
    assert sorted(review_server.person_linkage_case("pair:1:2")) == BLIND_DETAIL_KEYS

    # Decisions: refused in BOTH modes with the sibling message.
    for mode in ("standard", "blind_labeling"):
        with pytest.raises(HTTPException) as exc:
            review_server.decide_person_linkage.__wrapped__(
                "pair:1:2",
                review_server.PersonLinkageDecision(
                    reviewer="FT",
                    action="same_as",
                    person_ids=[1, 2],
                    rationale="Trying to settle the family mid-round.",
                    expected_status="open",
                    expected_event_id=None,
                    review_mode=mode,
                ),
            )
        assert exc.value.status_code == 409
        assert "Both entries in this case" in exc.value.detail

    # Decide one star: person 1 leaves the round, so the sibling thaws —
    # 2 is still in an open packet case, but 1 no longer is.
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:3",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="distinct",
            person_ids=[1, 3],
            rationale="Blind judgment recorded.",
            expected_status="open",
            expected_event_id=None,
            review_mode="blind_labeling",
        ),
    )
    detail = review_server.person_linkage_case("pair:1:2")
    assert "match_probability" in detail  # full payload again
    page = review_server.person_linkage_cases(
        lane="other_matches", status="All", offset=0, limit=100
    )
    assert "pair:1:2" in [case["case_id"] for case in page["cases"]]


def test_cross_family_pairs_between_packet_persons_also_freeze(tmp_path, monkeypatch):
    """The broad rule is deliberate: both-persons-in-packet freezes the pair
    even when the two belong to DIFFERENT packet cases (different families).

    Conservative for blinding — a labeler must not see the model's view of
    any packet person's twin-ness mid-round — at the cost of hiding some
    unrelated work until the round ends. If that cost ever bites, the
    documented narrowing is same-packet-component; this test pins the
    current choice so a change to it is a decision, not an accident.
    Fixture: disjoint packet cases 1:2 and 3:4; cross pair 1:3 in the cache.
    """
    _wire(tmp_path, monkeypatch, extra_pairs=[_pair_suggestion(1, 3)])
    _labeling_packet(tmp_path, monkeypatch, pair_keys=["1:2", "3:4"])
    page = review_server.person_linkage_cases(
        lane="other_matches", status="All", offset=0, limit=100
    )
    assert page["total"] == 0  # both 1:2 (packet) and 1:3 (cross) frozen
    assert sorted(review_server.person_linkage_case("pair:1:3")) == BLIND_DETAIL_KEYS
    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__(
            "pair:1:3",
            review_server.PersonLinkageDecision(
                reviewer="FT",
                action="distinct",
                person_ids=[1, 3],
                rationale="Cross-family, both in the round.",
                expected_status="open",
                expected_event_id=None,
            ),
        )
    assert exc.value.status_code == 409
    assert "Both entries in this case" in exc.value.detail


def test_pairs_between_non_packet_persons_hint_through_the_round(tmp_path, monkeypatch):
    """A pair with NO packet person is other work: it stays fully visible.

    The Agiati shape — while the round runs on other families, person 1's
    open case with person 2 must keep hinting on the Database record and
    stay decidable, or the round would silence the whole corpus.
    Fixture: the packet holds only 3:4; the default 1:2 pair is untouched.
    """
    _wire(tmp_path, monkeypatch, extra_pairs=[_pair_suggestion(3, 4)])
    _labeling_packet(tmp_path, monkeypatch, pair_keys=["3:4"])
    status = review_server.person_identity_status(1)
    assert status["open_cases"] == ["pair:1:2"]
    detail = review_server.person_linkage_case("pair:1:2")
    assert "match_probability" in detail  # full payload, not blind


# --- The flag's home, note, and way back (split-flag MVP, 2026-10-25) -------


def _flag_entry(person_id=1, rationale="Contract 11 of 1635 reads like a son; contract 10 is the father.", reviewer="FT"):
    """Flag an entry the way the People page does: read the entry's own split
    case first, then post against its current status and latest event."""
    case = review_server.person_linkage_case(f"split:{person_id}")
    return review_server.decide_person_linkage.__wrapped__(
        f"split:{person_id}",
        review_server.PersonLinkageDecision(
            reviewer=reviewer,
            action="flag_split",
            person_ids=[person_id],
            rationale=rationale,
            expected_status=case["status"],
            expected_event_id=case["latest_event_id"],
        ),
    )


def _lift_flag(flag, reviewer="WH"):
    return review_server.decide_person_linkage.__wrapped__(
        flag["case_id"],
        review_server.PersonLinkageDecision(
            reviewer=reviewer,
            action="reopen",
            person_ids=[flag["person_id"]],
            rationale="",
            expected_status="flag_split",
            expected_event_id=flag["event_id"],
        ),
    )


def test_flag_split_requires_a_note(tmp_path, monkeypatch):
    """A flag changes nothing by itself: the note is its whole content."""
    _wire(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _flag_entry(rationale="   ")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Say which contracts seem to belong to someone else, and why."
    assert review_server.person_identity_status(1)["split_flags"] == []


def test_reflagging_names_the_standing_flag_instead_of_a_reload_error(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _flag_entry()
    with pytest.raises(HTTPException) as exc:
        _flag_entry(reviewer="WH", rationale="Second opinion.")
    assert exc.value.status_code == 409
    assert exc.value.detail.startswith("Already flagged by FT on ")
    assert exc.value.detail.endswith("Open the entry to read or lift the flag.")
    assert "Reload" not in exc.value.detail
    # Only the first flag stands.
    assert [f["created_by"] for f in review_server.person_identity_status(1)["split_flags"]] == ["FT"]


def test_flagged_facet_lists_any_span_flags_with_who_when_note(tmp_path, monkeypatch):
    """Person 1 spans no years, so the split rule never lists it; the Flagged
    facet lists it anyway, with the note, and drops it once the flag is lifted."""
    _wire(tmp_path, monkeypatch)
    rule = review_server.person_linkage_cases(lane="possible_splits", offset=0, limit=100)
    assert rule["total"] == 0
    empty = review_server.person_linkage_cases(
        lane="possible_splits", facet="flagged", offset=0, limit=100
    )
    assert empty["total"] == 0 and empty["cases"] == [] and empty["facet"] == "flagged"

    saved = _flag_entry()
    page = review_server.person_linkage_cases(
        lane="possible_splits", facet="flagged", offset=0, limit=100
    )
    assert page["total"] == 1
    [row] = page["cases"]
    assert row["case_id"] == "split:1"
    assert row["kind"] == "split"
    assert row["person_ids"] == [1]
    assert row["status"] == "flag_split"
    assert row["decided_by"] == "FT"
    assert row["decided_at"]
    assert row["note"] == "Contract 11 of 1635 reads like a son; contract 10 is the father."
    assert row["flag"]["event_id"] == saved["event_id"]
    assert row["flag"]["case_id"] == "split:1"
    # The rule's own list is untouched by a flag.
    assert review_server.person_linkage_cases(lane="possible_splits", offset=0, limit=100)["total"] == 0
    # Name search inside the facet.
    assert review_server.person_linkage_cases(
        lane="possible_splits", facet="flagged", q="Ulivi", offset=0, limit=100
    )["total"] == 0
    assert review_server.person_linkage_cases(
        lane="possible_splits", facet="flagged", q="Franceschi", offset=0, limit=100
    )["total"] == 1

    _lift_flag(row["flag"])
    assert review_server.person_linkage_cases(
        lane="possible_splits", facet="flagged", offset=0, limit=100
    )["total"] == 0


def test_flags_show_under_decided_in_search_and_in_the_summary(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    before = review_server.person_linkage_summary()
    assert before["flagged_entries"] == 0
    saved = _flag_entry()

    decided = review_server.person_linkage_cases(lane="decided", offset=0, limit=100)
    rows = {row["case_id"]: row for row in decided["cases"]}
    assert "split:1" in rows
    assert rows["split:1"]["status"] == "flag_split"
    assert rows["split:1"]["decided_by"] == "FT"
    assert rows["split:1"]["note"].startswith("Contract 11 of 1635")

    found = review_server.person_linkage_cases(lane="all", q="Luca", offset=0, limit=100)
    ids = [row["case_id"] for row in found["cases"]]
    assert "split:1" in ids and "pair:1:2" in ids
    flagged = next(row for row in found["cases"] if row["case_id"] == "split:1")
    assert flagged["decided_by"] == "FT"

    after = review_server.person_linkage_summary()
    assert after["flagged_entries"] == 1
    assert after["lanes"]["decided"] == before["lanes"]["decided"] + 1
    assert after["lanes"]["possible_splits"] == before["lanes"]["possible_splits"]

    # The pair case carries the flag with its event id, so the page can lift it.
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["split_flags"] == [
        {
            "person_id": 1,
            "case_id": "split:1",
            "event_id": saved["event_id"],
            "created_by": "FT",
            "created_at": detail["split_flags"][0]["created_at"],
            "rationale": "Contract 11 of 1635 reads like a son; contract 10 is the father.",
            "root_event_id": saved["event_id"],
            "changed_by": None,
            "changed_at": None,
            "change_note": None,
            "marks": None,
        }
    ]
    assert detail["split_flagged_person_ids"] == [1]

    _lift_flag(detail["split_flags"][0])
    assert review_server.person_linkage_summary()["flagged_entries"] == 0
    assert review_server.person_linkage_case("pair:1:2")["split_flags"] == []
    # A lifted flag leaves Decided (its case is open again) but its note is
    # still findable by search, through the case history.
    decided = review_server.person_linkage_cases(lane="decided", offset=0, limit=100)
    assert "split:1" not in {row["case_id"] for row in decided["cases"]}
    found = review_server.person_linkage_cases(lane="all", q="Luca", offset=0, limit=100)
    lifted = next(row for row in found["cases"] if row["case_id"] == "split:1")
    assert lifted["status"] == "open"
    assert "decided_by" not in lifted
    history = review_server.person_linkage_case("split:1")["history"]
    assert [(event["action"], event["created_by"]) for event in history] == [
        ("reopen", "WH"),
        ("flag_split", "FT"),
    ]


def test_needs_review_lists_standing_split_flags(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    connection = review_server.open_db()
    try:
        assert review_server._person_split_flag_items(connection) == []
        saved = _flag_entry()
        [item] = review_server._person_split_flag_items(connection)
        assert item["key"] == f"person:1:person_split_flagged:{saved['event_id']}"
        assert item["group"] == "person_split_flagged"
        assert item["table"] == "person" and item["pk"] == "1"
        assert item["title"].startswith("Luca Franceschi — FT, ")
        assert item["title"].endswith(": Contract 11 of 1635 reads like a son; contract 10 is the father.")
        assert item["fix"] == {"kind": "identity"}
        assert item["severity"] == "medium"

        _lift_flag(review_server.person_identity_status(1)["split_flags"][0])
        assert review_server._person_split_flag_items(connection) == []

        # Re-flagging after a lift is a new item (new event id), so an old
        # dismissal of the first one cannot hide it.
        again = _flag_entry(rationale="Still looks like two men.")
        [item] = review_server._person_split_flag_items(connection)
        assert item["key"] == f"person:1:person_split_flagged:{again['event_id']}"
        assert again["event_id"] != saved["event_id"]
    finally:
        connection.close()


# --- Undo is one event: an undone link is open again (2026-10-26) ----------


def _undo(event_id, reviewer="FT"):
    return review_server.revoke_person_decision.__wrapped__(
        event_id, review_server.ReviewerOnly(reviewer=reviewer, reason="")
    )


def test_undoing_a_link_returns_the_case_to_its_lane(tmp_path, monkeypatch):
    """One click, one note. The revoke clears the links, so the case reads as
    open: back in its lane, gone from Decided, hinted on the person again."""
    _wire(tmp_path, monkeypatch)
    before = review_server.person_linkage_summary()["lanes"]
    assert [
        row["case_id"]
        for row in review_server.person_linkage_cases(
            lane="other_matches", q="Luca", offset=0, limit=100
        )["cases"]
    ] == ["pair:1:2"]
    linked = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="same_as",
            person_ids=[1, 2],
            rationale="One man.",
            expected_status="open",
            expected_event_id=None,
        ),
    )
    assert review_server.person_linkage_case("pair:1:2")["status"] == "same_as"
    mid = review_server.person_linkage_summary()["lanes"]
    assert mid["decided"] == before["decided"] + 1
    assert mid["other_matches"] == before["other_matches"] - 1

    undone = _undo(linked["event_id"])
    assert set(undone["revoked_link_ids"]) == set(linked["link_ids"])
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["status"] == "open"
    assert detail["latest_event_id"] == undone["event_id"]
    assert [event["action"] for event in detail["history"]] == ["revoke", "same_as"]
    assert [
        row["case_id"]
        for row in review_server.person_linkage_cases(
            lane="other_matches", q="Luca", offset=0, limit=100
        )["cases"]
    ] == ["pair:1:2"]
    assert review_server.person_linkage_cases(lane="decided", offset=0, limit=100)["total"] == 0
    after = review_server.person_linkage_summary()["lanes"]
    assert after["decided"] == before["decided"]
    assert after["other_matches"] == before["other_matches"]
    assert review_server.person_identity_status(1)["open_cases"] == ["pair:1:2"]
    # Nothing to reopen: the undo already did that.
    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__(
            "pair:1:2",
            review_server.PersonLinkageDecision(
                reviewer="FT",
                action="reopen",
                person_ids=[1, 2],
                expected_status="open",
                expected_event_id=undone["event_id"],
            ),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "Only a deferred or flagged case can be reopened."
    # A fresh decision lands against the undo note.
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="distinct",
            person_ids=[1, 2],
            rationale="Different fathers after all.",
            expected_status="open",
            expected_event_id=undone["event_id"],
        ),
    )
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["status"] == "distinct"
    assert [event["action"] for event in detail["history"]] == ["distinct", "revoke", "same_as"]


def test_undoing_a_blind_label_returns_the_case_to_the_round(tmp_path, monkeypatch):
    """A label undone is judged again, blind: the case re-enters the round the
    way a reopened one does, with the blind payload, the standard-mode 409 and
    the Database hints suppressed."""
    _wire(tmp_path, monkeypatch)
    _labeling_packet(tmp_path, monkeypatch)
    assert review_server.person_linkage_summary()["lanes"]["labeling_round"] == 1
    labelled = review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="same_as",
            person_ids=[1, 2],
            rationale="Blind call.",
            expected_status="open",
            expected_event_id=None,
            review_mode="blind_labeling",
        ),
    )
    assert review_server.person_linkage_summary()["lanes"]["labeling_round"] == 0
    assert "match_probability" in review_server.person_linkage_case("pair:1:2")

    undone = _undo(labelled["event_id"])
    assert review_server.person_linkage_summary()["lanes"]["labeling_round"] == 1
    detail = review_server.person_linkage_case("pair:1:2")
    assert detail["status"] == "open"
    assert "match_probability" not in detail
    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__(
            "pair:1:2",
            review_server.PersonLinkageDecision(
                reviewer="FT",
                action="distinct",
                person_ids=[1, 2],
                rationale="Standard mode.",
                expected_status="open",
                expected_event_id=undone["event_id"],
            ),
        )
    assert exc.value.status_code == 409
    assert "waiting under Judge blind" in exc.value.detail
    assert review_server.person_identity_status(1)["open_cases"] == []


def test_a_group_with_a_frozen_pair_is_refused_by_link_and_by_decision(tmp_path, monkeypatch):
    """M2 (review_2026-10-23): a group link must not open the blind round.

    The lanes and the Database hints already hide a group whose pairs include a
    frozen pair; the case URL, the old group URL and the decision endpoint must
    refuse it too. A two-entry group IS its packet pair, so without this
    /people/group:g:A:B showed the score that /people/pair:A:B hides.
    Fixture: group g1 = entries 1 and 2 joined by pair 1:2; the packet holds 1:2.
    """
    _, _, corr = _wire(tmp_path, monkeypatch, groups=[_group_projection()])
    # Without a packet the group is ordinary work, served in full.
    assert "match_probability" in review_server.person_linkage_case("group:g1")

    _labeling_packet(tmp_path, monkeypatch)
    for open_group in (
        lambda: review_server.person_linkage_case("group:g1"),
        lambda: review_server.person_linkage_group("g1"),
    ):
        with pytest.raises(HTTPException) as exc:
            open_group()
        assert exc.value.status_code == 409
        assert exc.value.detail == review_server._PERSON_GROUP_IN_ROUND_DETAIL

    # Deciding the group is refused in both modes: a same_as would link 1 and
    # 2 and settle the packet pair without a blind label.
    for mode in ("standard", "blind_labeling"):
        with pytest.raises(HTTPException) as exc:
            review_server.decide_person_linkage.__wrapped__(
                "group:g1",
                review_server.PersonLinkageDecision(
                    reviewer="FT",
                    action="same_as",
                    person_ids=[1, 2],
                    rationale="Settling the group mid-round.",
                    expected_status="open",
                    expected_event_id=None,
                    review_mode=mode,
                ),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == review_server._PERSON_GROUP_IN_ROUND_DETAIL
    clog = corrections_db.connect(corr)
    assert corrections_db.person_review_events(clog) == []
    assert corrections_db.active_reference_links(clog, "person") == []
    clog.close()

    # The packet pair itself is still judged blind; deciding it frees the group.
    assert sorted(review_server.person_linkage_case("pair:1:2")) == BLIND_DETAIL_KEYS
    review_server.decide_person_linkage.__wrapped__(
        "pair:1:2",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="defer",
            person_ids=[1, 2],
            rationale="Blind judgment recorded.",
            expected_status="open",
            expected_event_id=None,
            review_mode="blind_labeling",
        ),
    )
    assert "match_probability" in review_server.person_linkage_case("group:g1")


def test_the_group_refusal_follows_frozen_pairs_not_shared_people(tmp_path, monkeypatch):
    """Same rule as the lanes and hints: a group closes when ANY of its pairs is
    frozen, and a group that only shares a person with a packet case is other
    work. Fixture: packet 1:2; g2 = {1, 3} via 1:3 (entry 3 is outside the
    round); g3 = {1, 2, 3} via 1:2 (frozen) and 1:3 (not)."""
    _wire(
        tmp_path,
        monkeypatch,
        extra_pairs=[_pair_suggestion(1, 3)],
        groups=[
            _group_projection(person_ids=(1, 3), edges=("1:3",), group_key="g2"),
            _group_projection(person_ids=(1, 2, 3), edges=("1:2", "1:3"), group_key="g3"),
        ],
    )
    _labeling_packet(tmp_path, monkeypatch)
    assert "match_probability" in review_server.person_linkage_case("group:g2")
    with pytest.raises(HTTPException) as exc:
        review_server.person_linkage_case("group:g3")
    assert exc.value.status_code == 409


def test_a_group_decided_before_the_round_stays_open_and_lists_without_scores(tmp_path, monkeypatch):
    """A group linked before the round began is not locked by the refusal: its
    decision can still be read and undone (none of its pairs can take a blind
    label any more). In the Decided list, a decision the round still holds is
    shown with names and numbers only, like the round's own rows."""
    _wire(tmp_path, monkeypatch, groups=[_group_projection()])
    review_server.decide_person_linkage.__wrapped__(
        "group:g1",
        review_server.PersonLinkageDecision(
            reviewer="FT",
            action="same_as",
            person_ids=[1, 2],
            rationale="The same man, as the act says.",
            expected_status="open",
            expected_event_id=None,
        ),
    )
    _labeling_packet(tmp_path, monkeypatch)
    case = review_server.person_linkage_case("group:g1")
    assert case["status"] == "same_as"
    decided = review_server.person_linkage_cases(lane="decided", status="All", offset=0, limit=100)
    row = next(item for item in decided["cases"] if item["case_id"] == "group:g1")
    for key in ("match_probability", "review_score", "review_rank", "priority_band", "reasons", "score_band"):
        assert key not in row
    assert row["names"] and row["decided_by"] == "FT"


# --- Prerequisite A: the Word summaries of a split case's own acts (2026-10-27)


def _candidate(db_row_id, entry_id, key, date, strength, folio=("12r", "12v"), register="Mercanzia 10832"):
    return {
        "db_row_id": db_row_id,
        "source_entry_id": entry_id,
        "source_entry_key": key,
        "register_id": register,
        "entry_label_raw": "Nuova",
        "entry_registration_date_raw": date,
        "entry_folio_start": folio[0],
        "entry_folio_end": folio[1],
        "relationship_type": "narrative",
        "narrative_similarity_ratio": strength,
        "text_containment_ratio": 0.0,
    }


def _link_file(tmp_path, monkeypatch, rows):
    import json

    path = tmp_path / "link_candidates.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(review_server, "LINK_CANDIDATES_PATH", path)
    return path


def test_split_case_lists_its_own_word_summaries_per_act_in_date_order(tmp_path, monkeypatch):
    """Person 3 (Baccio Ulivi) appears on contract 10 (1623) and 11 (1635).
    The split case lists both acts oldest first, every candidate kept (no cap
    of 24), the reviewer's judgments shown, a confirmed-but-never-proposed
    link included; the pair case carries none of this."""
    _wire(tmp_path, monkeypatch)
    rows = [_candidate("contract:10", f"e10-{n:02d}", f"k10-{n:02d}", "5 gennaio 1623", 0.9 - n / 100)
            for n in range(26)]
    rows.append(_candidate("contract:11", "e11-a", "k11-a", "2 marzo 1635", 0.7))
    rows.append(_candidate("contract:99", "e99", "k99", "1 gennaio 1700", 0.99))  # another contract
    _link_file(tmp_path, monkeypatch, rows)
    monkeypatch.setattr(
        review_server,
        "decision_link_maps",
        lambda: ({"contract:11": {"k11-a", "k11-manual"}}, {"contract:10": {"k10-25"}}),
    )
    monkeypatch.setattr(
        review_server,
        "source_entries_by_key",
        lambda: {
            "k11-manual": {
                "source_entry_id": "e11-manual",
                "source_entry_key": "k11-manual",
                "register_id": "Mercanzia 10833",
                "event_label_raw": "Nuova",
                "registration_date_raw": "9 marzo 1635",
                "entry_folio_start": "40r",
                "comment_ids": ["c1"],
            }
        },
    )

    detail = review_server.person_linkage_case("split:3")
    acts = detail["entry_sources"]
    assert [act["contract_id"] for act in acts] == [10, 11]
    assert [act["registration_date"] for act in acts] == ["1623-01-01", "1635-01-01"]
    assert acts[0]["firm_name"] == "Pandolfini e compagni"
    assert acts[0]["document"] == "One Luca appears."
    first = acts[0]["word_sources"]
    assert len(first) == 26  # the cap of 24 does not apply to an entry's own acts
    assert [s["status"] for s in first].count("rejected") == 1
    assert first[-1]["source_entry_key"] == "k10-25" and first[-1]["status"] == "rejected"
    assert first[0]["status"] == "proposed" and first[0]["strength"] == 0.9
    assert first[0]["via_row_id"] == "contract:10" and first[0]["folio"] == "12r–12v"
    second = acts[1]["word_sources"]
    assert [(s["source_entry_id"], s["status"], s["relationship"]) for s in second] == [
        ("e11-a", "confirmed", "narrative"),
        ("e11-manual", "confirmed", "reviewer_confirmed"),
    ]
    assert second[1]["comment_count"] == 1 and second[1]["via"] == "Pandolfini e compagni"
    # Nothing from a contract this entry is not on, and nothing on a pair case.
    assert all(s["via_row_id"] != "contract:99" for act in acts for s in act["word_sources"])
    assert "entry_sources" not in review_server.person_linkage_case("pair:1:2")


def test_entry_word_sources_do_not_move_the_evidence_changed_check(tmp_path, monkeypatch):
    """A flag's snapshot must not include the summaries, and a link proposed
    or judged afterwards must not flip the standing flag to re-check."""
    _wire(tmp_path, monkeypatch)
    _link_file(tmp_path, monkeypatch, [_candidate("contract:10", "e10", "k10", "5 gennaio 1623", 0.8)])
    saved = _flag_entry(person_id=3, rationale="Contract 11 may be another Baccio.")
    conn = corrections_db.connect(tmp_path / "corrections.db")
    event = next(e for e in corrections_db.person_review_events(conn) if e["event_id"] == saved["event_id"])
    conn.close()
    assert "entry_sources" not in event["evidence_snapshot"]
    assert review_server.person_linkage_case("split:3")["needs_recheck"] is False

    _link_file(tmp_path, monkeypatch, [
        _candidate("contract:10", "e10", "k10", "5 gennaio 1623", 0.8),
        _candidate("contract:11", "e11", "k11", "2 marzo 1635", 0.6),
    ])
    monkeypatch.setattr(review_server, "decision_link_maps", lambda: ({"contract:10": {"k10"}}, {}))
    detail = review_server.person_linkage_case("split:3")
    assert [act["contract_id"] for act in detail["entry_sources"]] == [10, 11]
    assert detail["entry_sources"][0]["word_sources"][0]["status"] == "confirmed"
    assert detail["needs_recheck"] is False
    assert detail["status"] == "flag_split"


def test_entry_without_acts_has_no_word_sources(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_sql="INSERT INTO person VALUES (4,'Nessuno',NULL,NULL,'Atti',NULL,0,0);")
    detail = review_server.person_linkage_case("split:4")
    assert detail["entry_sources"] == []


# --- Marking which acts are whose: the server step (2026-10-27) -----------

_AUTO = object()

# Person 3 (Baccio Ulivi) with a second appearance on act 10: the Gherardi
# shape, two rows of one entry on one act.
_GHERARDI_SHAPE = """
INSERT INTO investor VALUES (105,3,10,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,0);
INSERT INTO investment VALUES (205,'lp',0);
INSERT INTO investor_group VALUES (105,205,0);
"""


def _mark(
    person_id,
    marks,
    reviewer="FT",
    rationale="The 1635 act reads like a son.",
    reason_code="",
    revises=None,
    other_entry_id=None,
    expected_status=_AUTO,
    expected_event_id=_AUTO,
):
    """Flag an entry with marks the way the page will: against the entry's
    own split case, with its current status and latest event."""
    case = review_server.person_linkage_case(f"split:{person_id}")
    seam = review_server.PersonSeam(
        assignments=[
            review_server.PersonSeamMark(investor_id=investor_id, side=side)
            for investor_id, side in marks.items()
        ],
        other_entry_id=other_entry_id,
        revises_event_id=revises,
    )
    return review_server.decide_person_linkage.__wrapped__(
        f"split:{person_id}",
        review_server.PersonLinkageDecision(
            reviewer=reviewer,
            action="flag_split",
            person_ids=[person_id],
            reason_code=reason_code,
            rationale=rationale,
            expected_status=case["status"] if expected_status is _AUTO else expected_status,
            expected_event_id=(
                case["latest_event_id"] if expected_event_id is _AUTO else expected_event_id
            ),
            seam=seam,
        ),
    )


def test_marks_must_cover_exactly_the_entrys_live_appearances(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)  # person 3: appearances 103 (act 10) and 104 (act 11)
    with pytest.raises(HTTPException) as exc:
        _mark(3, {103: "this"})
    assert exc.value.status_code == 400
    assert exc.value.detail == "Every appearance needs a mark; missing: #104."
    with pytest.raises(HTTPException) as exc:
        _mark(3, {103: "this", 104: "other", 101: "other"})
    assert exc.value.status_code == 400
    assert exc.value.detail == "Appearance #101 is not a live appearance of entry #3."
    with pytest.raises(HTTPException) as exc:
        _mark(3, {103: "this", 104: "elsewhere"})
    assert exc.value.status_code == 400
    assert exc.value.detail == "Unknown mark 'elsewhere' on appearance #104."
    twice = review_server.PersonSeam(
        assignments=[
            review_server.PersonSeamMark(investor_id=103, side="this"),
            review_server.PersonSeamMark(investor_id=103, side="other"),
            review_server.PersonSeamMark(investor_id=104, side="other"),
        ]
    )
    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__(
            "split:3",
            review_server.PersonLinkageDecision(
                reviewer="FT", action="flag_split", person_ids=[3], rationale="x",
                expected_status="open", expected_event_id=None, seam=twice,
            ),
        )
    assert exc.value.status_code == 400 and exc.value.detail == "An appearance is marked twice."
    one = review_server.PersonSeam(
        assignments=[review_server.PersonSeamMark(investor_id=101, side="this")]
    )
    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__(
            "pair:1:2",
            review_server.PersonLinkageDecision(
                reviewer="FT", action="flag_split", person_ids=[1], rationale="x",
                expected_status="open", expected_event_id=None, seam=one,
            ),
        )
    assert exc.value.status_code == 400 and exc.value.detail == "Mark the acts on the entry's own case."
    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__(
            "pair:1:2",
            review_server.PersonLinkageDecision(
                reviewer="FT", action="same_as", person_ids=[1, 2], rationale="x",
                expected_status="open", expected_event_id=None, seam=one,
            ),
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "Marks go with a flag, not with this decision."
    # Nothing was written by any of the refusals.
    assert review_server.person_identity_status(3)["split_flags"] == []
    assert review_server.person_linkage_case("split:3")["seam"] is None
    assert review_server.person_linkage_case("split:3")["status"] == "open"


def test_marks_are_saved_per_appearance_and_read_back(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_sql=_GHERARDI_SHAPE)
    saved = _mark(3, {103: "this", 105: "other", 104: "unsure"})
    view = review_server.person_linkage_case("split:3")["seam"]
    assert view["event_id"] == saved["event_id"] == view["root_event_id"]
    assert [(a["investor_id"], a["contract_id"], a["side"]) for a in view["assignments"]] == [
        (103, 10, "this"),
        (105, 10, "other"),
        (104, 11, "unsure"),
    ]
    assert view["counts"] == {"this": 1, "other": 1, "unsure": 1, "unmarked": 0, "hidden": 0}
    assert view["band"] is None and view["unmarked"] == [] and view["hidden"] == []
    flag = review_server.person_identity_status(3)["split_flags"][0]
    assert flag["marks"] == {"this": 1, "other": 1, "unsure": 1, "total": 3, "other_entry_id": None}
    assert flag["root_event_id"] == saved["event_id"] and flag["changed_by"] is None
    # Stored compactly, outside the evidence basis: the flag stays current.
    conn = corrections_db.connect(tmp_path / "corrections.db")
    event = next(
        e for e in corrections_db.person_review_events(conn) if e["event_id"] == saved["event_id"]
    )
    conn.close()
    assert event["evidence_snapshot"]["seam"] == {
        "version": 1,
        "assignments": [
            {"investor_id": 103, "side": "this"},
            {"investor_id": 104, "side": "unsure"},
            {"investor_id": 105, "side": "other"},
        ],
        "other_entry_id": None,
        "revises_event_id": None,
        "root": None,
    }
    assert review_server.person_linkage_case("split:3")["needs_recheck"] is False
    connection = review_server.open_db()
    try:
        [item] = review_server._person_split_flag_items(connection)
    finally:
        connection.close()
    assert item["key"] == f"person:3:person_split_flagged:{saved['event_id']}"
    assert item["title"].endswith(" · 1 of 3 appearances marked as someone else's")


def test_marks_show_the_band_and_drift(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_sql=_GHERARDI_SHAPE)
    _mark(3, {103: "this", 105: "this", 104: "other"})
    view = review_server.person_linkage_case("split:3")["seam"]
    assert view["band"] == [1623, 1635]
    conn = sqlite3.connect(tmp_path / "main.db")
    conn.executescript(
        "UPDATE investor SET is_deleted=1 WHERE investor_id=105;"
        "INSERT INTO investor VALUES (106,3,11,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,0);"
    )
    conn.commit()
    conn.close()
    view = review_server.person_linkage_case("split:3")["seam"]
    assert view["hidden"] == [105] and view["unmarked"] == [106]
    assert view["counts"] == {"this": 1, "other": 1, "unsure": 0, "unmarked": 1, "hidden": 1}
    assert [(a["investor_id"], a["side"]) for a in view["assignments"]] == [
        (103, "this"),
        (104, "other"),
        (106, "unmarked"),
    ]
    assert view["band"] == [1623, 1635]


def test_changing_marks_is_an_explicit_revision(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_sql=_GHERARDI_SHAPE)
    first = _mark(
        3, {103: "this", 105: "other", 104: "unsure"},
        reviewer="FT", rationale="The second row on act 10 is the bank's stake.",
    )
    # A plain re-flag, as the pair panel posts it, keeps its refusal.
    with pytest.raises(HTTPException) as exc:
        _flag_entry(person_id=3, reviewer="WH", rationale="Another note.")
    assert exc.value.status_code == 409 and exc.value.detail.startswith("Already flagged by FT on ")
    # A revision must name the event it revises and carry marks.
    with pytest.raises(HTTPException) as exc:
        _mark(
            3, {103: "this", 105: "other", 104: "other"},
            reviewer="WH", reason_code="seam_revision", revises="not-the-latest",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "The marks changed after you opened them. Reload before changing them."
    with pytest.raises(HTTPException) as exc:
        review_server.decide_person_linkage.__wrapped__(
            "split:3",
            review_server.PersonLinkageDecision(
                reviewer="WH", action="flag_split", person_ids=[3], reason_code="seam_revision",
                rationale="x", expected_status="flag_split", expected_event_id=first["event_id"],
            ),
        )
    assert exc.value.status_code == 400 and exc.value.detail == "A change of marks needs the marks."
    second = _mark(
        3, {103: "this", 105: "other", 104: "other"},
        reviewer="WH", rationale="Act 11 reads like the son.",
        reason_code="seam_revision", revises=first["event_id"],
    )
    assert second["event_id"] != first["event_id"]
    flag = review_server.person_identity_status(3)["split_flags"][0]
    assert flag["event_id"] == second["event_id"] and flag["root_event_id"] == first["event_id"]
    assert (flag["created_by"], flag["rationale"]) == ("FT", "The second row on act 10 is the bank's stake.")
    assert (flag["changed_by"], flag["change_note"]) == ("WH", "Act 11 reads like the son.")
    assert flag["marks"]["other"] == 2
    view = review_server.person_linkage_case("split:3")["seam"]
    assert (view["root_event_id"], view["event_id"]) == (first["event_id"], second["event_id"])
    assert view["counts"]["other"] == 2
    # The lists still say "flagged by FT"; the worklist item keeps its root key.
    page = review_server.person_linkage_cases(
        lane="possible_splits", facet="flagged", offset=0, limit=100
    )
    assert [(row["case_id"], row["decided_by"]) for row in page["cases"]] == [("split:3", "FT")]
    connection = review_server.open_db()
    try:
        [item] = review_server._person_split_flag_items(connection)
    finally:
        connection.close()
    assert item["key"] == f"person:3:person_split_flagged:{first['event_id']}"
    history = review_server.person_linkage_case("split:3")["history"]
    assert [(e["action"], e["reason_code"]) for e in history] == [
        ("flag_split", "seam_revision"),
        ("flag_split", None),
    ]
    # A further revision chains to the same root (this is what Undo will post).
    third = _mark(
        3, {103: "this", 105: "other", 104: "unsure"},
        reviewer="WH", rationale="Back to unsure.",
        reason_code="seam_revision", revises=second["event_id"],
    )
    flag = review_server.person_identity_status(3)["split_flags"][0]
    assert (flag["root_event_id"], flag["event_id"]) == (first["event_id"], third["event_id"])
    assert flag["marks"]["unsure"] == 1
    # Lifting names the latest event; afterwards nothing stands to revise.
    _lift_flag(flag, reviewer="WH")
    assert review_server.person_identity_status(3)["split_flags"] == []
    assert review_server.person_linkage_case("split:3")["seam"] is None
    with pytest.raises(HTTPException) as exc:
        _mark(
            3, {103: "this", 105: "other", 104: "unsure"},
            reason_code="seam_revision", revises=third["event_id"],
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "Nothing to change: this entry is not flagged."


def test_marks_are_refused_while_the_blind_round_holds_the_entry(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _labeling_packet(tmp_path, monkeypatch)  # pair 1:2 is open in the round
    with pytest.raises(HTTPException) as exc:
        _mark(1, {101: "other"})
    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "This entry is waiting under Judge blind; its acts can be marked once it is judged."
    )
    assert review_server.person_identity_status(1)["split_flags"] == []
    # A plain flag without marks is allowed as before, and an entry outside
    # the round can be marked.
    _flag_entry(person_id=1)
    _mark(3, {103: "this", 104: "other"})
    blind = review_server.person_linkage_case("pair:1:2")
    assert "seam" not in blind and "match_probability" not in blind


def test_other_entry_is_checked_and_kept_as_a_note(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _mark(3, {103: "this", 104: "other"}, other_entry_id=3)
    assert exc.value.status_code == 400
    assert exc.value.detail == "The other entry cannot be the entry itself."
    with pytest.raises(HTTPException) as exc:
        _mark(3, {103: "this", 104: "other"}, other_entry_id=999)
    assert exc.value.status_code == 400 and exc.value.detail == "Entry #999 is not in the database."
    _mark(3, {103: "this", 104: "other"}, other_entry_id=1)
    assert review_server.person_linkage_case("split:3")["seam"]["other_entry_id"] == 1
    assert review_server.person_identity_status(3)["split_flags"][0]["marks"]["other_entry_id"] == 1
    # A note, never a link or a flag on the other entry.
    assert review_server.person_identity_status(1)["split_flags"] == []
    assert review_server.person_linkage_case("pair:1:2")["active_links"] == []


def test_split_case_says_when_the_round_holds_the_entry(tmp_path, monkeypatch):
    """The screen hides marking while the blind round holds the entry; the
    payload says so. Appearances carry the proxy flag and stake names (null
    here: the reduced schema has neither column)."""
    _wire(tmp_path, monkeypatch)
    assert review_server.person_linkage_case("split:1")["marks_held_by_round"] is False
    _labeling_packet(tmp_path, monkeypatch)
    assert review_server.person_linkage_case("split:1")["marks_held_by_round"] is True
    assert review_server.person_linkage_case("split:3")["marks_held_by_round"] is False
    appearance = review_server.person_linkage_case("split:3")["persons"][0]["appearances"][0]
    assert appearance["via_proxy"] == 0 and appearance["stake_names"] is None
    assert "marks_held_by_round" not in review_server.person_linkage_case("pair:1:2")


def test_person_page_lists_one_line_per_appearance(tmp_path, monkeypatch):
    """Gherardi shape: two appearances on act 10 become two lines that the
    role and title tell apart; `id` still opens the contract, `key` is unique."""
    _wire(
        tmp_path,
        monkeypatch,
        extra_sql=_GHERARDI_SHAPE
        + "INSERT INTO title VALUES (1,'illustrissimo signor senatore');"
        + "UPDATE investor SET title=1, heirs_of=1 WHERE investor_id=103;",
    )
    connection = review_server.open_db()
    try:
        record = review_server.person_detail(connection, "3")
    finally:
        connection.close()
    [section] = [s for s in record["sections"] if s["title"].startswith("Appears in contracts")]
    assert section["title"] == "Appears in contracts (3)"
    assert section["columns"] == ["Firm", "Date", "Recorded as", "Profession"]
    rows = section["rows"]
    assert [row["id"] for row in rows] == ["10", "10", "11"]
    assert [row["key"] for row in rows] == ["10:103", "10:105", "11:104"]
    assert [row["investor_id"] for row in rows] == [103, 105, 104]
    assert rows[0]["cells"][2] == "illustrissimo signor senatore · general partner, through heirs"
    assert rows[1]["cells"][2] == "external investor"
    assert rows[2]["cells"][2] == "general partner"


def test_person_page_shows_the_marks_as_a_quiet_column(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, extra_sql=_GHERARDI_SHAPE)
    connection = review_server.open_db()
    try:
        before = review_server.person_detail(connection, "3")
        [section] = [s for s in before["sections"] if s["title"].startswith("Appears in contracts")]
        assert "Marked as" not in section["columns"]
        _mark(3, {103: "this", 105: "other", 104: "unsure"})
        after = review_server.person_detail(connection, "3")
        [section] = [s for s in after["sections"] if s["title"].startswith("Appears in contracts")]
        assert section["columns"][-1] == "Marked as"
        assert [(row["key"], row["cells"][-1]) for row in section["rows"]] == [
            ("10:103", ""),
            ("10:105", "someone else"),
            ("11:104", "unsure"),
        ]
        flag = after["identity_status"]["split_flags"][0]
        assert flag["marks"] == {"this": 1, "other": 1, "unsure": 1, "total": 3, "other_entry_id": None}
        # A plain flag without marks adds no column.
        _lift_flag(flag)
        _flag_entry(person_id=3)
        plain = review_server.person_detail(connection, "3")
        [section] = [s for s in plain["sections"] if s["title"].startswith("Appears in contracts")]
        assert "Marked as" not in section["columns"]
    finally:
        connection.close()
