# Corrections workflow (`/corrections`)

Status: **implemented (v1)**. Companion to [review_platform.md](review_platform.md)
(§9 cross-panel effects, §11.3 data contract). Read with [../AI.md](../AI.md) and
[../AGENTS.md](../AGENTS.md) — corrections write to the live database, so the
guardrails there are binding.

## 1. The one job

Corrections answers a single question per item:

> **Should this DB field change — and what is the justification?**

It is the only tool that *writes* to `data/sqlite/main.db`. Everything else
reads. Writes are deliberate, reversible, and audited.

## 2. Lifecycle

A proposal moves through an explicit state machine:

```
draft ─▶ proposed ─▶ approved ─▶ applied ─▶ (reverted)
                 ╲          ╲
                  ╲          ╰▶ (re-check fails → blocked)
                   ╰▶ rejected
```

- **proposed** — captured (usually via the "Suggest a fix" bridge in `/database`).
- **approved / rejected** — a human adjudicates. No DB write yet.
- **applied** — the gated write: re-read the current DB value, compare to the
  snapshot pre-image (abort on drift), write inside a transaction, log an event
  with pre- and post-images.
- **reverted** — restore the pre-image (only meaningful after applied); also a
  transactional write + event.

Two stores under `data/derived/word-pipeline/10_corrections/` (gitignored):

| File | Shape | Role |
|------|-------|------|
| `corrections_proposals.jsonl` | snapshot — latest state per `proposal_id` (rewritten) | current state |
| `corrections_events.jsonl` | append-only | full audit timeline (created / approved / rejected / applied / reverted) with pre/post images |

Keyed on stable identities (`proposal_id`, `db_row_id`, `source_entry_key`) so a
derived-layer rebuild never orphans a human action.

## 3. Guardrails (binding)

- **Never auto-apply** (`AI.md`). Approval and apply are distinct, human-triggered
  actions, each recorded as an event.
- **Schema-validated.** `table` ∈ {contract, sub_contract, person}; `field` must be
  a real column (checked against `PRAGMA table_info`) and in the v1 correctable
  registry. No fabricated columns.
- **Reversible.** The pre-image is snapshotted at proposal time and re-verified at
  apply; every applied/reverted write logs both images.
- **Drift-safe.** If the live DB value no longer matches the snapshot pre-image at
  apply time, apply is **blocked** — the proposal must be re-confirmed against the
  new value. (`evidence_fingerprint` also covers the source quote.)
- **Never delete rows** (`AGENTS.md`). Corrections are targeted `UPDATE`s only.

## 4. v1 scope

**Editable fields** (scalar, safe to edit as plain values; foreign keys deferred):

| Table | Fields |
|-------|--------|
| `contract` | firm_name, registration_date, start_date, folio, total, duration_months |
| `sub_contract` | sub_firm_name, sub_type (enum), registration_date, end_date, renewal_months, folio |
| `person` | first_name, father_mother, grandfather, last_name, nickname |

**Change types:** `correct` (value → value), `fill_missing` (empty → value),
`flag_uncertain` (annotation only — never writes to the DB).

**Source quote is encouraged but not required** (per decision): a correction needs
*either* a `source_quote` from a linked Word entry *or* a non-empty `rationale`
(editorial judgment). The origin (`manual` / `agent_suggested`) is always recorded.

## 5. UX

### 5.1 Suggest a fix (bridge, in `/database`)

Each correctable field on a record shows a quiet "Suggest fix" affordance. It opens
a right-side drawer (same family as the Word-source drawer) pre-filled with the
field, the table/row, and the **current value** (the pre-image). The reviewer:

- enters the proposed value with a **type-aware input** (date / number / enum
  dropdown / text),
- picks the **change type**,
- optionally attaches a **source**: one of the record's linked Word entries plus an
  exact `source_quote`,
- writes a **rationale**,
- submits → a `proposed` row is written. **No DB write.** The field then shows a
  "change pending" chip.

### 5.2 Review board (`/corrections`)

Queue rail (filter by status / table / origin) beside a single-proposal detail:

- **before → after** diff (strike old, highlight new),
- the **source quote** in a citation block + a button to open the Word entry/image,
- a **staleness banner** if the live DB value has drifted from the snapshot,
- a slim action bar (reviewer remembered): **Approve / Reject** (when proposed),
  **Apply** (when approved), **Revert** (when applied).

## 6. Cross-stream ties

- **Reconcile → Corrections.** A confirmed link scopes the row and supplies the
  `source_entry_key` / quote; the "verify date/folio" Medium tier is the natural
  seed. A proposal can carry `link_review_id` back to the reconcile case.
- **Corrections → Database.** A record's fields show **pending** chips and, after
  apply, an **edited** provenance chip (old → new · who · when) linking to the audit
  event. The database browser is where you see the change land.
- **Corrections → Reconcile (feedback).** Fixing a date/folio can dissolve a
  reconcile conflict; on the next `match-db` run that case should leave High.
- **Changes is upstream.** A `source_quote` may come from tracked-changes text; the
  `evidence_fingerprint` guards against later editorial drift.

## 7. API

```
GET  /api/corrections                 list (filter: status, table, origin)
POST /api/corrections                 create a proposal (captures pre-image)
GET  /api/corrections/{id}            one proposal + live drift check
POST /api/corrections/{id}/approve    proposed → approved
POST /api/corrections/{id}/reject     proposed → rejected
POST /api/corrections/{id}/apply      approved → applied (transactional write)
POST /api/corrections/{id}/revert     applied → reverted (restore pre-image)
```

Apply/revert re-read the current value, compare to the stored pre-image, write in a
transaction, and append an event with both images. Record detail
(`GET /api/db/record/...`) carries per-field `editable`/`input_type`/`options` and
the latest `correction` (pending or applied) for that column.
