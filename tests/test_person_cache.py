"""Focused tests for person-cache provenance and atomic storage."""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from workflows.person_cache import (
    COMPARISON_RULE_VERSION,
    DETERMINISTIC_RULE_VERSION,
    ModelBundleError,
    atomic_write_model_bundle,
    build_model_manifest,
    cache_report,
    manifest_path_for,
    source_input_fingerprint, canonical_json,
    validate_model_bundle,
    write_cache_atomic,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"person_id": 2, "name": "b", "year": float("nan"), "contracts": [9, 8]},
            {"person_id": 1, "name": "a", "year": 1550.0, "contracts": []},
        ]
    )


def _model() -> dict:
    return {
        "probability_two_random_records_match": 0.1,
        "blocking_rules_to_generate_predictions": [{"blocking_rule": str(i)} for i in range(5)],
        "comparisons": [
            {
                "output_column_name": "name",
                "comparison_levels": [
                    {"is_null_level": True, "label_for_charts": "missing"},
                    {
                        "label_for_charts": "same",
                        "m_probability": 0.8,
                        "u_probability": 0.2,
                    },
                    {
                        "label_for_charts": "different",
                        "m_probability": 0.2,
                        "u_probability": 0.8,
                    },
                ],
            }
        ],
    }


def _bundle(tmp_path):
    model_path = tmp_path / "person_model.json"
    model = _model()
    model_bytes = (json.dumps(model, ensure_ascii=False, indent=2) + "\n").encode()
    manifest = build_model_manifest(
        model_bytes,
        recall=0.5,
        seed=7,
        max_pairs=10,
        source_fingerprint=source_input_fingerprint(_frame()),
        training_timestamp="2026-08-28T12:00:00+00:00",
    )
    atomic_write_model_bundle(model_path, model, manifest)
    return model_path, manifest


def test_source_fingerprint_is_order_stable_and_content_sensitive():
    frame = _frame()
    assert source_input_fingerprint(frame) == source_input_fingerprint(
        frame.iloc[::-1].reset_index(drop=True)
    )
    changed = frame.copy()
    changed.loc[changed.person_id == 1, "name"] = "changed"
    assert source_input_fingerprint(changed) != source_input_fingerprint(frame)


def test_canonical_json_preserves_lists_but_sorts_sets():
    assert canonical_json({"ids": [548, 11821]}) == '{"ids":[548,11821]}'
    assert canonical_json({"ids": {11821, 548}}) == '{"ids":[11821,548]}'


def test_model_bundle_requires_matching_hash_recall_rules_and_source(tmp_path):
    model_path, manifest = _bundle(tmp_path)
    loaded_model, loaded_manifest = validate_model_bundle(
        model_path, manifest_path_for(model_path), _frame(), expected_recall=0.5
    )
    assert loaded_model["comparisons"][0]["output_column_name"] == "name"
    assert loaded_manifest == manifest

    with pytest.raises(ModelBundleError, match="requested recall"):
        validate_model_bundle(
            model_path, manifest_path_for(model_path), _frame(), expected_recall=0.8
        )

    changed = _frame()
    changed.loc[changed.person_id == 1, "name"] = "new source"
    with pytest.raises(ModelBundleError, match="source-input fingerprint mismatch"):
        validate_model_bundle(model_path, manifest_path_for(model_path), changed)

    model_path.write_text(model_path.read_text() + " ")
    with pytest.raises(ModelBundleError, match="SHA-256 mismatch"):
        validate_model_bundle(model_path, manifest_path_for(model_path), _frame())


def test_missing_manifest_is_a_hard_failure(tmp_path):
    model_path = tmp_path / "person_model.json"
    model_path.write_text(json.dumps(_model()))
    with pytest.raises(ModelBundleError, match="manifest is missing"):
        validate_model_bundle(model_path, manifest_path_for(model_path), _frame())


def _run() -> dict:
    return {
        "run_id": "pl-test",
        "run_timestamp": "2026-08-28T12:01:00+00:00",
        "model_sha256": "a" * 64,
        "model_training_timestamp": "2026-08-28T12:00:00+00:00",
        "recall": 0.5,
        "model_seed": 7,
        "model_max_pairs": 10.0,
        "source_input_fingerprint": "sha256:" + "b" * 64,
        "source_pointer_fingerprint": "",
        "comparison_rule_version": COMPARISON_RULE_VERSION,
        "deterministic_rule_version": DETERMINISTIC_RULE_VERSION,
        "group_threshold": 0.9,
        "blocking_lane_count": 5,
    }


def _pair(**updates) -> dict:
    row = {
        "pair_key": "1:2",
        "person_id_l": 1,
        "person_id_r": 2,
        "deterministic_tier": None,
        "precedence_verdict": "review",
        "source": "splink",
        "match_probability": 0.75,
        "match_weight": 1.5,
        "score_band": "high",
        "blocking_lanes_json": ["exact_first_and_surname"],
        "reasons_json": ["ranked for review"],
        "evidence_json": {"names": {}},
        "shared_contract_ids_json": [],
        "shared_firms_json": [],
        "shared_firm_words_json": [],
        "shared_partner_ids_json": [],
        "roles_json": {"left": "gp", "right": "gp"},
        "career_span_json": {"combined_span_years": 10},
        "contract_pointers_json": {"left": [8], "right": [9]},
        "source_pointers_json": [{"source_entry_key": "entry:1"}],
        "waterfall_contributions_json": [{"weight_bits": 1.5}],
        "run_id": "pl-test",
    }
    row.update(updates)
    return row


def test_cache_replacement_is_atomic_and_metadata_is_queryable(tmp_path):
    cache_path = tmp_path / "person_cache.db"
    cache_path.write_bytes(b"old cache remains if the build fails")
    old_bytes = cache_path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError):
        write_cache_atomic(
            cache_path,
            _run(),
            [_pair(person_id_l=2, person_id_r=1)],
            [],
        )
    assert cache_path.read_bytes() == old_bytes

    write_cache_atomic(cache_path, _run(), [_pair()], [])
    report = cache_report(cache_path)
    assert report["pair_count"] == 1
    assert report["tiers"] == {"model_only": 1}
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM person_cache_meta"))
        pair = connection.execute(
            "SELECT deterministic_tier, source, blocking_lanes_json, "
            "network_diagnostics_json, firm_token_diagnostics_json "
            "FROM person_pair_suggestion"
        ).fetchone()
    finally:
        connection.close()
    assert metadata["model_sha256"] == "a" * 64
    assert metadata["blocking_lane_count"] == "5"
    assert metadata["diagnostic_rule_version"] == "person-linkage-diagnostics-v1"
    assert metadata["schema_version"] == "3"
    assert pair[0] is None and pair[1] == "splink"
    assert json.loads(pair[2]) == ["exact_first_and_surname"]
    assert json.loads(pair[3]) == {} and json.loads(pair[4]) == {}
