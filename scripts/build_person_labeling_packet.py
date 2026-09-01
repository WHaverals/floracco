"""Freeze a stratified person-linkage packet for human labeling.

    uv run python scripts/build_person_labeling_packet.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from workflows.corrections_db import connect as connect_corrections
from workflows.corrections_db import person_review_events
from workflows.person_labeling import write_labeling_packet


def blind_label_packets() -> list[str]:
    """Packet ids that already carry blind labels in corrections.db.

    Regenerating a packet after labeling has started silently changes the
    sampling frame those labels were drawn from, which invalidates their
    inclusion probabilities and inverse-probability weights — so overwriting
    must be a deliberate, forced act, not a rerun.
    """
    connection = connect_corrections()
    try:
        events = person_review_events(connection)
    finally:
        connection.close()
    return sorted(
        {
            str(
                (event["evidence_snapshot"].get("labeling") or {}).get("packet_id")
                or "unknown packet"
            )
            for event in events
            if (event.get("evidence_snapshot") or {}).get("labeling")
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=PROJECT_ROOT / "data/sqlite/person_cache.db",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data/derived/person-linkage/labeling_packet_v3.jsonl",
    )
    parser.add_argument("--size", type=int, default=150)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--force",
        action="store_true",
        help="write a new packet even though blind labels already exist",
    )
    args = parser.parse_args(argv)
    labeled_packets = blind_label_packets()
    if labeled_packets and not args.force:
        print(
            "labeling-packet build refused: blind labels are already recorded "
            f"against {', '.join(labeled_packets)}; a new packet would change "
            "the sampling frame those labels were drawn from. Pass --force to "
            "overrule."
        )
        return 2
    report = write_labeling_packet(
        args.cache.resolve(),
        args.output.resolve(),
        target=args.size,
        seed=args.seed,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
