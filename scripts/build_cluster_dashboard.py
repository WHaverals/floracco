"""Build Splink's cluster studio dashboard over the person-linkage results.

Every proposed group — that is, every cluster containing more than one person
row — is included; single-row clusters are omitted because they contain no
proposed link to look at.

    uv run --extra linkage python scripts/build_cluster_dashboard.py

Loads the saved model (docs/person_linkage/person_model.json) rather than
retraining, and writes a self-contained HTML file that opens in any browser
with no internet connection.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))   # run as a script, not an installed package
OUT_PATH = PROJECT_ROOT / "data/derived/person_linkage/cluster_dashboard.html"
THRESHOLD = 0.90


def main() -> int:
    # NOT silenced. This line used to read `setLevel(logging.ERROR)`, which
    # swallowed Splink's "u values not fully trained" warning on every single
    # predict() call — the one warning that would have revealed that the model
    # being drawn here was malformed. A dashboard is a claim about the data;
    # suppressing the engine's own doubts about it is not a tidiness measure.
    logging.basicConfig(level=logging.WARNING, format="  splink: %(message)s")
    from splink import DuckDBAPI, Linker

    from workflows.person_features import load_person_spine, open_ro
    from workflows.person_model import MODEL_PATH, check_model_is_well_formed, prepare_frame

    faults = check_model_is_well_formed(json.loads(MODEL_PATH.read_text()))
    if faults:
        print("Refusing to build a dashboard from a malformed model:")
        for fault in faults:
            print(f"  FAULT  {fault}")
        print("Retrain with: uv run --extra linkage python scripts/train_person_model.py")
        return 1

    spine = load_person_spine(open_ro())
    linker = Linker(prepare_frame(spine), str(MODEL_PATH), db_api=DuckDBAPI(),
                    set_up_basic_logging=False)
    predictions = linker.inference.predict(threshold_match_probability=0.5)
    clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
        predictions, threshold_match_probability=THRESHOLD)

    frame = clusters.as_pandas_dataframe()
    sizes = frame.groupby("cluster_id").size()
    # Biggest groups first: a group of five joins four claims at once, so it is
    # both the most consequential to check and the likeliest over-merge. In
    # cluster-id order those sit scattered among 200-odd pairs.
    multi = sizes[sizes > 1].sort_values(ascending=False, kind="stable")

    # The dashboard builds its dropdown by iterating a JSON object keyed on
    # cluster id. JavaScript reorders integer-like keys into ascending numeric
    # order no matter what order we hand them over in, so a bare numeric id
    # cannot be sorted by anything else. Prefixing the rank makes the key a
    # plain string, which preserves our order; the real id stays after the dash
    # and in the label.
    rank = {cluster_id: i for i, cluster_id in enumerate(multi.index)}
    keyed = frame.assign(cluster_id=frame["cluster_id"].map(
        lambda c: f"g{rank[c]:03d}-{c}" if c in rank else f"x-{c}"))
    clusters = linker.table_management.register_table(
        keyed, "clusters_ranked", overwrite=True)

    # Factual labels only: id, number of records, and the years they span, so a
    # reader can navigate the list without the label asserting anything. Whether
    # a group is fully linked or a chain is left to the graph, which draws it —
    # a count here would contradict the picture, since the graph's edge filter
    # starts at the prediction floor rather than at THRESHOLD.
    years = spine.set_index("person_id")[["first_year", "last_year"]]
    ids, names = [], []
    for cluster_id, count in multi.items():
        members = frame.loc[frame.cluster_id == cluster_id, "person_id"]
        window = years.reindex(members).dropna()
        span = ""
        if len(window):
            span = f" · {int(window['first_year'].min())}–{int(window['last_year'].max())}"
        ids.append(f"g{rank[cluster_id]:03d}-{cluster_id}")
        names.append(f"{cluster_id} · {count} records{span}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    linker.visualisations.cluster_studio_dashboard(
        predictions, clusters, str(OUT_PATH),
        cluster_ids=ids, cluster_names=names, overwrite=True,
    )
    print(f"wrote {OUT_PATH.relative_to(PROJECT_ROOT)} "
          f"({OUT_PATH.stat().st_size / 1024:.0f} KB)")
    print(f"  {len(multi):,} groups of 2+ records, covering {int(multi.sum()):,} person rows")
    print(f"  clustered at match probability >= {THRESHOLD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
