"""The deterministic tiers: what a rule may decide, and what it may never.

Each test pins a judgement from `docs/person_linkage/research.md`. The central
one is transitivity: a rule that can call A=B and B=C while calling A≠C is not a
rule, and the Torrigiani family is the corpus's own proof of it.

    uv run --with pytest --extra linkage pytest tests/test_person_tiers.py
"""

from __future__ import annotations

import pandas as pd
import pytest

from workflows.person_tiers import (
    MAX_CAREER_YEARS, batch_offsets, classify_pair, combined_span,
    posthumous_conflict, same_recorded_chain, temporal_gap,
)


def person(person_id, first="luca", patronymic="raffaello", grandfather="luca",
           last="torrigiani", first_year=None, last_year=None, appearances=1,
           nickname=None, first_posthumous_year=None):
    """A spine-shaped row, with only the fields the tiers read."""
    return pd.Series({
        "person_id": person_id, "first_name": first.title(), "last_name": last.title(),
        "first_norm": first, "patronymic_norm": patronymic,
        "grandfather_norm": grandfather, "last_norm": last,
        "first_year": first_year, "last_year": last_year,
        "first_posthumous_year": first_posthumous_year,
        "n_appearances": appearances, "nickname": nickname,
    })


# The real family: three rows sharing the chain "Luca di Raffaello di Luca".
TORRIGIANI_761 = person(761, first_year=1546, last_year=1549)
TORRIGIANI_12307 = person(12307, first_year=1572, last_year=1595)
TORRIGIANI_12308 = person(12308, first_year=1638, last_year=1638)


def tier(a, b, shared=()):
    return classify_pair(a, b, shared)["tier"]


# ---------------------------------------------------------------------------
# The measure itself
# ---------------------------------------------------------------------------

def test_combined_span_is_the_implied_career_not_the_gap():
    # 1546-1549 and 1572-1595: only 23 years apart, but 49 years of career
    assert temporal_gap(TORRIGIANI_761, TORRIGIANI_12307) == 23
    assert combined_span(TORRIGIANI_761, TORRIGIANI_12307) == 49


def test_verdicts_are_transitive_on_the_torrigiani_family():
    # Judged on GAPS this family breaks the rule: 761≈12307 (23y) and
    # 12307≈12308 (43y) would both pass, yet 761≈12308 (89y) cannot. Judged on
    # the implied career the three verdicts agree, because the measure is
    # monotone: merging more rows can only lengthen the life.
    assert tier(TORRIGIANI_761, TORRIGIANI_12307) == "same_as_strong"     # 49 years
    assert tier(TORRIGIANI_12307, TORRIGIANI_12308) == "distinct_strong"  # 66 years
    assert tier(TORRIGIANI_761, TORRIGIANI_12308) == "distinct_strong"    # 92 years
    assert temporal_gap(TORRIGIANI_12307, TORRIGIANI_12308) < MAX_CAREER_YEARS  # the trap


def test_undated_rows_are_never_vetoed_by_time():
    undated = person(9999, first_year=None, last_year=None)
    assert combined_span(TORRIGIANI_761, undated) is None
    assert tier(TORRIGIANI_761, undated) in ("review", "batch_ghost")


# ---------------------------------------------------------------------------
# What a rule may never decide
# ---------------------------------------------------------------------------

def test_co_appearance_is_always_a_caution_never_a_verdict():
    # Two same-named rows on one contract are the SAME man entered twice 65% of
    # the time (research.md §5) — so this can neither merge nor split by rule,
    # however perfect the other evidence looks.
    a = person(818, first="giovanni", patronymic="jacopo", grandfather="simone",
               last="corsi", first_year=1569, last_year=1569)
    b = person(6692, first="giovanni", patronymic="jacopo", grandfather="simone",
               last="corsi", first_year=1569, last_year=1569)
    assert tier(a, b) == "same_as_strong"          # identical chain, one year
    assert tier(a, b, shared=[806]) == "caution_coappearance"   # ...but they co-sign
    verdict = classify_pair(a, b, [806])
    assert "806" in verdict["reasons"][0]           # the reviewer gets the contract


def test_grandfather_conflict_is_a_caution_not_a_veto():
    # Contradictory grandfathers can mean cousins — or one ward keyed twice by
    # two clerks (persons 11458/12322). Never decide it by rule.
    a = person(11458, grandfather="giovanni", first_year=1607, last_year=1641)
    b = person(12322, grandfather="simone", first_year=1607, last_year=1609)
    assert tier(a, b) == "caution_gf_conflict"


