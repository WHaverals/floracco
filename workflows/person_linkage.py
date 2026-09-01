"""Build the offline pair/group cache for person-identity review.

The batch reads ``main.db`` in SQLite read-only mode, validates the saved Splink
model and its companion manifest, scores the union of all five blocking lanes,
applies deterministic precedence, and atomically replaces ``person_cache.db``.

    uv run --extra linkage python workflows/person_linkage.py --dry-run
    uv run --extra linkage python workflows/person_linkage.py --recall 0.5
    uv run --extra linkage python workflows/person_linkage.py --report
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import logging
import math
import os
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from workflows.locks import maintenance_lock
from workflows.person_cache import (
    COMPARISON_RULE_VERSION,
    DETERMINISTIC_RULE_VERSION,
    DIAGNOSTIC_RULE_VERSION,
    ModelBundleError,
    cache_report,
    canonical_json,
    default_cache_path,
    manifest_path_for,
    sha256_path,
    source_input_fingerprint,
    validate_model_bundle,
    write_cache_atomic,
)
from workflows.person_features import (
    db_path,
    firm_token_document_frequency,
    load_person_spine,
    open_ro,
)
from workflows.person_model import (
    MODEL_PATH,
    flag_impossible_clusters,
    prepare_frame,
    score_with_precedence,
)
from workflows.person_tiers import build_tiers, combined_span, posthumous_conflict

DEFAULT_GROUP_THRESHOLD = 0.90
BLOCKING_LANES = (
    "exact_first_and_surname",
    "exact_surname_and_father",
    "exact_first_and_father",
    "exact_surname_and_grandfather",
    "surname_spelling_variant_with_same_father",
)
COMPARISON_LABELS = {
    "name": "Recorded name",
    "lineage": "Father and grandfather",
    "contemporaneity": "Career and business context",
    "role": "Partnership role",
    "husband": "Husband's name",
}
NETWORK_DIAGNOSTIC_VERSION = "person-network-diagnostics-v1"
# Person rows research already proves are fused inputs regardless of their
# recorded span (docs/person_linkage/research.md §2 and §1 face 2): 1164 is the
# 69-year over-merge of grandfather and grandson under one identical chain, and
# 3607 "Piero Capponi" carries no patronymic and is proven to hold at least
# three different men, including a Naples firm. Their diagnostics must be read
# as mixtures even when the span alone looks like one career.
KNOWN_FUSED_PERSON_IDS = frozenset({1164, 3607})
# Firm tokens seen in at most this many contracts are clamped to one shared
# "rare" IDF weight, so a single typo (a df-1 token) cannot dominate the
# similarity over genuinely repeated firm-name material.
FIRM_TOKEN_IDF_CAP_DF = 5


def default_source_links_path() -> Path:
    root = Path(__file__).resolve().parents[1] / "data"
    raw_root = os.getenv("FLORACCO_DATA_DIR")
    if raw_root:
        root = Path(raw_root).expanduser()
    return (
        root
        / "derived/word-pipeline/05_db_candidate_matches"
        / "source_entry_db_link_candidates.jsonl"
    )


def pair_key(left: int, right: int) -> str:
    low, high = sorted((int(left), int(right)))
    return f"{low}:{high}"


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value)) and value != ""


def _same_nonempty(left: Any, right: Any) -> bool:
    return _present(left) and _present(right) and left == right


def _levenshtein_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    short_index = long_index = differences = 0
    while short_index < len(left) and long_index < len(right):
        if left[short_index] == right[long_index]:
            short_index += 1
        else:
            differences += 1
            if differences > 1:
                return False
        long_index += 1
    return True


def _levenshtein_distance(left: str, right: str) -> int:
    """Small, dependency-free edit distance for review evidence."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, 1):
        current = [left_index]
        for right_index, right_char in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def matched_blocking_lanes(row: Mapping[str, Any]) -> list[str]:
    """Return every lane a prediction satisfies, not merely Splink's first key."""
    first_l, first_r = row.get("first_norm_l"), row.get("first_norm_r")
    last_l, last_r = row.get("last_norm_l"), row.get("last_norm_r")
    father_l, father_r = row.get("patronymic_norm_l"), row.get("patronymic_norm_r")
    grandfather_l = row.get("grandfather_norm_l")
    grandfather_r = row.get("grandfather_norm_r")
    matches = [
        _same_nonempty(first_l, first_r) and _same_nonempty(last_l, last_r),
        _same_nonempty(last_l, last_r) and _same_nonempty(father_l, father_r),
        _same_nonempty(first_l, first_r) and _same_nonempty(father_l, father_r),
        _same_nonempty(last_l, last_r)
        and _same_nonempty(grandfather_l, grandfather_r),
        _present(last_l)
        and _present(last_r)
        and last_l != last_r
        and _same_nonempty(father_l, father_r)
        and _levenshtein_at_most_one(str(last_l), str(last_r)),
    ]
    return [label for label, matched in zip(BLOCKING_LANES, matches) if matched]


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clean_scalar(value: Any) -> Any:
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted(_clean_scalar(item) for item in value)
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def score_band(probability: float | None) -> str:
    if probability is None:
        return "not_scored"
    if probability >= 0.90:
        return "very_high"
    if probability >= 0.70:
        return "high"
    if probability >= 0.50:
        return "possible"
    if probability >= 0.10:
        return "low"
    return "very_low"


