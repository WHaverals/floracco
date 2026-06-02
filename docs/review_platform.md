# FlorAcco review platform — architecture

Status: **active design**, being built incrementally. This is the reference the
`apps/review` frontend is built against.

Related: [workflows/README.md](workflows/README.md) (the pipeline that feeds it),
[qa_packet_schema.md](workflows/qa_packet_schema.md) (the data contract),
[tracked_changes_word_panel.md](workflows/tracked_changes_word_panel.md).

---

## 1. Principle: a hub of focused tools, not one panel

The platform supports several **distinct jobs** that have different mental
models, data, and rhythms. They must not be crammed into one screen. Instead, an
opening **hub** routes to small, single-responsibility tools. The rule:

> **Each tool does one job and deliberately does *not* do the others.** When a
> tool surfaces work that belongs to another (e.g. a field looks wrong during
> reconciliation), it **links out** with context rather than absorbing that job.

The reviewer (FT) is a historian working interpretively, with no model of the
computational backend. Every tool must be readable, transparent about provenance
and about *what the algorithm saw*, and never encode meaning in colour alone.

## 2. The blocks

| Block | Route | One job | Explicitly NOT here |
|-------|-------|---------|---------------------|
| **Hub / home** | `/` | Choose a task; see overall progress | Any per-case work |
| **Reconciliation** | `/reconcile` | Decide *which DB record(s), if any, a Word entry links to* | Editing tracked changes; editing DB fields |
| **Tracked-changes review** | `/changes` | Accept/reject Word editorial edits; resolve comments | The database (purely about the Word document) |
| **Database browser** | `/database` | *View* contracts / sub-contracts / people; search & explore | Editing anything (read-only) |
| **Database corrections** | `/corrections` | Propose → approve → apply structured field edits, with audit | Linking; free browsing |
| **Dashboard / exports** | `/dashboard` | Progress, coverage, exports | Per-case work |

Notes:

- **Image/folio verification is a shared component, not a top-level tool.** It is
  summoned inside Reconciliation and the Database browser.
- **Tools cross-link** so the workflow stays connected: Reconciliation →
  "field looks wrong" opens a prefilled draft in Corrections; Database browser →
  "view source" opens that register in Changes/Reconcile.

### Why these splits

- **Reconciliation vs. Corrections** are different acts: *which row* (linking) vs.
  *what is in the row* (field values). Keeping them apart lets Reconciliation be a
  fast yes/no/which loop and Corrections be a careful, auditable, field-by-field
  workflow (Stage 5/7).
- **Tracked-changes review is editorial, not relational** — about the Word
  document's truth, and document-centric (read a whole act), unlike the
  case-centric reconciliation queue.