def test_posthumous_conflict_routes_to_source_review_after_stronger_guards():
    deceased = person(
        6736, first="carlo", last="rinuccini",
        first_year=1652, last_year=1652, first_posthumous_year=1657,
    )
    later = person(
        5864, first="carlo", last="rinuccini",
        first_year=1654, last_year=1663,
    )
    verdict = classify_pair(deceased, later)
    assert verdict["tier"] == "caution_posthumous_conflict"
    assert "1657" in verdict["reasons"][0] and "1663" in verdict["reasons"][0]
    assert classify_pair(deceased, later, shared_contracts=[9])["tier"] == (
        "caution_coappearance"
    )


def test_posthumous_conflict_precedes_grandfather_conflict_and_not_span_veto():
    deceased = person(
        1, grandfather="piero", first_year=1600, last_year=1600,
        first_posthumous_year=1610,
    )
    later = person(
        2, grandfather="cosimo", first_year=1605, last_year=1620,
    )
    assert tier(deceased, later) == "caution_posthumous_conflict"
    far_later = person(
        3, grandfather="cosimo", first_year=1680, last_year=1690,
    )
    assert tier(deceased, far_later) == "distinct_strong"


def test_the_capponi_span_veto_fires_before_the_posthumous_branch():
    # Real ids from research.md §1: person 3607 "Piero Capponi" (no patronymic,
    # proven to hold at least three men) against person 3809. Both branches
    # have a case here — heirs from 1611 precede living activity through 1728 —
    # but a 128-year combined span is not a life, and it is the PRECEDENCE,
    # not the posthumous rule, that must decide: the caution lane exists to
    # route plausible identities to the sources, not to soften a refusal.
    capponi_3607 = person(3607, first="piero", patronymic="", grandfather="",
                          last="capponi", first_year=1600, last_year=1613,
                          first_posthumous_year=1611)
    capponi_3809 = person(3809, first="piero", patronymic="", grandfather="",
                          last="capponi", first_year=1673, last_year=1728)
    assert combined_span(capponi_3607, capponi_3809) == 128
    assert posthumous_conflict(capponi_3607, capponi_3809) == (1611, 1728)
    assert tier(capponi_3607, capponi_3809) == "distinct_strong"


def test_posthumous_boundary_and_missing_dates_do_not_create_a_caution():
    deceased = person(
        1, first_year=1600, last_year=1605, first_posthumous_year=1610,
    )
    same_year = person(2, first_year=1608, last_year=1610)
    undated = person(3, first_year=None, last_year=None)
    assert tier(deceased, same_year) != "caution_posthumous_conflict"
    assert tier(deceased, undated) != "caution_posthumous_conflict"


# ---------------------------------------------------------------------------
# What a rule may decide
# ---------------------------------------------------------------------------

def test_impossible_careers_are_refused():
    a = person(33, first="antonio", last="lenzi", first_year=1445, last_year=1445)
    b = person(4922, first="antonio", last="lenzi", first_year=1751, last_year=1751)
    verdict = classify_pair(a, b)
    assert verdict["tier"] == "distinct_strong"
    assert verdict["combined_span"] == 306
    assert "306-year career" in verdict["reasons"][0]


def test_explicit_generational_markers_separate_people():
    # The corpus's only explicit statement that two same-named men differ.
    elder = person(9753, first="giovanni", last="udny", nickname="Senior",
                   first_year=1780, last_year=1785)
    younger = person(9752, first="giovanni", last="udny", nickname="Junior",
                     first_year=1786, last_year=1790)
    assert tier(elder, younger) == "distinct_strong"
    # ...but a marker on only one side decides nothing
    assert tier(elder, person(9999, first="giovanni", last="udny",
                              first_year=1786, last_year=1790)) != "distinct_strong"


def test_batch_ghosts_are_recognised():
    # A row with an identical full chain that never appears anywhere, minted
    # beside one that does: a data-entry artefact, not a second person.
    real = person(11849, first="giovanni", patronymic="bartolomeo",
                  grandfather="piero", last="davanzati", first_year=1580,
                  last_year=1585, appearances=3)
    ghost = person(11850, first="giovanni", patronymic="bartolomeo",
                   grandfather="piero", last="davanzati", appearances=0)
    verdict = classify_pair(real, ghost)
    assert verdict["tier"] == "batch_ghost"
    assert any("no appearances" in r for r in verdict["reasons"])
    # a distant id is not the same evidence — that one goes to review
    far = person(2, first="giovanni", patronymic="bartolomeo", grandfather="piero",
                 last="davanzati", appearances=0)
    assert classify_pair(far, real)["tier"] == "review"


# ---------------------------------------------------------------------------
# The second data-entry pass, recognised by its shape rather than by proximity
# ---------------------------------------------------------------------------

