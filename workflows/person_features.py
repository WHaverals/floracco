"""The person spine: one row per live person, carrying every linkage input.

Stage 1 of the person-linkage plan (`docs/person_linkage/research.md` §10–11).
Everything downstream — the deterministic tiers, the splink comparisons, the
review worklist — reads this table, so its definitions are the place where the
project's decisions about *what counts as evidence* actually live.

Read-only by construction: the corpus is opened `mode=ro` and every derived
column is computed on the way out. Verbatim name fields are copied through
untouched; the `*_norm` columns are **blocking/comparison keys only** and are
never written back (the house rule that interpretive phrases are data).

Four definitions carry most of the historical judgement:

* **Career window** — first to last dated appearance, excluding (a) posthumous
  ``heirs_of`` rows, which otherwise stretch a life decades past its end
  (person 3460 invests in 1599 and his estate again in 1642), (b) the 20
  contracts dated ``0000-00-00``, which otherwise fabricate millennium-long
  spans, and (c) nothing else — institutions are excluded upstream, at the
  entity-kind pass.
* **Partners** — co-investors on shared contracts, minus the 22 "hubs" who
  appear on more than ``hub_threshold`` contracts. Sharing a Tempi brother (60
  contracts each) is nearly meaningless; sharing an obscure merchant is strong
  evidence. This is a poor man's term-frequency correction for the network.
* **Firms** — the whole firm names a person appears under, minus any named
  after the person themselves. An accomandita is a fixed-term partnership that
  gets RENEWED (353 firm names span 821 contracts here), so the same people
  recur together act after act. This is the one network signal that survives the
  corpus's own duplication: ``partners`` is keyed on ``person_id``, so a
  duplicated person's partners are duplicated too, while a firm name is a string
  and needs no resolving. Measured at an 18x enrichment between pairs the rules
  refuse and pairs they leave undecided — the strongest unused field in the
  database.
* **Dominant role** — gp (managing) vs lp (capital-providing). Known for ~100%
  of appearances and stable for 77% of multi-appearance persons, so it is real
  evidence, but weak enough that a flip is a question rather than a verdict.
  NB ``dominant_role == "mixed"`` means a TIE (n_gp == n_lp), **not** "appears in
  both roles" — 676 people do the latter and only 294 carry the label.

    from workflows.person_features import load_person_spine, open_ro
    spine = load_person_spine(open_ro())
"""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = "data/sqlite/main.db"

# Appears on more contracts than this → treated as a "hub" and dropped from
# every partner set (22 persons in the current corpus). Not a magic number:
# the distribution is flat below ~20 and spikes to 60 above it.
HUB_CONTRACT_THRESHOLD = 20

# Sentinels that mean "no date" rather than a date.
UNDATED = ("", "0000-00-00")

# Firm-name noise: legal boilerplate and connectives that carry no identity.
_FIRM_STOPWORDS = {
    "e", "et", "di", "del", "della", "dei", "delle", "degli", "da", "in", "il",
    "lo", "la", "compagni", "compagnia", "compagnie", "c", "co", "eredi",
    "fratelli", "figli", "figlio", "banco", "societa", "ditta",
}


# --- Entity kind -----------------------------------------------------------
# Not every row in `person` is a person. Institutions invest (the Arte della
# Lana appears as FIVE separate rows), estates invest ("eredi di X"), and the
# clerks sometimes wrote a placeholder ("MOLTEPLICI AZIONARI" — multiple
# shareholders — on 18 contracts). None of them can be linked as people.
#
# Detection is by distinctive MULTI-WORD phrases, never bare words, because
# Florentine surnames collide with institutional vocabulary: del Badia,
# Camerati and Compagni are real families (research.md §1), and a naive
# keyword list quietly reclassifies them as buildings.
_INSTITUTION_PHRASES = (
    "arte della", "arte di", "monte di", "monte della",
    "spedale", "ospedale", "opera del", "opera di",
    "congregazione", "confraternita", "convento", "monastero",
    "erario", "magistrato", "capitolo di", "universita di",
    "compagnia di gesu", "regio spedale", "regia dogana", "dogana di",
)
# Estates: the decedent's name is the subject, not a living person.
_ESTATE_PREFIXES = ("eredi", "heredi", "erede")

