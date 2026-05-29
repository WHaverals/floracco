# Data dictionary

Field-by-field reference for the Florentine Accomandite Corpus database. For the
physical layout of files and the database, see [data_layout.md](data_layout.md).
For historical, legal, and economic terms, see [glossary.md](glossary.md).
Operational rules for agents are in [AGENTS.md](../AGENTS.md).

**Status: draft for review.** This dictionary reconciles three sources (below).
Where the sources disagree or a meaning is interpretive, the entry is marked
**FT review pending** in the spirit of the glossary, and should be confirmed by
Francesca Trivellato before being used as authoritative documentation or as
controlled vocabulary in queries, notebooks, or database updates.

## Sources

This dictionary is compiled from three artifacts, none of which is complete on
its own:

| Source | What it gives | File |
|--------|---------------|------|
| **Current SQLite schema** | The columns and SQL types that actually exist in the working DB | `queries/schema/tables.sql` |
| **Original schema spec (Feb. 2014)** | The developer's original design, foreign keys, enums, and behavioral comments | `docs/schema/schema Feb. 2014.xlsb` |
| **Data-entry rules** | How a human encoder fills each field from the contract documents | `docs/schema/DB structure and input rules.xls` |

The schema **drifted** between the 2014 design and the current database (columns
were added, split, moved, or renamed). This document describes the **current
database** (`tables.sql`) as the primary structure, and notes the 2014 lineage
where it clarifies intent.

### Authority hierarchy (unchanged from project rules)

For contract *content*: **Word narratives → SQLite → agent suggestions.** The
database is a structured mirror and may lag the authoritative Word files. This
dictionary documents what the database fields *mean*, not that any given value is
correct.

## How to read this document

### Conventions

- **Type** is the column type as declared in the current SQLite schema
  (`tables.sql`). SQLite stores these flexibly; `tinyint(1)` columns are used as
  booleans (`0`/`1`).
- **Required** reflects `NOT NULL` in the schema and/or "required" in the
  data-entry rules.
- **Input rule** quotes or paraphrases `DB structure and input rules.xls`.
- **Observed** notes come from a snapshot of the working `main.db` and are
  descriptive of the current data, not prescriptive.

### Recurring patterns

- **Boolean flags (`tinyint(1)`)** encode yes/no answers from the data-entry
  rules. `0` = no/false (and usually the default), `1` = yes/true.
- **The `_db` suffix = "attributed by the database team."** Several flags
  (`economic_sector_db`, `place_db`, `jewish_db`) mark information that is
  **inferred by the encoders** rather than stated explicitly in the contract.
  This is a crucial provenance distinction: a `1` means "we deduced this," not
  "the document says this." Keep these separate when analyzing.
- **`discretion` flags** mark explicit open-ended formulas in the document
  (e.g. "e in ogni altro luogo" for place, "et altre cose" for activity). They
  are **not** set merely because an activity implies some breadth. See
  *Discretion* in the glossary.
- **`temp`** appears on most content tables. It comes from the original
  application's workflow and is **not** a corpus field; see [The `temp` flag](#the-temp-flag).
