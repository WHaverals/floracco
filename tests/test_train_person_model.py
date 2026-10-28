"""The training CLI must preserve the model-manifest provenance contract."""

from scripts.train_person_model import _parser, resolved_max_pairs


def test_default_u_budget_is_recorded_as_the_full_pair_census():
    assert resolved_max_pairs(5, None) == 10
    assert resolved_max_pairs(5, 7.5) == 7.5


def test_cli_exposes_recall_dry_run_and_explicit_manifest_bootstrap():
    parser = _parser()
    dry = parser.parse_args(["--recall", "0.8", "--dry-run"])
    assert dry.recall == 0.8
    assert dry.dry_run is True

    bootstrap = parser.parse_args(
        [
            "--bootstrap-manifest",
            "--training-timestamp",
            "2026-08-28T12:00:00+00:00",
            "--seed",
            "11",
        ]
    )
    assert bootstrap.bootstrap_manifest is True
    assert bootstrap.training_timestamp.endswith("+00:00")
    assert bootstrap.seed == 11