- **Database browsing wants to be read-only and entity-centric** (a person across
  contracts, a firm's sub-contracts) — the opposite of one-case-at-a-time.

## 3. Sitemap & routes

```
/                         Hub: tool cards + progress snapshot
/reconcile                Reconciliation queue (triage tiers)
/reconcile/:reviewId      One reconciliation case (deep link)
/changes                  Registers with pending tracked changes
/changes/:registerId      Editorial review of one register/entry
/database                 DB browser: search + faceted list
/database/:table/:id      One contract / sub_contract / person record
/corrections              Field-correction proposal queue
/corrections/:id          One proposal (review/approve/apply)
/dashboard                Progress, coverage, exports
```

A thin **persistent top nav** (Hub · Reconcile · Changes · Database ·
Corrections · Dashboard) is the global wayfinding. Each tool owns its own *local*
left rail (a single-purpose list/search), so the navigation reads clearly.

Implemented with `react-router-dom`. The app shell is a flex column:
`TopNav` (fixed) + a `route-area` (fills the viewport) that hosts the routed page.

## 4. Per-tool layout sketches

Each tool keeps **≤2 content panes + one focused action rail**.

**Hub `/`** — a calm landing page:
```
FlorAcco review platform
[ Reconcile · N open ] [ Tracked changes ] [ Database ] [ Corrections ] [ Dashboard ]
progress: x/N reconciled · …
```

**Reconciliation `/reconcile`** — *one question: is this DB record supported by the Word segment?* (implemented):
```
┌ queue ┬───────────── case bar: the question · register · Manuscript · N/total ──────────┐
│ +Q    ├ Word segment (clean)        │ Database record(s)                                 │
│       │ date · folio · label        │ per record: date·folio·type·firm·amount  [Supported]│
│       │ clean reading (highlights)  │ narrative (highlights)                             │
│       ├──────────────────────────────────────────────────────────────────────────────── │
│       │ ● verdict line   [Text Strong·94%] [phrase 18w] [0 conflicts]  ▸ Show signals    │
│       ├──────────────────────────────────────────────────────────────────────────────── │
│       │ [initials] [+Note]                         Not sure · None match · Confirm·next  │
└───────┴──────────────────────────────────────────────────────────────────────────────── ┘
```
No tracked changes here (they live in `/changes`); no field-editing (that is `/corrections`).
The four legacy judgment dropdowns are derived from the single Supported/None/Not-sure choice,
so the saved decision schema is unchanged.

**Tracked-changes `/changes/:registerId`** — document-centric single column:
```
┌ registers ┬ act rendered with changes ┬ ✓ accept ins / ✗ reject del / resolve 💬 ┐
```

**Database `/database`** — entity-first, read-only:
```
┌ search/facets ┬ results ┬ record detail (fields, linked source, images) ┐
```

**Corrections `/corrections`** — auditable proposal queue:
```
┌ proposals ┬ field diff (current → proposed, source quote required) ┬ approve/apply ┐
```

## 5. Evidence display rules (applies everywhere evidence is shown)

- **Headline the decision-relevant signal, not a misleading one.** The DB
  `document` is often a *snippet* of the Word narrative, so the symmetric
  `difflib` ratio understates a real match. Lead with **match strength =
  `max(narrative_similarity_ratio, text_containment_ratio)`** and show coverage.
- **Qualitative band first, number second.** Strong / Partial / Weak, then a
  **rounded percentage** ("91%"). Never show raw 5-decimal floats in the
  headline; keep the exact value in a details/tooltip only.
- Conflicts shown as a count with an alert colour **and** a label.

## 6. Triage: shrink the manual queue

The queue should contain only what needs a human. Three tiers (partly a pipeline
decision in `qa-packet`, partly a UI affordance):

1. **Auto-resolvable** — high-confidence, zero conflicts, corroborated by ID +
   date + folio + text. Offer **batch-confirm** + sampled spot-check, not
   one-by-one.
2. **Fast lane** — one obvious candidate, single minor flag; streamlined yes/next.
3. **Full reconciliation** — genuine ambiguity, conflicts, or alignment
   diagnostics; the full compare workspace.

**Implemented triage (2026-05-29).** The over-flagging was fixed in the pipeline.
`review_bucket_for_match` + `qa_rows_for_matches` now produce a 3-tier set:

- **High (131)** — *real linking problems only*: `Match has conflicts to resolve`,
  `Ambiguous match to choose`, `Word entry with weak or rejected DB candidates`,
  `Word entry with no DB match`.
- **Medium (215)** — `Likely match — verify date/folio` (id + narrative agree, only
  the date/folio differs — a field-verification question, headed for Corrections),
  plus the DB-only review stream.
- **Low (437)** — `Confirm multi-row link` (the normal combined-act pattern, one-tap
  confirm), `Expected Word-only (non-accomandita)`, `Expected DB-only`.

The structural diagnostics (`word_entry_combines_multiple_db_rows`,
`db_row_linked_to_multiple_word_entries`) are now informational and no longer force
High. The synthetic control-sample buckets were dropped. A separate matcher fix
stopped co-linking independent main contracts on folio-overlap alone (twin-contract
rows 117 → 5). Net: queue High **637 → 131**.

## 7. Shared building blocks (reuse, not rewrite)

- `TrackedText` + `highlight.ts` → Changes tool + Reconciliation Word pane.
- `WordPanel`, `DatabasePanel`, `ImagePanel` → Reconciliation + Database detail.
- A small **verdict** component (band + rounded %) reused wherever evidence shows.
- Decision persistence (keyed on the content-stable `source_entry_key`)
  generalizes to per-tool logs (editorial decisions, correction proposals).

## 8. Build order

Done:

1. **Routing + Hub + top nav.** (Declutters immediately.)
2. **Workspace under `/reconcile`**, stripped to linking-only (correction picker
   links out later).
3. **Queue-collapse fix + evidence headline/precision + lean triage** (§6).
4. **Read-only `/database` browser.** Entity-first search over contracts,
   sub-contracts, and people; record detail with foreign keys resolved
   (currency, economic sector, places, titles, person names), related rows
   (sub-contracts, investors, investments) that deep-link to their own records,
   the stored `document` narrative, and the linked Word source(s) for that
   `db_row_id` (from `source_entry_db_link_candidates.jsonl`). Endpoints:
   `GET /api/db/search?table=…&q=…` and `GET /api/db/record/{table}/{id}`.
   Routes: `/database`, `/database/:table`, `/database/:table/:id`. Read-only —
   no writes to SQLite. The stored `document` field is de-escaped for display
   (`clean_document` turns literal `\r\n` into line breaks; DB untouched).
   Each linked Word source is clickable and opens a slide-in panel
   (`GET /api/word-entry/{id}`) with the clean reading text and the manuscript
   image(s), and is tagged **confirmed / proposed / rejected** from
   `review_decisions.csv` (see §9.6) so review progress shows on the record.
   Contract and sub_contract rows carry direct links; a **person** record has no
   direct Word link, so it surfaces Word/manuscript context *indirectly* —
   aggregating the linked entries of the contracts where the person is an
   investor (`investor.contract_id` → main `contract`), each card labelled
   `via "<contract>"`, capped with a provenance note. Never presented as a
   direct person↔document claim.

**Chosen near-term direction — end-to-end DB updates first.** The headline
deliverable is *updating the database*, so we prioritise a thin vertical slice
over finishing the editorial tool:

5. **`/corrections` (Stage 5 → 7) — done (v1 + candidate queue Phase 1).** Propose →
   approve → apply field edits, with audit + reversibility; the only path that
   writes to SQLite. Built with the "Suggest a fix" bridge from `/database`. Scalar
   fields only in v1 (FKs deferred). Full design in
   [corrections_workflow.md](corrections_workflow.md); data contract in §11.3.
   **Correction candidates (Phase 1) added:** a ranked "possibly needs correction"
   queue (a second mode in `/corrections`) that surfaces the DB rows/fields most
   likely to need eyes — Word↔DB conflicts (stage 05) + DB-intrinsic corrupt/missing
   signals — with the **DB value and Word evidence side by side and no pre-fill**
   (DB = truth to perfect, Word = evidence). Candidates are hypotheses, never writes;
   the reconcile linker is untouched. Builder: `workflows/correction_candidates.py`;
   full design in [correction_candidates.md](correction_candidates.md). Next:
   Phase 2 = tracked-changes-touching-a-DB-field candidates (with `/changes`); add
   agent-suggested proposals.
6. **`/changes`** — the full editorial tool (renderer + `word_entry_rich` data
   already exist).
7. **`/dashboard`** — observes the logs + coverage; never a blocker.

**Accepted trade-off:** doing Database → Corrections before a full Changes tool
means we reconcile and correct against the *current* clean (revisions-accepted)
Word text while tracked changes are not yet formally adjudicated. This is
acceptable because (a) decisions are keyed on the content-stable
`source_entry_key` and survive a later re-extraction, and (b) Corrections records
the exact `source_quote` it relied on, so an editorial change that later alters
that quote is detectable (see §10 staleness). The cost is possible re-confirmation
of a small number of cases once `/changes` lands.

## 9. Data flow & cross-panel effects

Two stores underlie everything (see §10 for shapes):

- **Derived artifacts** — pipeline output (`05_db_candidate_matches`,
  `06_qa_packet`). Regenerated wholesale by re-running `match-db` + `qa-packet`.
  Disposable.
- **Human logs** — append/snapshot stores keyed on content-stable identities
  (`source_entry_key`, `db_row_id`) so they survive a derived-layer rebuild.
  Authoritative. Today: `08_review_decisions/review_decisions.csv` (linking).
  Planned: an editorial-decision log (Changes) and a corrections proposal store.

Authority chain (`AGENTS.md`): **Word narratives → SQLite → agent suggestions.**

```
WORD (.docx, tracked changes)                 SQLite (contract / sub_contract / person)
        │                                                   │
   [normalize/segment]                                      │
        ▼                                                   ▼
  CHANGES ── settles ──► authoritative Word text ──┐
  (editorial truth)                                │
                                                   ▼
                       RECONCILE ── which DB row(s) a Word entry supports
                       (linking)        │
                                        ├─► link log (review_decisions.csv)
                                        └─► hands date/folio-verify cases to ▼
                                                                       CORRECTIONS
  DATABASE ◄── reads ── SQLite ◄──────── applied writes (audited) ───── (field edits, Stage 5/7)
  (read-only)                                                            proposal store
                       DASHBOARD observes all logs + coverage
```

**Cross-panel consequences** (where an action in one tool must surface in another):

1. **Changes → Reconcile / Corrections.** Accepting/rejecting an edit changes the
   authoritative narrative. A deleted phrase the matcher leaned on can move the
   reconcile text band; a `source_quote` a correction cited can disappear. Changes
   is logically *upstream* of trustworthy reconciliation.
2. **Reconcile → Corrections.** A confirmed link *scopes* which row Corrections may
   edit and supplies the justifying narrative. The "verify date/folio" tier is the
   explicit handoff.
3. **Corrections → Reconcile (feedback).** Fixing a DB field can dissolve a
   reconcile conflict (e.g. correcting a date removes `registration_date_differs`),
   so that case should drop out of High on the next pipeline run.
4. **Reconcile "none / Word-only" → DB-only & new-row work.** Rejecting all
   candidates feeds the DB-only stream and, eventually, new-row proposals.
5. **Re-run invalidation.** Editorial or DB changes can alter derived artifacts; a
   rebuild re-attaches decisions by stable key, but a decision made against
   now-changed evidence must surface a *re-confirm* prompt, not silently persist.
6. **Reconcile → Database (implemented).** A DB record's "Linked Word source" list
   is annotated from `review_decisions.csv`: a reviewer-**confirmed** link
   (`selected_db_row_ids`, keyed on `source_entry_key`) is shown as Confirmed and
   takes priority over matcher-only **proposed** links; **rejected** links are
   shown de-emphasised. Confirmed links the matcher never proposed are added too.
   So a `/reconcile` decision immediately changes what the `/database` record
   shows — the read path is the proposal store + decision log, not a SQLite write.

## 10. Open design decisions

These set how tightly the tools couple; decide before building Changes/Corrections.

- **Does Changes write back into Word-derived artifacts, or only log decisions?**
  Recommended: **log only** (append-only, keyed by `source_entry_key` + change
  identity). A separate rebuild step can materialise "accepted text" if needed;
  the log stays the source of truth and preserves editorial provenance
  (`AGENTS.md`: never flatten editorial history).
- **When do derived artifacts recompute?** Manual staged re-run today
  (`match-db`/`qa-packet`). Live recompute is out of scope; stable keys make staged
  rebuilds safe. A future "rebuild" affordance can trigger it.
- **Corrections never auto-apply** (`AI.md`): proposal → human approve → applied
  write, with a required `source_quote` and full audit. Apply must validate the
  field is a real schema column (`AGENTS.md`: never fabricate column names) and
  store the pre-image so a write is reversible.
- **Decision staleness.** When evidence changes under a saved decision/proposal,
  show "evidence changed — re-confirm" rather than keeping or dropping silently.
  Mechanism: store an evidence fingerprint at decision time (see §11).

## 11. Data contracts (sketches)

All three human logs live in the gitignored data dir alongside
`08_review_decisions/review_decisions.csv`, keyed on content-stable identities so a
derived-layer rebuild never orphans a human decision. Shapes below are proposals
(only the reconcile log exists today).

### 11.1 Reconcile decision log — *exists*

`data/derived/word-pipeline/08_review_decisions/review_decisions.csv`. A **snapshot**
store (latest decision per `review_id`, rewritten on save). Key:
`review_id = source_entry_key + "__" + suggested_db_row_id`. Columns: see
`DECISION_FIELDNAMES` in `workflows/review_server.py` (`reviewer`, `updated_at`,
`source_entry_key`, `main_judgment`, `selected_db_row_ids`, `rejected_db_row_ids`,
…). Reading: the app derives per-case state from this.

### 11.2 Changes editorial-decision log — *planned*

Purpose: record accept/reject of each tracked change and comment/footnote
resolution per Word entry, independent of the DB. **Append-only JSONL** (editorial
history is provenance); the app resolves latest-per-`change_key`.

`data/derived/word-pipeline/09_editorial_decisions/changes_decisions.jsonl`

```json
{
  "change_key": "Camera_di_Commercio_1262_e62f14ccceaa8::deletion::3::8f2a1c",
  "source_entry_key": "Camera_di_Commercio_1262_e62f14ccceaa8",
  "register_id": "Camera_di_Commercio_1262",
  "kind": "deletion",                  // insertion | deletion | move | comment | note
  "raw_change_id": "42",               // w:id from the .docx (display/debug only; not stable)
  "author": "FT",                      // from the revision, if present
  "authored_at": "2024-11-03",         // revision date, if present
  "target_text": "che fin sotto l'1 maggio 1771",  // inserted/deleted/commented text
  "occurrence_index": 3,               // nth change of this kind in the entry (ordering anchor)
  "decision": "reject",                // accept | reject  (comments: resolve | keep_open; notes: acknowledge)
  "decided_by": "FT",
  "decided_at": "2026-05-29T18:20:11Z",
  "note": "transcription artifact, not an authorial change",
  "evidence_fingerprint": "sha1:…"     // hash of (kind+target_text+context) at decision time → staleness
}
```

Key design — `change_key`: the `.docx` `w:id` is document-local and not stable
across re-extraction, so the stable key is a hash of
`(source_entry_key, kind, normalised target_text, occurrence_index)`. Comments key
on `(source_entry_key, comment author+date+text)`; relying on the raw comment id
alone is unsafe.

### 11.3 Corrections proposal store — *planned*

Purpose: Stage 5 (propose) → approve → Stage 7 (apply) structured field edits, with
audit and reversibility. **Two files**: a proposal store (latest state per
`proposal_id`) and an append-only event log (every transition), so the timeline is
auditable and applies are reversible.

`…/10_corrections/corrections_proposals.jsonl`

```json
{
  "proposal_id": "c1a2…",
  "created_at": "2026-05-29T18:31:02Z",
  "origin": "agent_suggested",          // manual | agent_suggested
  "origin_detail": "model:…; verify-date-folio seed",
  "db_table": "sub_contract",           // must be a real schema table
  "db_row_id": "sub_contract:826",
  "primary_key": {"contract_id": 826},
  "field": "registration_date",         // must be a real schema column (no fabrication)
  "change_type": "correct",             // correct | fill_missing | flag_uncertain
  "current_value": "1778-03-20",        // pre-image, snapshot at proposal time
  "proposed_value": "1778-03-19",
  "rationale": "Narrative dates the act 19 marzo 1778; DB has 20.",
  "source": {
    "source_entry_key": "Camera_di_Commercio_1262_ecdb…",
    "source_quote": "19 marzo 1778 [disdetta] di 1529 + [nuovo] 2637",  // REQUIRED (AI.md)
    "source_locator": {"register_id": "Camera_di_Commercio_1262", "folio": "6r-7r"},
    "link_review_id": "Camera_di_Commercio_1262_ecdb…__contract:2637; sub_contract:826"
  },
  "evidence_fingerprint": "sha1:…",     // of current_value + source_quote at proposal time
  "status": "proposed",                 // draft | proposed | approved | rejected | applied | superseded
  "reviewed_by": null, "reviewed_at": null,
  "applied_at": null, "applied_by": null, "applied_run_id": null,
  "supersedes": null, "superseded_by": null
}
```

`…/10_corrections/corrections_events.jsonl` (append-only audit)

```json
{"event": "applied", "proposal_id": "c1a2…", "at": "2026-05-30T09:10:00Z",
 "by": "FT", "run_id": "apply-2026-05-30", "pre_image": "1778-03-20",
 "post_image": "1778-03-19"}
```

Apply-time invariants (Stage 7): re-read `current_value` and compare to the stored
pre-image (abort on drift), validate `field` against the live schema, write inside a
transaction, and record an event with both images for reversal. Per `AI.md`, agent
output is never applied without a recorded human approval event.
