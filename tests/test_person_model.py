"""The Splink specification: the parts a refactor could silently invert.

Training itself is exercised in the notebook (it needs the real corpus and
takes minutes); these tests pin the *specification* — the data contract Splink
depends on, and the two places where we deliberately overrule the algorithm.

    uv run --with pytest --extra linkage pytest tests/test_person_model.py
"""

from __future__ import annotations

import json
from copy import deepcopy

import pandas as pd
import pytest

from workflows.person_model import (
    MAX_CAREER_YEARS, comparisons, em_training_rules, prepare_frame,
    score_with_precedence, settings,
)


def _spine_row(**kw):
    base = {
        "person_id": 1, "entity_kind": "person", "full_name_norm": "piero strozzi",
        "first_norm": "piero", "last_norm": "strozzi", "patronymic_norm": "",
        "grandfather_norm": "", "first_year": 1550.0, "last_year": 1560.0,
        "partners": [], "firm_tokens": [], "firms": [], "contracts": [],
        "dominant_role": "gp",
        "husband_first_norm": "", "husband_last_norm": "",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# The data contract Splink relies on
# ---------------------------------------------------------------------------

def test_missing_values_reach_splink_as_null_not_empty_string():
    # Splink gives null levels zero weight by design (the MAR treatment). An
    # empty string would instead MATCH every other empty string, so two people
    # who each lack a patronymic would read as agreeing — in a corpus where 43%
    # lack one, that is a false-match engine.
    frame = prepare_frame(pd.DataFrame([_spine_row(), _spine_row(person_id=2)]))
    for column in ("patronymic_norm", "grandfather_norm", "husband_last_norm"):
        assert frame[column].isna().all(), column
        assert not (frame[column] == "").any(), column


def test_unknown_role_is_null_but_empty_networks_stay_arrays():
    frame = prepare_frame(pd.DataFrame([_spine_row(dominant_role="unknown")]))
    assert frame["dominant_role"].isna().all()
    # empty arrays are real evidence-free arrays, not missing values
    assert frame["partners"].iloc[0] == [] and frame["firm_tokens"].iloc[0] == []


def test_non_people_never_reach_the_model():
    rows = [_spine_row(person_id=1),
            _spine_row(person_id=2, entity_kind="institution"),
            _spine_row(person_id=3, entity_kind="collective")]
    assert list(prepare_frame(pd.DataFrame(rows)).person_id) == [1]


# ---------------------------------------------------------------------------
# Where we overrule the algorithm on purpose
# ---------------------------------------------------------------------------

def test_father_son_signature_cannot_become_positive_evidence():
    # Left free, EM learns this level as +8 bits FOR a match (measured
    # 2026-08-28): it cannot see that a man is not his own father, only that
    # such pairs share a surname and a network. Its m is therefore fixed below
    # any plausible u so the level can only ever argue against a match.
    lineage = next(c for c in comparisons()
                   if c._output_column_name == "lineage").get_comparison("duckdb")
    level = next(l for l in lineage.comparison_levels
                 if "father-and-son" in (l.label_for_charts or ""))
    assert level._fix_m_probability is True
    assert level._m_probability == pytest.approx(0.0001)


def test_em_sessions_free_the_lineage_comparison():
    # A comparison cannot be trained in a session whose blocking rule fixes any
    # column it reads, and `lineage` reads first_norm (via the father-and-son
    # level). Blocking only on first+surname therefore leaves the patronymic
    # chain — the most important comparison — silently untrained. The first
    # session must free it.
    first = em_training_rules()[0].get_blocking_rule("duckdb").blocking_rule_sql
    assert "last_norm" in first
    assert "first_norm" not in first
    assert "patronymic_norm" not in first


def test_rules_win_over_the_model():
    # Corsi 818/12322 score +6.3 from the model but are refused by the career
    # rule; the verdict must stay the rule's. A probabilistic score may rank
    # what is undecided — it may not resurrect what time has excluded.
    tiers = pd.DataFrame([
        {"person_id_l": 818, "person_id_r": 12322, "tier": "distinct_strong",
         "name": "Giovanni Corsi"},
        {"person_id_l": 1591, "person_id_r": 12025, "tier": "review",
         "name": "Giovanni Lanfranchi"},
    ])
    predictions = pd.DataFrame([
        {"person_id_l": 818, "person_id_r": 12322, "match_weight": 6.3},
        {"person_id_l": 12025, "person_id_r": 1591, "match_weight": 9.0},  # order flipped
    ])
    out = score_with_precedence(tiers, predictions).set_index("person_id_l")
    assert out.loc[818, "verdict"] == "distinct_strong"
    assert out.loc[818, "match_weight"] == 6.3          # recorded, not obeyed
    assert out.loc[1591, "verdict"] == "review"
    assert out.loc[1591, "match_weight"] == 9.0          # matched despite pair order


# ---------------------------------------------------------------------------
# The specification holds together
# ---------------------------------------------------------------------------

def test_settings_declare_the_comparisons_and_the_person_key():
    spec = settings().get_settings("duckdb")
    assert spec.column_info_settings.unique_id_column_name == "person_id"
    assert [c.output_column_name for c in spec.comparisons] == [
        "name", "lineage", "contemporaneity", "role", "husband"]


def test_time_and_network_stay_one_comparison():
    # THE assumption Fellegi-Sunter rests on: comparisons are independent GIVEN
    # match status, because their weights get multiplied. `career` and `network`
    # were separate comparisons and are not remotely independent — you cannot
    # share a business partner with someone who died before you were born.
    # Measured on 2.7M random pairs: P(share a partner | careers overlap) =
    # 4.76% against 0.12% when they do not overlap, a 40x ratio and +4.35 bits
    # of excess over independence. The model was paying that twice on its most
    # common high-scoring pattern. Splitting them again would silently
    # reintroduce it, so the merge is pinned here.
    names = [c._output_column_name for c in comparisons()]
    assert "contemporaneity" in names
    assert "career" not in names and "network" not in names, (
        "time and network must stay ONE comparison; separately, FS multiplies "
        "a dependence of about +4 bits as though it were independent evidence")

    merged = next(c for c in comparisons()
                  if c._output_column_name == "contemporaneity").get_comparison("duckdb")
    labels = [l.label_for_charts for l in merged.comparison_levels]
    # the joint events must be graded, strongest first
    assert (labels.index("contemporaries, 3+ shared partners")
            < labels.index("contemporaries, a shared partner")
            < labels.index("contemporaries, no shared network"))
    # and the span veto must still be inside the top level's own SQL
    top = next(l for l in merged.comparison_levels
               if l.label_for_charts == "contemporaries, 3+ shared partners")
    assert str(MAX_CAREER_YEARS) in top.sql_condition, (
        "the contemporaries levels must bound the implied career themselves; "
        "Splink evaluates them before any level that would restrain them")


def test_the_same_firm_is_the_strongest_network_evidence():
    # An accomandita is a fixed-term partnership that gets RENEWED: 353 firm
    # names in this corpus span 821 contracts. So the same people reappear
    # together act after act, and the firm NAME is a string — unlike `partners`,
    # which is keyed on person_id and therefore inherits the very duplication
    # the model exists to resolve. Luigi Capponi's two rows share no partner id
    # because his partner Alessandro is himself split in two.
    # Measured: pairs sharing an independent firm are 0.3% of those the rules
    # refuse against 5.4% of those they leave undecided — an 18x enrichment.
    merged = next(c for c in comparisons()
                  if c._output_column_name == "contemporaneity").get_comparison("duckdb")
    labels = [l.label_for_charts for l in merged.comparison_levels]
    assert "contemporaries, the same firm" in labels
    # it must outrank every weaker network signal, since first match wins
    assert (labels.index("contemporaries, the same firm")
            < labels.index("contemporaries, 3+ shared partners")
            < labels.index("contemporaries, shared firm only"))

    # ...and it must ALSO be reachable when careers abut rather than overlap,
    # which is the commonest shape of a split person. Without this the firm
    # bought nothing there: Bellacci 2734/2932 (1591 and 1592, same firm) sat
    # unchanged at 0.445, because two firm-name TOKENS already satisfied the
    # generic "shared network" level it fell into. With it, 0.873.
    assert "career <= 30 years, the same firm" in labels
    assert (labels.index("career <= 30 years, the same firm")
            < labels.index("career <= 30 years, shared network"))


def test_a_firm_named_after_the_person_is_not_evidence_about_them():
    # *Mazzeo Mazzei e compagni* is named for Mazzeo Mazzei, so any two records
    # of that name share it whether or not they are one man. Counting it would
    # be the `name` comparison wearing a hat — the same self-reference already
    # fixed for firm TOKENS.
    from workflows.person_features import _drop_self_referential_firms

    people = pd.DataFrame([
        {"first_norm": "mazzeo", "last_norm": "mazzei",
         "firms": ["mazzeo mazzei e compagni", "zanobi lippi e compagni"]},
    ])
    kept = _drop_self_referential_firms(people).iloc[0]
    assert "zanobi lippi e compagni" in kept, "a firm named for someone ELSE is the evidence"
    assert not any("mazzei" in f for f in kept), "a firm named for the person is the name twice"


def test_co_appearing_pairs_get_no_time_or_network_evidence():
    # Two rows on the SAME act share every other signatory mechanically and
    # their dates coincide by construction, so BOTH halves of this comparison
    # would restate the one fact that already routes the pair to a human.
    # Measured: of 20 pairs above p=0.90 sharing a contract, 19 lost their
    # entire partner overlap once that contract was excluded — two of them
    # edges holding the Corsi 818 over-merge together. `_partner_sets` used to
    # claim the tiers prevented this; they do not, because the tiers and the
    # model block independently and every blocked pair is scored.
    import duckdb

    merged = next(c for c in comparisons()
                  if c._output_column_name == "contemporaneity").get_comparison("duckdb")
    null_level = merged.comparison_levels[0]
    assert null_level.is_null_level
    assert "contracts_l" in null_level.sql_condition, (
        "co-appearance must make this comparison say nothing")

    con = duckdb.connect()
    def is_null(cl, cr):
        return con.execute(
            f"SELECT {null_level.sql_condition} FROM (SELECT "
            f"1550 AS first_year_l, 1560 AS last_year_l, 1550 AS first_year_r, 1560 AS last_year_r,"
            f" [1,2,3] AS partners_l, [1,2,3] AS partners_r,"
            f" ['a']::VARCHAR[] AS firms_l, ['a']::VARCHAR[] AS firms_r,"
            f" ?::BIGINT[] AS contracts_l, ?::BIGINT[] AS contracts_r)", [cl, cr]).fetchone()[0]

    assert is_null([7, 8], [8, 9]), "pairs sharing an act must fall to the null level"
    assert not is_null([7, 8], [9, 10]), "pairs sharing no act must keep their evidence"


def test_every_m_and_u_vector_sums_to_one():
    # The levels of one comparison partition the outcomes, so P(level | match)
    # must sum to 1 across them, and likewise for non-matches. The model shipped
    # on 2026-08-28 violated this on three of six comparisons (name 1.372,
    # lineage 0.863, husband 1.081) and nothing noticed, because every published
    # figure was derived from that same file. Renormalising moved a third of the
    # high-confidence pairs. This is the cheapest possible guard on the artifact.
    from workflows.person_model import MODEL_PATH, check_model_is_well_formed

    if not MODEL_PATH.exists():          # the model is a build product, not source
        pytest.skip(f"{MODEL_PATH} not built")
    faults = check_model_is_well_formed(json.loads(MODEL_PATH.read_text()))
    assert faults == [], "; ".join(faults)


def test_well_formedness_check_actually_catches_a_broken_model():
    # A guard that cannot fail is not a guard. Mutate a good model and confirm
    # each fault is reported.
    from workflows.person_model import check_model_is_well_formed

    model = {"comparisons": [{
        "output_column_name": "name",
        "comparison_levels": [
            {"is_null_level": True},
            {"label_for_charts": "exact", "m_probability": 0.6, "u_probability": 0.1},
            {"label_for_charts": "else", "m_probability": 0.4, "u_probability": 0.9},
        ],
    }]}
    assert check_model_is_well_formed(model) == []

    broken = deepcopy(model)
    broken["comparisons"][0]["comparison_levels"][1]["m_probability"] = 0.9   # sums to 1.3
    assert any("m_probability sums to" in f for f in check_model_is_well_formed(broken))

    missing = deepcopy(model)
    del missing["comparisons"][0]["comparison_levels"][1]["u_probability"]
    assert any("no u_probability" in f for f in check_model_is_well_formed(missing))


def test_a_collapsed_m_is_refused_even_though_it_sums_to_one():
    # Summing to 1 is necessary but nowhere near sufficient. EM drove the husband
    # "else" level to m=1.5e-14 — a -45.9 bit veto, enough to annihilate a pair
    # however strongly everything else agreed — and the vector still summed to
    # exactly 1.0, so the original check passed it. It sat on the ONE comparison
    # serving the 242 women, where a single scribal variant in a husband's
    # surname would have destroyed a genuine match with no appeal.
    from workflows.person_model import MIN_LEARNABLE_M, check_model_is_well_formed

    collapsed = {"comparisons": [{
        "output_column_name": "husband",
        "comparison_levels": [
            {"is_null_level": True},
            {"label_for_charts": "same husband", "m_probability": 0.5, "u_probability": 0.001},
            {"label_for_charts": "else", "m_probability": 1.5e-14, "u_probability": 0.999},
        ],
    }]}
    # it sums to 1.0, so the sum test alone would wave it through
    levels = collapsed["comparisons"][0]["comparison_levels"][1:]
    assert sum(l["m_probability"] for l in levels) == pytest.approx(0.5, abs=0.5)
    faults = check_model_is_well_formed(collapsed)
    assert any("collapsed estimate" in f for f in faults)

    # ...but the SAME tiny value is fine when we assert it on purpose: the
    # sibling and father-and-son signatures are deliberately minuscule.
    asserted = deepcopy(collapsed)
    asserted["comparisons"][0]["comparison_levels"][2]["fix_m_probability"] = True
    assert not any("collapsed estimate" in f for f in check_model_is_well_formed(asserted))


def test_u_is_exempt_because_it_is_estimated_on_a_census():
    # The asymmetry is load-bearing. `u` comes from every one of the ~64M pairs,
    # so a u of 5e-06 rests on hundreds of observations and is entirely credible.
    # `m` comes from EM against the handful of true matches the corpus holds.
    # An earlier version of this guard checked both and flagged four legitimate
    # u values — including the exact-name level, which is *supposed* to be rare.
    from workflows.person_model import check_model_is_well_formed

    tiny_u = {"comparisons": [{
        "output_column_name": "lineage",
        "comparison_levels": [
            {"is_null_level": True},
            {"label_for_charts": "father and grandfather agree",
             "m_probability": 0.01, "u_probability": 5.47e-06},
            {"label_for_charts": "else", "m_probability": 0.99, "u_probability": 0.999995},
        ],
    }]}
    assert not any("collapsed" in f for f in check_model_is_well_formed(tiny_u))


def test_husband_disagreement_argues_against_without_overruling_everything():
    # A husband-name disagreement is mild evidence against, not a veto. The real
    # reasons for one are all attested here: remarriage, the wife-to-widow
    # formula, and scribal variation in the husband's surname.
    husband = next(c for c in comparisons()
                   if c._output_column_name == "husband").get_comparison("duckdb")
    else_level = husband.comparison_levels[-1]
    assert else_level._fix_m_probability is True, (
        "EM cannot learn this level — it is non-null on 14 of 15,637 pairs")
    assert else_level._m_probability == pytest.approx(0.10)


def test_no_em_session_may_pin_the_name_comparison():
    # THE trap, and the one Splink cannot detect for us. It decides a comparison
    # is trainable by checking the columns that comparison READS, and `name`
    # reads only `full_name_norm`. A rule blocking on (first_norm, last_norm)
    # pins full_name_norm exactly without ever naming it, so Splink trains
    # `name` on an all-exact-match population, observes only its exact level,
    # and the cross-session median then breaks the vector. Assert that no
    # session fixes BOTH halves of the name at once.
    for rule in em_training_rules():
        sql = rule.get_blocking_rule("duckdb").blocking_rule_sql
        assert not ("first_norm" in sql and "last_norm" in sql), (
            f"blocking rule {sql!r} pins full_name_norm without naming it, which "
            f"silently corrupts the `name` comparison")


def test_spelling_level_prices_variation_without_folding_gender():
    # One scribe writes the father `pagolo`, another `paolo`. Exact equality
    # reads that as two different men — 36 pairs were being scored as a flat
    # contradiction over a single letter. This level lets EM price the evidence
    # (measured ~+4 bits: real, and properly weaker than the +7..+11 of an exact
    # chain agreement) instead of us hand-curating a table of "same name" rulings.
    #
    # The final-vowel exclusion is the whole safety argument and must not be
    # dropped: in Italian a final -o/-a is GENDER. Without it this level would
    # hand positive evidence to a `francesco`/`francesca` pair — a man and a
    # woman — which is worse than the contradiction it replaces.
    import duckdb

    lineage = next(c for c in comparisons()
                   if c._output_column_name == "lineage").get_comparison("duckdb")
    level = next(l for l in lineage.comparison_levels
                 if "one letter apart" in (l.label_for_charts or ""))
    sql = level.sql_condition
    con = duckdb.connect()

    def fires(left: str, right: str) -> bool:
        # the level's SQL names *_l / *_r columns, so bind a one-row relation
        return con.execute(
            f"SELECT {sql} FROM (SELECT ? AS patronymic_norm_l, ? AS patronymic_norm_r)",
            [left, right]).fetchone()[0]

    # scribal variation: fires
    for left, right in [("pagolo", "paolo"), ("roberto", "ruberto"),
                        ("iacob", "jacob"), ("cammillo", "camillo"),
                        ("larione", "ilarione")]:
        assert fires(left, right), f"{left}/{right} should register as a spelling variant"

    # gender and family morphology: must NOT fire, however close
    for left, right in [("francesco", "francesca"), ("niccola", "niccolo")]:
        assert not fires(left, right), (
            f"{left}/{right} differ only in the final vowel, which is semantic in "
            f"Italian — folding it would give a man/woman pair positive evidence")

    # genuinely different names two letters apart: out of range
    assert not fires("giovanni", "bartolomeo")

    # the guards still come first — a spelling variant must never outrank them
    labels = [l.label_for_charts for l in lineage.comparison_levels]
    assert labels.index("sibling signature (same father, different name)") < \
        labels.index(level.label_for_charts)
    assert labels.index("father-and-son signature") < labels.index(level.label_for_charts)


def test_surname_variant_lane_needs_the_father_to_agree():
    # Three of the four blocking rules sort on the surname, so a surname written
    # two ways falls through all of them. Persons 2320/12200 (*Salvatore Innori
    # di Vincenzo* / *Salvadore Inori di Vincenzo*) share a father AND a
    # grandfather and were still never compared — not scored badly, never scored.
    #
    # The father-agreement clause is what keeps this lane honest, and it is load
    # bearing: surname edit-distance ALONE puts `gucci`/`pucci`,
    # `corbini`/`corsini` and `carini`/`casini` at the top of the list — distinct
    # Florentine houses that merely look alike (906 pairs, mostly noise, against
    # 406 with the clause). Drop the clause and the lane becomes a machine for
    # proposing that two great families are one person.
    import duckdb

    from workflows.person_model import SURNAME_VARIANT_LANE, blocking_rules

    assert SURNAME_VARIANT_LANE in blocking_rules(), "the lane must actually be wired in"
    assert "patronymic_norm" in SURNAME_VARIANT_LANE, (
        "the lane must require the father to agree; surname distance alone welds houses")

    con = duckdb.connect()

    def fires(last_l, pat_l, last_r, pat_r):
        return con.execute(
            f"SELECT {SURNAME_VARIANT_LANE} FROM (SELECT ? AS last_norm, ? AS patronymic_norm) l,"
            f" (SELECT ? AS last_norm, ? AS patronymic_norm) r",
            [last_l, pat_l, last_r, pat_r]).fetchone()[0]

    # the case it exists for: surname drifts, father agrees
    assert fires("innori", "vincenzo", "inori", "vincenzo")
    assert fires("brandolini", "giorgio", "barandolini", "giorgio")

    # distinct houses that merely look alike: the father must save us
    assert not fires("gucci", "domenico", "pucci", "lorenzo")
    assert not fires("corbini", "vincenzo", "corsini", "neri")

    # already covered by the exact surname+father lane, so not this one's job
    assert not fires("corsi", "jacopo", "corsi", "jacopo")

    # two people who merely both lack a father must never pair: SQL NULL = NULL
    # is false, which is the whole reason this clause is safe with missing data
    assert not fires("innori", None, "inori", None)


def test_normalisation_preserves_the_deliberately_fixed_levels():
    # The sibling and father-and-son levels encode what a historian knows and EM
    # cannot see, so normalisation must redistribute around them, never over
    # them — otherwise repairing the invariant would quietly delete the two
    # guards that stop brothers and father/son pairs merging.
    from workflows.person_model import normalize_m_probabilities

    class _Level:
        def __init__(self, m, fixed=False):
            self.m_probability, self._fix_m_probability = m, fixed

    class _Comparison:
        output_column_name = "lineage"
        def __init__(self, levels): self._comparison_levels_excluding_null = levels

    class _Linker:
        def __init__(self, c): self._settings_obj = type("S", (), {"comparisons": [c]})()

    fixed_a, fixed_b = _Level(0.00002, True), _Level(0.0001, True)
    free = [_Level(0.4), _Level(0.2), _Level(0.26)]          # sums to 0.86 with the fixed pair
    comparison = _Comparison([fixed_a, *free, fixed_b])
    normalize_m_probabilities(_Linker(comparison))

    assert fixed_a.m_probability == 0.00002        # untouched
    assert fixed_b.m_probability == 0.0001
    total = sum(l.m_probability for l in comparison._comparison_levels_excluding_null)
    assert total == pytest.approx(1.0)
    # relative weights among the free levels — what EM actually learned — survive
    assert free[0].m_probability / free[1].m_probability == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Guards that only make sense at the level of a whole cluster
# ---------------------------------------------------------------------------

def test_sibling_signature_argues_against_a_match():
    # Brothers agree on father, surname and business network — everything the
    # lineage comparison can see. Unguarded, eleven `di Jacopo Corsi` rows
    # (four of them brothers named in the act) chained into one 101-year life.
    lineage = next(c for c in comparisons()
                   if c._output_column_name == "lineage").get_comparison("duckdb")
    labels = [l.label_for_charts for l in lineage.comparison_levels]
    sibling = next(l for l in lineage.comparison_levels
                   if "sibling" in (l.label_for_charts or ""))
    assert sibling._fix_m_probability is True
    assert sibling._m_probability < 0.001          # below any plausible u ⇒ negative
    # it must be tested BEFORE the agreement levels, or brothers match on them
    assert labels.index(sibling.label_for_charts) < labels.index("father and grandfather agree")


def test_lineage_reads_no_column_the_first_em_session_fixes():
    # The lineage comparison is trained in the session blocked on the surname.
    # If any of its levels mentions `last_norm`, the whole comparison is
    # silently skipped — which happened twice: once via the father-and-son
    # level, once when the sibling level first tested the surname too.
    lineage = next(c for c in comparisons()
                   if c._output_column_name == "lineage").get_comparison("duckdb")
    sql = " ".join(l.sql_condition for l in lineage.comparison_levels)
    assert "last_norm" not in sql
    assert "last_norm" in em_training_rules()[0].get_blocking_rule("duckdb").blocking_rule_sql


def test_impossible_clusters_are_flagged():
    # A chain of plausible links can still describe a person who never lived.
    from workflows.person_model import flag_impossible_clusters

    spine = pd.DataFrame([
        {"person_id": 1, "first_year": 1546.0, "last_year": 1549.0},
        {"person_id": 2, "first_year": 1572.0, "last_year": 1595.0},
        {"person_id": 3, "first_year": 1638.0, "last_year": 1638.0},
        {"person_id": 4, "first_year": 1600.0, "last_year": 1610.0},
        {"person_id": 5, "first_year": 1605.0, "last_year": 1615.0},
    ])
    clusters = pd.DataFrame([
        {"cluster_id": "torrigiani", "person_id": 1},   # 1546 … 1638 = 92 years
        {"cluster_id": "torrigiani", "person_id": 2},
        {"cluster_id": "torrigiani", "person_id": 3},
        {"cluster_id": "ok", "person_id": 4},           # 1600 … 1615 = 15 years
        {"cluster_id": "ok", "person_id": 5},
    ])
    flagged = flag_impossible_clusters(clusters, spine).set_index("cluster_id")
    assert bool(flagged.loc["torrigiani", "impossible"]) is True
    assert flagged.loc["torrigiani", "implied_career_years"] == 92
    assert bool(flagged.loc["ok", "impossible"]) is False
