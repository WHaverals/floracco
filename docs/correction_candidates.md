# Correction candidates — "which DB rows need a reviewer's eyes?"

Status: implemented (Phase 1 + Phase 2 tracked-change enrichment + contract-scoped person picker).
Companion to [corrections_workflow.md](corrections_workflow.md) and
[review_platform.md](review_platform.md).

## The one job

Surface, in a ranked queue, the **database rows/fields most likely to need correction**, with the
evidence for *why*, so a reviewer can decide. Candidates are **hypotheses, not proposals** — they
never write to the database and they never pre-fill a value.

Two framing rules, set by the project lead:

1. **The database is the truth we are perfecting. Word is evidence that helps — never an
   auto-applied value.** A candidate shows the **DB value and the Word evidence side by side and
   pre-fills nothing.** The reviewer reads the narrative / tracked change / manuscript image and
   decides what the DB *should* say (which may be the DB value as-is, the Word value, or neither).
2. **The reconcile linker is not touched.** Candidates *read* the linker's outputs (stage 05) and
   the saved review decisions; they do not change matching, the reconcile UI, or any link.

For folio specifically (and as the general rule): **show both, no pre-fill.**

## Candidate vs. proposal

| | Correction candidate | Correction proposal |
|---|---|---|
| Author | derived by a builder | a human |
| Writes to DB | never | only after approve → apply |
| Carries a value | no (evidence only) | yes (or a flag) |
| Lifecycle | regenerated each build; dismissable | `proposed → approved → applied → (reverted)` |
| Store | `correction_candidates.jsonl` (regenerable) | `corrections_proposals.jsonl` (append/update) |

"Draft correction" on a candidate opens the existing proposer **with the value field empty** and the
Word evidence attached — the bridge between the two.

## Signal families (grounded in the current corpus)

### Family 1 — Word ↔ DB conflicts (from the matcher, stage 05 `conflicts`)
Highest precision: the linker already found the linked Word source disagreeing with the DB field.

