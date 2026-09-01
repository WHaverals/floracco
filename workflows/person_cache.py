"""Storage and provenance helpers for the offline person-linkage batch.

``person_cache.db`` is derived and disposable.  This module deliberately knows
nothing about review decisions: it validates the saved model bundle, fingerprints
the exact model input, and atomically replaces a pair/group suggestion cache.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_MANIFEST_VERSION = 1
CACHE_SCHEMA_VERSION = 3
COMPARISON_RULE_VERSION = "person-linkage-comparisons-v1"
DETERMINISTIC_RULE_VERSION = "person-linkage-deterministic-v1"
DIAGNOSTIC_RULE_VERSION = "person-linkage-diagnostics-v1"
MODEL_MANIFEST_PATH = PROJECT_ROOT / "docs/person_linkage/person_model.manifest.json"

REQUIRED_MANIFEST_FIELDS = {
    "manifest_version",
    "model_sha256",
    "recall",
    "seed",
    "max_pairs",
    "source_input_fingerprint",
    "training_timestamp",
    "comparison_rule_version",
}


class ModelBundleError(RuntimeError):
    """The saved model and its required manifest cannot safely be used."""


def manifest_path_for(model_path: Path) -> Path:
    """Return ``person_model.manifest.json`` beside ``person_model.json``."""
    return model_path.with_name(f"{model_path.stem}.manifest.json")


def default_cache_path(main_db_path: Path) -> Path:
    raw = os.getenv("FLORACCO_PERSON_CACHE_PATH")
    return Path(raw).expanduser() if raw else main_db_path.parent / "person_cache.db"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value: Any) -> Any:
    """Convert pandas/numpy values to stable, strict-JSON values."""
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=lambda item: repr(item))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value) if value.is_integer() else value
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def source_input_fingerprint(frame: pd.DataFrame) -> str:
    """Fingerprint the exact, ordered Splink input without depending on CSV types."""
    if "person_id" not in frame.columns:
        raise ValueError("person linkage input has no person_id column")
    columns = list(frame.columns)
    ordered = frame.sort_values("person_id", kind="stable")
    digest = hashlib.sha256()
    digest.update(canonical_json({"columns": columns}).encode())
    digest.update(b"\n")
    for row in ordered.itertuples(index=False, name=None):
        digest.update(canonical_json(dict(zip(columns, row))).encode())
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def build_model_manifest(
    model_bytes: bytes,
    *,
    recall: float,
    seed: int,
    max_pairs: float,
    source_fingerprint: str,
    training_timestamp: str,
    origin: str = "trained",
) -> dict[str, Any]:
    """Build the companion manifest from explicit, auditable inputs."""
    if not 0 < recall <= 1:
        raise ValueError("recall must be greater than 0 and at most 1")
    if max_pairs <= 0:
        raise ValueError("max_pairs must be positive")
    if not source_fingerprint.startswith("sha256:") or len(source_fingerprint) != 71:
        raise ValueError("source_fingerprint must be a sha256:<64 hex digits> value")
    # Reject a timestamp without an offset.  A local wall-clock time is not
    # adequate provenance and is especially unsafe in a portable artifact.
    parsed = datetime.fromisoformat(training_timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("training_timestamp must include a timezone")
    return {
        "manifest_version": MODEL_MANIFEST_VERSION,
        "model_sha256": sha256_bytes(model_bytes),
        "recall": float(recall),
        "seed": int(seed),
        "max_pairs": float(max_pairs),
        "source_input_fingerprint": source_fingerprint,
        "training_timestamp": training_timestamp,
        "comparison_rule_version": COMPARISON_RULE_VERSION,
        "origin": origin,
    }


def _fsync_file(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_model_bundle(
    model_path: Path, model: Mapping[str, Any], manifest: Mapping[str, Any]
) -> None:
    """Replace model then manifest; any interrupted midpoint fails closed.

    Two filesystem names cannot be replaced in one POSIX operation.  Writing and
    fsyncing both temporary files first, then replacing the model before the
    hash-bearing manifest, guarantees that a concurrent batch either sees a
    matching pair or rejects it.  It never accepts a half-updated bundle.
    """
    model_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_path_for(model_path)
    model_bytes = (json.dumps(model, ensure_ascii=False, indent=2) + "\n").encode()
    expected = str(manifest.get("model_sha256") or "")
    actual = sha256_bytes(model_bytes)
    if expected != actual:
        raise ValueError(f"manifest model hash {expected!r} does not match model {actual}")

    temp_paths: list[Path] = []
    try:
        for target, payload in (
            (model_path, model_bytes),
            (
                manifest_path,
                (json.dumps(dict(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            ),
        ):
            fd, raw = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            temp = Path(raw)
            temp_paths.append(temp)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                _fsync_file(handle)
        os.replace(temp_paths[0], model_path)
        temp_paths.pop(0)
        os.replace(temp_paths[0], manifest_path)
        temp_paths.pop(0)
        _fsync_directory(model_path.parent)
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)


def atomic_write_manifest(model_path: Path, manifest: Mapping[str, Any]) -> None:
    """Atomically attach provenance to an existing, byte-identical model."""
    actual = sha256_path(model_path)
    expected = str(manifest.get("model_sha256") or "")
    if actual != expected:
        raise ValueError(f"manifest model hash {expected!r} does not match model {actual}")
    target = manifest_path_for(model_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(dict(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    fd, raw = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            _fsync_file(handle)
        os.replace(temp, target)
        _fsync_directory(target.parent)
    finally:
        temp.unlink(missing_ok=True)


def validate_model_bundle(
    model_path: Path,
    manifest_path: Path,
    frame: pd.DataFrame,
    *,
    expected_recall: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a well-formed model only when every manifest assertion matches."""
    if not model_path.is_file():
        raise ModelBundleError(f"saved model is missing: {model_path}")
    if not manifest_path.is_file():
        raise ModelBundleError(
            f"model manifest is missing: {manifest_path}; retrain, or use the "
            "explicit validated-existing-model bootstrap in train_person_model.py"
        )
    model_bytes = model_path.read_bytes()
    try:
        model = json.loads(model_bytes)
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelBundleError(f"cannot read model bundle: {exc}") from exc
    if not isinstance(model, dict) or not isinstance(manifest, dict):
        raise ModelBundleError("model and manifest must each be one JSON object")

    missing = sorted(REQUIRED_MANIFEST_FIELDS - manifest.keys())
    if missing:
        raise ModelBundleError(f"model manifest is missing fields: {', '.join(missing)}")
    if manifest["manifest_version"] != MODEL_MANIFEST_VERSION:
        raise ModelBundleError(
            f"unsupported model manifest version {manifest['manifest_version']!r}; "
            f"expected {MODEL_MANIFEST_VERSION}"
        )
    try:
        recall = float(manifest["recall"])
        seed = int(manifest["seed"])
        max_pairs = float(manifest["max_pairs"])
        trained_at = datetime.fromisoformat(
            str(manifest["training_timestamp"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ModelBundleError(f"model manifest contains invalid provenance values: {exc}") from exc
    if not 0 < recall <= 1:
        raise ModelBundleError("model manifest recall must be greater than 0 and at most 1")
    if max_pairs <= 0 or not math.isfinite(max_pairs):
        raise ModelBundleError("model manifest max_pairs must be a finite positive number")
    if trained_at.tzinfo is None:
        raise ModelBundleError("model manifest training_timestamp must include a timezone")
    if type(manifest["seed"]) is not int:
        raise ModelBundleError("model manifest seed must be an integer")
    source_fingerprint = str(manifest["source_input_fingerprint"])
    if not source_fingerprint.startswith("sha256:") or len(source_fingerprint) != 71:
        raise ModelBundleError("model manifest source_input_fingerprint is not SHA-256")
    actual_hash = sha256_bytes(model_bytes)
    if manifest["model_sha256"] != actual_hash:
        raise ModelBundleError(
            f"model SHA-256 mismatch: manifest has {manifest['model_sha256']}, file has {actual_hash}"
        )
    if manifest["comparison_rule_version"] != COMPARISON_RULE_VERSION:
        raise ModelBundleError(
            "model comparison/rule version does not match this batch: "
            f"{manifest['comparison_rule_version']!r} != {COMPARISON_RULE_VERSION!r}"
        )
    if expected_recall is not None and not math.isclose(
        recall, expected_recall, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ModelBundleError(
            f"requested recall {expected_recall:g} does not match the saved model's "
            f"{recall:g}; retrain the model at that recall"
        )
    fingerprint = source_input_fingerprint(frame)
    if manifest["source_input_fingerprint"] != fingerprint:
        raise ModelBundleError(
            "source-input fingerprint mismatch; the model was not trained for the "
            "current person spine"
        )

    from workflows.person_model import check_model_is_well_formed

    faults = check_model_is_well_formed(model)
    if faults:
        raise ModelBundleError("saved model is not well formed: " + "; ".join(faults))
    prior = _json_value(model.get("probability_two_random_records_match"))
    if not isinstance(prior, (int, float)) or not 0 < prior < 1:
        raise ModelBundleError(
            "saved model probability_two_random_records_match must be between 0 and 1"
        )
    rules = model.get("blocking_rules_to_generate_predictions")
    if not isinstance(rules, list) or len(rules) != 5:
        raise ModelBundleError("saved model must contain all five person-linkage blocking lanes")
    return model, manifest


def _json_column(value: Any) -> str:
    return canonical_json(value)


def write_cache_atomic(
    cache_path: Path,
    run: Mapping[str, Any],
    pairs: Iterable[Mapping[str, Any]],
    groups: Iterable[Mapping[str, Any]],
) -> None:
    """Write a complete SQLite cache in a sibling temp file, then replace."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    run = dict(run)
    run.setdefault("diagnostic_rule_version", DIAGNOSTIC_RULE_VERSION)
    pair_rows = sorted((dict(row) for row in pairs), key=lambda row: row["pair_key"])
    for row in pair_rows:
        row.setdefault("review_score", None)
        row.setdefault("review_rank", None)
        row.setdefault("review_percentile", None)
        row.setdefault("priority_band", None)
        row.setdefault("high_concordance", 0)
        row.setdefault("concordance_reasons_json", [])
        row.setdefault("network_diagnostics_json", {})
        row.setdefault("firm_token_diagnostics_json", {})
    group_rows = sorted((dict(row) for row in groups), key=lambda row: row["group_key"])
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{cache_path.name}.", suffix=".tmp", dir=cache_path.parent
    )
    os.close(fd)
    tmp_path = Path(raw_tmp)
    tmp_path.unlink(missing_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(tmp_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.executescript(
            """
            CREATE TABLE person_cache_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE cache_run (
              run_id TEXT PRIMARY KEY,
              run_timestamp TEXT NOT NULL,
              model_sha256 TEXT NOT NULL,
              model_training_timestamp TEXT NOT NULL,
              recall REAL NOT NULL,
              model_seed INTEGER NOT NULL,
              model_max_pairs REAL NOT NULL,
              source_input_fingerprint TEXT NOT NULL,
              source_pointer_fingerprint TEXT NOT NULL,
              comparison_rule_version TEXT NOT NULL,
              deterministic_rule_version TEXT NOT NULL,
              diagnostic_rule_version TEXT NOT NULL,
              group_threshold REAL NOT NULL,
              blocking_lane_count INTEGER NOT NULL CHECK (blocking_lane_count = 5)
            );

            CREATE TABLE person_pair_suggestion (
              pair_key TEXT PRIMARY KEY,
              person_id_l INTEGER NOT NULL,
              person_id_r INTEGER NOT NULL,
              deterministic_tier TEXT,
              precedence_verdict TEXT NOT NULL,
              source TEXT NOT NULL CHECK (source IN ('rule', 'splink')),
              match_probability REAL,
              match_weight REAL,
              review_score REAL,
              review_rank INTEGER,
              review_percentile REAL,
              priority_band TEXT,
              high_concordance INTEGER NOT NULL CHECK (high_concordance IN (0, 1)),
              concordance_reasons_json TEXT NOT NULL CHECK (json_valid(concordance_reasons_json)),
              network_diagnostics_json TEXT NOT NULL CHECK (json_valid(network_diagnostics_json)),
              firm_token_diagnostics_json TEXT NOT NULL CHECK (json_valid(firm_token_diagnostics_json)),
              score_band TEXT NOT NULL,
              blocking_lanes_json TEXT NOT NULL CHECK (json_valid(blocking_lanes_json)),
              reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json)),
              evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
              shared_contract_ids_json TEXT NOT NULL CHECK (json_valid(shared_contract_ids_json)),
              shared_firms_json TEXT NOT NULL CHECK (json_valid(shared_firms_json)),
              shared_firm_words_json TEXT NOT NULL CHECK (json_valid(shared_firm_words_json)),
              shared_partner_ids_json TEXT NOT NULL CHECK (json_valid(shared_partner_ids_json)),
              roles_json TEXT NOT NULL CHECK (json_valid(roles_json)),
              career_span_json TEXT NOT NULL CHECK (json_valid(career_span_json)),
              contract_pointers_json TEXT NOT NULL CHECK (json_valid(contract_pointers_json)),
              source_pointers_json TEXT NOT NULL CHECK (json_valid(source_pointers_json)),
              waterfall_contributions_json TEXT NOT NULL
                CHECK (json_valid(waterfall_contributions_json)),
              run_id TEXT NOT NULL REFERENCES cache_run(run_id),
              CHECK (person_id_l < person_id_r),
              CHECK (
                (source = 'splink' AND deterministic_tier IS NULL)
                OR source = 'rule'
              )
            );

            CREATE TABLE person_group_projection (
              group_key TEXT PRIMARY KEY,
              person_ids_json TEXT NOT NULL CHECK (json_valid(person_ids_json)),
              edge_pair_keys_json TEXT NOT NULL CHECK (json_valid(edge_pair_keys_json)),
              pair_matrix_json TEXT NOT NULL CHECK (json_valid(pair_matrix_json)),
              size INTEGER NOT NULL CHECK (size >= 2),
              first_year INTEGER,
              last_year INTEGER,
              implied_career_years INTEGER,
              career_guard INTEGER NOT NULL CHECK (career_guard IN (0, 1)),
              guard_status TEXT NOT NULL,
              is_likely_same INTEGER NOT NULL CHECK (is_likely_same IN (0, 1)),
              reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json)),
              run_id TEXT NOT NULL REFERENCES cache_run(run_id)
            );

            CREATE INDEX pair_worklist_idx
              ON person_pair_suggestion(precedence_verdict, priority_band, review_score);
            CREATE INDEX pair_concordance_idx
              ON person_pair_suggestion(high_concordance, review_score);
            CREATE INDEX pair_left_idx ON person_pair_suggestion(person_id_l);
            CREATE INDEX pair_right_idx ON person_pair_suggestion(person_id_r);
            """
        )
        run_columns = (
            "run_id",
            "run_timestamp",
            "model_sha256",
            "model_training_timestamp",
            "recall",
            "model_seed",
            "model_max_pairs",
            "source_input_fingerprint",
            "source_pointer_fingerprint",
            "comparison_rule_version",
            "deterministic_rule_version",
            "diagnostic_rule_version",
            "group_threshold",
            "blocking_lane_count",
        )
        with connection:
            connection.execute(
                f"INSERT INTO cache_run ({','.join(run_columns)}) "
                f"VALUES ({','.join('?' for _ in run_columns)})",
                tuple(run[column] for column in run_columns),
            )
            metadata = {
                "schema_version": str(CACHE_SCHEMA_VERSION),
                "run_id": str(run["run_id"]),
                "run_timestamp": str(run["run_timestamp"]),
                "model_sha256": str(run["model_sha256"]),
                "recall": str(run["recall"]),
                "source_input_fingerprint": str(run["source_input_fingerprint"]),
                "source_pointer_fingerprint": str(run["source_pointer_fingerprint"]),
                "comparison_rule_version": str(run["comparison_rule_version"]),
                "deterministic_rule_version": str(run["deterministic_rule_version"]),
                "diagnostic_rule_version": str(run["diagnostic_rule_version"]),
                "blocking_lane_count": str(run["blocking_lane_count"]),
                "pair_count": str(len(pair_rows)),
                "group_count": str(len(group_rows)),
            }
            connection.executemany(
                "INSERT INTO person_cache_meta (key, value) VALUES (?, ?)",
                sorted(metadata.items()),
            )

            pair_columns = (
                "pair_key",
                "person_id_l",
                "person_id_r",
                "deterministic_tier",
                "precedence_verdict",
                "source",
                "match_probability",
                "match_weight",
                "review_score",
                "review_rank",
                "review_percentile",
                "priority_band",
                "high_concordance",
                "concordance_reasons_json",
                "network_diagnostics_json",
                "firm_token_diagnostics_json",
                "score_band",
                "blocking_lanes_json",
                "reasons_json",
                "evidence_json",
                "shared_contract_ids_json",
                "shared_firms_json",
                "shared_firm_words_json",
                "shared_partner_ids_json",
                "roles_json",
                "career_span_json",
                "contract_pointers_json",
                "source_pointers_json",
                "waterfall_contributions_json",
                "run_id",
            )
            json_columns = {column for column in pair_columns if column.endswith("_json")}
            connection.executemany(
                f"INSERT INTO person_pair_suggestion ({','.join(pair_columns)}) "
                f"VALUES ({','.join('?' for _ in pair_columns)})",
                [
                    tuple(
                        _json_column(row[column]) if column in json_columns else row[column]
                        for column in pair_columns
                    )
                    for row in pair_rows
                ],
            )

            group_columns = (
                "group_key",
                "person_ids_json",
                "edge_pair_keys_json",
                "pair_matrix_json",
                "size",
                "first_year",
                "last_year",
                "implied_career_years",
                "career_guard",
                "guard_status",
                "is_likely_same",
                "reasons_json",
                "run_id",
            )
            group_json_columns = {column for column in group_columns if column.endswith("_json")}
            connection.executemany(
                f"INSERT INTO person_group_projection ({','.join(group_columns)}) "
                f"VALUES ({','.join('?' for _ in group_columns)})",
                [
                    tuple(
                        _json_column(row[column]) if column in group_json_columns else row[column]
                        for column in group_columns
                    )
                    for row in group_rows
                ],
            )
            connection.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")
        connection.close()
        connection = None

        check = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if integrity != "ok":
            raise RuntimeError(f"temporary person cache failed integrity check: {integrity}")
        os.replace(tmp_path, cache_path)
        _fsync_directory(cache_path.parent)
    finally:
        if connection is not None:
            connection.close()
        tmp_path.unlink(missing_ok=True)


def cache_report(cache_path: Path) -> dict[str, Any]:
    """Read a compact report without opening the corpus database."""
    if not cache_path.is_file():
        raise FileNotFoundError(cache_path)
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run = dict(connection.execute("SELECT * FROM cache_run").fetchone())
        tiers = {
            (row["deterministic_tier"] or "model_only"): row["n"]
            for row in connection.execute(
                """
                SELECT deterministic_tier, COUNT(*) AS n
                FROM person_pair_suggestion
                GROUP BY deterministic_tier
                """
            )
        }
        run["pair_count"] = connection.execute(
            "SELECT COUNT(*) FROM person_pair_suggestion"
        ).fetchone()[0]
        run["group_count"] = connection.execute(
            "SELECT COUNT(*) FROM person_group_projection"
        ).fetchone()[0]
        run["guarded_group_count"] = connection.execute(
            "SELECT COUNT(*) FROM person_group_projection WHERE career_guard = 1"
        ).fetchone()[0]
        run["tiers"] = tiers
        return run
    finally:
        connection.close()