- **Modern-calendar dates.** All dates were entered "according to the modern
  calendar." In the source documents the month is written out in Italian; the
  encoders converted Florentine-style dating where needed. See
  [Date conventions](#date-conventions).

### Key identifiers

| ID | Meaning |
|----|---------|
| `person_id` | One **human being**, stable across all contracts they appear in. The encoder is instructed to *search for an existing person before creating a new one*. |
| `investor_id` | One **appearance of a person on one contract** (one role on one act). The same `person_id` yields many `investor_id`s over time. |
| `contract_id` | The main accomandita record. Also the **primary key of `sub_contract`** (each subcontract has its own `contract_id`), linked back to its parent via `sub_contract.main_contract_id`. |
| `investment_id` | One capital tranche on a contract. Linked to investors through `investor_group`. |

> **FT review pending:** Same display name ≠ same person. `person_id` is meant to
> carry identity, but de-duplication quality across decades is exactly what the
> entity-disambiguation work (OpenRefine / SPLINK) is meant to test. Do not treat
> `person_id` as a guaranteed unique-human key without review.

## Data model overview

```
contract ──< contract_place >── place
   │  └─ currency_id ─> currency
   │  └─ economic_sector ─> economic_activity
   │
   ├──< investor >── person
   │       └─ title / title_* ─> title
   │       └─ place_of_residence / place_of_origin ─> place
   │
   ├──< investment >
   │       └──< investor_group >── investor   (joint investments)
   │
   └──< sub_contract  (main_contract_id ─> contract.contract_id)

title, place, currency, economic_activity = shared lookup lists
admin = application users (not corpus data)
```

- A **contract** has one or more **investors** (people in a role) and one or more
  **investments** (capital tranches).
- **`investor_group`** is the many-to-many bridge that lets several investors
  share one investment ("joint" investments). This replaced the 2014 design,
  which hard-coded only two investors per investment
  (`investor_id_1`, `investor_id_2`).
- A **sub_contract** is a later act (termination, renewal, balance, variation)
  attached to a main contract.

---

## Table: `contract`

The main accomandita record. One row per registered new partnership.
2014 note: *"search for existing before creating a new contract."*

| Column | Type | Required | Meaning / input rule | Notes |
|--------|------|----------|----------------------|-------|
| `contract_id` | int | PK | Internal contract identifier. | Original number in the Word narratives is the event number (e.g. `[Nuova] 1922`); see matching workflow. |
| `archive` | varchar(20) | | Holding archive. Input rule example: "ASF = Archivio di Stato, Florence." | Observed values: mostly `ASF`, one `ASL`, plus blanks/whitespace. |
| `series` | varchar(150) | | Archival series. Input rule example: "Mercanzia." | Observed spelling variants (`Mercancia`, `Mercazia`) and folder values leaked into this field; a known data-quality issue. |
| `folder` | varchar(20) | | Archival folder/register number, e.g. `10845`. | Links Word file ↔ image folder ↔ DB; see `data_layout.md`. |
| `folio` | varchar(20) | | Folio reference. Input rule: "numbers only, without cc. or fols., but with indication of recto and verso." | Word narratives keep `c.`/`cc.` prefixes; the pipeline normalizes both. |
| `registration_date` | date | NOT NULL | Date of registration, **modern calendar**. Input rule: "When no date is given, insert the date of previous act." | Observed: ~21 rows hold `0000-00-00` (placeholder/missing); a known issue. |
| `firm_name` | varchar(100) | | Firm name — "what in the documents is usually indicated as *sotto nome di…*". | 2014 width was 50; widened to 100. See *Firm name / sotto nome di* in glossary. |
| `pl_discretion` | tinyint(1) | default 0 | **Place** discretion. `1` when the document uses an open formula such as "e in ogni altro luogo." | Maps to "Discretion y/n" (place) in the input rules. |
| `economic_sector` | int | | The contract's economic activity, as a **foreign key into `economic_activity`**. | Despite the name "sector," it points to a single free-text activity row. Input rule for the activity text: "Enter exactly as it appears in the document." One activity per contract. |
| `economic_sector_db` | tinyint(1) | NOT NULL default 0 | **Attributed by database.** `1` when no explicit activity is stated but it can be deduced from the contract. | Observed: rarely set (~19 rows). See the `_db` pattern above. |
| `ec_discretion` | tinyint(1) | default 0 | **Economic activity** discretion. `1` only when the document is explicit, e.g. "et altre cose." | Input rule is emphatic: do **not** set it merely because an activity implies breadth (example given: contract 4087). |
| `start_date` | date | | Partnership start date, modern calendar. Input rule: "When only month is given, first day of the month is entered. When no start date is specified, date when partnership was signed is entered." | |
| `duration_months` | int | | Expected duration at registration, in months. | **Sentinel: `-1`** means duration not specified or vague (e.g. "a beneplacito di ciascuna delle parti"). Observed in ~73 rows. |
| `automatic_renewal` | tinyint(1) | default 0 | `1` when the contract renews automatically unless terminated before expiry. | See *Automatic renewal* in glossary. |
| `automatic_renewal_months` | int | | If renewal is automatic, the number of months each automatic renewal lasts. | |
| `clauses` | tinyint(1) | default 0 | `1` when specific conditions/agreements beyond the standard registration formula are present. | |
| `administrators` | tinyint(1) | NOT NULL default 0 | `1` when someone other than a General Partner is named as managing/co-managing the partnership, **or** when the GP name differs from the firm name. | Present in current schema and input rules; not in the 2014 column list. See *Administrators* in glossary. |
| `total` | int | | Total sum invested (as stated, or corrected on review). Input rule: "Omit submultiples." | 2014 comment: "this total may conflict with investment totals" — by design; see `numerical_discrepancy`. |
| `numerical_discrepancy` | tinyint(1) | NOT NULL default 0 | `1` when the total and partial investments do not coincide, or when some investors contribute an unspecified cash amount. | |
| `additional_docs` | tinyint(1) | NOT NULL default 0 | Flags presence of additional supporting documents. | 2014 spec note: "added 24 Dec 2022." Exact scope **FT review pending**. |
| `currency_id` | int | | Currency of the contract, foreign key into `currency`. | Currency text entered "exactly as it appears in the document." |
| `document` | longtext | | **Full text** of the contract (summary transcription). | 2014 stored this as a `blob`/link to contract text. This mirrors (and may lag) the authoritative Word narrative; useful as a text-similarity signal in matching, not a separate archival source. |
| `temp` | tinyint(1) | default 1 | Application workflow flag. | See [The `temp` flag](#the-temp-flag). Observed: always `1`. |

> **Lineage:** In the 2014 design, place(s) of activity lived directly on the
> contract (`place_of_activity_1`, `place_of_activity_2`). They were moved into
> the **`contract_place`** join table (see below), and the single `discretion`
> flag was split into `pl_discretion` (place) and `ec_discretion` (activity).

---

## Table: `contract_place`

Operating locations of a contract. Join table between `contract` and `place`,
allowing the "up to 3 places of activity" from the input rules (and an optional
address).

| Column | Type | Required | Meaning / input rule | Notes |
|--------|------|----------|----------------------|-------|
| `place_id` | int | PK (composite) | Place, foreign key into `place`. | Input rule for place text: "Slight modernization, e.g. Lecci = Lecce." |
| `contract_id` | int | PK (composite) | The contract this place belongs to. | |
| `address` | varchar(100) | | Optional finer address: "When a particular street or square name or other address-like information is included." | |
| `place_db` | tinyint(1) | NOT NULL default 0 | **Attributed by database** flag for the place. | See note below; **FT review pending**. |

> **FT review pending (source ambiguity):** The data-entry spreadsheet labels the
> place attribution row identically to the economic-activity one ("Economic
> activity attributed by database?"), apparently a copy/paste in the original. By
> position it corresponds to the place "attributed by database" flag now stored as
> `place_db`. Confirm this mapping.

---

## Table: `sub_contract`

Later acts attached to a main contract: terminations, renewals, balances, and
variations. One row per subsequent act.

2014 matching note: *"search for matching firm name, primary_contract
registration date, firm name, first GP. If no match found, set up empty
new_main contract."*

| Column | Type | Required | Meaning / input rule | Notes |
|--------|------|----------|----------------------|-------|
| `contract_id` | int | PK | The subcontract's **own** identifier. | Not the parent; the parent is `main_contract_id`. |
| `archive` | varchar(20) | NOT NULL | Holding archive. | |
| `series` | varchar(150) | NOT NULL | Archival series. | |
| `folder` | varchar(20) | NOT NULL | Archival folder/register. | |
| `folio` | varchar(20) | NOT NULL | Folio reference. | |
| `registration_date` | date | NOT NULL | Registration date, modern calendar. Input rule: "When no date is given, date of previous registered act is used." | 2014 note: "for terminations can also be end date when end_date field is null." |
| `end_date` | date | | Partnership end date. Input rule: "Leave blank when not mentioned explicitly… When only month but not day is given, last day of the month is entered." | |
| `renewal_months` | int | | Number of months of renewal. | Present in current schema (the 2014 design carried renewal only on the main contract). |
| `sub_firm_name` | varchar(100) | | Firm name **only if different** from the main contract. | |
| `document` | longtext | | Full text of the subcontract (summary transcription). | As with `contract.document`. |
| `sub_type` | TEXT | | Type of subsequent act. Controlled values: **`termination`, `renewal`, `balance`, `variation`**. | Observed counts: termination ≫ variation > balance > renewal (one row `NULL`). These DB categories may not map one-to-one onto the Italian bracket tags in the Word files; see *Balance / renewal / termination / variation* in glossary. |
| `main_contract_id` | int | NOT NULL | The parent contract. 2014: "enforce foreign key (may be empty contract)." | An "empty" main contract may be created when a subsequent act has no matched parent. |
| `temp` | tinyint(1) | default 1 | Application workflow flag. | |

> **Subcontract usage rule:** "We use data from subcontracts to
> complement/correct those in the main contract (e.g. names, residence). However,
> we normally use the titles from the main contracts rather than those from
> subcontracts." See *Subcontract* in glossary.

---

## Table: `person`

One human being, intended to be stable across contracts.
2014 note: *"search for existing before creating a new person."*

| Column | Type | Required | Meaning / input rule | Notes |
|--------|------|----------|----------------------|-------|
| `person_id` | int | PK | Stable person identifier. | See [Key identifiers](#key-identifiers). |
| `first_name` | varchar(100) | | Given name. Input rule: "Minimal modernization, e.g. Lionardo → Leonardo, Raffael → Raffaello, Vergilio → Virgilio." | |
| `father_mother` | varchar(50) | | Patronymic/matronymic. Input rule: "When father's or grandfather's name does not appear but can be inferred with certainty from other contracts, they are added." | See *Quondam / fu* in glossary for deceased-parent formulas. |
| `grandfather` | varchar(20) | | Grandfather's name. | |
| `last_name` | varchar(200) | | Surname. Input rule: "Foreign last names sometimes require tweaking because their spelling in the documents is phonetical and irregular." | A core target for entity disambiguation (typos/variants vs. same-name-different-person). |
| `nickname` | varchar(50) | | Nickname. | |
| `is_woman` | tinyint(1) | default 0 | `1` if the person is a woman. | |
| `temp` | tinyint(1) | default 1 | Application workflow flag. | |

> **Lineage:** The 2014 design placed `citizen_florence` on `person`. In the
> current schema, citizenship is recorded **per appearance** on `investor`
> instead (see below).

---

## Table: `investor`

One person's appearance and characteristics on **one** contract. This is where
role-, status-, and presence-related attributes live. Maps to the input-rule
section "CHARACTERISTICS OF THE PERSON AND THE INVESTMENT."

| Column | Type | Required | Meaning / input rule | Notes |
|--------|------|----------|----------------------|-------|
| `investor_id` | int | PK | One appearance of one person on one contract. | |
| `person_id` | int | NOT NULL | The underlying person (FK `person`). | |
| `contract_id` | int | NOT NULL | The contract (FK `contract`). | |
| `title` | int | | The person's own title/status (FK `title`). | See `title` lookup notes. |
| `title_husband` | int | | Husband's title (FK `title`). | |
| `title_grandfather` | int | | Grandfather's title (FK `title`). | |
| `title_father_mother` | int | | Father's/mother's title (FK `title`). | |
| `husband_first_name` | varchar(20) | | Husband's given name. | Recorded for married women / widows. |
| `husband_last_name` | varchar(20) | | Husband's surname. | |
| `place_of_residence` | int | | Place of residence (FK `place`). Input rule: "When in doubt, choose place of origin." | |
| `place_of_origin` | int | | Place of origin (FK `place`). | Added relative to 2014 (which carried only residence). |
| `profession` | varchar(100) | | Profession. Input rule: "Enter only when explicitly mentioned. Use this field to indicate whether someone is a nobleman or a citizen of a city other than Florence." | |
| `citizen_florence` | tinyint(1) | NOT NULL default 0 | `1` if a citizen of Florence. | Observed: ~1,569 of ~17,495 investors. Moved here from `person` since 2014. |
| `is_widow` | tinyint(1) | default 0 | `1` if a widow. | See *Vedova* in glossary. |
| `is_guardian` | tinyint(1) | NOT NULL default 0 | `1` if acting as guardian/tutor. | |
| `guardian_of` | varchar(200) | | Names of the children for whom (typically) a woman is tutor. | |
| `is_jewish` | tinyint(1) | default 0 | `1` if Jewish (as stated). | |
| `jewish_db` | tinyint(1) | NOT NULL default 0 | **Attributed by database:** `1` when inferred from surname or other elements but **not** stated explicitly in the contract. | Provenance flag; keep separate from `is_jewish`. |
| `is_convert` | tinyint(1) | default 0 | `1` if a convert. | |
| `via_proxy` | tinyint(1) | default 0 | `1` if acting via proxy. | Input rule: proxy names are **not** entered **except** when the proxy is a woman — then her name is recorded instead of those she acts for (often a guardian of minors). See *Proxy* in glossary. |
| `is_joint` | tinyint(1) | NOT NULL default 0 | `1` when the investment is joint with other investors (more names share one investment). | Realized structurally via `investor_group`. |
| `heirs` | tinyint(4) | NOT NULL default 0 | "& heir(s)?" — i.e. *ed eredi* ("and heirs"). | Distinct from `heirs_of`; see *Eredi di / ed eredi* in glossary. |
| `heirs_of` | tinyint(4) | NOT NULL default 0 | "heirs of?" — i.e. *Eredi di* ("heirs of"). | |
| `and_c` | tinyint(4) | NOT NULL default 0 | "& C?" — i.e. *e compagni* ("and partners/company"). | |
| `temp` | tinyint(1) | default 1 | Application workflow flag. | |

> **Note:** The investor's **role** (general vs. limited partner) is not stored on
> `investor`; it lives on the linked **`investment.type`** (`gp`/`lp`).

---

## Table: `investment`

One capital tranche associated with a contract. Maps to the input-rule section
"EACH INVESTOR/INVESTMENT."

| Column | Type | Required | Meaning / input rule | Notes |
|--------|------|----------|----------------------|-------|
| `investment_id` | int | PK | Investment identifier. | 2014 used a compound key over two investors + contract; current uses a surrogate `investment_id` plus `investor_group`. |
| `contract_id` | int | NOT NULL | The contract (FK `contract`). | |
| `type` | TEXT | | Role of the investment: **`gp`** (general partner) or **`lp`** (limited partner). | Input rule label "contract type: GP/LP." Stored lowercase. See *GP* / *LP* / *accomandante* / *accomandatario* in glossary (**FT review pending** on the historical mapping). |
| `partnership_name` | varchar(200) | | Partnership name for the investment. | |
| `investment_cash` | int | | Cash amount. Input rule: "**NULL is unspecified, zero is none.** When part cash and part in kind, enter the cash figure here." | Observed: ~148 `NULL`, ~4,645 zero. |
| `investment_non_cash` | varchar(200) | | Description of the in-kind amount. Input rule: "When it is not possible to distinguish estimated cash vs. goods, the amount is listed as cash and this field holds the description." | |
| `temp` | tinyint(1) | NOT NULL default 1 | Application workflow flag. | |

---

## Table: `investor_group`

Many-to-many bridge linking investors to investments. This is what makes **joint
investments** possible (several investors sharing one capital tranche), replacing
the 2014 design's fixed two-investor investment.

| Column | Type | Required | Meaning | Notes |
|--------|------|----------|---------|-------|
| `investor_id` | int | PK (composite) | An investor (FK `investor`). | |
| `investment_id` | int | PK (composite) | An investment (FK `investment`). | Observed: ~17,492 rows, roughly one per investor, with joint investments sharing an `investment_id`. |

---

## Lookup tables

These hold shared values referenced by foreign keys. They are working lists, not
yet curated controlled vocabularies; expect variants and duplicates that the
standardization/disambiguation work is meant to clean up.

### `place`

| Column | Type | Required | Meaning |
|--------|------|----------|---------|
| `place_id` | int | PK | Place identifier. |
| `place_name` | varchar(50) | NOT NULL | Place name (slightly modernized). |

Referenced by `contract_place.place_id`, `investor.place_of_residence`,
`investor.place_of_origin`. A geographic gazetteer for operating locations,
residence, and origin.

### `title`

| Column | Type | Required | Meaning |
|--------|------|----------|---------|
| `title_id` | int | PK | Title identifier. |
| `title_name` | varchar(150) | NOT NULL | Title or status label. |

Referenced by the four `investor.title*` columns.

> **FT review pending:** The `title` list mixes honorifics/status (e.g. *barone*,
> *capitano*, *avvocato*) with composite phrases and apparent place names. Treat
> it as raw status/title evidence, not a clean status taxonomy, until reviewed.

### `currency`

| Column | Type | Required | Meaning |
|--------|------|----------|---------|
| `currency_id` | int | PK | Currency identifier. |
| `currency` | varchar(200) | NOT NULL | Currency, entered exactly as in the document. |
| `start_date` | date | | Validity start. |
| `end_date` | date | | Validity end. |
| `ratio` | int | | Conversion ratio. |

Referenced by `contract.currency_id`. Entries range from simple units (*scudi*,
*ducati*, *fiorini*) to descriptive share definitions (e.g. "azioni di lire 500
ciascuna"). See the Money and Accounting section of the glossary.

### `economic_activity`

| Column | Type | Required | Meaning |
|--------|------|----------|---------|
| `ec_activity_id` | int | PK | Activity identifier. |
| `activity` | varchar(200) | NOT NULL | Economic activity, entered exactly as in the document. |

Referenced by `contract.economic_sector`. This is the **raw, free-text** activity
list (e.g. "arte del ritaglio", "negozio di quoiaio"); a controlled
economic-activity taxonomy does not yet exist. See the Economic Activity Terms
section of the glossary and the LLM standardization plan in
`docs/workflows/README.md`.

---

## Application table: `admin`

Not corpus data — accounts for the original web application.

| Column | Type | Required | Meaning |
|--------|------|----------|---------|
| `login` | varchar(10) | PK | User login. |
| `password` | varchar(20) | | Password (legacy app). |
| `name` | varchar(50) | | User name. |
| `email` | varchar(50) | | User email. |
| `admin_level` | int(2) | NOT NULL | Privilege level. |

> Do not expose or commit any credential values from this table.

---

## Cross-cutting conventions

### Date conventions

- All dates are stored "according to the modern calendar"; the source documents
  write the month out in Italian, and early records may use Florentine-style
  double dating (see `docs/workflows/README.md` and the glossary).
- **Missing registration date** on a main contract is filled with the *previous
  act's* date; on a subcontract, with the *previous registered act's* date.
- **Missing start date** is filled with the partnership signing date; a
  month-only start date uses the **first** day of the month.
- **Missing/month-only end date** uses the **last** day of the month; an absent
  end date is left blank.
- Observed placeholder `0000-00-00` values exist in `contract.registration_date`
  (~21 rows) — a known data-quality item, not a valid date.

### Sentinel and "unspecified" values

| Field | Convention |
|-------|-----------|
| `contract.duration_months` | `-1` = duration unspecified or vague (e.g. *a beneplacito*). |
| `investment.investment_cash` | `NULL` = amount unspecified; `0` = explicitly none. |
| `sub_firm_name` | Filled **only** when different from the main contract's firm name. |

### The `_db` ("attributed by database") flags

`economic_sector_db`, `place_db`, and `jewish_db` mark values **inferred by the
encoders**, not stated in the document. Always distinguish "the document says X"
from "we inferred X" in analysis and in any reconciliation proposal.

### The `temp` flag

`temp` appears on `contract`, `sub_contract`, `person`, `investor`, and
`investment`. It derives from the original application's editing workflow
(default `1`). In the current snapshot it is uniformly `1` and carries no
analytical meaning here.

> **FT review pending:** Confirm the original intent of `temp` (e.g. draft vs.
> committed records) before relying on or removing it.

### Schema drift vs. the 2014 design (summary)

| Change | 2014 design | Current database |
|--------|-------------|------------------|
| Place(s) of activity | `place_of_activity_1/2` on `contract` | `contract_place` join table (+ `address`, `place_db`) |
| Discretion | single `discretion` on `contract` | split into `pl_discretion` + `ec_discretion` |
| Citizenship | `citizen_florence` on `person` | `citizen_florence` on `investor` |
| Joint investments | two fixed investor slots per investment | `investment_id` + `investor_group` bridge |
| Place of origin, renewal on subcontract, `administrators`, `additional_docs`, `numerical_discrepancy` | absent or note-only | present as columns |
| Audit `Changes` table | specified in 2014 | **not present** in current SQLite (`tables.sql`) |
| `document` storage | `blob` / link to text | `longtext` (inline full text) |

> The 2014 spec included a `Changes` audit table (per-edit log keyed to `admin`).
> It is **absent** from the current schema dump. Any future write-back/audit
> mechanism (Stage 7 of the reconciliation workflow) should account for this gap.

---

## Open items for review

- Confirm the `place_db` vs. economic-activity attribution label mapping (source
  spreadsheet ambiguity noted above).
- Confirm the historical role mapping `gp`/`lp` ↔ *accomandatario*/*accomandante*
  (tracked in the glossary as **FT review pending**).
- Confirm the intent and disposition of the `temp` flag.
- Define the scope of `additional_docs` (added Dec 2022).
- Decide how DB `sub_type` categories relate to the Italian bracket tags in the
  Word narratives (`[Disdetta]`, `[Rinnovo]`, `[Bilancio]`, `[Modifica]`, …).
- Document data-quality normalizations needed for `archive` and `series`
  (blank/whitespace values, spelling variants such as `Mercancia`/`Mercazia`).
