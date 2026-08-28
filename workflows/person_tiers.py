"""Deterministic tiers: the candidate pairs a rule can settle, and the labels.

Stage 1, step 4 of `docs/person_linkage/research.md`. Two jobs in one pass:

1. **Fast lanes for humans** — the pairs where a transparent rule already gives
   an answer a historian can check at a glance ("identical name chain, one row
   never appears anywhere, consecutive ids": a data-entry ghost), plus the pairs
   a rule can *refuse* ("identical name, 143 years apart").
2. **A validation set for the model** — the confident decisions are what the
   probabilistic stage is checked AGAINST. research.md §10 originally planned
   for them to be the label factory that estimates `m`; measuring them killed
   that plan, because the lanes yield 873 confident negatives but only ~17
   confident positives, far too few and far too one-sided to train six
   comparisons on. `person_model.train_model` therefore learns `m` by EM, and
   these tiers judge the result instead. Nothing here is a guess either way.

Everything is a *proposal*. No tier writes to the database, and the two lanes
that look most decisive are deliberately the ones that never auto-decide:

* **co-appearance** — two same-named rows on one contract look like proof of
  two people, and are the same man entered twice 65% of the time (§5). Caution
  lane, with the contract in hand, always.
* **grandfather conflict** — contradictory grandfathers can mean two cousins,
  or one ward double-keyed by two clerks (persons 11458/12322). Caution, never
  a veto.

The one rule allowed to be decisive against a match is time: the Florentine
custom of naming the eldest son after his grandfather reproduces whole name
chains every second generation, so an identical name half a century later is
evidence of *two* people (§2).
"""

from __future__ import annotations

import itertools
from collections import Counter
import sqlite3
from typing import Any, Iterable

import pandas as pd

# A working life. Person 1164 spans 69 years and is a known over-merge; the
# measured distribution puts only 5 people above 60 (research.md §10, C3).
MAX_CAREER_YEARS = 60
# Ids minted in the same data-entry pass sit in a narrow band (§1: the second
# pass is visible in the id ranges 11448-12330).
GHOST_ID_WINDOW = 12
# ...but proximity is only ONE of the two shapes a re-entry takes, and it is the
# weaker one. When a whole block of people was keyed again, consecutive source
# ids map to consecutive new ids at a CONSTANT OFFSET — persons 275, 276, 277,
# 278 all reappear 11,792 ids later, and 4753/4754 reappear 7,142 later. A fixed
# window cannot see that however wide you make it without also swallowing every
# unrelated pair in between; a repeated offset can, and is better evidence,
# because an offset shared by several pairs is a fact about the data-entry pass
# rather than about any one pair. Measured on the 23 blocked pairs that carry a
# full chain with exactly one ghost: the window alone reaches 9 of them, and the
# offset test lifts that to 15 without loosening what "minted together" means.
BATCH_OFFSET_MIN_PAIRS = 2

TIERS = (
    "batch_ghost",         # same_as: a row that never appears, twinning one that does
    "same_as_strong",      # same_as: full chain + compatible careers
    "distinct_strong",     # distinct: the temporal veto, explicit elder/younger
    "caution_coappearance",# human only: they share a contract
    "caution_gf_conflict", # human only: contradictory grandfathers
    "review",              # the gray middle → the probabilistic stage
)


def _window(row: Any) -> tuple[float, float] | None:
    if pd.isna(row.first_year) or pd.isna(row.last_year):
        return None
    return (float(row.first_year), float(row.last_year))


def temporal_gap(a: Any, b: Any) -> float | None:
    """Years between two careers: 0 if they overlap, None if either is undated."""
    wa, wb = _window(a), _window(b)
    if wa is None or wb is None:
        return None
    if wa[0] <= wb[1] and wb[0] <= wa[1]:
        return 0.0
    return max(wb[0] - wa[1], wa[0] - wb[1])


def combined_span(a: Any, b: Any) -> float | None:
    """The career one person would need if these two rows were one — first
    appearance of either to last appearance of either.

    This, not the gap between them, is the honest test. The Torrigiani family
    shows why: three rows share the chain *Luca di Raffaello di Luca*
    (761: 1546-49, 12307: 1572-95, 12308: 1638). Judged on gaps alone,
    761≈12307 (23y) and 12307≈12308 (43y) both look mergeable — yet 761≈12308
    (89y) is impossible, so the rule contradicts itself. Judged on combined
    span, the answers are 49 / 66 / 92 years: only the first is a life, and
    because the measure is monotone the verdicts can never disagree.
    """
    wa, wb = _window(a), _window(b)
    if wa is None or wb is None:
        return None
    return max(wa[1], wb[1]) - min(wa[0], wb[0])


def _chain(row: Any) -> tuple[str, str, str, str]:
    return (row.first_norm, row.patronymic_norm, row.grandfather_norm, row.last_norm)


