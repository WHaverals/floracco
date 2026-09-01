"""Build a reproducible, stratified human-labeling packet from person_cache.db."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAMPLE_ALGORITHM_VERSION = "person-labeling-v3"
# Baseline quotas over the mutually exclusive strata. They sum to the default
# 150-case target and are recorded verbatim in the manifest, so target-vs-filled
# stays auditable without reading this code. Rows land in the FIRST stratum
# whose test they satisfy, in this dict's order.
STRATUM_QUOTAS = {
    "audit_anchor": 4,
    "posthumous_conflict": 15,
    "deterministic_likely_duplicate": 17,
    "other_source_caution": 10,
    "fused_input_stress": 5,
    "aa_high_normalized_low": 6,
    "network_sparse_ego": 8,
    "network_prolific_ego": 6,
    "network_high": 6,
    "network_medium": 6,
    "network_low": 4,
    "rare_firm_token": 8,
    "common_firm_token_only": 6,
    "exact_name_missing_lineage": 8,
    "role_conflict": 22,
    "zero_or_other_network": 19,
}
# Shared firm tokens at or below this document frequency are the "rare" lane —
# the same clamp point as the linkage IDF cap (FIRM_TOKEN_IDF_CAP_DF), where a
# lone typo and a genuinely rare firm name become indistinguishable by weight.
RARE_FIRM_TOKEN_MAX_DF = 5
# ...and above this one, every shared token is boilerplate ("compagnia", a saint,
# a mega-family surname) that says almost nothing about shared identity.
COMMON_FIRM_TOKEN_MIN_DF = 50
AUDIT_ANCHOR_PAIRS = {
    "2274:12017",  # exact name/father; role dominance differs
    "5308:6844",   # exact name + same firm; one lineage missing
    "7020:7047",   # Antonino/Antonio + same father + shared partner
    "8051:11718",  # Lapini/Gapini + same father + shared partners/activity
}


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key, value in list(item.items()):
        if key.endswith("_json") and isinstance(value, str):
            item[key] = json.loads(value)
    return item


def _stable_order(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(
            f"{SAMPLE_ALGORITHM_VERSION}:{seed}:{row['pair_key']}".encode()
        ).hexdigest(),
    )


def build_labeling_sample(
    rows: list[dict[str, Any]], *, target: int = 150, seed: int = 7
) -> list[dict[str, Any]]:
    """Sample mutually exclusive strata with explicit inclusion probabilities."""
    def comparison(row: dict[str, Any], name: str) -> dict[str, Any]:
        return next(
            (
                item
                for item in row.get("waterfall_contributions_json") or []
                if item.get("comparison") == name
            ),
            {},
        )

    def stratum(row: dict[str, Any]) -> str:
        tier = row.get("deterministic_tier")
        network = row.get("network_diagnostics_json") or {}
        weighted_jaccard = float(network.get("weighted_jaccard") or 0)
        adamic_adar = float(network.get("adamic_adar") or 0)
        common_neighbors = int(network.get("common_neighbor_count") or 0)
        minimum_ego = min(
            int(network.get("left_ego_size") or 0),
            int(network.get("right_ego_size") or 0),
        )
        shared_tokens = (row.get("firm_token_diagnostics_json") or {}).get(
            "shared_tokens"
        ) or []
        shared_dfs = [int(item.get("document_frequency") or 0) for item in shared_tokens]
        if row.get("pair_key") in AUDIT_ANCHOR_PAIRS:
            return "audit_anchor"
        if tier == "caution_posthumous_conflict":
            return "posthumous_conflict"
        if tier in {"batch_ghost", "same_as_strong"}:
            return "deterministic_likely_duplicate"
        if tier in {"caution_coappearance", "caution_gf_conflict"}:
            return "other_source_caution"
        if network.get("potential_fused_input"):
            return "fused_input_stress"
        if adamic_adar >= 1.0 and weighted_jaccard < 0.10:
            return "aa_high_normalized_low"
        # The ego-size extremes come before the weighted-Jaccard bands: a shared
        # neighbor is near-decisive when either side's ego network is tiny (the
        # min() below — one sparse side is enough), and near-noise when even the
        # smaller side is prolific. The bands then catch the middle-sized egos.
        if common_neighbors >= 1 and minimum_ego <= 2:
            return "network_sparse_ego"
        if common_neighbors >= 1 and minimum_ego >= 10:
            return "network_prolific_ego"
        if weighted_jaccard >= 0.10:
            return "network_high"
        if weighted_jaccard >= 0.03:
            return "network_medium"
        if weighted_jaccard > 0:
            return "network_low"
        if shared_dfs and min(shared_dfs) <= RARE_FIRM_TOKEN_MAX_DF:
            return "rare_firm_token"
        if shared_dfs and min(shared_dfs) > COMMON_FIRM_TOKEN_MIN_DF:
            return "common_firm_token_only"
        if (
            row.get("precedence_verdict") == "review"
            and comparison(row, "name").get("gamma") == 3
            and comparison(row, "lineage").get("gamma") == -1
        ):
            return "exact_name_missing_lineage"
        if (
            row.get("precedence_verdict") == "review"
            and float(comparison(row, "role").get("weight_bits") or 0) < 0
        ):
            return "role_conflict"
        return "zero_or_other_network"

    populations: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        populations.setdefault(stratum(row), []).append(row)
    quotas = STRATUM_QUOTAS
    selected_by_stratum: dict[str, list[dict[str, Any]]] = {}
    for name, population in populations.items():
        selected_by_stratum[name] = _stable_order(population, seed)[
            : min(quotas.get(name, 0), len(population))
        ]
    selected_count = sum(len(values) for values in selected_by_stratum.values())
    if selected_count > target:
        trimmed = _stable_order(
            [
                {**row, "_assigned_stratum": name}
                for name, values in selected_by_stratum.items()
                for row in values
            ],
            seed,
        )[:target]
        selected_by_stratum = {name: [] for name in populations}
        for row in trimmed:
            name = str(row.pop("_assigned_stratum"))
            selected_by_stratum[name].append(row)
        selected_count = target
    while selected_count < target:
        available = [
            (
                len(populations[name]) - len(selected_by_stratum[name]),
                name,
            )
            for name in populations
            if len(populations[name]) > len(selected_by_stratum[name])
        ]
        if not available:
            break
        _, name = max(available)
        selected_by_stratum[name].append(
            _stable_order(populations[name], seed)[len(selected_by_stratum[name])]
        )
        selected_count += 1

    sample: list[dict[str, Any]] = []
    for name, selected in selected_by_stratum.items():
        population_size = len(populations[name])
        sample_size = len(selected)
        if not sample_size:
            continue
        inclusion_probability = sample_size / population_size
        for row in selected:
            sample.append(
                {
                    **row,
                    "labeling_stratum": name,
                    "stratum_population": population_size,
                    "stratum_sample_size": sample_size,
                    "inclusion_probability": inclusion_probability,
                    "sampling_weight": 1.0 / inclusion_probability,
                }
            )
    # Blind handoff order must not reveal the stratum or model priority.
    return sorted(
        sample,
        key=lambda row: hashlib.sha256(
            f"{SAMPLE_ALGORITHM_VERSION}:blind-order:{seed}:{row['pair_key']}".encode()
        ).hexdigest(),
    )


def write_labeling_packet(
    cache_path: Path,
    output_path: Path,
    *,
    target: int = 150,
    seed: int = 7,
) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run = dict(connection.execute("SELECT * FROM cache_run LIMIT 1").fetchone())
        rows = [
            _decode(row)
            for row in connection.execute("SELECT * FROM person_pair_suggestion")
        ]
    finally:
        connection.close()
    sample = build_labeling_sample(rows, target=target, seed=seed)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in sample
    )
    output_path.write_text(body, encoding="utf-8")
    manifest = {
        "algorithm_version": SAMPLE_ALGORITHM_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "target_size": target,
        "actual_size": len(sample),
        "packet_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "cache_run": run,
        "strata": dict(Counter(row["labeling_stratum"] for row in sample)),
        "stratum_quotas": dict(STRATUM_QUOTAS),
        "stratum_populations": {
            stratum: max(
                int(row["stratum_population"])
                for row in sample
                if row["labeling_stratum"] == stratum
            )
            for stratum in {row["labeling_stratum"] for row in sample}
        },
        "target_population": "all scored person-linkage candidate pairs",
    }
    manifest_path = output_path.with_name(f"{output_path.stem}.manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "packet_path": str(output_path), "manifest_path": str(manifest_path)}