| reason_code | field | DB value | Word evidence | count* |
|---|---|---|---|---|
| `registration_date_differs` | registration_date | `db_registration_date` | `entry_registration_date_raw` | 162 |
| `folio_differs` | folio | `db_folio_raw` | entry folio (start–end) | 47 |
| `event_type_table_differs` | sub_type (sub_contract) | `db_sub_type` | relationship/label | 9 |
| `db_register_differs` / `db_register_missing` | — (provenance, flag-only) | register | source register | 21 |
| `text_similarity_low` | — | — | — | 2 → **excluded** (link quality, reconcile's job) |

A conflict on a **human-confirmed** link is the strongest candidate (identity is settled, so the
field really should agree) — the server boosts these.

### Family 3 — DB-intrinsic (corrupt / missing; computed from SQLite)

| signal | reason_code | field | strength | count* |
|---|---|---|---|---|
| contract `numerical_discrepancy = 1` | `numerical_discrepancy` | total (flag) | low | 271 |
| contract `registration_date = 0000-00-00` | `db_date_missing` | registration_date | high | 21 |
| person no first **and** last name | `person_no_name` | last_name | medium | 14 |
| sub_contract orphan `main_contract_id` | `orphan_main_contract` | — (flag) | high | 4 |
| sub_contract `registration_date = 0000-00-00` | `db_date_missing` | registration_date | high | 2 |
| sub_contract `sub_type` blank | `missing_sub_type` | sub_type | medium | 1 |
| contract `firm_name` blank | `missing_firm_name` | firm_name | low | 879 |

`numerical_discrepancy` is surfaced **as a low-confidence flag**: it is usually an artifact of the
source contract itself, not a data-entry error — review, but often no change is needed.
`missing_firm_name` is surfaced as low-priority "missing — the Word narrative may supply it."

### Excluded on purpose (would cry wolf)
- sub_contract `sub_firm_name` blank — **3,097 (89%)**: the norm for terminations/variations.
- person `last_name` blank — 536: mostly mononyms / patronymics.
- `temp = 1` — all rows; meaningless.
- sub_contract `end_date < registration_date` — 865: almost certainly semantic, not an error.

### Family 2 — tracked changes touching a DB field (Phase 2, **enrichment only**)
A tracked change that edits a date or folio **does not create a candidate** — it *enriches* the
matcher's already-vetted Family 1 conflict for that row with the revision. This is deliberate: Word
is evidence, not truth, and we never guess which DB field a free-floating number maps to.

- **Trigger:** a Family 1 `registration_date_differs` / `folio_differs` candidate exists for the row,
  **and** the source entry edited a date / folio span (digit-anchored detection, span-level).
- **Date pre-fill:** the enriched date candidate gets a `suggested_value` = **the entry's own
  registration date** (parsed to ISO), never some other date in the narrative. Folios are never
  pre-filled (show both).
- **`revision_evidence`:** removed/added spans **focused on the field** (full date/folio matches, or
  short character-level numeric edits like `2`→`8`), plus the reviser's author + date. Unrelated
  spelling fixes elsewhere in the entry are filtered out so the lens shows the actual change.
- **Deliberately *not* candidates:** durations (`"di 2 in 2 anni"` = *biennially*, not a 2-year term),
  amounts (would mislead), and names (need the person picker — see below). Each needs field-aware
  extraction we cannot do reliably yet.

In the current build: **28** date conflicts enriched (19 with a parseable pre-filled date). Surfaced
in the queue with a "tracked change" tag and a revision-diff lens on the detail card.

\* counts as of the build that produced this doc; regenerated each run.

## Name corrections & the person picker

Names recur heavily in this corpus (one display name maps to many `person_id`s), so a free-text
field is unsafe and name **candidates** are deferred until the picker is proven. The picker itself is
built and usable now:

- **Backend:** `GET /api/db/contract-persons/{contract_id}` — read-only; returns the contract's
  investors with disambiguating context (profession, residence, `#person_id`, how many contracts each
  appears on). No inserts.
- **Frontend (`PersonPicker`):** from a contract record in `/database` → "Correct a person's name",
  the picker lists those investors (filterable) plus an explicit **"search the whole database"** escape
  hatch. Picking a person opens the proposer on that person's surname, carrying the contract's Word
  sources as citable evidence. "New person" is intentionally *not* offered — that needs entity
  resolution we don't attempt; the reviewer records it in the rationale and flags it.
- **NER / LLM / SPLINK:** off the table for now. If used later, strictly for *candidate
  generation / ranking* — **never** decision authority.

## Candidate record (`correction_candidates.jsonl`)

```jsonc
{
  "candidate_key": "sha1:…",            // stable: hash(db_row_id, field, reason_code, evidence)
  "db_row_id": "contract:2682",
  "db_table": "contract",
  "primary_key": {"contract_id": "2682"},
  "field": "registration_date",          // null = flag-only (no directly editable field)
  "field_label": "Registration date",
  "editable": true,                       // field is in the correctable registry
  "input_type": "date",                   // for the drafting input
  "options": null,                        // enum options when input_type == "enum"
  "family": "word_db_conflict",          // or "db_intrinsic"
  "reason_code": "registration_date_differs",
  "title": "Registration date disagrees with the Word source",
  "explanation": "The database date … differs from the Word source …",
  "strength": "high",                    // high | medium | low (precision tier)
  "priority_score": 90,                   // base sort weight (server may boost)
  "db_value": "1500-01-01",              // live DB value at build time (the truth, shown as-is)
  "word_value": "12 maggio 1513",        // Word evidence value — shown, never pre-filled
  "suggested_value": "1513-05-12",       // adjudicated reading to pre-fill (tracked-change dates only); else null
  "revision_evidence": {                  // present only when a tracked change edited this field
    "insertions": ["20 marzo"],          // field-focused added spans
    "deletions": ["19 gennaio"],         // field-focused removed spans
    "author": "samuela", "date": "2020-08-27T12:37:00Z"
  },
  "source_entry_id": "Camera_…_00042",
  "source_entry_key": "Camera_…_ed451a89f495a",
  "register_id": "Camera_di_Commercio_1262",
  "source_folio": "c. 12r",
  "evidence_snippet": "…first ~280 chars of the linked narrative…",
  "generated_at": "2026-…",
  "builder_version": 1
}
```

## Architecture & lifecycle

- **Builder:** `workflows/correction_candidates.py build` reads stage 05 link candidates + SQLite +
  stage 04 source entries, writes `data/derived/word-pipeline/10_corrections/correction_candidates.jsonl`.
  Regenerable and diffable; never hand-edited. Does **not** write to SQLite.
- **`candidate_key`** is content-stable, so a dismissal sticks unless the evidence moves.
- **Dismissal log:** `correction_candidate_dismissals.jsonl` (append-only), keyed on `candidate_key`
  with a reason (e.g. "expected: original vs current foliation"). Dismissed candidates are hidden by
  default and shown under a filter.
- **Self-cleaning:** apply a fix → re-run `match-db` + rebuild candidates → the conflict is gone →
  the candidate is not regenerated. Filling a blank field likewise drops its "missing" candidate.
- **Live annotation (server, read-only):** at request time the server marks each candidate with
  `link_confirmed` (from `review_decisions.csv`), `dismissed`, and `existing_proposal` (an open/applied
  proposal already covers this field) so handled rows fall to the bottom or out of view.

## Server API (read + dismiss only; writes go through the proposals API)

- `GET  /api/correction-candidates` — ranked, filterable (family, reason, table, register, strength,
  `include_dismissed`, `include_handled`); annotated with link/dismissal/proposal state.
- `POST /api/correction-candidates/{candidate_key}/dismiss` — `{reviewer, reason}` → append to the
  dismissal log.
- Drafting a fix reuses `POST /api/corrections` (origin `manual`) with the candidate's evidence
  attached and **no proposed value**.

## UX — `/corrections`, second mode "Possibly needs correction"

A toggle at the top of the Corrections tool switches between **Proposals** (the existing review
board) and **Possibly needs correction** (the candidate queue). The candidate card shows the **DB
value and the Word evidence side by side, nothing pre-filled**, plus "Open Word entry / image",
**Draft correction** (→ proposer, empty value), and **Dismiss with reason**. Manual "Suggest a fix"
from `/database` is unchanged.