def _review_score(waterfall: list[dict[str, Any]]) -> float | None:
    """Evidence bits used only to order review, with role deliberately neutral."""
    comparisons = [
        row
        for row in waterfall
        if row.get("kind") == "comparison" and row.get("comparison") != "role"
    ]
    return sum(float(row.get("weight_bits") or 0.0) for row in comparisons) if comparisons else None


def _name_review_evidence(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any]:
    fields = {
        "given_name": ("first_norm", "first_name"),
        "surname": ("last_norm", "last_name"),
        "father": ("patronymic_norm", "father_mother"),
        "grandfather": ("grandfather_norm", "grandfather"),
    }
    result: dict[str, Any] = {}
    for label, (normalized, verbatim) in fields.items():
        left_norm = str(left.get(normalized) or "")
        right_norm = str(right.get(normalized) or "")
        result[label] = {
            "left": _clean_scalar(left.get(verbatim)),
            "right": _clean_scalar(right.get(verbatim)),
            "both_recorded": bool(left_norm and right_norm),
            "exact": bool(left_norm and right_norm and left_norm == right_norm),
            "edit_distance": (
                _levenshtein_distance(left_norm, right_norm)
                if left_norm and right_norm
                else None
            ),
        }
    return result


def _high_concordance_reasons(
    *,
    tier: str | None,
    name_evidence: Mapping[str, Any],
    span: float | int | None,
    shared_firms: list[Any],
    shared_partners: list[Any],
) -> list[str]:
    """Transparent routing rules that promote review but never assert identity."""
    if tier in {
        "distinct_strong",
        "caution_coappearance",
        "caution_posthumous_conflict",
        "caution_gf_conflict",
    }:
        return []
    if span is None or float(span) > 60:
        return []
    given = name_evidence["given_name"]
    surname = name_evidence["surname"]
    father = name_evidence["father"]
    exact_name = bool(given["exact"] and surname["exact"])
    father_same = bool(father["exact"])
    network = bool(shared_firms or shared_partners)
    reasons: list[str] = []
    if (
        father_same
        and network
        and surname["exact"]
        and not given["exact"]
        and given["edit_distance"] is not None
        and int(given["edit_distance"]) <= 2
    ):
        reasons.append("near given name; surname and father agree; business context overlaps")
    if (
        father_same
        and network
        and given["exact"]
        and not surname["exact"]
        and surname["edit_distance"] is not None
        and int(surname["edit_distance"]) <= 1
    ):
        reasons.append("near surname; given name and father agree; business context overlaps")
    if (
        father_same
        and network
        and not given["exact"]
        and not surname["exact"]
        and given["edit_distance"] is not None
        and surname["edit_distance"] is not None
        and int(given["edit_distance"]) <= 2
        and int(surname["edit_distance"]) <= 1
    ):
        reasons.append("near given name and surname; father agrees; business context overlaps")
    if exact_name and father_same and float(span) <= 30:
        reasons.append("exact name and father within a 30-year combined career")
    if exact_name and shared_firms:
        reasons.append("exact name and the same firm")
    return reasons


