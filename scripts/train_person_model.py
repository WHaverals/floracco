"""Train the person-linkage model and save it, refusing to save a broken one.

    uv run --extra linkage python scripts/train_person_model.py
    uv run --extra linkage python scripts/train_person_model.py --recall 0.8 --dry-run

Until 2026-08-29 this had no script: the shipped model was produced by a
hand-run notebook cell. That is a large part of why a malformed model survived
in the repo — there was no reproducible path to re-derive it, no check on what
was written, and Splink's own "u values not fully trained" warning was being
suppressed by the log level set in the dashboard script.

So this script does three things a notebook cell was not doing:

* it retrains from the modules, so the model is a function of the code;
* it runs `check_model_is_well_formed` and **refuses to save** if the model is
  not a valid Fellegi–Sunter model (every comparison's m and u must sum to 1);
* it lets Splink's warnings through, and prints the resulting counts, so the
  operator sees what they just built.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))   # run as a script, not an installed package


def resolved_max_pairs(row_count: int, requested: float | None) -> float:
    """Record the actual u-estimation budget, including the default census."""
    return (
        row_count * (row_count - 1) / 2
        if requested is None
        else float(requested)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recall", type=float, default=0.5,
                        help="assumed recall of the deterministic rules; an ASSUMPTION, "
                             "not a measurement, and the largest single lever on how many "
                             "pairs clear any threshold (default: 0.5)")
    parser.add_argument("--max-pairs", type=float, default=None,
                        help="pair budget for u estimation; default is every pair, which "
                             "makes u a census rather than a sample and cannot drift with "
                             "the seed")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--threshold", type=float, default=0.90,
                        help="clustering threshold, for the summary only")
    parser.add_argument("--dry-run", action="store_true",
                        help="train and report, but do not write the model file")
    parser.add_argument("--model-path", type=Path, default=None,
                        help="saved model path (default: docs/person_linkage/person_model.json)")
    parser.add_argument(
        "--bootstrap-manifest",
        action="store_true",
        help="validate an existing model and atomically add its manifest without retraining; "
             "requires --training-timestamp and is an explicit provenance attestation",
    )
    parser.add_argument(
        "--training-timestamp",
        help="historical ISO-8601 training time for --bootstrap-manifest; never inferred",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # Deliberately NOT silenced: the warning that would have caught the 2026-08-28
    # model said "u values not fully trained" on every predict() call.
    logging.basicConfig(level=logging.WARNING, format="  splink: %(message)s")

    from workflows.person_features import load_person_spine, open_ro
    from workflows.person_cache import (
        atomic_write_manifest,
        atomic_write_model_bundle,
        build_model_manifest,
        source_input_fingerprint,
    )
    from workflows.person_model import (
        MODEL_PATH, check_model_is_well_formed, flag_impossible_clusters,
        normalize_m_probabilities, prepare_frame, train_model,
    )

    model_path = (args.model_path or MODEL_PATH).resolve()
    with closing(open_ro()) as connection:
        spine = load_person_spine(connection)
    frame = prepare_frame(spine)
    print(f"spine: {len(spine):,} rows, {len(frame):,} classified as people")
    actual_max_pairs = resolved_max_pairs(len(frame), args.max_pairs)
    budget = (
        f"every pair ({actual_max_pairs:g}; u is a census)"
        if args.max_pairs is None
        else f"{actual_max_pairs:g}"
    )

    if args.bootstrap_manifest:
        if args.dry_run:
            print("--bootstrap-manifest and --dry-run cannot be used together")
            return 2
        if not args.training_timestamp:
            print(
                "--bootstrap-manifest requires --training-timestamp; the script will "
                "not invent or infer historical provenance"
            )
            return 2
        if not model_path.is_file():
            print(f"saved model is missing: {model_path}")
            return 2
        model_bytes = model_path.read_bytes()
        try:
            model = json.loads(model_bytes)
        except json.JSONDecodeError as exc:
            print(f"saved model is not valid JSON: {exc}")
            return 1
        faults = check_model_is_well_formed(model)
        if faults:
            print("Refusing to manifest a model that is not well formed:")
            for fault in faults:
                print(f"  FAULT  {fault}")
            return 1
        rules = model.get("blocking_rules_to_generate_predictions")
        if not isinstance(rules, list) or len(rules) != 5:
            print("Refusing to manifest a model without all five blocking lanes.")
            return 1
        try:
            manifest = build_model_manifest(
                model_bytes,
                recall=args.recall,
                seed=args.seed,
                max_pairs=actual_max_pairs,
                source_fingerprint=source_input_fingerprint(frame),
                training_timestamp=args.training_timestamp,
                origin="validated_existing_model",
            )
            atomic_write_manifest(model_path, manifest)
        except ValueError as exc:
            print(f"Refusing to write manifest: {exc}")
            return 2
        print(
            f"wrote {model_path.with_name(model_path.stem + '.manifest.json')} "
            "for the byte-identical validated model"
        )
        print(
            "origin=validated_existing_model: recall, seed, pair budget, source input, "
            "and historical timestamp are operator-attested; no retraining occurred"
        )
        return 0

    print(f"training with recall={args.recall}, max_pairs={budget}, seed={args.seed} ...")

    linker = train_model(frame, recall=args.recall, max_pairs=args.max_pairs,
                         seed=args.seed)

    model = linker.misc.save_model_to_json()
    faults = check_model_is_well_formed(model)
    print("\nwell-formedness:")
    if faults:
        for fault in faults:
            print(f"  FAULT  {fault}")
        print("\nRefusing to save a model that is not a valid Fellegi-Sunter model.")
        return 1
    for comparison in model["comparisons"]:
        levels = [l for l in comparison["comparison_levels"] if not l.get("is_null_level")]
        print(f"  {comparison['output_column_name']:<10} "
              f"m={sum(l['m_probability'] for l in levels):.6f}  "
              f"u={sum(l['u_probability'] for l in levels):.6f}  OK")

    predictions = linker.inference.predict(threshold_match_probability=0.0)
    scored = predictions.as_pandas_dataframe()
    clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
        linker.inference.predict(threshold_match_probability=min(args.threshold, 0.5)),
        threshold_match_probability=args.threshold).as_pandas_dataframe()
    sizes = clusters.groupby("cluster_id").size()
    multi = sizes[sizes > 1]
    impossible = flag_impossible_clusters(
        clusters[clusters.cluster_id.isin(set(multi.index))], spine)

    lam = model["probability_two_random_records_match"]
    n = len(frame)
    implied = lam * n * (n - 1) / 2
    print(f"\nresults at threshold {args.threshold}:")
    print(f"  pairs scored               : {len(scored):,}")
    print(f"  pairs >= {args.threshold}              : {int((scored.match_probability >= args.threshold).sum()):,}")
    print(f"  groups of 2+ records       : {len(multi):,} covering {int(multi.sum()):,} rows")
    print(f"  groups implying > a career : {int(impossible.impossible.sum()) if len(impossible) else 0}")
    # This comparison is deliberately NOT like-for-like, and the direction of
    # the mismatch matters: `implied` counts expected matches over ALL pairs,
    # while the posterior sum covers only the BLOCKED ones. Every unscored pair
    # carries a little posterior mass too, so the true contradiction is larger
    # than what prints here. Quote this as a floor, never as the figure.
    ratio = scored.match_probability.sum() / implied if implied else float("nan")
    print(f"\ncalibration (no labels needed, so run it every time):")
    print(f"  prior implies              : {implied:,.0f} true matches corpus-wide (all pairs)")
    print(f"  posteriors sum to          : {scored.match_probability.sum():,.0f} (blocked pairs only)")
    print(f"  overshoot                  : AT LEAST {ratio:.1f}x"
          f"{'   <-- a posterior cannot outrun its prior; this is a lower bound' if ratio > 2 else ''}")

    if args.dry_run:
        print("\n--dry-run: model and manifest not written")
        return 0
    model_bytes = (json.dumps(model, ensure_ascii=False, indent=2) + "\n").encode()
    trained_at = datetime.now(timezone.utc).isoformat()
    manifest = build_model_manifest(
        model_bytes,
        recall=args.recall,
        seed=args.seed,
        max_pairs=actual_max_pairs,
        source_fingerprint=source_input_fingerprint(frame),
        training_timestamp=trained_at,
    )
    atomic_write_model_bundle(model_path, model, manifest)
    try:
        display_path = model_path.relative_to(PROJECT_ROOT)
    except ValueError:
        display_path = model_path
    print(f"\nwrote {display_path}")
    print(f"wrote {model_path.with_name(model_path.stem + '.manifest.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