# Collectives: ONE row standing for an unknown number of real people. In this
# corpus they are shareholders — "MOLTEPLICI AZIONARI", "azionisti vari" — and
# they mark the share companies of the corpus's final decades, so they are a
# historical signal as well as a counting hazard (docs/data_quality/
# non_person_rows.md). Matched on the role words only: `vari` and `diversi`
# alone are Florentine surnames (Corvari, Giavarini, Gaspero Diversi).
_COLLECTIVE_RE = re.compile(r"\b(azionist\w*|azionari\w*|caratist\w*)\b")


def classify_entity_kind(first: str | None, patronymic: str | None,
                         grandfather: str | None, last: str | None) -> str:
    """One of ``person`` | ``institution`` | ``estate`` | ``collective`` |
    ``placeholder``.

    Only ``person`` rows take part in person linkage. The others are set aside
    — not deleted, not judged, just excluded from a question that does not
    apply to them. (Whether institutions deserve a linkage lane of their own —
    the Arte della Lana wants folding too — is an open question for the team,
    research.md §9.)

    ``collective`` is kept distinct from ``placeholder`` on purpose: a
    placeholder is one unidentified person, a collective is *many* people in
    one row, which distorts counts differently and carries historical meaning.
    """
    blob = " ".join(normalize_name(v) for v in (first, patronymic, grandfather, last)).strip()
    blob = re.sub(r"\s+", " ", blob)
    if not blob:
        return "placeholder"
    first_norm = normalize_name(first)

    if any(phrase in blob for phrase in _INSTITUTION_PHRASES):
        return "institution"
    if first_norm.split(" ")[0] in _ESTATE_PREFIXES if first_norm else False:
        return "estate"
    if _COLLECTIVE_RE.search(blob):
        return "collective"
    # A collective written in the clerk's shorthand: ALL CAPS and multi-word
    # ("MOLTEPLICI AZIONARI" — multiple shareholders, on 18 contracts, and
    # recorded in `last_name`, so the test must look at every field), or bare
    # initials ("C.M").
    raw = " ".join(str(v or "").strip() for v in (first, patronymic, grandfather, last)).strip()
    raw = re.sub(r"\s+", " ", raw)
    if raw and raw.isupper() and len(raw.split()) > 1 and any(ch.isalpha() for ch in raw):
        return "placeholder"
    if re.fullmatch(r"(?:[a-z]\.?){1,3}", blob.replace(" ", "")):
        return "placeholder"
    if blob.startswith("amico di") or blob.startswith("amici di"):
        return "placeholder"
    return "person"


