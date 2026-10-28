"""Reproducible, verdict-free sampling for the first human labeling round."""

import json
import sqlite3

from workflows import corrections_db
from workflows.person_labeling import (
    STRATUM_QUOTAS,
    build_labeling_sample,
    write_labeling_packet,
)

from scripts.build_person_labeling_packet import main as builder_main


def _row(key: str, **updates):
    row = {
        "pair_key": key,
        "precedence_verdict": "review",
        "deterministic_tier": None,
        "high_concordance": 0,
        "priority_band": "priority_4",
        "waterfall_contributions_json": [],
        "shared_firms_json": [],
        "shared_partner_ids_json": [],
        "network_diagnostics_json": {
            "weighted_jaccard": 0,
            "adamic_adar": 0,
            "potential_fused_input": False,
        },
        "firm_token_diagnostics_json": {"shared_tokens": []},
    }
    row.update(updates)
    return row


def test_labeling_sample_is_seeded_unique_and_stratified():
    rows = [
        _row(
            f"{index}:{index + 100}",
            deterministic_tier=(
                "caution_posthumous_conflict" if index < 20
                else "caution_gf_conflict" if 20 <= index < 40
                else None
            ),
            network_diagnostics_json={
                "weighted_jaccard": 0.15 if 40 <= index < 70 else (
                    0.05 if 70 <= index < 100 else 0
                ),
                "adamic_adar": 0.5,
                "potential_fused_input": 100 <= index < 120,
            },
        )
        for index in range(200)
    ]
    first = build_labeling_sample(rows, target=150, seed=7)
    second = build_labeling_sample(rows, target=150, seed=7)
    assert [row["pair_key"] for row in first] == [row["pair_key"] for row in second]
    assert len(first) == len({row["pair_key"] for row in first}) == 150
    assert {row["labeling_stratum"] for row in first} >= {
        "posthumous_conflict",
        "other_source_caution",
        "network_high",
        "network_medium",
        "fused_input_stress",
    }
    for row in first:
        assert row["inclusion_probability"] == (
            row["stratum_sample_size"] / row["stratum_population"]
        )
        assert row["sampling_weight"] == 1 / row["inclusion_probability"]


def test_stratum_quotas_are_the_150_target_exactly():
    # The quotas are the design; a drifted sum silently reweights every stratum
    # through the trim/top-up passes instead of by declared intent.
    assert sum(STRATUM_QUOTAS.values()) == 150


def test_v3_ego_and_firm_token_strata_are_reachable_and_ordered():
    def network(**updates):
        base = {
            "weighted_jaccard": 0,
            "adamic_adar": 0,
            "common_neighbor_count": 0,
            "left_ego_size": 0,
            "right_ego_size": 0,
            "potential_fused_input": False,
        }
        base.update(updates)
        return base

    rows = [
        # A shared neighbor between tiny egos wins over its high band score...
        _row("1:2", network_diagnostics_json=network(
            weighted_jaccard=0.2, common_neighbor_count=1,
            left_ego_size=2, right_ego_size=8,
        )),
        # ...as does one between two prolific egos...
        _row("3:4", network_diagnostics_json=network(
            weighted_jaccard=0.2, common_neighbor_count=3,
            left_ego_size=10, right_ego_size=14,
        )),
        # ...while the middle-sized egos still fall through to the bands.
        _row("5:6", network_diagnostics_json=network(
            weighted_jaccard=0.15, common_neighbor_count=1,
            left_ego_size=5, right_ego_size=6,
        )),
        # One df<=5 token is enough for the rare lane, whatever else is shared.
        _row("7:8", firm_token_diagnostics_json={"shared_tokens": [
            {"token": "raro", "document_frequency": 2},
            {"token": "compagnia", "document_frequency": 80},
        ]}),
        # Every token common: the boilerplate-only lane.
        _row("9:10", firm_token_diagnostics_json={"shared_tokens": [
            {"token": "compagnia", "document_frequency": 80},
            {"token": "santa", "document_frequency": 200},
        ]}),
        # df exactly 50 is neither rare nor "every df > 50".
        _row("11:12", firm_token_diagnostics_json={"shared_tokens": [
            {"token": "maria", "document_frequency": 50},
        ]}),
    ]
    sample = build_labeling_sample(rows, target=len(rows), seed=7)
    strata = {row["pair_key"]: row["labeling_stratum"] for row in sample}
    assert strata["1:2"] == "network_sparse_ego"
    assert strata["3:4"] == "network_prolific_ego"
    assert strata["5:6"] == "network_high"
    assert strata["7:8"] == "rare_firm_token"
    assert strata["9:10"] == "common_firm_token_only"
    assert strata["11:12"] == "zero_or_other_network"