def test_a_repeated_id_offset_is_evidence_of_a_re_entered_block():
    # Proximity is only one shape a re-entry takes, and the weaker one. When a
    # whole block was keyed again, consecutive source ids reappear at a CONSTANT
    # offset: persons 275-278 — Piero, Giuliano, Niccolo and Giovanni, four
    # brothers "di Bartolomeo di Lorenzo" — each turn up 11,792 ids later. A
    # fixed window cannot reach that without swallowing everything in between.
    def pair(src, offset):
        return (person(src, first=f"n{src}", patronymic="bartolomeo",
                       grandfather="lorenzo", last="", appearances=0),
                person(src + offset, first=f"n{src}", patronymic="bartolomeo",
                       grandfather="lorenzo", last="", first_year=1451,
                       last_year=1451, appearances=1))
    block = [pair(275, 11792), pair(276, 11792)]
    offsets = batch_offsets(block)
    assert 11792 in offsets

    a, b = block[0]
    assert classify_pair(a, b, (), offsets)["tier"] == "batch_ghost"
    # ...and without that knowledge it stays in review, as before
    assert classify_pair(a, b)["tier"] == "review"


def test_a_lone_offset_is_not_a_block():
    # One pair at an unusual offset is a coincidence, not a data-entry pass.
    # Only a RECURRING offset says something about how the corpus was keyed.
    lone = [(person(500, first="x", last="", appearances=0),
             person(9000, first="x", last="", first_year=1500, last_year=1500))]
    assert batch_offsets(lone) == frozenset()


def test_ghost_lane_reaches_surname_less_people_but_same_as_strong_does_not():
    # 536 people have no surname recorded; for them "Piero di Bartolomeo di
    # Lorenzo" IS the whole name, and demanding a fourth part excluded that
    # entire population from the ghost lane. The lane can afford the weaker
    # chain because it ALSO requires id evidence. `same_as_strong` cannot, since
    # it asserts identity on the name alone — so it keeps the four-part test.
    ghost = person(275, first="piero", patronymic="bartolomeo",
                   grandfather="lorenzo", last="", appearances=0)
    real = person(277, first="piero", patronymic="bartolomeo",
                  grandfather="lorenzo", last="", first_year=1451,
                  last_year=1451, appearances=1)
    assert same_recorded_chain(ghost, real)
    assert classify_pair(ghost, real)["tier"] == "batch_ghost"

    # the same surname-less chain, both sides appearing: NOT same_as_strong
    live_a = person(275, first="piero", patronymic="bartolomeo",
                    grandfather="lorenzo", last="", first_year=1451,
                    last_year=1455, appearances=2)
    live_b = person(277, first="piero", patronymic="bartolomeo",
                    grandfather="lorenzo", last="", first_year=1460,
                    last_year=1465, appearances=2)
    assert classify_pair(live_a, live_b)["tier"] != "same_as_strong"

    # a three-part chain missing the GRANDFATHER is not specific enough either
    vague = person(275, first="piero", patronymic="bartolomeo",
                   grandfather="", last="", appearances=0)
    vague_real = person(277, first="piero", patronymic="bartolomeo",
                        grandfather="", last="", first_year=1451,
                        last_year=1451, appearances=1)
    assert not same_recorded_chain(vague, vague_real)


def test_the_ghost_lane_still_needs_exactly_one_ghost():
    # Two ghosts are not a duplicate of anything observable, and two live rows
    # are an ordinary identity question. The XOR is load-bearing.
    # Use a LARGE, REPEATED offset, so the test cannot pass merely because the
    # window filter dropped it — that is what let an earlier version of this
    # test survive a mutation relaxing the XOR to an OR.
    def ghost_ghost(src):
        return (person(src, first=f"n{src}", patronymic="bartolomeo",
                       grandfather="lorenzo", last="", appearances=0),
                person(src + 9000, first=f"n{src}", patronymic="bartolomeo",
                       grandfather="lorenzo", last="", appearances=0))
    two_ghosts = [ghost_ghost(300), ghost_ghost(301)]
    assert batch_offsets(two_ghosts) == frozenset(), (
        "a pair of ghosts twins nothing observable — it must not define a block")
    assert classify_pair(*two_ghosts[0], (), frozenset({9000}))["tier"] != "batch_ghost"

    # and two LIVE rows are an ordinary identity question, not a data artefact
    def live_live(src):
        return (person(src, first=f"m{src}", patronymic="bartolomeo",
                       grandfather="lorenzo", last="", first_year=1451,
                       last_year=1455, appearances=2),
                person(src + 9000, first=f"m{src}", patronymic="bartolomeo",
                       grandfather="lorenzo", last="", first_year=1460,
                       last_year=1465, appearances=2))
    assert batch_offsets([live_live(400), live_live(401)]) == frozenset()
