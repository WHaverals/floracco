"""The probabilistic stage: a Splink model over the person spine.

Stage 1, step 5 of `docs/person_linkage/research.md`; the comparison design is
§10 of that memo and the reasoning for each choice lives there. This module
holds only the *specification* — the data preparation, the blocking rules and
the comparisons — so that the settings a historian can argue with are readable
in one place, and so notebook and production batch cannot drift.

Two things the guide (`splink_llm_context_long.txt`) makes load-bearing:

* **Correlated fields go into ONE comparison**, not several, because
  Fellegi–Sunter multiplies evidence as if independent. Father and grandfather
  are one comparison; time and the business network are one comparison
  (`contemporaneity`), which is where partners, firms and firm-name tokens all
  live — they are the same fact seen at different resolutions.
* **Missing must be NULL, not ""**. Splink gives null levels zero weight (the
  MAR treatment); an empty string would instead read as agreement, which for a
  corpus where 43% lack a patronymic would be catastrophic.

The model never decides anything. It ranks candidate pairs for people to
review, and its output is a *proposal* like every other machine suggestion in
this project.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

# A working life; see person_tiers.MAX_CAREER_YEARS and research.md §10 (C3).
MAX_CAREER_YEARS = 60
MODEL_PATH = Path(__file__).resolve().parents[1] / "docs/person_linkage/person_model.json"

# Columns Splink reads. Everything else in the spine is for humans.
MODEL_COLUMNS = [
    "person_id", "full_name_norm", "first_norm", "last_norm",
    "patronymic_norm", "grandfather_norm",
    "first_year", "last_year", "partners", "firm_tokens", "firms", "contracts",
    "dominant_role", "husband_first_norm", "husband_last_norm",
]


def prepare_frame(spine: pd.DataFrame) -> pd.DataFrame:
    """Spine → Splink input: people only, with true NULLs for missing values.

    The empty-string-to-NULL conversion is not cosmetic. Splink's null levels
    carry zero weight by design, so a missing patronymic must be NULL; left as
    "" it would match every other missing patronymic and read as evidence of
    sameness — the same failure that `normalize_name` guards at the other end.
    """
    people = spine[spine.entity_kind == "person"].copy()
    frame = people[MODEL_COLUMNS].copy()
    for column in ("full_name_norm", "first_norm", "last_norm",
                   "patronymic_norm", "grandfather_norm",
                   "husband_first_norm", "husband_last_norm"):
        frame[column] = frame[column].replace("", None)
    frame["dominant_role"] = frame["dominant_role"].replace("unknown", None)
    # Empty arrays mean "no network evidence", which the comparison reads as a
    # null level; keep them as empty lists so DuckDB sees a real array.
    for column in ("partners", "firm_tokens", "firms", "contracts"):
        frame[column] = frame[column].apply(lambda v: v if isinstance(v, list) else [])
    return frame.reset_index(drop=True)


# A pair whose SURNAME is a scribal variant falls through every lane above that
# sorts on the surname — and three of the four do. Persons 2320 and 12200
# (*Salvatore Innori di Vincenzo*, 1588, and *Salvadore Inori di Vincenzo*,
# 1589) share a father AND a grandfather, and were still never compared: the
# two rules that use the father also demand the surname, and the surname
# differs. They were not scored badly, they were never scored at all, and the
# research memo found them by hand rather than by machine.
#
# Requiring the father's name to agree is what makes this safe, and it is doing
# real work. Measured on a surname edit-distance alone, the top of the list is
# `gucci`/`pucci`, `corbini`/`corsini`, `carini`/`casini` — distinct Florentine
# houses that happen to look alike; 906 pairs, mostly noise. Adding the father
# constraint cuts it to 406 pairs (about 3% more comparisons) because two men
# of different houses rarely share a father's name as well. The father
# agreement is the curation; no hand-written list of "these surnames are one
# family" is needed, and none is used.
#
# NB `l.patronymic_norm = r.patronymic_norm` excludes nulls for free — SQL
# never makes NULL equal NULL — so this lane cannot fire on two people who
# merely both lack a father.
SURNAME_VARIANT_LANE = (
    "levenshtein(l.last_norm, r.last_norm) <= 1"
    " AND l.last_norm <> r.last_norm"
    " AND l.patronymic_norm = r.patronymic_norm"
)


def blocking_rules() -> list[Any]:
    """Which pairs get scored at all. Several strict rules beat one loose one:
    a true match need only satisfy ONE of them (guide, Blocking topic)."""
    from splink import block_on

    return [
        block_on("first_norm", "last_norm"),           # the main lane
        block_on("last_norm", "patronymic_norm"),      # first-name variants
        block_on("first_norm", "patronymic_norm"),     # the 536 surname-less
        block_on("last_norm", "grandfather_norm"),     # rare but decisive
        SURNAME_VARIANT_LANE,                          # surname variants
    ]


def comparisons() -> list[Any]:
    """The five comparisons, in evidence order.

    research.md §10 designed six. `career` and `network` became ONE
    (`contemporaneity`) on 2026-08-29, because Fellegi-Sunter multiplies
    weights as though comparisons were independent and those two are not:
    you cannot share a business partner with someone who died before you
    were born (measured, +4.35 bits of excess). The memo's table is
    amended accordingly.
    """
    import splink.comparison_level_library as cll
    from splink.comparison_library import CustomComparison

    name = CustomComparison(
        output_column_name="name",
        comparison_description="Full name, term-frequency adjusted",
        comparison_levels=[
            cll.NullLevel("full_name_norm"),
            cll.ExactMatchLevel("full_name_norm", term_frequency_adjustments=True),
            cll.JaroWinklerLevel("full_name_norm", 0.92),
            cll.JaroWinklerLevel("full_name_norm", 0.85),
            cll.ElseLevel(),
        ],
    )

    # Father and grandfather are one comparison: they are correlated, and the
    # naming custom makes them agree for the WRONG pairs as well as the right
    # ones. The father-son signature is a level of its own so the model can
    # learn how strongly it argues AGAINST a match (research.md §10, C2).
    father_son = (
        "(patronymic_norm_r = first_norm_l AND grandfather_norm_r = patronymic_norm_l)"
        " OR (patronymic_norm_l = first_norm_r AND grandfather_norm_l = patronymic_norm_r)"
    )
    lineage = CustomComparison(
        output_column_name="lineage",
        comparison_description="Patronymic chain (father, then grandfather)",
        comparison_levels=[
            cll.NullLevel("patronymic_norm"),
            # SIBLINGS — checked before any agreement level, because brothers
            # agree on everything this comparison looks at. Bardo, Giovanni,
            # Simone and Lorenzo *di Jacopo Corsi* share a father, a surname and
            # a business network, and the corpus names them as brothers; left
            # unguarded the model chained all eleven Corsi rows into a single
            # 101-year "person". Two records of one man effectively always share
            # a given name (spelling aside), so a genuinely different given name
            # over the same father is a sibling, not a match. `m` is fixed: this
            # is knowledge about Florentine families, not a pattern EM can see.
            # NB: deliberately does NOT test the surname. Every blocking rule
            # that can pair two dissimilar given names already shares one, and
            # naming a fourth column here would put `last_norm` inside this
            # comparison — which silently un-trains the whole of `lineage` in
            # the EM session that blocks on the surname (measured; see the test).
            cll.CustomLevel(
                "patronymic_norm_l = patronymic_norm_r"
                " AND jaro_winkler_similarity(first_norm_l, first_norm_r) < 0.85",
                label_for_charts="sibling signature (same father, different name)",
            ).configure(m_probability=0.00002, fix_m_probability=True),
            cll.CustomLevel(
                "patronymic_norm_l = patronymic_norm_r"
                " AND grandfather_norm_l = grandfather_norm_r",
                label_for_charts="father and grandfather agree",
            ),
            cll.CustomLevel(
                "patronymic_norm_l = patronymic_norm_r"
                " AND (grandfather_norm_l IS NULL OR grandfather_norm_r IS NULL)",
                label_for_charts="father agrees, grandfather unrecorded",
            ),
            cll.CustomLevel(
                "patronymic_norm_l = patronymic_norm_r",
                label_for_charts="father agrees, grandfather differs",
            ),
            # A man is not his own father. EM cannot see that: it only sees
            # that these pairs also share a surname and a business network, so
            # left free it learns the signature as +8 bits of evidence FOR a
            # match (measured 2026-08-28). We therefore fix `m` below the
            # coincidence rate `u`, which is the one thing here the data cannot
            # teach and the historian already knows.
            cll.CustomLevel(
                father_son, label_for_charts="father-and-son signature"
            ).configure(m_probability=0.0001, fix_m_probability=True),
            # SCRIBAL VARIATION, priced by EM rather than by us. One scribe
            # writes the father *Pagolo*, another *Paolo*; exact equality reads
            # that as two different men. Measured before this level: 36 pairs
            # had a father or grandfather one letter apart being scored as a
            # flat contradiction, including `pagolo`/`paolo` at p=0.003 and the
            # memo's own worked example `duccio`/`puccio`.
            #
            # The obvious alternative — a hand-curated table of "these spellings
            # are one name" — was built, measured and REJECTED. It is 83 rows of
            # philological judgement requiring a historian's adjudication, and
            # this level beats it on its own ground: it independently recovered
            # `iacob`/`jacob`, `cammillo`/`camillo` and `laldadio`/`laudadio`,
            # none of which were on the curated list. research.md §2's warning
            # that "blanket edit-distance... welds distinct names" is about
            # FOLDING by distance — an operation that asserts two names are the
            # same. A comparison level asserts nothing; it carries the weight EM
            # measures, and EM prices this one at about +4 bits — real evidence,
            # properly weaker than the +7 to +11 of an exact chain agreement.
            # `mario`/`marco` is swept in and gets that same +4 it does not
            # deserve; measured, that moves such pairs from 0.002 to 0.039, an
            # error that is bounded and honest rather than silent.
            #
            # The final-vowel exclusion is the one piece of linguistics hard
            # coded here, and it earns its place: in Italian a final -o/-a is
            # GENDER (`francesco`/`francesca` are a man and a woman) and a final
            # -i/-o distinguishes a family from an individual. One morphological
            # rule, not a table of judgements.
            cll.CustomLevel(
                "levenshtein(patronymic_norm_l, patronymic_norm_r) <= 1"
                " AND NOT (length(patronymic_norm_l) = length(patronymic_norm_r)"
                " AND substr(patronymic_norm_l, 1, length(patronymic_norm_l) - 1)"
                "   = substr(patronymic_norm_r, 1, length(patronymic_norm_r) - 1))",
                label_for_charts="father's name one letter apart (spelling)",
            ),
            cll.ElseLevel(),
        ],
    )

    # ------------------------------------------------------------------
    # TIME AND NETWORK ARE ONE COMPARISON. This was two — `career` and
    # `network` — and splitting them broke the assumption Fellegi-Sunter rests
    # on. FS multiplies each comparison's weight as if they were independent
    # GIVEN match status. These two are not independent and cannot be: you
    # cannot share a business partner with someone who died before you were
    # born. Measured on 2.7M randomly sampled pairs (2026-08-29):
    #
    #     P(share a partner | careers overlap)     = 4.76%
    #     P(share a partner | careers don't)       = 0.12%      — 40x
    #     excess over independence                 = +4.35 bits
    #
    # So the model was counting one fact — "these two were active at the same
    # time in the same circles" — twice, and paying about four bits for it on
    # its single most common high-scoring pattern (646 pairs sit in the
    # overlap x 3-shared-partners cell alone). That is also the whole
    # explanation of the calibration overshoot: double-counted evidence
    # inflates every posterior, so their sum has to outrun the prior.
    #
    # The cure is the guide's own (correlated fields go into ONE comparison
    # with rich internal levels — the ForenameSurname pattern), and it is the
    # principle this module already applies to father+grandfather and to
    # partners+firms. We simply had not applied it to the correlation nobody
    # had noticed. Levels are joint events, so EM prices the conjunction once.
    span = ("greatest(last_year_l, last_year_r) - least(first_year_l, first_year_r)")
    overlap = "first_year_l <= last_year_r AND first_year_r <= last_year_l"
    within_life = f"{overlap} AND {span} <= {MAX_CAREER_YEARS}"
    p3 = "array_length(list_intersect(partners_l, partners_r)) >= 3"
    p1 = "array_length(list_intersect(partners_l, partners_r)) >= 1"
    f2 = "array_length(list_intersect(firm_tokens_l, firm_tokens_r)) >= 2"
    # THE SAME FIRM, across two different contracts. An accomandita is a
    # fixed-term partnership that gets RENEWED — 353 firm names in this corpus
    # span 821 contracts — so the same people reappear together, act after act.
    # This is the one route around a problem `partners` cannot solve: partner
    # sets are keyed on `person_id`, and in a corpus whose defining flaw is
    # duplicated people, a duplicated person's partners are duplicated too.
    # Luigi Capponi's two rows (1228/11903) share no partner id, because his
    # partner Alessandro is himself split across 1005/11904. Resolving that
    # needs identity, which is the problem being solved. A firm NAME is a
    # string and needs no resolving.
    # Measured across the deterministic tiers: pairs sharing an independent firm
    # are 0.3% of those the rules REFUSE against 5.4% of those they leave
    # undecided — an 18x enrichment, the strongest and safest of any unused
    # field in the database. Firms named after the person themselves are
    # stripped in the spine (`_drop_self_referential_firms`): sharing those is
    # guaranteed by name equality and would be the `name` comparison in a hat.
    same_firm = "array_length(list_intersect(firms_l, firms_r)) >= 1"
    any_net = f"({p1} OR {f2} OR {same_firm})"
    no_dates = "first_year_l IS NULL OR first_year_r IS NULL"
    no_net = ("(array_length(partners_l) = 0 AND array_length(firms_l) = 0)"
              " OR (array_length(partners_r) = 0 AND array_length(firms_r) = 0)")
    # Two rows on the SAME act share every other signatory mechanically, and
    # their dates coincide by construction — so both halves of this comparison
    # would be restating the one fact that already routes the pair to a human
    # (`person_tiers` caution_coappearance). Measured: of the 20 pairs above
    # p=0.90 that share a contract, 19 lose their ENTIRE partner overlap once
    # that contract is excluded — including two edges holding the Corsi 818
    # over-merge together. `person_features._partner_sets` claimed the tiers
    # protected this comparison; they do not, because the tiers and the model
    # block independently and every blocked pair is scored.
    co_appear = "array_length(list_intersect(contracts_l, contracts_r)) > 0"

    contemporaneity = CustomComparison(
        output_column_name="contemporaneity",
        comparison_description="Active at the same time, in the same circles",
        comparison_levels=[
            # Null whenever EITHER half is unavailable, not only when both are.
            # The tempting alternative — keeping a separate "undated but sharing
            # partners" level — was built and REJECTED: it is never observed in
            # the surname-blocked EM population (undated people are almost all
            # zero-appearance ghosts, who have no partners either), so Splink
            # could not estimate it, fell back to a default, and produced a
            # bogus +15.20 bits — which `normalize_m_probabilities` then
            # laundered into a vector that PASSED the well-formedness check.
            # That is the third time this project has hit an untrainable level;
            # the standing check in the test suite exists because of it.
            # Cost of the broader null: 115 pairs of 15,637 lose one half of
            # this evidence. Cheap, and it fails loudly rather than silently.
            {"sql_condition": f"({no_dates}) OR ({no_net}) OR ({co_appear})",
             "label_for_charts": "no evidence, or the pair shares an act",
             "is_null_level": True},
            cll.CustomLevel(f"{within_life} AND {same_firm}",
                            label_for_charts="contemporaries, the same firm"),
            cll.CustomLevel(f"{within_life} AND {p3}",
                            label_for_charts="contemporaries, 3+ shared partners"),
            cll.CustomLevel(f"{within_life} AND {p1}",
                            label_for_charts="contemporaries, a shared partner"),
            cll.CustomLevel(f"{within_life} AND {f2}",
                            label_for_charts="contemporaries, shared firm only"),
            cll.CustomLevel(within_life,
                            label_for_charts="contemporaries, no shared network"),
            # Careers that ABUT rather than overlap are the commonest shape of a
            # split person — one record ends in 1591, the next begins in 1592 —
            # and for those the firm is the only network evidence that survives.
            # Without this level they fell in with "shared network", which a
            # mere two firm-name TOKENS also satisfies, so firm identity bought
            # them nothing: Bellacci 2734/2932 and Guascoli 5937/8547 both sat
            # unchanged at 0.445 with the firm level present but unreachable.
            # 115 such pairs exist in the EM population, so it trains.
            cll.CustomLevel(f"{span} <= 30 AND {same_firm}",
                            label_for_charts="career <= 30 years, the same firm"),
            cll.CustomLevel(f"{span} <= 30 AND {any_net}",
                            label_for_charts="career <= 30 years, shared network"),
            cll.CustomLevel(f"{span} <= 30",
                            label_for_charts="career <= 30 years, no shared network"),
            # The <=60 band needs its own network split too. Without it a pair
            # 35 years apart sharing three partners scored identically to one
            # sharing nothing at all — 56 pairs were having real network
            # evidence discarded, 4 of them sharing 3+ partners.
            cll.CustomLevel(f"{span} <= {MAX_CAREER_YEARS} AND {any_net}",
                            label_for_charts=f"career <= {MAX_CAREER_YEARS} years, shared network"),
            cll.CustomLevel(f"{span} <= {MAX_CAREER_YEARS}",
                            label_for_charts=f"career <= {MAX_CAREER_YEARS} years, no shared network"),
            cll.ElseLevel(),   # longer than a working life
        ],
    )

    role = CustomComparison(
        output_column_name="role",
        comparison_description="Partnership role (managing vs capital-providing)",
        comparison_levels=[
            cll.NullLevel("dominant_role"),
            cll.CustomLevel(
                "dominant_role_l = dominant_role_r AND dominant_role_l IN ('gp', 'lp')",
                label_for_charts="same role throughout",
            ),
            cll.CustomLevel(
                "dominant_role_l = 'mixed' OR dominant_role_r = 'mixed'",
                label_for_charts="at least one side mixed",
            ),
            cll.ElseLevel(),   # one gp, one lp
        ],
    )

    # For the 242 women the richest field they have, and stable across the
    # wife → widow transition.
    husband = CustomComparison(
        output_column_name="husband",
        comparison_description="Husband's name (women only)",
        comparison_levels=[
            cll.NullLevel("husband_last_norm"),
            cll.CustomLevel(
                "husband_first_norm_l = husband_first_norm_r"
                " AND husband_last_norm_l = husband_last_norm_r",
                label_for_charts="same husband, both names",
            ),
            cll.ExactMatchLevel("husband_last_norm"),
            # A husband-name DISAGREEMENT is mild evidence against, and we assert
            # that rather than letting EM decide — because EM cannot decide it.
            # This comparison is non-null on 14 of 15,637 scored pairs (156 rows
            # in the whole corpus record a husband), and from that EM drove this
            # level's m to 1.5e-14, i.e. a -45.9 bit veto: enough to annihilate a
            # pair however strongly every other comparison agreed. It fired only
            # on pairs the model already rejected, so nothing was harmed — but it
            # sat on the one comparison that exists for the 242 women, where a
            # single scribal variant in a husband's surname would have destroyed
            # a genuine match with no appeal. That is the fourth time an
            # under-observed level has collapsed to a bogus extreme in this model.
            #
            # 0.10 says: among true matches where both rows name a husband, about
            # one in ten disagree. That is deliberately generous, because the real
            # reasons are all well attested here — remarriage, the wife-to-widow
            # formula, and plain scribal variation in the husband's surname. It
            # still scores -3.3 bits, so a disagreement argues against a match; it
            # simply no longer overrules the entire rest of the evidence.
            cll.ElseLevel().configure(m_probability=0.10, fix_m_probability=True),
        ],
    )

    return [name, lineage, contemporaneity, role, husband]


def settings() -> Any:
    """The full Splink specification for this corpus."""
    from splink import SettingsCreator

    return SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="person_id",
        blocking_rules_to_generate_predictions=blocking_rules(),
        comparisons=comparisons(),
        retain_intermediate_calculation_columns=True,
        retain_matching_columns=True,
    )


def em_training_rules() -> list[Any]:
    """Blocking rules for the EM sessions, in order.

    A comparison cannot be estimated in a session whose blocking rule fixes any
    column it reads — and `lineage` reads `first_norm`, because of the
    father-and-son level. Blocking on first+surname therefore silently trains
    everything *except* the patronymic chain, which is the comparison that
    matters most. Blocking on the surname alone frees it.

    The second session used to block on `(first_norm, last_norm)`, and that was
    a subtler instance of the very same trap — one Splink cannot detect for us.
    Splink decides a comparison is trainable by checking whether the blocking
    rule fixes the columns that comparison READS, and `name` reads only
    `full_name_norm`. Blocking on first+surname pins `full_name_norm` exactly,
    without ever naming it, so Splink believed `name` was free and trained it on
    a population that was ~100% exact matches. Only its exact-match level was
    ever observed there; the Jaro-Winkler and else levels were not. Splink then
    sets each level's final `m` to the *median across sessions*, level by level
    (`linker.py:_populate_m_u_from_trained_values`), so the exact level was
    pulled toward 1.0 by the second session while its siblings kept their
    first-session values — and the vector stopped summing to 1 (measured:
    `name` summed to 1.372, with m=0.628 for an exact name match against m=0.627
    for no similarity at all — the model asserting a true match is as likely to
    carry a completely different name as an identical one). `husband` broke the
    same way, at 1.081.

    We now run a SINGLE session, and the reason is structural rather than
    empirical. The corruption above needs two ingredients: more than one
    session, and a level observed in some of them but not all. With one session
    the median is taken over one value, so the second ingredient cannot occur
    and the whole failure mode is impossible by construction — not merely absent
    on today's data.

    Six configurations were trained and compared (2026-08-29). Two others also
    produce well-formed models — `(last, first)`, and any single session — but
    `(last, patronymic)` and `(last, patronymic+grandfather)` still leave
    `husband` unevenly covered, so `normalize_m_probabilities` would be
    repairing the *sum* while the *values* stayed distorted. That is papering
    over the bug, and it is exactly how the original one hid.

    `(last, first)` survives the invariant but fails on quality: blocking on the
    given name builds a population dominated by pairs with different surnames,
    and EM then puts most of the match mass on the near-match levels, learning
    m=0.136 for an exact name match against m=0.479 for merely similar. In a
    corpus where every deterministic candidate pair is an exact full-name match,
    a model asserting that true matches usually have *different* names is not
    one to ship. Blocking on the surname alone gives the most plausible estimate
    of the six (m=0.265, +11.47 bits) and the best recall on the pairs the rules
    already call duplicates.

    The cost is honest and worth stating: a single session is one estimate
    rather than a median of several, and its population — pairs sharing a
    surname — is not the corpus. `normalize_m_probabilities` and
    `check_model_is_well_formed` stay as defence in depth regardless.
    """
    from splink import block_on

    return [block_on("last_norm")]


def normalize_m_probabilities(linker: Any) -> dict[str, float]:
    """Restore the sum-to-1 invariant on every comparison's `m` vector.

    `m` values are the probabilities of each outcome *given a true match*, so
    across the non-null levels of one comparison they must sum to 1. Two things
    in this model break that, and only one of them is a bug:

    * Training a comparison in a session whose blocking rule secretly pins it —
      fixed structurally in `em_training_rules`, and guarded by a test.
    * Fixing `m` on a level by hand, which we do deliberately twice in
      `lineage` (the sibling and father-and-son signatures). EM still assigns
      those levels their share of the responsibility during the E-step, but the
      M-step overwrites it with our constant and nothing reclaims the
      difference — about 0.137 of the mass, leaving `lineage` summing to 0.86.

    The second is not something to remove: those two levels encode what a
    historian knows and EM cannot see. But the mass has to go somewhere, so it
    is redistributed proportionally across the levels EM *did* estimate, which
    leaves their relative weights — the thing EM actually learned — untouched.

    Known limitation, measured rather than assumed. The exact remedy would be a
    constrained M-step *inside* the EM loop: Splink's M-step normalises over all
    levels and then discards the result for fixed ones
    (`expectation_maximisation.compute_proportions_for_new_parameters_sql`), so
    correcting it in-loop would also change each subsequent E-step. Doing that
    means monkey-patching a module global in a third-party library, which is
    precisely the kind of thing that breaks silently on upgrade — and silent
    breakage is what this whole repair is about. The measured difference is
    small (about three pairs in 160, in the conservative direction), so we take
    the robust approximation and write the discrepancy down instead of hiding
    it. If Splink ever exposes a hook for a constrained M-step, prefer it.

    Returns the pre-normalisation sum for each comparison it changed, so a
    caller can report what was off and by how much.
    """
    adjusted: dict[str, float] = {}
    for comparison in linker._settings_obj.comparisons:
        levels = comparison._comparison_levels_excluding_null
        fixed = sum(l.m_probability or 0.0 for l in levels if l._fix_m_probability)
        free = [l for l in levels if not l._fix_m_probability]
        loose = sum(l.m_probability or 0.0 for l in free)
        before = fixed + loose
        if abs(before - 1.0) < 1e-9 or loose <= 0 or (1.0 - fixed) <= 0:
            continue
        scale = (1.0 - fixed) / loose
        for level in free:
            level.m_probability = (level.m_probability or 0.0) * scale
        adjusted[comparison.output_column_name] = before
    return adjusted


def train_model(df: pd.DataFrame, *, recall: float = 0.5, max_pairs: float | None = None,
                seed: int = 7, db_api: Any = None) -> Any:
    """Estimate the model. Returns the trained linker.

    `u` (how often fields agree by coincidence) comes from random sampling and
    needs no labels. `m` (how often they agree for true matches) comes from EM
    inside the name blocks — the memo's revised recipe (§10): the deterministic
    tiers yield only ~10 confident positives, far too few to train on, so they
    serve as a validation set instead.

    `max_pairs` now defaults to *every* pair, which makes `u` exact rather than
    sampled. It was 5e6, and that was quietly starving the `husband` comparison:
    one of its levels was never observed at all, so Splink fell back to a
    default `u` and logged "u values not fully trained" on every subsequent
    `predict()` — a warning the dashboard script was swallowing with a log
    level. Since `husband` is the richest evidence the *women* in this corpus
    have, running it on a default was a quiet fairness cost, not an untidy log.

    The reason a large sample was needed is not obvious and is worth recording:
    `estimate_u_using_random_sampling` samples **rows, not pairs**
    (`splink/internals/estimate_u.py`), taking `proportion = rows_needed(max_pairs)
    / total_rows`. A pair survives only if BOTH its rows do, so any
    sub-population is thinned by `proportion²`, not `proportion`. Only 156 of
    11,330 rows carry a husband; at the old setting that population was cut to
    almost nothing. Passing the full pair count makes `proportion` clamp to 1
    and skips sampling altogether, so `u` is a census and cannot drift with the
    seed. It costs a little time and removes a whole class of silent failure.

    `recall` — "what fraction of true matches do our deterministic rules
    catch?" — is an assumption, not a measurement, and it multiplies the odds of
    every scored pair. It is the single largest lever on how many pairs clear
    any threshold (measured: 0.3 → 353 pairs, 0.5 → 260, 0.8 → 167, 1.0 → 142),
    so any published count must name the value it used.
    """
    from splink import DuckDBAPI, Linker

    if max_pairs is None:                      # every pair: a census, not a sample
        max_pairs = len(df) * (len(df) - 1) / 2
    linker = Linker(df, settings(), db_api=db_api or DuckDBAPI(),
                    set_up_basic_logging=False)
    linker.training.estimate_probability_two_random_records_match(
        deterministic_rules(), recall=recall)
    linker.training.estimate_u_using_random_sampling(max_pairs=max_pairs, seed=seed)
    for rule in em_training_rules():
        linker.training.estimate_parameters_using_expectation_maximisation(
            rule, estimate_without_term_frequencies=True)
    normalize_m_probabilities(linker)
    return linker


# The smallest `m` an EM run on this corpus can credibly LEARN rather than
# collapse to. This applies to `m` ONLY, and the asymmetry is the whole point:
# `u` is estimated from a census of all 64 million pairs, so a u of 5e-06 is
# supported by hundreds of observations and is entirely credible — while `m` is
# estimated by EM against the handful of true matches the corpus contains, and
# below about 1e-04 it has stopped estimating and started collapsing. Every `m`
# this model actually learns sits at 4.3e-03 or above; the one collapse we found
# sat at 1.5e-14, eleven orders of magnitude below the rest, and carried a -45.9
# bit veto. Levels fixed BY HAND are exempt — the sibling and father-and-son
# signatures are deliberately tiny, and asserting them is the entire point.
MIN_LEARNABLE_M = 1e-4


def check_model_is_well_formed(model: dict[str, Any], *, tolerance: float = 1e-6) -> list[str]:
    """Faults that make a saved model not a Fellegi–Sunter model at all.

    Returns a list of human-readable complaints; empty means sound. Run this on
    any model file before trusting a number derived from it — the shipped model
    of 2026-08-28 failed three of these checks and nothing caught it, because
    every published figure was a property of that file and the only warning
    Splink gave was being suppressed.
    """
    faults: list[str] = []
    for comparison in model.get("comparisons", []):
        label = comparison.get("output_column_name", "?")
        levels = [l for l in comparison["comparison_levels"] if not l.get("is_null_level")]
        for kind in ("m_probability", "u_probability"):
            missing = [l.get("label_for_charts", "?") for l in levels if l.get(kind) is None]
            if missing:
                faults.append(f"{label}: no {kind} on level(s) {missing}")
            total = sum(l[kind] for l in levels if l.get(kind) is not None)
            if missing:
                continue
            if abs(total - 1.0) > tolerance:
                faults.append(
                    f"{label}: {kind} sums to {total:.6f}, not 1 — the levels of one "
                    f"comparison partition the outcomes, so their probabilities must sum to 1")
        # Summing to 1 is necessary but nowhere near sufficient: a level whose
        # value has COLLAPSED to near-zero still sums fine with its siblings, and
        # produces a weight large enough to overrule every other comparison.
        for level in levels:
            if level.get("fix_m_probability"):
                continue                       # asserted on purpose, not estimated
            m_value, u_value = level.get("m_probability"), level.get("u_probability")
            if m_value is None or u_value in (None, 0) or m_value >= MIN_LEARNABLE_M:
                continue
            weight = math.log2(m_value / u_value)
            faults.append(
                f"{label}: level {level.get('label_for_charts', '?')!r} has "
                f"m={m_value:.2e}, below {MIN_LEARNABLE_M:g} — that is a collapsed "
                f"estimate, not a learned one, and it carries {weight:+.1f} bits, "
                f"enough to overrule every other comparison. Assert it by hand "
                f"(fix_m_probability) if you mean it.")
    return faults


def score_with_precedence(tiers: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    """Join model scores onto the tiers — with the rules winning, always.

    The model is not allowed to overturn a deterministic verdict. The worked
    example this docstring used to give has now been overtaken by events, and
    the correction is worth keeping: persons 818 and 12322 (*Giovanni di Jacopo
    di Simone Corsi*) used to score **+6.3, p=0.99** against a rule that refused
    them, because the model saw an identical name chain and an "overlapping
    network". After the 2026-08-29 remodelling — time and network merged into
    one comparison, and a shared act made to carry no evidence — the same pair
    scores **-0.77**. The model now refuses what the rule refuses.

    That does not retire the precedence rule; it removes one instance of the
    problem it exists for. A score may rank what is undecided, and it may never
    resurrect a pair that time has already excluded, whether or not any pair
    currently tries to. One `distinct_strong` pair still scores above the
    weakest rule-confirmed match, and `flag_impossible_clusters` still guards
    the group level, which no pairwise precedence can reach.

    The model's job is to *rank the remainder*, which it does well: it puts the
    Lanfranchi pairs the research phase found by hand at the top of the review
    lane without ever having been told about them.
    """
    scores = predictions.assign(
        key=[tuple(sorted(p)) for p in zip(predictions.person_id_l, predictions.person_id_r)]
    ).set_index("key")["match_weight"]
    out = tiers.copy()
    out["match_weight"] = [scores.get(tuple(sorted(p)))
                           for p in zip(out.person_id_l, out.person_id_r)]
    decided = out.tier.isin(("distinct_strong", "batch_ghost", "same_as_strong"))
    out.loc[decided, "model_overruled"] = False
    out["verdict"] = out.tier
    # A caution stays a caution however the model scores it; a decided pair
    # keeps its verdict; only `review` is ordered by the model.
    out = out.sort_values(["tier", "match_weight"], ascending=[True, False])
    return out.reset_index(drop=True)


def flag_impossible_clusters(clusters: pd.DataFrame, spine: pd.DataFrame,
                             *, max_years: int = MAX_CAREER_YEARS) -> pd.DataFrame:
    """Clusters that would imply a career no one could have lived.

    Pairwise scores cannot see this. Connected-components clustering merges
    A–B and B–C into one identity, so a chain of individually plausible links
    can still produce an impossible person: unguarded, eleven *di Jacopo Corsi*
    rows — four brothers among them — chained into a single 101-year life.

    This is the pair rule (`person_tiers.combined_span`) lifted to the group,
    and it belongs here rather than in the threshold: a cluster is not wrong
    because its links were weak, it is wrong because the person it describes
    could not have existed. Flagged clusters go to review, never to a merge.
    """
    years = spine.set_index("person_id")[["first_year", "last_year"]]
    rows = []
    for cluster_id, group in clusters.groupby("cluster_id"):
        if len(group) < 2:
            continue
        window = years.reindex(group["person_id"]).dropna()
        if len(window) < 2:
            continue
        span = window["last_year"].max() - window["first_year"].min()
        rows.append({
            "cluster_id": cluster_id, "size": len(group),
            "first_year": int(window["first_year"].min()),
            "last_year": int(window["last_year"].max()),
            "implied_career_years": int(span),
            "impossible": bool(span > max_years),
            "person_ids": sorted(group["person_id"]),
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values("implied_career_years", ascending=False).reset_index(drop=True)


def deterministic_rules() -> list[str]:
    """Rules admitting very few false positives, for estimating the prior. Each
    demands an identical name chain AND a life-length career, which is the
    strongest thing a rule can say here (research.md §10)."""
    return [
        "l.full_name_norm = r.full_name_norm"
        " AND l.patronymic_norm = r.patronymic_norm"
        " AND l.grandfather_norm = r.grandfather_norm"
        f" AND greatest(l.last_year, r.last_year) - least(l.first_year, r.first_year) <= {MAX_CAREER_YEARS}",
        "l.full_name_norm = r.full_name_norm"
        " AND l.patronymic_norm = r.patronymic_norm"
        " AND array_length(list_intersect(l.partners, r.partners)) >= 2",
    ]
