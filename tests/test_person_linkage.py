"""Focused tests for pair union, evidence, and guarded group projections."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from workflows.person_linkage import (
    BLOCKING_LANES,
    KNOWN_FUSED_PERSON_IDS,
    _firm_token_diagnostics,
    _network_diagnostics,
    build_group_projections,
    build_pair_records,
    load_source_pointer_index,
    matched_blocking_lanes,
    waterfall_contributions,
)


def _person(person_id: int, **updates) -> dict:
    row = {
        "person_id": person_id,
        "first_name": "Salvatore",
        "father_mother": "Vincenzo",
        "grandfather": "Piero",
        "last_name": "Innori",
        "nickname": None,
        "first_norm": "salvatore",
        "patronymic_norm": "vincenzo",
        "grandfather_norm": "piero",
        "last_norm": "innori",
        "first_year": 1588.0,
        "last_year": 1588.0,
        "n_appearances": 1,
        "contracts": [8],
        "firms": ["firm a"],
        "firm_tokens": ["firm"],
        "partners": [90],
        "dominant_role": "gp",
        "partners_all": [],
        "partner_events_all": {},
        "span_years": 0,
        "first_posthumous_year": float("nan"),
    }
    row.update(updates)
    return row


def _model() -> dict:
    return {
        "probability_two_random_records_match": 0.1,
        "comparisons": [
            {
                "output_column_name": "name",
                "comparison_levels": [
                    {"is_null_level": True, "label_for_charts": "name missing"},
                    {"label_for_charts": "same name"},
                    {"label_for_charts": "different name"},
                ],
            }
        ],
    }


def _prediction(left: int, right: int, **updates) -> dict:
    prior = math.log2(0.1 / 0.9)
    row = {
        "person_id_l": left,
        "person_id_r": right,
        "match_probability": 1 / (1 + 2 ** -(prior + 4)),
        "match_weight": prior + 4,
        "first_norm_l": "salvatore",
        "first_norm_r": "salvadore",
        "last_norm_l": "innori",
        "last_norm_r": "inori",
        "patronymic_norm_l": "vincenzo",
        "patronymic_norm_r": "vincenzo",
        "grandfather_norm_l": "piero",
        "grandfather_norm_r": "piero",
        "gamma_name": 1,
        "bf_name": 16.0,
        "match_key": "4",
    }
    row.update(updates)
    return row


def test_all_five_blocking_lanes_are_identifiable():
    cases = [
        _prediction(
            1,
            2,
            first_norm_r="salvatore",
            last_norm_r="innori",
            patronymic_norm_r="other",
            grandfather_norm_r="other",
        ),
        _prediction(
            1,
            2,
            first_norm_r="other",
            last_norm_r="innori",
            patronymic_norm_r="vincenzo",
            grandfather_norm_r="other",
        ),
        _prediction(
            1,
            2,
            first_norm_r="salvatore",
            last_norm_r="otherhouse",
            patronymic_norm_r="vincenzo",
            grandfather_norm_r="other",
        ),
        _prediction(
            1,
            2,
            first_norm_r="other",
            last_norm_r="innori",
            patronymic_norm_r="other",
            grandfather_norm_r="piero",
        ),
        _prediction(1, 2),
    ]
    for expected, row in zip(BLOCKING_LANES, cases):
        assert expected in matched_blocking_lanes(row)
    assert matched_blocking_lanes(cases[-1]) == [
        "surname_spelling_variant_with_same_father"
    ]


def test_pair_cache_preserves_model_only_variants_and_rule_precedence(tmp_path):
    spine = pd.DataFrame(
        [
            _person(1, last_name="Innori", last_norm="innori", contracts=[8]),
            _person(
                2,
                first_name="Salvadore",
                first_norm="salvadore",
                last_name="Inori",
                last_norm="inori",
                first_year=1589,
                last_year=1589,
                contracts=[9],
            ),
            _person(3, first_name="Giovanni", first_norm="giovanni", last_name="Corsi",
                    last_norm="corsi", first_year=1500, last_year=1510, contracts=[10]),
            _person(4, first_name="Giovanni", first_norm="giovanni", last_name="Corsi",
                    last_norm="corsi", first_year=1600, last_year=1610, contracts=[11]),
        ]
    )
    tiers = pd.DataFrame(
        [
            {
                "person_id_l": 3,
                "person_id_r": 4,
                "tier": "distinct_strong",
                "name": "Giovanni Corsi",
                "reasons": ["a 110-year career is impossible"],
                "shared_contracts": [],
            }
        ]
    )
    predictions = pd.DataFrame(
        [
            _prediction(1, 2),
            _prediction(
                3,
                4,
                first_norm_l="giovanni",
                first_norm_r="giovanni",
                last_norm_l="corsi",
                last_norm_r="corsi",
                patronymic_norm_l="vincenzo",
                patronymic_norm_r="vincenzo",
                match_probability=0.999,
                match_weight=10.0,
            ),
        ]
    )
    links = tmp_path / "links.jsonl"
    links.write_text(
        json.dumps(
            {
                "db_row_id": "contract:8",
                "source_entry_id": "entry-8",
                "source_entry_key": "stable-8",
                "register_id": "reg-1",
                "entry_folio_start": "12r",
                "word_entry_text": "A narrative body must never enter the cache",
                "document": "Nor may the DB narrative",
            }
        )
        + "\n"
    )
    source_index = load_source_pointer_index(links)
    records = build_pair_records(
        spine,
        tiers,
        predictions,
        _model(),
        run_id="pl-test",
        source_index=source_index,
    )
    by_key = {row["pair_key"]: row for row in records}

    variant = by_key["1:2"]
    assert variant["deterministic_tier"] is None
    assert variant["source"] == "splink"
    assert variant["high_concordance"] == 1
    assert variant["concordance_reasons_json"]
    assert variant["review_score"] is not None
    assert variant["review_rank"] is not None
    assert variant["priority_band"] in {
        "priority_1", "priority_2", "priority_3", "priority_4"
    }
    assert variant["blocking_lanes_json"] == [
        "surname_spelling_variant_with_same_father"
    ]
    assert variant["source_pointers_json"][0]["source_entry_key"] == "stable-8"
    assert "narrative" not in json.dumps(variant["source_pointers_json"]).lower()

    refused = by_key["3:4"]
    assert refused["deterministic_tier"] == "distinct_strong"
    assert refused["precedence_verdict"] == "distinct_strong"
    assert refused["match_weight"] == 10.0  # retained as evidence, never obeyed


def test_waterfall_is_signed_and_reaches_the_splink_weight():
    prediction = _prediction(1, 2)
    rows = waterfall_contributions(prediction, _model())
    assert rows[0]["kind"] == "prior" and rows[0]["weight_bits"] < 0
    assert rows[1]["direction"] == "supports"
    assert rows[-1]["cumulative_weight_bits"] == prediction["match_weight"]


def test_whole_group_career_guard_overrules_pairwise_chain():
    spine = pd.DataFrame(
        [
            _person(1, first_year=1546, last_year=1549),
            _person(2, first_year=1572, last_year=1595),
            _person(3, first_year=1638, last_year=1638),
        ]
    )
    pairs = [
        {
            "pair_key": "1:2",
            "person_id_l": 1,
            "person_id_r": 2,
            "deterministic_tier": None,
            "precedence_verdict": "review",
            "match_probability": 0.95,
        },
        {
            "pair_key": "2:3",
            "person_id_l": 2,
            "person_id_r": 3,
            "deterministic_tier": None,
            "precedence_verdict": "review",
            "match_probability": 0.96,
        },
    ]
    groups = build_group_projections(pairs, spine, run_id="pl-test", threshold=0.9)
    assert len(groups) == 1
    group = groups[0]
    assert group["person_ids_json"] == [1, 2, 3]
    assert group["implied_career_years"] == 92
    assert group["career_guard"] == 1
    assert group["guard_status"] == "career_impossible"
    assert group["is_likely_same"] == 0
    unscored = next(row for row in group["pair_matrix_json"] if row["pair_key"] == "1:3")
    assert unscored["scored"] is False


def test_whole_group_guard_detects_transitive_posthumous_conflict():
    spine = pd.DataFrame(
        [
            _person(
                1, first_year=1600, last_year=1605,
                first_posthumous_year=1610,
            ),
            _person(2, first_year=1604, last_year=1612),
            _person(3, first_year=1615, last_year=1620),
        ]
    )
    pairs = [
        {
            "pair_key": "1:2", "person_id_l": 1, "person_id_r": 2,
            "deterministic_tier": None, "precedence_verdict": "review",
            "match_probability": 0.95,
        },
        {
            "pair_key": "2:3", "person_id_l": 2, "person_id_r": 3,
            "deterministic_tier": None, "precedence_verdict": "review",
            "match_probability": 0.95,
        },
    ]
    group = build_group_projections(
        pairs, spine, run_id="pl-test", threshold=0.9
    )[0]
    assert "posthumous_conflict" in group["guard_status"]
    assert group["is_likely_same"] == 0
    assert "1610 before 1620" in " ".join(group["reasons_json"])


def test_network_diagnostics_use_inverse_degree_weighted_jaccard():
    left = _person(
        1,
        partners_all=[3, 4],
        partner_events_all={
            "3": [{"contract_id": 10, "year": 1600}],
            "4": [{"contract_id": 11, "year": 1600}],
        },
    )
    right = _person(
        2,
        partners_all=[3, 5],
        partner_events_all={
            "3": [{"contract_id": 12, "year": 1604}],
            "5": [{"contract_id": 13, "year": 1604}],
        },
    )
    result = _network_diagnostics(
        left, right, degrees={3: 6, 4: 2, 5: 2}, excluded_contracts=set()
    )
    shared_weight = 1 / math.log(8)
    union_weight = shared_weight + 2 * (1 / math.log(4))
    assert result["adamic_adar"] == pytest.approx(shared_weight)
    assert result["weighted_jaccard"] == pytest.approx(
        shared_weight / union_weight
    )
    assert result["jaccard"] == pytest.approx(1 / 3)
    assert result["overlap_coefficient"] == pytest.approx(1 / 2)
    assert result["shared_within_5_years"] == 1


def test_network_diagnostics_remove_the_candidates_shared_act():
    left = _person(
        1,
        partner_events_all={"3": [{"contract_id": 10, "year": 1600}]},
    )
    right = _person(
        2,
        partner_events_all={"3": [{"contract_id": 10, "year": 1600}]},
    )
    result = _network_diagnostics(
        left, right, degrees={3: 2}, excluded_contracts={10}
    )
    assert result["common_neighbor_count"] == 0
    assert result["weighted_jaccard"] == 0


def test_known_fused_inputs_are_flagged_regardless_of_span():
    # The list itself is a pinned research finding, not a tunable.
    assert KNOWN_FUSED_PERSON_IDS == frozenset({1164, 3607})
    # Person 3607 "Piero Capponi" holds at least three men; a 13-year span on
    # both sides must not launder it into an ordinary single-career input.
    flagged = _network_diagnostics(
        _person(3607, span_years=13),
        _person(3610, span_years=13),
        degrees={},
        excluded_contracts=set(),
    )
    assert flagged["potential_fused_input"] is True
    ordinary = _network_diagnostics(
        _person(5, span_years=13),
        _person(3610, span_years=13),
        degrees={},
        excluded_contracts=set(),
    )
    assert ordinary["potential_fused_input"] is False
    # ...and the flag survives the full pair-record path, where the spine rows
    # are re-shaped before they reach the diagnostics.
    spine = pd.DataFrame([_person(3607, span_years=13), _person(3610, span_years=13)])
    tiers = pd.DataFrame(
        [
            {
                "person_id_l": 3607,
                "person_id_r": 3610,
                "tier": "review",
                "name": "Piero Capponi",
                "reasons": ["the gray middle"],
                "shared_contracts": [],
            }
        ]
    )
    records = build_pair_records(
        spine, tiers, pd.DataFrame([_prediction(3607, 3610)]), _model(),
        run_id="pl-test",
    )
    assert records[0]["network_diagnostics_json"]["potential_fused_input"] is True


def test_a_zero_bayes_factor_survives_into_the_waterfall():
    # `or 1.0` once swallowed a genuine 0.0 into the multiplicative identity;
    # a zero Bayes factor is the model's strongest possible statement and must
    # reach the reviewer as such.
    prediction = _prediction(1, 2, bf_name=0.0, match_weight=None)
    rows = waterfall_contributions(prediction, _model())
    name_row = next(row for row in rows if row["comparison"] == "name")
    assert name_row["bayes_factor"] == 0.0
    assert name_row["weight_bits"] == float("-inf")
    assert name_row["direction"] == "against"


def test_unknown_degree_neighbors_contribute_nothing_not_the_maximum():
    # Neighbors 4 and 5 are absent from the degree map. Treating them as
    # degree 0 would hand each the MAXIMUM weight 1/log 2; they must instead
    # drop out of both Adamic-Adar and the weighted-Jaccard union.
    left = _person(
        1,
        partner_events_all={
            "3": [{"contract_id": 10, "year": 1600}],
            "4": [{"contract_id": 11, "year": 1600}],
            "5": [{"contract_id": 12, "year": 1600}],
        },
    )
    right = _person(
        2,
        partner_events_all={
            "3": [{"contract_id": 13, "year": 1604}],
            "5": [{"contract_id": 14, "year": 1604}],
        },
    )
    result = _network_diagnostics(
        left, right, degrees={3: 6}, excluded_contracts=set()
    )
    assert result["adamic_adar"] == pytest.approx(1 / math.log(8))
    assert result["weighted_jaccard"] == pytest.approx(1.0)
    unknown = next(
        row for row in result["shared_neighbors"] if row["person_id"] == 5
    )
    assert unknown["degree"] == 0  # the display keeps the honest raw value
    assert unknown["inverse_degree_weight"] == 0.0


def test_firm_token_idf_caps_singletons_and_normalizes_by_union():
    result = _firm_token_diagnostics(
        {"firm_tokens": ["giovanni", "raro"]},
        {"firm_tokens": ["giovanni", "unico"]},
        document_count=100,
        document_frequency={"giovanni": 50, "raro": 1, "unico": 1},
    )
    assert result["shared_tokens"][0]["token"] == "giovanni"
    assert result["shared_tokens"][0]["document_frequency"] == 50
    assert 0 < result["weighted_jaccard"] < (1 / 3)