def db_path() -> Path:
    """The corpus database, honouring the project's env override."""
    raw = os.getenv("FLORACCO_DB_PATH", DEFAULT_DB_PATH)
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def open_ro(path: Path | None = None) -> sqlite3.Connection:
    """Read-only corpus connection — offline tools never hold a write lock."""
    target = path or db_path()
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_name(value: str | None) -> str:
    """Canonicalise a name for blocking/comparison keys — encoding only.

    Folds what is demonstrably scribal or technical: case, accents, the three
    stored apostrophe encodings (``de\\' medici`` ×31, ``de' medici``,
    ``de’ medici`` — a MySQL-escape artefact that otherwise splits the Medici
    block), stray punctuation and whitespace.

    Deliberately does NOT fold what is semantic in Italian: the final vowel
    (``-o``/``-a`` is gender: Francesco/Francesca; surname ``-i``/``-o`` is
    family vs individual), nor doubled consonants (the gemination lesson from
    the activity matcher, `docs/code_review/findings.md` E2). Spelling-variant
    folding (Piero/Pietro, Pagolo/Paolo) is a separate, curated step.
    """
    # NULL must survive as "missing", never as the literal string "nan": two
    # people who both lack a patronymic would otherwise *agree* on it, which
    # is a false-positive generator, not a missing value.
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if not value:
        return ""
    text = str(value)
    text = text.replace("\\'", "'").replace("’", "'").replace("`", "'").replace("´", "'")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _appearances(conn: sqlite3.Connection) -> pd.DataFrame:
    """Live investor rows on live contracts, with year and posthumous flag."""
    rows = conn.execute(
        """
        SELECT i.person_id            AS person_id,
               i.investor_id          AS investor_id,
               i.contract_id          AS contract_id,
               CASE WHEN i.heirs_of = 1 THEN 1 ELSE 0 END AS posthumous,
               CASE WHEN c.registration_date IS NULL
                      OR c.registration_date IN ('', '0000-00-00')
                    THEN NULL
                    ELSE CAST(substr(c.registration_date, 1, 4) AS INTEGER)
               END                    AS year,
               c.firm_name            AS firm_name
        FROM investor i
        JOIN contract c ON c.contract_id = i.contract_id AND c.is_deleted = 0
        WHERE i.is_deleted = 0
        """
    ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def _career_windows(appearances: pd.DataFrame) -> pd.DataFrame:
    """First/last dated year over *living* appearances, plus the posthumous
    terminus. A career is what someone did while alive; an estate acting in
    their name is evidence they had already died, not that they worked on."""
    alive = appearances[(appearances["posthumous"] == 0) & appearances["year"].notna()]
    window = alive.groupby("person_id")["year"].agg(
        first_year="min", last_year="max", n_dated="count"
    )
    window["span_years"] = window["last_year"] - window["first_year"]

    dead = appearances[(appearances["posthumous"] == 1) & appearances["year"].notna()]
    posthumous = dead.groupby("person_id")["year"].min().rename("first_posthumous_year")
    return window.join(posthumous, how="outer")


def _partner_sets(appearances: pd.DataFrame, hub_threshold: int) -> pd.DataFrame:
    """Co-investor ids per person, with high-degree hubs removed.

    Note on pairs that co-appear on one contract: they are each other's
    "partners" and would inflate their own overlap. This docstring used to say
    the case needed no handling because such pairs were "diverted to the caution
    lane before scoring, so they never reach the network comparison". **That was
    false.** The deterministic tiers and the Splink model block independently:
    every blocked pair is scored, and `score_with_precedence` joins the tiers on
    afterwards without filtering anything. Measured 2026-08-29: of the 20 pairs
    above p=0.90 that share a contract, 19 lost their ENTIRE partner overlap
    once that contract was excluded — two of them edges holding the Corsi 818
    over-merge together.

    The inflation cannot be removed here, because these sets are per-person and
    the exclusion is a property of a *pair*. It is handled where it belongs, in
    `person_model`'s `contemporaneity` comparison, which treats a shared act as
    a null level: when two rows appear in the same document, their overlapping
    dates and shared signatories are restatements of that document, not
    independent evidence about identity.
    """
    degree = appearances.groupby("person_id")["contract_id"].nunique()
    hubs = set(degree[degree > hub_threshold].index)

    pairs = appearances[["person_id", "contract_id"]].merge(
        appearances[["person_id", "contract_id"]], on="contract_id", suffixes=("", "_partner")
    )
    pairs = pairs[pairs["person_id"] != pairs["person_id_partner"]]
    pairs = pairs[~pairs["person_id_partner"].isin(hubs)]

    partners = (
        pairs.groupby("person_id")["person_id_partner"]
        .agg(lambda s: sorted(set(s)))
        .rename("partners")
        .to_frame()
    )
    partners["n_partners"] = partners["partners"].apply(len)
    return partners


def _partner_network_all(appearances: pd.DataFrame) -> pd.DataFrame:
    """Unfiltered temporal ego networks for diagnostics, never model input.

    Unlike `partners`, this preserves high-activity neighbors so weighted
    diagnostics can evaluate replacing the hard hub cutoff rather than
    inheriting it.
    """
    pairs = appearances[
        ["person_id", "contract_id", "year"]
    ].merge(
        appearances[["person_id", "contract_id"]],
        on="contract_id",
        suffixes=("", "_partner"),
    )
    pairs = pairs[pairs["person_id"] != pairs["person_id_partner"]].drop_duplicates(
        ["person_id", "person_id_partner", "contract_id"]
    )
    if pairs.empty:
        return pd.DataFrame(columns=["partners_all", "partner_events_all"])

    def partner_ids(group: pd.DataFrame) -> list[int]:
        return sorted({int(value) for value in group["person_id_partner"]})

    def events(group: pd.DataFrame) -> dict[str, list[dict[str, int | None]]]:
        result: dict[str, list[dict[str, int | None]]] = {}
        for row in group.sort_values(["person_id_partner", "year", "contract_id"]).itertuples():
            result.setdefault(str(int(row.person_id_partner)), []).append(
                {
                    "contract_id": int(row.contract_id),
                    "year": int(row.year) if pd.notna(row.year) else None,
                }
            )
        return result

    grouped = pairs.groupby("person_id")
    return pd.DataFrame(
        {
            "partners_all": grouped.apply(partner_ids, include_groups=False),
            "partner_events_all": grouped.apply(events, include_groups=False),
        }
    )


def _firm_tokens(appearances: pd.DataFrame) -> pd.Series:
    """Distinctive WORDS from the firm names a person appears under.

    The weakest of the three network signals inside `contemporaneity`, and the
    only one that fires when two people were in *similar-sounding* firms rather
    than demonstrably the same one. `_firm_names` carries the strong version:
    whole firm identity, which is far better evidence and is priced far higher
    (+10.05 against +3.92). Both live in one comparison, because a shared firm
    and shared firm words are the same fact at two resolutions.

    Raw tokens only; a person's own name is stripped afterwards by
    `_drop_self_referential_tokens`, which needs the name columns.
    """

    def tokens(names: pd.Series) -> list[str]:
        out: set[str] = set()
        for name in names.dropna():
            for token in normalize_name(name).split():
                if len(token) >= 3 and token not in _FIRM_STOPWORDS:
                    out.add(token)
        return sorted(out)

    return appearances.groupby("person_id")["firm_name"].agg(tokens).rename("firm_tokens")


def firm_token_document_frequency(
    conn: sqlite3.Connection,
) -> tuple[int, dict[str, int]]:
    """Contract-level token document frequencies for unscored IDF diagnostics."""
    rows = conn.execute(
        """
        SELECT contract_id, firm_name
        FROM contract
        WHERE is_deleted=0 AND TRIM(COALESCE(firm_name, '')) <> ''
        """
    ).fetchall()
    frequencies: dict[str, int] = {}
    for row in rows:
        tokens = {
            token
            for token in normalize_name(row["firm_name"]).split()
            if len(token) >= 3 and token not in _FIRM_STOPWORDS
        }
        for token in tokens:
            frequencies[token] = frequencies.get(token, 0) + 1
    return len(rows), frequencies


def _firm_names(appearances: pd.DataFrame) -> pd.Series:
    """The FIRMS a person appears in, as whole normalised names.

    Distinct from `_firm_tokens`, and the distinction is the point. An
    accomandita is a fixed-term partnership that gets **renewed**: 353 firm
    names in this corpus span 821 contracts, *Mario Morelli e compagni* across
    eight. So the same people reappear together contract after contract, and two
    records inside the same firm six years apart are in the same business.

    That matters because it is the one route around a problem the partner sets
    cannot solve. `partners` is keyed on `person_id`, and in a corpus whose
    defining flaw is duplicated people, a duplicated person's partners are
    duplicated too — Luigi Capponi's two rows share no partner *id* because his
    partner Alessandro is himself split across two ids. Resolving that needs
    identity, which is the problem being solved. A firm NAME is a string; it
    needs no resolving.

    Measured over the deterministic tiers: pairs sharing an independent firm are
    0.3% of those the rules refuse outright against 5.4% of those they leave
    undecided — an 18x enrichment, the strongest of any unused field in the
    database, and the safest.
    """

    def names(values: pd.Series) -> list[str]:
        out = {normalize_name(v) for v in values.dropna()}
        return sorted(n for n in out if n)

    return (appearances.groupby("person_id")["firm_name"].agg(names)
            .rename("firms"))


def _drop_self_referential_firms(people: pd.DataFrame) -> pd.Series:
    """Drop firms named after the person themselves — that is the name twice.

    *Mazzeo Mazzei e compagni* is named for Mazzeo Mazzei, so any two records of
    that name share it automatically, whether or not they are one man. Counting
    it would be the `name` comparison wearing a hat. A firm named for somebody
    ELSE is exactly the evidence this feature exists for and stays.
    """
    own = [
        {t for column in ("first_norm", "last_norm") for t in str(row[column] or "").split()
         if len(t) >= 3}
        for _, row in people[["first_norm", "last_norm"]].iterrows()
    ]
    return pd.Series(
        [sorted(f for f in firms if not any(t in f for t in mine))
         for firms, mine in zip(people["firms"], own)],
        index=people.index, name="firms",
    )


def _drop_self_referential_tokens(people: pd.DataFrame) -> pd.Series:
    """Remove a person's own name from their own firm tokens.

    A Florentine firm is named after its partners — *Giovanni Corsi e
    compagni* — so tokenising the firm name puts the person's own given name
    and surname straight back into their own `firm_tokens`. Two rows of the
    same name then intersect on those tokens automatically, and the
    `contemporaneity` comparison reads it as independent evidence that they are
    one man. It is not evidence of anything: it is the name, counted twice, once
    by the `name` comparison and again by the network half of this one. Measured before this guard, one pair
    (11820/12009) shared *no* partners at all yet scored the firm-token level
    on the tokens `gianfigliazzi` and `leonardo` — its own name echoed back.

    Only the person's own chain is removed. A partner's name appearing in the
    firm title is exactly the network evidence this feature is for, and stays.
    """
    # No length filter here: `_firm_tokens` already keeps only tokens of 3+
    # characters, so a shorter own-name token could not be present to remove.
    own = [
        {t for column in ("first_norm", "last_norm", "patronymic_norm", "grandfather_norm")
         for t in str(row[column] or "").split()}
        for _, row in people[["first_norm", "last_norm",
                              "patronymic_norm", "grandfather_norm"]].iterrows()
    ]
    return pd.Series(
        [sorted(set(tokens) - mine) for tokens, mine in zip(people["firm_tokens"], own)],
        index=people.index, name="firm_tokens",
    )


def _roles(conn: sqlite3.Connection) -> pd.DataFrame:
    """gp/lp counts per person, and the dominant role (ties → 'mixed')."""
    rows = conn.execute(
        """
        SELECT i.person_id AS person_id,
               lower(trim(inv.type)) AS role,
               COUNT(*) AS n
        FROM investor i
        JOIN contract c        ON c.contract_id = i.contract_id AND c.is_deleted = 0
        JOIN investor_group g  ON g.investor_id = i.investor_id AND g.is_deleted = 0
        JOIN investment inv    ON inv.investment_id = g.investment_id AND inv.is_deleted = 0
        WHERE i.is_deleted = 0
          AND lower(trim(COALESCE(inv.type, ''))) IN ('gp', 'lp')
        GROUP BY i.person_id, role
        """
    ).fetchall()
    counts = (
        pd.DataFrame([dict(r) for r in rows])
        .pivot(index="person_id", columns="role", values="n")
        .fillna(0)
        .astype(int)
    )
    for role in ("gp", "lp"):
        if role not in counts:
            counts[role] = 0
    counts = counts.rename(columns={"gp": "n_gp", "lp": "n_lp"})
    counts["dominant_role"] = counts.apply(
        lambda r: "gp" if r["n_gp"] > r["n_lp"] else ("lp" if r["n_lp"] > r["n_gp"] else "mixed"),
        axis=1,
    )
    return counts[["n_gp", "n_lp", "dominant_role"]]


def _context_profiles(conn: sqlite3.Connection) -> pd.DataFrame:
    """Verbatim contextual evidence for review display, never model weights.

    These fields are promising but correlated with firms and business circles.
    Keeping them in the cache lets historians label with the full record while
    preventing an unlabeled model from multiplying them as independent facts.
    """
    rows = conn.execute(
        """
        SELECT i.person_id,
               NULLIF(TRIM(i.profession), '') AS profession,
               residence.place_name AS residence,
               origin.place_name AS origin,
               t.title_name AS title,
               parent_title.title_name AS father_mother_title,
               grandfather_title.title_name AS grandfather_title,
               husband_title.title_name AS husband_title,
               activity.activity AS economic_activity
        FROM investor i
        JOIN contract c ON c.contract_id=i.contract_id AND c.is_deleted=0
        LEFT JOIN place residence
          ON residence.place_id=i.place_of_residence AND i.place_of_residence <> 0
        LEFT JOIN place origin
          ON origin.place_id=i.place_of_origin AND i.place_of_origin <> 0
        LEFT JOIN title t ON t.title_id=i.title AND i.title <> 0
        LEFT JOIN title parent_title
          ON parent_title.title_id=i.title_father_mother AND i.title_father_mother <> 0
        LEFT JOIN title grandfather_title
          ON grandfather_title.title_id=i.title_grandfather AND i.title_grandfather <> 0
        LEFT JOIN title husband_title
          ON husband_title.title_id=i.title_husband AND i.title_husband <> 0
        LEFT JOIN economic_activity activity
          ON activity.ec_activity_id=c.economic_sector AND c.economic_sector <> 0
        WHERE i.is_deleted=0
        """
    ).fetchall()
    columns = (
        "professions",
        "residences",
        "origins",
        "titles",
        "father_mother_titles",
        "grandfather_titles",
        "husband_titles",
        "economic_activities",
    )
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame([dict(row) for row in rows])

    def values(series: pd.Series) -> list[str]:
        return sorted(
            {
                str(value).strip()
                for value in series
                if value is not None and pd.notna(value) and str(value).strip()
            },
            key=str.casefold,
        )

    return frame.groupby("person_id").agg(
        professions=("profession", values),
        residences=("residence", values),
        origins=("origin", values),
        titles=("title", values),
        father_mother_titles=("father_mother_title", values),
        grandfather_titles=("grandfather_title", values),
        husband_titles=("husband_title", values),
        economic_activities=("economic_activity", values),
    )


def _husbands(conn: sqlite3.Connection) -> pd.DataFrame:
    """Husband names from a woman's investor rows — for the 242 women the
    richest field they have (59% filled), and stable across the wife→widow
    transition (person 10880/11346, both 'widow of Raffaello Torrigiani')."""
    rows = conn.execute(
        """
        SELECT i.person_id AS person_id,
               i.husband_first_name AS husband_first,
               i.husband_last_name  AS husband_last
        FROM investor i
        JOIN contract c ON c.contract_id = i.contract_id AND c.is_deleted = 0
        WHERE i.is_deleted = 0
          AND (TRIM(COALESCE(i.husband_first_name, '')) <> ''
            OR TRIM(COALESCE(i.husband_last_name, ''))  <> '')
        """
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["husband_first_norm", "husband_last_norm"])
    frame = pd.DataFrame([dict(r) for r in rows])
    frame["husband_first_norm"] = frame["husband_first"].map(normalize_name)
    frame["husband_last_norm"] = frame["husband_last"].map(normalize_name)
    # most frequent non-empty value per person (scribes vary; the man does not)
    return frame.groupby("person_id")[["husband_first_norm", "husband_last_norm"]].agg(
        lambda s: s[s != ""].mode().iat[0] if (s != "").any() else ""
    )


def load_person_spine(
    conn: sqlite3.Connection, *, hub_threshold: int = HUB_CONTRACT_THRESHOLD
) -> pd.DataFrame:
    """One row per live person with every linkage input. See module docstring.

    Columns: the verbatim name fields; their normalized twins; appearance
    counts; the career window and posthumous terminus; partner/firm/contract
    arrays; role counts and dominance; husband names.
    """
    people = pd.DataFrame(
        [
            dict(r)
            for r in conn.execute(
                """
                SELECT person_id, first_name, father_mother, grandfather,
                       last_name, nickname, is_woman
                FROM person WHERE is_deleted = 0
                """
            ).fetchall()
        ]
    ).set_index("person_id")

    for source, target in (
        ("first_name", "first_norm"),
        ("father_mother", "patronymic_norm"),
        ("grandfather", "grandfather_norm"),
        ("last_name", "last_norm"),
    ):
        people[target] = people[source].map(normalize_name)
    people["entity_kind"] = [
        classify_entity_kind(r.first_name, r.father_mother, r.grandfather, r.last_name)
        for r in people.itertuples()
    ]
    people["full_name_norm"] = (
        (people["first_norm"] + " " + people["last_norm"]).str.strip().str.replace(r"\s+", " ", regex=True)
    )

    appearances = _appearances(conn)
    people["n_appearances"] = appearances.groupby("person_id").size()
    people["n_posthumous"] = appearances.groupby("person_id")["posthumous"].sum()
    people["contracts"] = appearances.groupby("person_id")["contract_id"].agg(lambda s: sorted(set(s)))

    people = people.join(_career_windows(appearances))
    people = people.join(_partner_sets(appearances, hub_threshold))
    people = people.join(_partner_network_all(appearances))
    people = people.join(_firm_tokens(appearances))
    people = people.join(_firm_names(appearances))
    people = people.join(_roles(conn))
    people = people.join(_context_profiles(conn))
    people = people.join(_husbands(conn))

    # A person with no live appearances has empty arrays, not missing ones —
    # 856 such rows exist (the batch-entry ghosts), and they are candidates too.
    for column in (
        "contracts",
        "partners",
        "partners_all",
        "firm_tokens",
        "firms",
        "professions",
        "residences",
        "origins",
        "titles",
        "father_mother_titles",
        "grandfather_titles",
        "husband_titles",
        "economic_activities",
    ):
        people[column] = people[column].apply(lambda v: v if isinstance(v, list) else [])
    people["partner_events_all"] = people["partner_events_all"].apply(
        lambda value: value if isinstance(value, dict) else {}
    )
    # After the arrays are real lists and the name columns exist: a firm named
    # after its partners must not hand a person their own name back as network
    # evidence. See `_drop_self_referential_tokens`.
    people["firm_tokens"] = _drop_self_referential_tokens(people)
    people["firms"] = _drop_self_referential_firms(people)
    counts = ["n_appearances", "n_posthumous", "n_dated", "n_partners", "n_gp", "n_lp"]
    people[counts] = people[counts].fillna(0).astype(int)
    people["dominant_role"] = people["dominant_role"].fillna("unknown")
    for column in ("husband_first_norm", "husband_last_norm"):
        people[column] = people[column].fillna("")

    return people.reset_index()
