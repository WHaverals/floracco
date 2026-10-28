"""No emoji in the review platform's text (WH, 2026-10-26: "no emojis! ever!").

Scans the React sources and the server modules whose strings reach the screen.
Plain typographic marks the interface uses as labels (✎ ✕ ✓ ⚑ ▸ ▾ → ≠) are
not emoji and stay; the pictographs below, and the two marks that browsers draw
as coloured emoji (⚠ and ↩), fail the test.

    uv run --with pytest pytest tests/test_no_emoji.py
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Pictographs (U+1F000–U+1FAFF), the emoji variation selector, and the two
# symbols that render as colour emoji on common systems.
EMOJI = re.compile("[\U0001F000-\U0001FAFF️⚠↩]")

SOURCES = [
    *sorted((ROOT / "apps/review/src").rglob("*.tsx")),
    *sorted((ROOT / "apps/review/src").rglob("*.ts")),
    ROOT / "workflows/review_server.py",
    ROOT / "workflows/data_quality.py",
    ROOT / "workflows/jewish_review.py",
    ROOT / "workflows/search_index.py",
]


def test_no_emoji_in_interface_text() -> None:
    hits = [
        f"{path.relative_to(ROOT)}:{n}: {line.strip()[:80]}"
        for path in SOURCES
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if EMOJI.search(line)
    ]
    assert hits == [], "Emoji found:\n" + "\n".join(hits)