def _fake_cache(path, rows):
    """A person_cache.db reduced to the two tables the packet writer reads."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE cache_run (run_id TEXT)")
    connection.execute("INSERT INTO cache_run VALUES ('pl-test')")
    connection.execute(
        "CREATE TABLE person_pair_suggestion ("
        "pair_key TEXT, precedence_verdict TEXT, deterministic_tier TEXT, "
        "network_diagnostics_json TEXT, firm_token_diagnostics_json TEXT, "
        "waterfall_contributions_json TEXT)"
    )
    for row in rows:
        connection.execute(
            "INSERT INTO person_pair_suggestion VALUES (?,?,?,?,?,?)",
            (
                row["pair_key"],
                row["precedence_verdict"],
                row["deterministic_tier"],
                json.dumps(row["network_diagnostics_json"]),
                json.dumps(row["firm_token_diagnostics_json"]),
                json.dumps(row["waterfall_contributions_json"]),
            ),
        )
    connection.commit()
    connection.close()


def test_manifest_records_the_baseline_stratum_quotas(tmp_path):
    cache = tmp_path / "person_cache.db"
    _fake_cache(cache, [_row(f"{i}:{i + 100}") for i in range(20)])
    report = write_labeling_packet(
        cache, tmp_path / "packet.jsonl", target=10, seed=7
    )
    manifest = json.loads(
        (tmp_path / "packet.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["stratum_quotas"] == STRATUM_QUOTAS
    assert manifest["algorithm_version"] == "person-labeling-v3"
    assert report["stratum_quotas"] == STRATUM_QUOTAS


def _labeling_event(path, labeling):
    connection = corrections_db.connect(path)
    corrections_db.append_person_review_event(
        connection,
        action="same_as",
        person_ids=[1, 2],
        by="tester",
        evidence_snapshot={"case_id": "pair:1:2", "labeling": labeling},
    )
    connection.commit()
    connection.close()


def test_builder_refuses_once_a_blind_label_exists(tmp_path, monkeypatch, capsys):
    corrections = tmp_path / "corrections.db"
    _labeling_event(corrections, {"packet_id": "person-labeling-v2:abc123def456"})
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", str(corrections))
    cache = tmp_path / "person_cache.db"
    _fake_cache(cache, [_row(f"{i}:{i + 100}") for i in range(20)])
    output = tmp_path / "packet.jsonl"

    assert builder_main(["--cache", str(cache), "--output", str(output)]) == 2
    message = capsys.readouterr().out
    assert "refused" in message
    assert "person-labeling-v2:abc123def456" in message  # names the labeled packet
    assert not output.exists()

    # --force is the only way past the guard, and it must actually build.
    assert builder_main(
        ["--cache", str(cache), "--output", str(output), "--force"]
    ) == 0
    assert output.exists()


def test_builder_ignores_review_events_without_labeling_metadata(
    tmp_path, monkeypatch
):
    corrections = tmp_path / "corrections.db"
    _labeling_event(corrections, None)  # an ordinary review, not a blind label
    monkeypatch.setenv("FLORACCO_CORRECTIONS_DB_PATH", str(corrections))
    cache = tmp_path / "person_cache.db"
    _fake_cache(cache, [_row(f"{i}:{i + 100}") for i in range(20)])
    output = tmp_path / "packet.jsonl"
    assert builder_main(["--cache", str(cache), "--output", str(output)]) == 0
    assert output.exists()