def _generational_marker(value: str | None) -> str | None:
    """`Junior` / `Senior` / `il Vecchio` / `il giovane` — the corpus's only
    explicit statement that two same-named men are different men (§10)."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    for marker in ("junior", "senior", "il vecchio", "vecchio", "il giovane", "giovane"):
        if marker in text:
            return "younger" if "giovane" in marker or "junior" in marker else "elder"
    return None


def candidate_pairs(spine: pd.DataFrame) -> list[tuple[Any, Any]]:
    """Blocked pairs: same first+surname, or (for the surname-less) same
    first+patronymic. Only rows classified as `person` take part."""
    people = spine[spine.entity_kind == "person"]
    pairs: dict[tuple[int, int], tuple[Any, Any]] = {}

    def add_block(frame: pd.DataFrame, keys: list[str]) -> None:
        usable = frame[(frame[keys] != "").all(axis=1)]
        for _, group in usable.groupby(keys, sort=False):
            if len(group) < 2:
                continue
            for a, b in itertools.combinations(group.itertuples(), 2):
                key = (min(a.person_id, b.person_id), max(a.person_id, b.person_id))
                if key not in pairs:
                    pairs[key] = (a, b) if a.person_id < b.person_id else (b, a)

    add_block(people, ["first_norm", "last_norm"])
    add_block(people[people.last_norm == ""], ["first_norm", "patronymic_norm"])
    return list(pairs.values())


def same_recorded_chain(a: Any, b: Any) -> bool:
    """The recorded chains are identical and name three generations.

    Deliberately weaker than `full_chain`, and only ever used by the ghost lane:
    a **surname absent on both sides is allowed**. 536 people in this corpus have
    no surname recorded, and for them *Piero di Bartolomeo di Lorenzo* IS the
    whole name — demanding a fourth part excludes that entire population from a
    rule that should reach them. Persons 275-278 are exactly this: four
    consecutive surname-less rows, each re-keyed 11,792 ids later.

    `same_as_strong` keeps the stricter four-part test, because it asserts
    identity on the name alone. The ghost lane can afford the weaker one because
    it additionally requires the id evidence — proximity or a shared batch
    offset — which is what carries the claim.
    """
    chain_a, chain_b = _chain(a), _chain(b)
    if chain_a != chain_b:
        return False
    first, patronymic, grandfather, _surname = chain_a
    return bool(first and patronymic and grandfather)


def ghost_pair(a: Any, b: Any) -> bool:
    """An identical recorded chain where exactly one row never appears in any act."""
    return bool(same_recorded_chain(a, b)
                and ((a.n_appearances == 0) != (b.n_appearances == 0)))


def batch_offsets(pairs: Iterable[tuple[Any, Any]]) -> frozenset[int]:
    """Id offsets that recur among ghost pairs — the signature of a re-entered block.

    One pair sharing an offset with another is not a coincidence worth much on
    its own; several pairs sharing one are a fact about how the second data-entry
    pass was made. Only offsets beyond `GHOST_ID_WINDOW` are returned, since
    nearer ones are already covered and would only duplicate the reason string.
    """
    counts: Counter[int] = Counter()
    for a, b in pairs:
        if ghost_pair(a, b):
            counts[abs(a.person_id - b.person_id)] += 1
    return frozenset(offset for offset, n in counts.items()
                     if n >= BATCH_OFFSET_MIN_PAIRS and offset > GHOST_ID_WINDOW)


def classify_pair(a: Any, b: Any, shared_contracts: Iterable[int] = (),
                  known_batch_offsets: frozenset[int] = frozenset()) -> dict[str, Any]:
    """Assign one pair to a tier, with the reasons a reviewer will read.

    `known_batch_offsets` comes from `batch_offsets()` over the whole candidate
    set; it is what lets a pair be recognised as part of a re-entered block
    rather than merely an adjacent one. Absent it, the rule falls back to
    proximity alone and behaves exactly as it did before.
    """
    reasons: list[str] = []
    gap = temporal_gap(a, b)
    chain_a, chain_b = _chain(a), _chain(b)
    full_chain = all(chain_a) and chain_a == chain_b
    gf_conflict = bool(a.grandfather_norm and b.grandfather_norm
                       and a.grandfather_norm != b.grandfather_norm)

    span = combined_span(a, b)
    shared = sorted(shared_contracts)
    if shared:
        return {"tier": "caution_coappearance", "gap_years": gap, "combined_span": None,
                "reasons": [f"both appear on contract {shared[0]}"
                            + (f" (+{len(shared)-1} more)" if len(shared) > 1 else ""),
                            "same-name pairs on one contract are one man entered twice "
                            "65% of the time — read the act"],
                "shared_contracts": shared}

    marker_a, marker_b = _generational_marker(a.nickname), _generational_marker(b.nickname)
    if marker_a and marker_b and marker_a != marker_b:
        return {"tier": "distinct_strong", "gap_years": gap, "combined_span": None,
                "reasons": [f"recorded as {a.nickname!r} and {b.nickname!r} — "
                            "the act itself distinguishes them"],
                "shared_contracts": []}

    if span is not None and span > MAX_CAREER_YEARS:
        return {"tier": "distinct_strong", "gap_years": gap, "combined_span": span,
                "reasons": [f"as one person this would be a {int(span)}-year career "
                            f"({int(min(a.first_year, b.first_year))}-"
                            f"{int(max(a.last_year, b.last_year))}) — longer than a working "
                            "life; the naming custom repeats whole chains every second "
                            "generation"],
                "shared_contracts": []}

    if gf_conflict:
        return {"tier": "caution_gf_conflict", "gap_years": gap, "combined_span": span,
                "reasons": [f"grandfathers differ ({a.grandfather_norm} vs "
                            f"{b.grandfather_norm}) — cousins, or one ward keyed twice"],
                "shared_contracts": []}

    ghost_side = (a.n_appearances == 0) != (b.n_appearances == 0)
    offset = abs(a.person_id - b.person_id)
    in_block = offset in known_batch_offsets
    if same_recorded_chain(a, b) and ghost_side and (offset <= GHOST_ID_WINDOW or in_block):
        why = (f"person ids {a.person_id} and {b.person_id} sit {offset:,} apart — the same "
               f"gap as other identical-chain ghost pairs, so this block of people was "
               f"keyed a second time"
               if in_block else
               f"person ids {a.person_id} and {b.person_id} were minted together")
        return {"tier": "batch_ghost", "gap_years": gap, "combined_span": span,
                "reasons": ["identical full name chain",
                            "one row has no appearances at all", why],
                "shared_contracts": []}
    if full_chain and ghost_side:
        reasons.append("identical full name chain; one row never appears")
        return {"tier": "review", "gap_years": gap, "combined_span": span, "reasons": reasons, "shared_contracts": []}

    if full_chain and span is not None:
        return {"tier": "same_as_strong", "gap_years": gap, "combined_span": span,
                "reasons": ["identical full name chain (name, father, grandfather, surname)",
                            f"as one person: a {int(span)}-year career "
                            f"({int(min(a.first_year, b.first_year))}-"
                            f"{int(max(a.last_year, b.last_year))}) — a plausible life"],
                "shared_contracts": []}

    if a.patronymic_norm and b.patronymic_norm and a.patronymic_norm != b.patronymic_norm:
        reasons.append("different fathers recorded")
    if gap is None:
        reasons.append("at least one side has no dated appearance")
    elif gap == 0:
        reasons.append("careers overlap")
    else:
        reasons.append(f"careers {int(gap)} years apart")
    return {"tier": "review", "gap_years": gap, "combined_span": span, "reasons": reasons, "shared_contracts": []}


def build_tiers(spine: pd.DataFrame) -> pd.DataFrame:
    """Every blocked pair, tiered, with reasons. One row per pair."""
    contracts = dict(zip(spine.person_id, spine.contracts))
    pairs = candidate_pairs(spine)
    # One pass to learn how the second data-entry pass was shaped, then classify.
    offsets = batch_offsets(pairs)
    records = []
    for a, b in pairs:
        shared = set(contracts.get(a.person_id, [])) & set(contracts.get(b.person_id, []))
        verdict = classify_pair(a, b, shared, offsets)
        records.append({
            "person_id_l": a.person_id, "person_id_r": b.person_id,
            "name": " ".join(x for x in (a.first_name, a.last_name)
                             if x and not pd.isna(x)).strip(),
            "tier": verdict["tier"], "gap_years": verdict["gap_years"],
            "combined_span": verdict.get("combined_span"),
            "reasons": verdict["reasons"], "shared_contracts": verdict["shared_contracts"],
        })
    frame = pd.DataFrame(records)
    return frame.sort_values(["tier", "person_id_l"]).reset_index(drop=True)


def training_labels(tiers: pd.DataFrame) -> pd.DataFrame:
    """The confident decisions, as labelled pairs.

    **Nothing calls this.** It was written for research.md §10's original plan of
    estimating `m` from the deterministic lanes, which measuring abandoned —
    they yield far too few positives (see the module docstring). Kept because
    the same shape is what Splink's `estimate_m_from_pairwise_labels` and its
    accuracy-analysis charts consume, and the review lane will produce enough
    confirmed pairs to make one of those worth running.

    Positives are the tiers a rule settles as the same person; negatives are the
    temporal/explicit vetoes. The caution lanes are deliberately absent: they are
    the pairs we know we cannot label without reading the document.
    """
    positive = tiers[tiers.tier.isin(["batch_ghost", "same_as_strong"])]
    negative = tiers[tiers.tier == "distinct_strong"]
    return pd.concat([
        positive.assign(clerical_match_score=1.0),
        negative.assign(clerical_match_score=0.0),
    ])[["person_id_l", "person_id_r", "clerical_match_score", "tier", "reasons"]]
