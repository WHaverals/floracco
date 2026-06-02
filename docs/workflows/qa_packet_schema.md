# QA packet schema

Field reference for the Word–DB match QA packet, the contract between the
matching pipeline (`workflows/word_pipeline.py`, stage `qa-packet`) and the
review platform (`workflows/review_server.py` + `apps/review`).

**Schema version: 2** (2026-05-29). Bump this when fields are added, removed, or
their meaning changes, and note it in [LOG.md](../../LOG.md). The review server
and app read these field names directly; treat renames as breaking changes.

- v2: added the editorial-history fields in "Word entry — editorial history"
  below (`word_entry_revision_text`, `word_entry_has_revisions`,
  `word_entry_revision_summary`, `word_entry_comments`, `word_entry_notes`) for
  the tracked-changes Word panel. The three nested fields
  (`word_entry_revision_summary`, `word_entry_comments`, `word_entry_notes`) are
  present in the JSONL and HTML rows but **omitted from the CSV** (which keeps
  one scalar value per cell); `word_entry_revision_text` is kept in the CSV as a
  string. See [tracked_changes_word_panel.md](tracked_changes_word_panel.md).

## Files

The `qa-packet` stage writes three serializations of the **same rows** to
`data/derived/word-pipeline/06_qa_packet/`:

| File | Use |
|------|-----|
| `word_db_match_qa_packet.jsonl` | Source of truth consumed by the review server and scripted workflows. |
| `word_db_match_qa_packet.csv` | Same rows for spreadsheet filtering/sorting. |
| `word_db_match_qa_packet.html` | Static side-by-side reading view. |

Each line in the JSONL is one **review case**. A case is either a Word entry and
its proposed DB link(s) (`packet_section = "Word entry review"`) or an unmatched
DB row surfaced for review (`packet_section = "DB-only review"`). The packet is a
**curated subset** of all matches (ambiguous/multiple/word-only cases, serious
conflicts, weak candidates, alignment diagnostics, plus per-register positive
controls), not every entry.

## Identity and provenance

| Field | Type | Meaning |
|-------|------|---------|
| `source_entry_key` | string | **Content-stable** identifier of the Word source entry (register + folio span + earliest ISO date + event label + event number, hashed; collisions disambiguated by content hash). **Review decisions are keyed on this** so they survive re-segmentation. Empty for DB-only rows with no associated Word entry. See [README.md](README.md) → "Source-entry ID stability". |
| `source_entry_id` | string | Human-readable positional id (`register_id` + sequential ordinal). **Display only** — it is renumbered when segmentation changes, so do not key persistence on it. |
| `register_id` | string | Register the entry/row belongs to (e.g. `Mercanzia_10845`). For DB-only rows without a register this is `_unknown_register`. |
| `packet_section` | string | `Word entry review` or `DB-only review`. |

> The review server derives two more fields at load time that are **not** in the
> JSONL: `case_index` (load order) and `review_id` (= `entry_identity__suggested_db_row_id`,
> where `entry_identity` is `source_entry_key` if present, else `source_entry_id`).

## Review routing

| Field | Type | Meaning |
|-------|------|---------|
| `review_priority` | string | `High` / `Medium` / `Low`. |
| `recommended_review_bucket` | string | Human-facing grouping (e.g. "Alignment diagnostic needing review", "Word-DB-image success control"). |
| `recommended_reviewer_action` | string | One-sentence suggested next step. |
| `match_status` | string | Human label of the match outcome (high-confidence, candidate, ambiguous, matched-multiple, word-only, or "DB row has no proposed Word link"). |

## Word entry (left panel)

| Field | Type | Meaning |
|-------|------|---------|
| `entry_label` | string | Raw bracket label, e.g. `[Disdetta]`. |
| `entry_type_interpretation` | string | Normalized label guess, e.g. `termination`. |
| `entry_number` | int/null | Word event number (the act number in `[Nuova] 1922`). |
| `referenced_entry_number` | int/null | Event number this act refers back to (for later acts). |
| `word_registration_date` | string | Raw Italian date as written in the narrative. |
| `word_folio_range` | string | Folio span of the entry, e.g. `66r-67r`. |
| `word_entry_text` | string | Compacted current narrative text of the entry (the "clean", revisions-accepted reading). |

## Word entry — editorial history (tracked changes)

Source data for the tracked-changes Word panel. The review server parses
`word_entry_revision_text` into a render-ready token stream (`word_entry_rich`,
served per case but **not** stored in the packet) and the React app renders
"Clean" (default) and "Tracked" views. See
[tracked_changes_word_panel.md](tracked_changes_word_panel.md) for the marker
grammar and render rules.