def assign_review_priorities(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign stable ordinal priorities; raw model probability remains technical."""
    eligible = [
        row
        for row in records
        if row.get("precedence_verdict") == "review"
        and row.get("review_score") is not None
    ]
    eligible.sort(
        key=lambda row: (-float(row["review_score"]), str(row["pair_key"]))
    )
    total = len(eligible)
    for rank, row in enumerate(eligible, 1):
        percentile = rank / total if total else 1.0
        row["review_rank"] = rank
        row["review_percentile"] = percentile
        row["priority_band"] = (
            "priority_1"
            if percentile <= 0.02
            else "priority_2"
            if percentile <= 0.10
            else "priority_3"
            if percentile <= 0.30
            else "priority_4"
        )
    for row in records:
        row.setdefault("review_rank", None)
        row.setdefault("review_percentile", None)
        row.setdefault("priority_band", None)
    return records


def _network_diagnostics(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    degrees: Mapping[int, int],
    excluded_contracts: set[int],
) -> dict[str, Any]:
    """Unscored rarity/size/time-aware ego-network diagnostics."""

    def usable_events(person: Mapping[str, Any]) -> dict[int, list[dict[str, Any]]]:
        result: dict[int, list[dict[str, Any]]] = {}
        for raw_neighbor, events in (person.get("partner_events_all") or {}).items():
            kept = [
                dict(event)
                for event in events
                if int(event["contract_id"]) not in excluded_contracts
            ]
            if kept:
                result[int(raw_neighbor)] = kept
        return result

    left_events, right_events = usable_events(left), usable_events(right)
    left_ids, right_ids = set(left_events), set(right_events)
    shared, union = left_ids & right_ids, left_ids | right_ids

    def weight(neighbor: int) -> float:
        # A neighbor absent from the degree map has no observed partnerships to
        # weigh; treating it as degree 0 would hand it the MAXIMUM rarity weight
        # (1/log 2), so unknown or non-positive degrees contribute nothing.
        degree = int(degrees.get(neighbor, 0))
        if degree <= 0:
            return 0.0
        return 1.0 / math.log(2.0 + degree)

    adamic_adar = sum(weight(neighbor) for neighbor in shared)
    union_weight = sum(weight(neighbor) for neighbor in union)
    weighted_jaccard = adamic_adar / union_weight if union_weight else 0.0
    common_jaccard = len(shared) / len(union) if union else 0.0
    overlap = len(shared) / min(len(left_ids), len(right_ids)) if left_ids and right_ids else 0.0

    shared_details = []
    aligned = {5: 0, 15: 0, 30: 0}
    for neighbor in sorted(shared):
        left_years = [
            int(event["year"]) for event in left_events[neighbor] if event.get("year") is not None
        ]
        right_years = [
            int(event["year"]) for event in right_events[neighbor] if event.get("year") is not None
        ]
        minimum_gap = (
            min(abs(left_year - right_year) for left_year in left_years for right_year in right_years)
            if left_years and right_years
            else None
        )
        if minimum_gap is not None:
            for window in aligned:
                aligned[window] += int(minimum_gap <= window)
        shared_details.append(
            {
                "person_id": neighbor,
                "degree": int(degrees.get(neighbor, 0)),
                "inverse_degree_weight": weight(neighbor),
                "minimum_year_gap": minimum_gap,
                "left_events": left_events[neighbor],
                "right_events": right_events[neighbor],
            }
        )

    return {
        "version": NETWORK_DIAGNOSTIC_VERSION,
        "excluded_shared_contract_ids": sorted(excluded_contracts),
        "left_ego_size": len(left_ids),
        "right_ego_size": len(right_ids),
        "common_neighbor_count": len(shared),
        "jaccard": common_jaccard,
        "overlap_coefficient": overlap,
        "adamic_adar": adamic_adar,
        "weighted_jaccard": weighted_jaccard,
        "shared_within_5_years": aligned[5],
        "shared_within_15_years": aligned[15],
        "shared_within_30_years": aligned[30],
        "shared_neighbors": shared_details,
        "potential_fused_input": bool(
            float(left.get("span_years") or 0) > 60
            or float(right.get("span_years") or 0) > 60
            or int(left.get("person_id") or 0) in KNOWN_FUSED_PERSON_IDS
            or int(right.get("person_id") or 0) in KNOWN_FUSED_PERSON_IDS
        ),
    }


def _firm_token_diagnostics(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    document_count: int,
    document_frequency: Mapping[str, int],
) -> dict[str, Any]:
    """Unscored capped-IDF similarity over self-name-stripped firm tokens."""
    left_tokens = set(left.get("firm_tokens") or [])
    right_tokens = set(right.get("firm_tokens") or [])
    shared, union = left_tokens & right_tokens, left_tokens | right_tokens
    cap = (
        math.log((document_count + 1) / (FIRM_TOKEN_IDF_CAP_DF + 1))
        if document_count >= FIRM_TOKEN_IDF_CAP_DF + 1
        else 0.0
    )

    def idf(token: str) -> float:
        raw = math.log((document_count + 1) / (int(document_frequency.get(token, 0)) + 1))
        return min(cap, raw) if cap > 0 else raw

    shared_weight = sum(idf(token) for token in shared)
    union_weight = sum(idf(token) for token in union)
    left_squared = sum(idf(token) ** 2 for token in left_tokens)
    right_squared = sum(idf(token) ** 2 for token in right_tokens)
    cosine_denominator = math.sqrt(left_squared * right_squared)
    return {
        "document_count": document_count,
        "idf_cap": cap,
        "weighted_jaccard": shared_weight / union_weight if union_weight else 0.0,
        "cosine": (
            sum(idf(token) ** 2 for token in shared) / cosine_denominator
            if cosine_denominator
            else 0.0
        ),
        "shared_tokens": [
            {
                "token": token,
                "document_frequency": int(document_frequency.get(token, 0)),
                "capped_idf": idf(token),
            }
            for token in sorted(shared)
        ],
    }


def _gamma_labels(model: Mapping[str, Any], comparison: str) -> dict[int, str]:
    spec = next(
        item for item in model.get("comparisons", []) if item.get("output_column_name") == comparison
    )
    levels = spec.get("comparison_levels", [])
    non_null = [level for level in levels if not level.get("is_null_level")]
    labels = {
        len(non_null) - index - 1: str(level.get("label_for_charts") or comparison)
        for index, level in enumerate(non_null)
    }
    null_level = next((level for level in levels if level.get("is_null_level")), None)
    labels[-1] = str((null_level or {}).get("label_for_charts") or "No evidence")
    return labels


def waterfall_contributions(
    prediction: Mapping[str, Any], model: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Compact signed/cumulative explanation matching Splink's final weight."""
    probability = float(model["probability_two_random_records_match"])
    prior_bayes_factor = probability / (1.0 - probability)
    prior_weight = math.log2(prior_bayes_factor)
    cumulative = prior_weight
    rows: list[dict[str, Any]] = [
        {
            "kind": "prior",
            "comparison": "prior",
            "label": "Chance before comparing these records",
            "gamma": None,
            "bayes_factor": prior_bayes_factor,
            "weight_bits": prior_weight,
            "cumulative_weight_bits": cumulative,
            "direction": "against" if prior_weight < 0 else "supports",
        }
    ]
    for comparison_spec in model.get("comparisons", []):
        comparison = str(comparison_spec["output_column_name"])
        gamma_value = _finite_float(prediction.get(f"gamma_{comparison}"))
        gamma = int(gamma_value) if gamma_value is not None else -1
        # `or 1.0` would also swallow a genuine 0.0 Bayes factor; only a truly
        # absent value may default to the multiplicative identity.
        bayes_factor_value = _finite_float(prediction.get(f"bf_{comparison}"))
        bayes_factor = bayes_factor_value if bayes_factor_value is not None else 1.0
        tf_factor_value = _finite_float(prediction.get(f"bf_tf_adj_{comparison}"))
        tf_factor = tf_factor_value if tf_factor_value is not None else 1.0
        combined = bayes_factor * tf_factor
        weight = math.log2(combined) if combined > 0 else float("-inf")
        cumulative += weight
        rows.append(
            {
                "kind": "comparison",
                "comparison": comparison,
                "comparison_label": COMPARISON_LABELS.get(comparison, comparison),
                "label": _gamma_labels(model, comparison).get(gamma, f"comparison level {gamma}"),
                "gamma": gamma,
                "bayes_factor": combined,
                "term_frequency_factor": tf_factor if tf_factor != 1.0 else None,
                "weight_bits": weight,
                "cumulative_weight_bits": cumulative,
                "direction": "supports" if weight > 1e-12 else (
                    "against" if weight < -1e-12 else "none"
                ),
            }
        )
    expected = _finite_float(prediction.get("match_weight"))
    if expected is not None and abs(cumulative - expected) > 1e-6:
        # Retain an explicit residual rather than silently claiming a waterfall
        # adds up when a future Splink release introduces another factor.
        residual = expected - cumulative
        rows.append(
            {
                "kind": "residual",
                "comparison": "unmapped",
                "label": "Other saved-model adjustment",
                "gamma": None,
                "bayes_factor": 2**residual,
                "weight_bits": residual,
                "cumulative_weight_bits": expected,
                "direction": "supports" if residual > 0 else "against",
            }
        )
    return rows


SOURCE_POINTER_FIELDS = (
    "source_entry_id",
    "source_entry_key",
    "register_id",
    "entry_label_raw",
    "entry_label_guess",
    "entry_registration_date_raw",
    "entry_folio_start",
    "entry_folio_end",
    "relationship_type",
    "link_ordinal",
)


def load_source_pointer_index(path: Path | None) -> dict[int, list[dict[str, Any]]]:
    """Load identifiers and folio pointers only; narrative bodies are excluded."""
    if path is None or not path.is_file():
        return {}
    result: dict[int, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            db_row_id = str(row.get("db_row_id") or "")
            if not db_row_id.startswith("contract:"):
                continue
            try:
                contract_id = int(db_row_id.split(":", 1)[1])
            except ValueError:
                continue
            pointer = {
                field: _clean_scalar(row.get(field))
                for field in SOURCE_POINTER_FIELDS
                if row.get(field) not in (None, "")
            }
            pointer["via_contract_id"] = contract_id
            result.setdefault(contract_id, []).append(pointer)
    for pointers in result.values():
        pointers.sort(
            key=lambda item: (
                str(item.get("source_entry_key") or item.get("source_entry_id") or ""),
                int(item.get("link_ordinal") or 0),
            )
        )
    return result


def _row_name(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _clean_scalar(row.get(key))
        for key in (
            "first_name",
            "father_mother",
            "grandfather",
            "last_name",
            "nickname",
            "first_norm",
            "patronymic_norm",
            "grandfather_norm",
            "last_norm",
        )
    }


def _shared(left: Mapping[str, Any], right: Mapping[str, Any], column: str) -> list[Any]:
    return sorted(set(left.get(column) or []) & set(right.get(column) or []))


def _model_reasons(
    prediction: Mapping[str, Any], waterfall: list[dict[str, Any]], lanes: list[str]
) -> list[str]:
    reasons = [
        "the saved model compared this pair through "
        + ", ".join(lane.replace("_", " ") for lane in lanes)
    ]
    comparisons = [row for row in waterfall if row["kind"] == "comparison"]
    supports = sorted(comparisons, key=lambda row: row["weight_bits"], reverse=True)
    cautions = sorted(comparisons, key=lambda row: row["weight_bits"])
    if supports and supports[0]["weight_bits"] > 0:
        reasons.append(
            f"strongest model support: {supports[0]['comparison_label'].lower()} — "
            f"{supports[0]['label']}"
        )
    if cautions and cautions[0]["weight_bits"] < 0:
        reasons.append(
            f"strongest model caution: {cautions[0]['comparison_label'].lower()} — "
            f"{cautions[0]['label']}"
        )
    return reasons


def build_pair_records(
    spine: pd.DataFrame,
    tiers: pd.DataFrame,
    predictions: pd.DataFrame,
    model: Mapping[str, Any],
    *,
    run_id: str,
    source_index: Mapping[int, list[dict[str, Any]]] | None = None,
    firm_document_count: int = 0,
    firm_token_df: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Union deterministic candidates with every model prediction.

    ``score_with_precedence`` is intentionally the only path by which tier rows
    receive model weights.  Model-only rows are appended afterwards with a null
    deterministic tier, preserving surname/name variants outside tier blocking.
    """
    source_index = source_index or {}
    normalized_predictions = predictions.copy()
    normalized_predictions["person_id_l"] = normalized_predictions["person_id_l"].astype(int)
    normalized_predictions["person_id_r"] = normalized_predictions["person_id_r"].astype(int)
    normalized_predictions["_pair_key"] = [
        pair_key(left, right)
        for left, right in zip(
            normalized_predictions.person_id_l, normalized_predictions.person_id_r
        )
    ]
    normalized_predictions = (
        normalized_predictions.sort_values(
            ["_pair_key", "match_probability"], ascending=[True, False], kind="stable"
        )
        .drop_duplicates("_pair_key", keep="first")
        .reset_index(drop=True)
    )

    # Mandatory deterministic precedence.  It records the model score but never
    # allows that score to replace a rule/caution verdict.
    preceded = score_with_precedence(tiers, normalized_predictions)
    tier_rows = {
        pair_key(row.person_id_l, row.person_id_r): row._asdict()
        for row in preceded.itertuples(index=False)
    }
    prediction_rows = {
        row["_pair_key"]: row
        for row in normalized_predictions.to_dict(orient="records")
    }
    # `drop=False` keeps person_id inside each row, so the network diagnostics
    # can recognise the known fused inputs by id.
    people = {
        int(person_id): row
        for person_id, row in (
            spine.set_index("person_id", drop=False).to_dict(orient="index").items()
        )
    }
    partner_degrees = {
        person_id: len(set(row.get("partners_all") or []))
        for person_id, row in people.items()
    }
    firm_token_df = firm_token_df or {}
    records: list[dict[str, Any]] = []
    for key in sorted(set(tier_rows) | set(prediction_rows)):
        left_id, right_id = (int(value) for value in key.split(":"))
        left, right = people[left_id], people[right_id]
        tier_row = tier_rows.get(key)
        prediction = prediction_rows.get(key)
        tier = str(tier_row["tier"]) if tier_row is not None else None
        probability = _finite_float(prediction.get("match_probability")) if prediction else None
        match_weight = (
            _finite_float(tier_row.get("match_weight"))
            if tier_row is not None
            else (_finite_float(prediction.get("match_weight")) if prediction else None)
        )
        lanes = matched_blocking_lanes(prediction) if prediction else []
        waterfall = waterfall_contributions(prediction, model) if prediction else []
        reasons = (
            list(tier_row.get("reasons") or [])
            if tier_row is not None
            else _model_reasons(prediction, waterfall, lanes)
        )

        shared_contracts = [int(value) for value in _shared(left, right, "contracts")]
        shared_firms = _shared(left, right, "firms")
        shared_firm_words = _shared(left, right, "firm_tokens")
        shared_partners = [int(value) for value in _shared(left, right, "partners")]
        left_contracts = [int(value) for value in sorted(left.get("contracts") or [])]
        right_contracts = [int(value) for value in sorted(right.get("contracts") or [])]
        all_contracts = sorted(set(left_contracts) | set(right_contracts))
        source_pointers = [
            pointer
            for contract_id in all_contracts
            for pointer in source_index.get(contract_id, [])
        ]
        span = combined_span(pd.Series(left), pd.Series(right))
        career = {
            "left": {
                "first_year": _clean_scalar(left.get("first_year")),
                "last_year": _clean_scalar(left.get("last_year")),
            },
            "right": {
                "first_year": _clean_scalar(right.get("first_year")),
                "last_year": _clean_scalar(right.get("last_year")),
            },
            "combined_span_years": _clean_scalar(span),
        }
        roles = {
            "left": _clean_scalar(left.get("dominant_role")),
            "right": _clean_scalar(right.get("dominant_role")),
            "left_profile": {
                "gp": int(left.get("n_gp") or 0),
                "lp": int(left.get("n_lp") or 0),
            },
            "right_profile": {
                "gp": int(right.get("n_gp") or 0),
                "lp": int(right.get("n_lp") or 0),
            },
        }
        name_review = _name_review_evidence(left, right)
        context_fields = (
            "professions",
            "residences",
            "origins",
            "titles",
            "father_mother_titles",
            "grandfather_titles",
            "husband_titles",
            "economic_activities",
        )
        context = {
            field: {
                "left": _clean_scalar(left.get(field) or []),
                "right": _clean_scalar(right.get(field) or []),
                "shared": _shared(left, right, field),
            }
            for field in context_fields
        }
        concordance_reasons = _high_concordance_reasons(
            tier=tier,
            name_evidence=name_review,
            span=span,
            shared_firms=shared_firms,
            shared_partners=shared_partners,
        )
        network_diagnostics = _network_diagnostics(
            left,
            right,
            degrees=partner_degrees,
            excluded_contracts=set(shared_contracts),
        )
        firm_token_diagnostics = _firm_token_diagnostics(
            left,
            right,
            document_count=firm_document_count,
            document_frequency=firm_token_df,
        )
        evidence = {
            "names": {"left": _row_name(left), "right": _row_name(right)},
            "name_review": name_review,
            "appearance_counts": {
                "left": int(left.get("n_appearances") or 0),
                "right": int(right.get("n_appearances") or 0),
            },
            "shared": {
                "contract_ids": shared_contracts,
                "firms": shared_firms,
                "firm_words": shared_firm_words,
                "partner_ids": shared_partners,
            },
            "roles": roles,
            "career": career,
            "context_for_review": context,
            "network_diagnostics": network_diagnostics,
            "firm_token_diagnostics": firm_token_diagnostics,
        }
        records.append(
            {
                "pair_key": key,
                "person_id_l": left_id,
                "person_id_r": right_id,
                "deterministic_tier": tier,
                "precedence_verdict": (
                    str(tier_row.get("verdict") or tier) if tier_row is not None else "review"
                ),
                "source": "rule" if tier_row is not None else "splink",
                "match_probability": probability,
                "match_weight": match_weight,
                "review_score": _review_score(waterfall),
                "high_concordance": int(bool(concordance_reasons)),
                "concordance_reasons_json": concordance_reasons,
                "network_diagnostics_json": network_diagnostics,
                "firm_token_diagnostics_json": firm_token_diagnostics,
                "score_band": score_band(probability),
                "blocking_lanes_json": lanes,
                "reasons_json": reasons,
                "evidence_json": evidence,
                "shared_contract_ids_json": shared_contracts,
                "shared_firms_json": shared_firms,
                "shared_firm_words_json": shared_firm_words,
                "shared_partner_ids_json": shared_partners,
                "roles_json": roles,
                "career_span_json": career,
                "contract_pointers_json": {
                    "left": {"person_id": left_id, "contract_ids": left_contracts},
                    "right": {"person_id": right_id, "contract_ids": right_contracts},
                },
                "source_pointers_json": source_pointers,
                "waterfall_contributions_json": waterfall,
                "run_id": run_id,
            }
        )
    return assign_review_priorities(records)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        self.parent.setdefault(item, item)
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        self.parent[high] = low


def _projection_edge(row: Mapping[str, Any], threshold: float) -> bool:
    tier = row["deterministic_tier"]
    if tier in ("batch_ghost", "same_as_strong"):
        return True
    return (
        row["precedence_verdict"] == "review"
        and row["match_probability"] is not None
        and float(row["match_probability"]) >= threshold
    )


def build_group_projections(
    pairs: Iterable[Mapping[str, Any]],
    spine: pd.DataFrame,
    *,
    run_id: str,
    threshold: float = DEFAULT_GROUP_THRESHOLD,
) -> list[dict[str, Any]]:
    """Project connected pair suggestions, then guard the whole implied career."""
    pair_rows = {str(row["pair_key"]): dict(row) for row in pairs}
    union = _UnionFind()
    edge_keys: set[str] = set()
    for key, row in pair_rows.items():
        if _projection_edge(row, threshold):
            union.union(int(row["person_id_l"]), int(row["person_id_r"]))
            edge_keys.add(key)

    components: dict[int, list[int]] = {}
    for person_id in union.parent:
        components.setdefault(union.find(person_id), []).append(person_id)
    components = {
        root: sorted(members) for root, members in components.items() if len(members) >= 2
    }
    if not components:
        return []

    cluster_rows = [
        {"cluster_id": "g:" + ":".join(map(str, members)), "person_id": person_id}
        for members in components.values()
        for person_id in members
    ]
    guarded = flag_impossible_clusters(pd.DataFrame(cluster_rows), spine)
    guard_map = (
        guarded.set_index("cluster_id").to_dict(orient="index") if not guarded.empty else {}
    )
    indexed_spine = spine.set_index("person_id")
    years = indexed_spine[["first_year", "last_year"]]
    groups: list[dict[str, Any]] = []
    for members in sorted(components.values()):
        group_key = "g:" + ":".join(map(str, members))
        member_set = set(members)
        member_edges = sorted(
            key
            for key in edge_keys
            if {
                int(pair_rows[key]["person_id_l"]),
                int(pair_rows[key]["person_id_r"]),
            }
            <= member_set
        )
        matrix = []
        contradictions = []
        posthumous_conflicts = []
        for left, right in itertools.combinations(members, 2):
            key = pair_key(left, right)
            row = pair_rows.get(key)
            status = {
                "pair_key": key,
                "person_id_l": left,
                "person_id_r": right,
                "scored": row is not None,
                "is_projection_edge": key in edge_keys,
                "deterministic_tier": row.get("deterministic_tier") if row else None,
                "match_probability": row.get("match_probability") if row else None,
            }
            matrix.append(status)
            if row and row.get("deterministic_tier") == "distinct_strong":
                contradictions.append(key)
            conflict = posthumous_conflict(
                indexed_spine.loc[left], indexed_spine.loc[right]
            )
            if conflict:
                posthumous_conflicts.append((key, conflict))

        guard = guard_map.get(group_key, {})
        career_impossible = bool(guard.get("impossible", False))
        reasons: list[str] = []
        statuses: list[str] = []
        if career_impossible:
            statuses.append("career_impossible")
            reasons.append(
                f"the whole group implies a {int(guard['implied_career_years'])}-year "
                "career, longer than the 60-year guard"
            )
        if contradictions:
            statuses.append("deterministic_conflict")
            conflict_noun = (
                "a rule-based distinct pair"
                if len(contradictions) == 1
                else "rule-based distinct pairs"
            )
            reasons.append(
                f"the projected chain contains {conflict_noun}: "
                + ", ".join(contradictions)
            )
        if posthumous_conflicts:
            statuses.append("posthumous_conflict")
            reasons.append(
                "the projected chain contains heirs-before-later-living conflicts: "
                + ", ".join(
                    f"{key} ({years_[0]} before {years_[1]})"
                    for key, years_ in posthumous_conflicts
                )
            )
        if not statuses:
            statuses.append("clear")
            edge_noun = "pair suggestion" if len(member_edges) == 1 else "pair suggestions"
            reasons.append(
                f"{len(members)} records are connected by {len(member_edges)} "
                f"{edge_noun}; this is a review projection, not a merge"
            )
        window = years.reindex(members).dropna(how="any")
        first_year = int(window.first_year.min()) if len(window) else None
        last_year = int(window.last_year.max()) if len(window) else None
        implied = last_year - first_year if first_year is not None else None
        groups.append(
            {
                "group_key": group_key,
                "person_ids_json": members,
                "edge_pair_keys_json": member_edges,
                "pair_matrix_json": matrix,
                "size": len(members),
                "first_year": first_year,
                "last_year": last_year,
                "implied_career_years": implied,
                "career_guard": int(career_impossible),
                "guard_status": "+".join(statuses),
                "is_likely_same": int(
                    not career_impossible
                    and not contradictions
                    and not posthumous_conflicts
                ),
                "reasons_json": reasons,
                "run_id": run_id,
            }
        )
    return groups


def _run_id(run_timestamp: str, model_hash: str, source_fingerprint: str) -> str:
    payload = canonical_json(
        {
            "run_timestamp": run_timestamp,
            "model_sha256": model_hash,
            "source_input_fingerprint": source_fingerprint,
        }
    )
    return "pl-" + hashlib.sha256(payload.encode()).hexdigest()[:20]


def build_cache(
    *,
    main_db_path: Path,
    cache_path: Path,
    model_path: Path = MODEL_PATH,
    manifest_path: Path | None = None,
    source_links_path: Path | None = None,
    expected_recall: float | None = None,
    group_threshold: float = DEFAULT_GROUP_THRESHOLD,
    dry_run: bool = False,
    wait_for_lock: bool = False,
    run_timestamp: str | None = None,
) -> dict[str, Any]:
    """Run the complete offline batch under the repository maintenance lock."""
    if not 0 < group_threshold <= 1:
        raise ValueError("group_threshold must be greater than 0 and at most 1")
    manifest_path = manifest_path or manifest_path_for(model_path)
    if source_links_path is None:
        source_links_path = default_source_links_path()
    run_timestamp = run_timestamp or datetime.now(timezone.utc).isoformat()
    parsed_run_time = datetime.fromisoformat(run_timestamp.replace("Z", "+00:00"))
    if parsed_run_time.tzinfo is None:
        raise ValueError("run_timestamp must include a timezone")

    with maintenance_lock(wait=wait_for_lock):
        with closing(open_ro(main_db_path)) as connection:
            spine = load_person_spine(connection)
            firm_document_count, firm_token_df = firm_token_document_frequency(connection)
        frame = prepare_frame(spine)
        model, manifest = validate_model_bundle(
            model_path, manifest_path, frame, expected_recall=expected_recall
        )

        from splink import DuckDBAPI, Linker

        linker = Linker(frame, model, db_api=DuckDBAPI(), set_up_basic_logging=False)
        predictions = linker.inference.predict(
            threshold_match_probability=0.0
        ).as_pandas_dataframe()
        tiers = build_tiers(spine)
        fingerprint = source_input_fingerprint(frame)
        run_id = _run_id(run_timestamp, manifest["model_sha256"], fingerprint)
        source_index = load_source_pointer_index(source_links_path)
        source_pointer_fingerprint = (
            f"sha256:{sha256_path(source_links_path)}"
            if source_links_path and source_links_path.is_file()
            else ""
        )
        pairs = build_pair_records(
            spine,
            tiers,
            predictions,
            model,
            run_id=run_id,
            source_index=source_index,
            firm_document_count=firm_document_count,
            firm_token_df=firm_token_df,
        )
        groups = build_group_projections(
            pairs, spine, run_id=run_id, threshold=group_threshold
        )
        run = {
            "run_id": run_id,
            "run_timestamp": run_timestamp,
            "model_sha256": manifest["model_sha256"],
            "model_training_timestamp": manifest["training_timestamp"],
            "recall": float(manifest["recall"]),
            "model_seed": int(manifest["seed"]),
            "model_max_pairs": float(manifest["max_pairs"]),
            "source_input_fingerprint": fingerprint,
            "source_pointer_fingerprint": source_pointer_fingerprint,
            "comparison_rule_version": COMPARISON_RULE_VERSION,
            "deterministic_rule_version": DETERMINISTIC_RULE_VERSION,
            "diagnostic_rule_version": DIAGNOSTIC_RULE_VERSION,
            "group_threshold": float(group_threshold),
            "blocking_lane_count": len(BLOCKING_LANES),
        }
        if not dry_run:
            write_cache_atomic(cache_path, run, pairs, groups)
        return {
            **run,
            "pair_count": len(pairs),
            "model_prediction_count": len(predictions),
            "model_only_count": sum(row["deterministic_tier"] is None for row in pairs),
            "group_count": len(groups),
            "guarded_group_count": sum(row["career_guard"] for row in groups),
            "cache_path": None if dry_run else str(cache_path),
            "dry_run": dry_run,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="main.db path")
    parser.add_argument("--cache", type=Path, default=None, help="person_cache.db path")
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--source-links", type=Path, default=None)
    parser.add_argument(
        "--recall",
        type=float,
        default=None,
        help="require this recall assumption; a mismatch with the saved manifest is fatal",
    )
    parser.add_argument("--group-threshold", type=float, default=DEFAULT_GROUP_THRESHOLD)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run", action="store_true", help="score and print the report without writing a cache"
    )
    modes.add_argument(
        "--report", action="store_true", help="report an existing cache without reading main.db"
    )
    parser.add_argument("--wait", action="store_true", help="wait for the maintenance lock")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="  splink: %(message)s")
    main_path = (args.db or db_path()).resolve()
    cache_path = (args.cache or default_cache_path(main_path)).resolve()
    try:
        if args.report:
            report = cache_report(cache_path)
        else:
            report = build_cache(
                main_db_path=main_path,
                cache_path=cache_path,
                model_path=args.model.resolve(),
                manifest_path=args.manifest.resolve() if args.manifest else None,
                source_links_path=(
                    args.source_links.resolve() if args.source_links else None
                ),
                expected_recall=args.recall,
                group_threshold=args.group_threshold,
                dry_run=args.dry_run,
                wait_for_lock=args.wait,
            )
    except (FileNotFoundError, ModelBundleError, ValueError, RuntimeError) as exc:
        print(f"person-linkage batch refused: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