| Field | Type | Meaning |
|-------|------|---------|
| `word_entry_revision_text` | string | Narrative with inline revision/comment/note markers preserved (e.g. `<INS …>…</INS>`, `<DEL …>…</DEL>`, `<MOVEFROM/MOVETO …>`, `<COMMENT_START/COMMENT_END id=…>`, `<COMMENT_REF/FOOTNOTE_REF/ENDNOTE_REF id=…>`). Empty when the entry has none. |
| `word_entry_has_revisions` | bool | Whether the entry contains any insertion/deletion/move. |
| `word_entry_revision_summary` | object | Counts: `{insertions, deletions, moves, comments, notes}`. *(JSONL/HTML only — not in CSV.)* |
| `word_entry_comments` | array | Resolved comment bodies referenced by the entry: `[{id, author, date, initials, text}]`. *(JSONL/HTML only — not in CSV.)* |
| `word_entry_notes` | array | Resolved footnote/endnote bodies: `[{id, kind, text}]` where `kind` is `footnote` or `endnote`. *(JSONL/HTML only — not in CSV.)* |

## Proposed DB link(s) (right panel)

| Field | Type | Meaning |
|-------|------|---------|
| `suggested_db_row_ids` | string | Semicolon-separated DB row ids proposed as links, e.g. `contract:604; sub_contract:18`. The review form lets the reviewer select/reject among these. |
| `suggested_db_rows_plain_language` | string | Readable summary of those rows. |
| `suggested_db_documents_text` | string | Concatenated DB `document` text for the suggested rows. |
| `suggested_link_count` | int | Number of suggested DB links (>1 ⇒ one Word entry → several DB rows). |
| `suggested_relationship_type` | string | Heuristic group type (`simple_one_to_one`, `word_entry_to_multiple_subcontracts`, `db_only_unlinked`, …) — **review item**, not authoritative. |
| `top_db_row_id` | string | Best single candidate id. |
| `top_db_table` | string | `contract` or `sub_contract`. |
| `top_db_contract_id` | int | DB `contract_id` of the top candidate. |
| `top_db_main_contract_id` | int/null | Parent contract id for a sub_contract. |
| `top_db_type` | string | `sub_type` for sub_contracts, else the table name. |
| `top_db_document_text` | string | Compacted DB narrative of the top candidate. |
| `candidate_count` | int/null | How many DB candidates were scored for this entry. |
| `top_candidates_plain_language` | string | Readable summary of the scored candidates. |

## Match evidence (metrics and signals)

These back the evidence panel. Per project rules, similarity claims must cite
these recorded metrics — do not call a match "confident" without them.

| Field | Type | Meaning |
|-------|------|---------|
| `top_match_score` | float | Total score of the top candidate. |
| `top_match_signals_plain_language` | string | Positive signals (semicolon-separated, human-labeled), e.g. folio/date/id/text signals. |
| `top_match_conflicts_plain_language` | string | Recorded conflicts, or "no conflict recorded". |
| `narrative_similarity_ratio` | float | Symmetric character-level `difflib` similarity (0–1). |
| `text_containment_ratio` | float | Order-aware fraction of the shorter token sequence contained in the other (snippet-friendly; 0–1). |
| `word_token_coverage_in_db` | float | Share of distinctive Word tokens present in the DB text (0–1). |
| `db_token_coverage_in_word` | float | Share of distinctive DB tokens present in the Word text (0–1). |
| `shared_phrase_count` | int/null | Number of long shared phrases. |
| `longest_shared_phrase_words` | int/null | Length (words) of the longest shared phrase. |
| `field_overlap_count` | int | Count of structured DB fields (names, places, amounts, dates) found in the Word text. |
| `field_overlap_plain_language` | string | Readable list of those field overlaps. |

## Alignment diagnostics

| Field | Type | Meaning |
|-------|------|---------|
| `alignment_diagnostic_types` | string | Semicolon-separated diagnostic types raised (e.g. `possible_stile_fiorentino_date_alignment`, `possible_original_folio_numbering_match`). |
| `alignment_diagnostics_plain_language` | string | Readable explanation, evidence, and recommended action for each diagnostic. |

## Manuscript image candidates

| Field | Type | Meaning |
|-------|------|---------|
| `image_candidate_paths` | string | Semicolon-separated candidate image file paths (resolved/served by the review server under allowed roots only). |
| `image_candidates_plain_language` | string | Readable summary of the image candidates. |
| `image_candidates_need_review` | bool | Whether the image-folio mapping for this case is uncertain. |

## Review decisions (written by the platform)

The review server appends decisions to
`data/derived/word-pipeline/08_review_decisions/review_decisions.csv`. It never
writes to SQLite, Word, or images. Columns:

`review_id`, `updated_at`, `reviewer`, `source_entry_key`, `source_entry_id`,
`suggested_db_row_id`, `register_id`, `review_priority`,
`recommended_review_bucket`, `main_judgment`, `image_judgment`,
`field_correction_needed`, `next_action`, `review_note`,
`image_candidate_paths`, `selected_db_row_ids`, `rejected_db_row_ids`,
`suggested_relationship_type`.

`review_id` is `entry_identity__suggested_db_row_id` where `entry_identity` is
`source_entry_key` (preferred) or `source_entry_id` (fallback for rows without a
key). Because the key is content-stable, a decision keeps matching its case
across re-runs of segmentation and matching. Store/inspect `source_entry_key`
when reconciling decisions, not `source_entry_id`.
