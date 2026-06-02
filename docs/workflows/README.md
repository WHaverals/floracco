# Word to Database Reconciliation

Plan for turning the Word contract narratives into reviewable, structured data that can be compared with SQLite and eventually linked to manuscript images.

This is a staged workflow. The immediate goal is **not** to update the database. The immediate goal is to preserve the Word evidence, extract it safely, align it with existing database rows, and produce human-reviewable JSONL files.

## Purpose

The project currently has three evidence layers that overlap but have drifted:

- **Word narratives** in `data/corpus/word/`: authoritative contract summaries, with pending tracked changes and editorial notes.
- **SQLite** in `data/sqlite/main.db`: structured mirror used by the old application, but potentially out of date relative to Word.
- **Images** in `data/corpus/img/`: folio photographs in scan order, not yet authoritatively mapped to folio references.

The reconciliation workflow should make these layers share one ecosystem:

1. Every extracted Word entry knows its source file, folio, date, event label, and text.
2. Every extracted Word entry can be matched to a candidate DB row when possible.
3. Every proposed DB correction is traceable to a Word source and, later, an image.
4. Tracked changes remain visible as pending corrections, not silently accepted.
5. Human review decides what becomes a database update.

## System Dependencies

Word reconciliation needs one system dependency in addition to the Python environment:

- **LibreOffice** (`soffice`) for converting legacy `.doc` files to normalized `.docx` processing copies.

Check whether it is available:

```bash
soffice --version
```

On macOS, LibreOffice may be installed but not on `PATH`. In that case the executable is often:

```bash
/Applications/LibreOffice.app/Contents/MacOS/soffice --version
```

Workflow scripts should check both `soffice` on `PATH` and the common macOS application path. They should also accept an explicit `--soffice` path.

LibreOffice is not a Python package and should not be added to `pyproject.toml`.

## Source Rules

### Source hierarchy

For contract content:

1. **Word narratives** are authoritative.
2. **SQLite** is the structured mirror.
3. **Derived JSONL/XML and LLM outputs** are proposals or working artifacts.

### Never edit originals

Original Word files must remain untouched:

```text
data/corpus/word/
```

Processing may create derived files in a separate folder, for example:

```text
data/derived/word-pipeline/00_inventory/
data/derived/word-pipeline/01_normalized_docx/
data/derived/word-pipeline/02_validation/
```

Derived files can be regenerated. Originals are evidence.

### Why create normalized `.docx` copies?

The Word corpus contains a mix of legacy `.doc` files and newer `.docx` files. We normalize them into derived `.docx` copies for processing because `.docx` is a zip package with readable XML inside it. That XML lets scripts inspect text, paragraph breaks, insertions, deletions, and comments without opening Word by hand.

The conversion is **not** an editorial decision. It does not approve corrections, accept tracked changes, or replace the original files. It creates a working copy that can be checked and parsed consistently.

This is why the workflow has three separate ideas:

- **original Word files**: evidence; never edited by scripts;
- **normalized `.docx` files**: derived processing copies; safe to delete and rebuild;
- **validation outputs**: checks that the processing copies still contain the evidence we need before extraction begins.

For existing `.docx` files, normalization is just a copy into the derived folder with a simpler register-based filename. For legacy `.doc` files, LibreOffice creates the derived `.docx` copy. The manifest records both source and derived checksums so every output can be traced back to the original file.

### Track changes

Tracked changes in Word are **pending corrections**. They have not necessarily been fully reviewed or applied to the database.

Do not:

- accept or reject tracked changes automatically;
- flatten tracked changes into plain text without preserving revision state;
- treat inserted text as approved database truth;
- delete deleted text from the evidence trail.

Do:

- preserve insertions, deletions, comments, uncertainty markers, and editorial notes;
- record whether an extracted field is affected by revisions;
- send revision-bearing proposals to human review.

## What Is In The Word Files

The Word files are not just prose summaries. They contain semi-structured records.

Typical elements:

- register front matter: transcription/revision history, status notes, rubrics, physical description, cartulation notes;
- folio references: `c. 1r`, `cc. 1r-2v`, `c. n.n`;
- registration dates, often with Florentine-style double dating in earlier records;
- bracketed event labels: `[Nuova]`, `[Disdetta]`, `[Modifica]`, `[Bilancio]`, `[Cessione]`, `[Rinnovo]`, `[Ratifica]`, `[Proroga]`;
- event references: e.g. `[Disdetta] di 3488`;
- narrative summaries;
- firm names and `sotto nome di...` phrases;
- economic activity phrases;
- capital, currency, duration, renewal, and termination clauses;
- people, roles, titles, proxies, heirs, guardians, and presence/absence notes;
- marginal and cross-volume references: `a margine`, `libro antecedente`, `presente libro`, `libro susseguente`;
- editorial markers: `[sic]`, `[?]`, `[in bianco]`, `[manca il giorno]`, `[senza luogo]`, `[illeggibile]`, `controllare originale`.

The glossary in `docs/glossary.md` records many of these terms and marks uncertain ones for FT review.

## Link To SQLite

The current SQLite schema does **not** contain image filenames. It does contain fields that can link Word and DB records:

- `contract.archive`
- `contract.series`
- `contract.folder`
- `contract.folio`
- `contract.registration_date`
- `contract.document`
- `sub_contract.archive`
- `sub_contract.series`
- `sub_contract.folder`
- `sub_contract.folio`
- `sub_contract.registration_date`
- `sub_contract.document`
- `sub_contract.main_contract_id`

The first matching strategy should use:

1. `folder`
2. `folio`
3. `registration_date`
4. bracketed event label and event number
5. text similarity between Word narrative and DB `document`

The result should be a **candidate match**, not an automatic truth claim.

## Link To Images

Image folders correspond to registers, but image filenames are scan-order based rather than folio based. There is currently no authoritative folio/image map.

Example image folders:

```text
data/corpus/img/Mercanzia 10838 ridimensionato/
data/corpus/img/Dipartimento esecutivo della Camera di Commercio 1262 ridemensionato/
```

The DB can provide expected folio references, but it cannot directly map a folio to an image filename.

Create a separate derived map:

```json
{
  "series": "Mercanzia",
  "folder": "10838",
  "image_file": "Mercanzia 10838_046.jpg",
  "scan_order": 46,
  "folio_guess": "23r",
  "folio_confirmed": null,
  "confidence": "low",
  "notes": "Derived from scan order; not manually verified"
}
```

This map should begin as provisional. Improve it later with manual calibration, visible folio numbers, recto/verso sequencing, and image review.

## Derived Data Layers

Use layered JSONL outputs rather than one all-purpose file.

### 1. Register inventory

One row per Word file:

```json
{
  "source_file": "5_Mercanzia 10838_track changes.doc",
  "archive": "ASF",
  "series": "Mercanzia",
  "folder": "10838",
  "file_format": "doc",
  "status_from_filename": "track changes",
  "sha256": "...",
  "front_matter_summary": "...",
  "derived_docx_path": "data/derived/word-pipeline/01_normalized_docx/Mercanzia_10838.docx"
}
```

### 2. Normalized DOCX validation

One row per normalized `.docx` file:

```json
{
  "register_id": "Mercanzia_10838",
  "source_file": "5_Mercanzia 10838_track changes.doc",
  "normalized_path": "data/derived/word-pipeline/01_normalized_docx/Mercanzia_10838.docx",
  "valid_docx": true,
  "text_characters": 123456,
  "insertion_count": 120,
  "deletion_count": 80,
  "comment_reference_count": 4,
  "bracket_label_count": 250,
  "folio_count": 180,
  "date_like_count": 300,
  "validation_status": "ok"
}
```

Why validate before extraction:

- conversion can fail silently or produce incomplete files;
- tracked changes and comments are historical/editorial evidence;
- bracket labels, folio references, dates, and front matter are parser anchors;
- extraction should begin only after the normalized files are readable and evidence-rich.

### 3. Extracted register evidence

This is the first extraction layer. It is intentionally **not** one row per contract yet. It preserves the Word evidence at register and paragraph level so later parsers can segment acts without losing provenance.

Outputs:

- `data/derived/word-pipeline/03_extracted_registers/registers.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/registers.csv`
- `data/derived/word-pipeline/03_extracted_registers/paragraphs.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/revisions.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/comments.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/footnotes.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/relationships.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/issues.jsonl`

Why this layer exists:

- Word paragraphs carry the natural order of the register text.
- Folios, dates, and bracket labels often appear in separate neighboring paragraphs.
- Tracked changes and comments can affect individual words or phrases inside a paragraph.
- Footnotes often point to photographs, original supporting documents, other registers, or archival references.
- Segmenting contracts too early would hide uncertainty and make later review harder.

For each paragraph, preserve:

- `current_text`: the current visible Word text;
- `revision_aware_text`: text with insertion, deletion, comment, and footnote markers;
- paragraph order and style;
- bracket labels, date candidates, and folio evidence;
- linked `comment_ids`, `footnote_ids`, and `endnote_ids`;
- flags for insertions, deletions, and moves;
- a paragraph XML checksum for traceability.

Folio evidence is split into two categories:

- `folio_heading`: a folio marker at the start of a paragraph, used to update `current_folio_context`;
- `inline_folio_mentions`: references inside prose, such as "a carta 16", which should not change the current folio context.

The parser normalizes common heading patterns while preserving the raw text:

- `c. 1r` -> start `1r`, end `1r`;
- `c. 1v-2r` -> start `1v`, end `2r`;
- `cc. 2r-v` -> start `2r`, end `2v`;
- `c. 142v-142bisv` -> start `142v`, end `142bisv`;
- `c. 171(a)v-172(a)r` -> start `171(a)v`, end `172(a)r`;
- `c. n.n` -> start `n.n`, end `n.n`.

This distinction matters for segmentation: entry boundaries should normally follow folio-heading paragraphs, not inline cross-references.

For each revision, preserve:

- revision kind: insertion, deletion, move-from, or move-to;
- revision ID, author, date, text, and paragraph index.

For comments and footnotes, preserve each note as a separate evidence row.

### 4. Source entries

One row per Word act/entry. This is the most important first artifact.

```json
{
  "source_entry_id": "Mercanzia_10838_c_1r-1v_1604-09-18_Nuova_1922",
  "source_file": "5_Mercanzia 10838_track changes.doc",
  "archive": "ASF",
  "series": "Mercanzia",
  "folder": "10838",
  "folio_raw": "c. 1r-1v",
  "folio_start": "1r",
  "folio_end": "1v",
  "registration_date_raw": "18 settembre 1604",
  "registration_date_iso": "1604-09-18",
  "event_label_raw": "[Nuova]",
  "event_label_normalized": "new_contract",
  "event_number_raw": "1922",
  "text": {
    "current": "...",
    "with_revision_markup": "...",
    "insertions": [],
    "deletions": [],
    "comments": []
  },
  "editorial_markers": ["sic", "senza luogo"],
  "cross_references": [
    {
      "kind": "a_margine",
      "raw": "Vedi la disdetta..."
    }
  ],
  "db_match": {
    "status": "unmatched",
    "candidates": []
  },
  "review": {
    "status": "not_reviewed",
    "notes": []
  }
}
```

Why one row per Word entry first:

- Word contains main contracts and later acts.
- A DB contract can have many related subcontracts.
- Some Word entries are disdette, cessioni, bilanci, or marginal declarations.
- Forcing everything into one per-contract object too early hides evidence and provenance.

### 5. Contract bundles

One row per DB contract, derived later by grouping source entries and DB rows:

```json
{
  "db_contract_id": 1922,
  "main_entry_id": "Mercanzia_10838_c_1r-1v_1604-09-18_Nuova_1922",
  "sub_entry_ids": [],
  "db_row": {},
  "word_evidence": [],
  "field_reconciliation": [],
  "image_candidates": [],
  "review": {
    "status": "not_reviewed"
  }
}
```

### 6. Field reconciliation proposals

One row per proposed field-level correction:

```json
{
  "proposal_id": "contract_1922_firm_name_001",
  "db_table": "contract",
  "db_id": 1922,
  "field": "firm_name",
  "db_value": "Cosimo Nasi e compagni",
  "word_value": "Cosimo Nasi e compagni di Messina",
  "source_entry_id": "Mercanzia_10838_c_1r-1v_1604-09-18_Nuova_1922",
  "source_quote": "Nome ditta: Cosimo Nasi e compagni di Messina",
  "revision_state": "no_tracked_change",
  "proposal": "update_db",
  "confidence": "candidate",
  "review_status": "pending"
}
```

### 7. Image folio map

One row per image file:

```json
{
  "series": "Mercanzia",
  "folder": "10838",
  "image_file": "Mercanzia 10838_046.jpg",
  "scan_order": 46,
  "folio_guess": null,
  "folio_confirmed": null,
  "confidence": "unmapped"
}
```

## Staged Implementation Plan

### Stage 0: Inventory and invariants

Goal: know what exists before transforming anything.

Tasks:

- list all Word files;
- record checksums;
- identify `.doc` vs `.docx`;
- record filename status: `track changes`, `clean_input completed`, `in progress`;
- extract front matter without trying to parse contracts;
- count bracket labels and editorial markers;
- compare expected 20 registers with image folders and DB folders.

Output:

- `data/derived/word-pipeline/00_inventory/word_files.jsonl`
- `data/derived/word-pipeline/00_inventory/word_files.csv`
- `data/derived/word-pipeline/00_inventory/db_folders.csv`
- `data/derived/word-pipeline/00_inventory/image_folders.csv`
- `data/derived/word-pipeline/00_inventory/register_coverage.csv`
- `data/derived/word-pipeline/00_inventory/issues.jsonl`

Command:

```bash
uv run python workflows/word_pipeline.py inventory
```

Do not:

- alter Word originals;
- accept tracked changes;
- create DB updates.

### Stage 1: Normalize Word copies

Goal: create processing copies that can be parsed consistently.

Why this stage exists:

- legacy `.doc` files are difficult to inspect safely with normal XML tools;
- `.docx` files expose the Word document as readable XML;
- a single normalized folder gives later scripts one predictable input format;
- keeping normalized files under `data/derived/` protects the originals from accidental edits.

Tasks:

- convert `.doc` to `.docx` into `data/derived/word-pipeline/01_normalized_docx/`;
- copy existing `.docx` into the same derived area if useful;
- record source checksum and derived checksum;
- test whether tracked changes survive conversion.

Preferred tool:

- LibreOffice headless for `.doc` to `.docx`;
- direct `.docx` XML parsing for already-modern files.

Command:

```bash
uv run python workflows/word_pipeline.py normalize
```

If `soffice` is not on `PATH`, pass it explicitly:

```bash
uv run python workflows/word_pipeline.py normalize --soffice /Applications/LibreOffice.app/Contents/MacOS/soffice
```

If this is run from a restricted automation sandbox, LibreOffice may time out while launching the macOS app. Run the same command from the local terminal or an unrestricted agent shell.

Output:

- `data/derived/word-pipeline/01_normalized_docx/*.docx`
- `data/derived/word-pipeline/01_normalized_docx/normalization_manifest.jsonl`
- `data/derived/word-pipeline/01_normalized_docx/normalization_manifest.csv`
- `data/derived/word-pipeline/01_normalized_docx/normalization_summary.md`

Do not:

- overwrite source Word files;
- store derived copies in git;
- trust conversion until sampled against originals.

### Stage 1b: Validate normalized Word copies

Goal: verify that normalized `.docx` files are safe to use for extraction.

This is a separate command because conversion and validation answer different questions. `normalize` asks, "Can we create processing copies?" `validate-normalized` asks, "Do those processing copies still contain the structures we need?"

Tasks:

- confirm each normalized file is a readable `.docx` zip;
- confirm `word/document.xml` exists and has extractable text;
- count paragraphs, insertions, deletions, comment references, and comments;
- count bracket labels, folio references, and date-like strings;
- record a front-matter snippet for quick human inspection;
- for source files that were already `.docx`, compare source and normalized text/revision counts.

Command:

```bash
uv run python workflows/word_pipeline.py validate-normalized
```

Output:

- `data/derived/word-pipeline/02_validation/normalized_docx_validation.jsonl`
- `data/derived/word-pipeline/02_validation/normalized_docx_validation.csv`
- `data/derived/word-pipeline/02_validation/issues.jsonl`
- `data/derived/word-pipeline/02_validation/validation_summary.md`

Interpretation:

- `validation_status = ok` means the file is readable and contains the expected parser evidence.
- `validation_status = review` means extraction should pause for that register until a human checks the issue.
- For legacy `.doc` sources, the script validates the converted `.docx`; it cannot inspect the original binary `.doc` revision XML directly.

Do not:

- treat validation as approval of tracked changes;
- accept or reject revisions during validation;
- begin automated extraction from files marked `review`.

### Stage 2: Extract revision-aware Word XML

Goal: preserve text and tracked changes.

Tasks:

- parse `word/document.xml`;
- capture paragraph-level text in original order;
- preserve `w:ins`, `w:del`, comments, footnotes, endnotes, and revision metadata if present;
- preserve bracket tags and uncertainty markers;
- export both plain current text and revision-aware text.

Command:

```bash
uv run python workflows/word_pipeline.py extract-registers
```

Outputs:

- `data/derived/word-pipeline/03_extracted_registers/registers.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/paragraphs.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/revisions.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/comments.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/footnotes.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/relationships.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/issues.jsonl`

Interpretation:

- `registers.jsonl` gives one summary row per register.
- `paragraphs.jsonl` is the main extraction product for the next parser.
- `revisions.jsonl`, `comments.jsonl`, and `footnotes.jsonl` are separate evidence tables that allow human review.
- `relationships.jsonl` records linked Word package parts for traceability.

Do not:

- use only plain text for authoritative extraction;
- silently drop deleted text or comments.
- segment contracts or create DB proposals in this stage.

### Stage 3: Segment source entries

Goal: turn each register text into one row per candidate act/entry.

Why this stage exists:

- the extracted paragraphs preserve evidence, but they are still too granular for Word ↔ DB reconciliation;
- source entries need stable IDs before they can be matched to SQLite rows, images, or human review notes;
- segmentation should happen before field extraction so that ambiguous acts can be reviewed as whole source units.

Command:

```bash
uv run python workflows/word_pipeline.py segment-entries
```

Input:

- `data/derived/word-pipeline/03_extracted_registers/registers.jsonl`
- `data/derived/word-pipeline/03_extracted_registers/paragraphs.jsonl`

Parser signals:

- folio-heading paragraphs: `c. 1r`, `cc. 1r-2v`, `cc. 2r-v`, `c. n.n`;
- inline folio mentions as cross-references, not entry boundaries;
- date lines;
- bracket event labels such as `[Nuova]`, `[Disdetta]`, `[cessione]`, `[modifica]`, and `[bilancio]`;
- compound or variant labels such as `[nuova/modifica]`, `[Ratifica di disdetta]`, `[variation]`, `[Continuazione]`, `[Conferma]`, `[Stralcio di accomandita finita]`, and `[restituzione capitali]`, preserved raw and marked for review when they combine event types;
- event numbers and references such as `[Nuova] 1922`, `[Disdetta] di 3488`;
- unlabeled `Senza accomandita` acts, which appear in some Camera di Commercio registers;
- the next folio/date/event-label boundary.

Outputs:

- `data/derived/word-pipeline/04_source_entries/source_entries.jsonl`
- `data/derived/word-pipeline/04_source_entries/source_entries.csv`
- `data/derived/word-pipeline/04_source_entries/entry_paragraphs.jsonl`
- `data/derived/word-pipeline/04_source_entries/unsegmented_paragraphs.jsonl`
- `data/derived/word-pipeline/04_source_entries/register_segmentation_summary.csv`
- `data/derived/word-pipeline/04_source_entries/issues.jsonl`
- `data/derived/word-pipeline/04_source_entries/segmentation_summary.md`

Interpretation:

- `source_entries.jsonl` gives one candidate source unit per segmented act.
- `entry_paragraphs.jsonl` maps every assigned paragraph to an entry and records whether the paragraph acted as folio, date, label, or body evidence.
- `unsegmented_paragraphs.jsonl` preserves front matter, register preambles, blank rows, and any paragraph not assigned to an entry.
- `issues.jsonl` is an evaluation aid. Current parser warnings include entries without dates and entries whose label line contains multiple event labels, such as `[Disdetta] ... + [Nuova] ...` or `[nuova/modifica]`.

Do not:

- assume every entry is a new contract;
- throw away front matter;
- normalize event labels without preserving raw labels.
- match entries to SQLite or update the database.

### Stage 4: Match Word entries to DB rows

Goal: candidate alignment, not automatic truth.

Why this stage exists:

- Word entries and SQLite rows are close but not identical evidence layers;
- one Word narrative can summarize more than one act, while SQLite may split those acts across `contract` and `sub_contract` rows;
- new contracts usually point to `contract.contract_id`, while many later acts point to `sub_contract.main_contract_id`;
- folder, folio, date, event number, event type, and narrative text can disagree, so every match must remain inspectable.
- combined Word entries are matched component by component where possible: for example, `[Disdetta] ... + [Nuovo] 4325` may correctly propose both a `sub_contract` row for the disdetta component and a `contract` row for the nuovo component.

Command:

```bash
uv run python workflows/word_pipeline.py match-db
```

Matching signals:

- `folder`;
- `folio`;
- `registration_date`;
- event number;
- event type;
- text similarity against DB `document`, backed by explicit metrics rather than an uninspected label;
- directional token coverage (`word_token_coverage_in_db`, `db_token_coverage_in_word`);
- shared phrase counts and longest shared phrase length;
- structured DB field overlap: firm/sub-firm names, person names, professions, places, addresses, cash amounts, totals, duration/renewal fields, and currency;
- subcontracts via `sub_contract.main_contract_id` and references like "di 3488."
- component-aware labels and IDs inside combined Word entries, so a secondary `[Nuovo]`, `[Rinnovo]`, `[Bilancio]`, or `[Disdetta]` component can support an additional DB link.
- exact ID fallback for DB rows with missing/malformed register metadata, kept as a conflict rather than a clean match.

Important interpretation:

- the DB `document` field is useful evidence because it often mirrors the Word narrative closely;
- it is not a separate archival source, so a literal text match should help align records but should not overrule the authoritative Word narrative;
- field-overlap evidence is interpretable support for a suggestion, not proof that the row is correct;
- the review unit is now the Word source entry plus its proposed DB link group, not only the single highest-scoring DB row.

Outputs:

- `data/derived/word-pipeline/05_db_candidate_matches/entry_db_matches.jsonl`
- `data/derived/word-pipeline/05_db_candidate_matches/entry_db_matches.csv`
- `data/derived/word-pipeline/05_db_candidate_matches/match_candidates.jsonl`
- `data/derived/word-pipeline/05_db_candidate_matches/source_entry_db_link_candidates.jsonl`
- `data/derived/word-pipeline/05_db_candidate_matches/source_entry_db_link_candidates.csv`
- `data/derived/word-pipeline/05_db_candidate_matches/alignment_diagnostics.jsonl`
- `data/derived/word-pipeline/05_db_candidate_matches/alignment_diagnostics.csv`
- `data/derived/word-pipeline/05_db_candidate_matches/db_only_rows.jsonl`
- `data/derived/word-pipeline/05_db_candidate_matches/duplicate_link_candidates.jsonl`
- `data/derived/word-pipeline/05_db_candidate_matches/review_buckets.csv`
- `data/derived/word-pipeline/05_db_candidate_matches/register_match_summary.csv`
- `data/derived/word-pipeline/05_db_candidate_matches/issues.jsonl`
- `data/derived/word-pipeline/05_db_candidate_matches/match_summary.md`

Match states:

- `matched_high_confidence`
- `matched_candidate`
- `matched_multiple`
- `ambiguous`
- `word_only`

Diagnostics:

- `word_only`: source entry has no plausible DB row yet.
- `db_only_rows.jsonl`: DB row was not proposed as a Word-entry link candidate.
- `duplicate_link_candidates.jsonl`: one DB row is proposed for multiple Word entries.
- `review_buckets.csv`: interpretable buckets for expected Word-only rows, true unresolved rows, DB metadata problems, and multi-match cases.
- `top_conflicts`: visible disagreement such as `registration_date_differs`, `folio_differs`, `event_type_table_differs`, `db_register_missing`, or `text_similarity_low`.
- `ambiguous`: plausible candidates exist, but the margin is too weak or the signals conflict.
- `matched_multiple`: one Word entry plausibly maps to multiple DB rows with equivalent evidence, often because a combined Word act is represented as separate DB subcontracts.

Alignment diagnostics:

- these are part of `match-db`, not a separate segmentation stage;
- they do not change Word segmentation or approve DB links;
- they explain unresolved or suspicious alignment cases in reviewer-facing language;
- examples include DB rows whose text appears in a Word entry but were not linked, marginal-date confusion, original/current folio numbering problems, one Word entry combining multiple DB rows, and one DB row being suggested for multiple Word entries.

Alignment layer:

- `match_candidates.jsonl` keeps the ranked scoring evidence for audit and debugging;
- `source_entry_db_link_candidates.csv/jsonl` is the review alignment layer, with one row per proposed Word-entry to DB-row link;
- `match_group_id` groups all proposed links for the same Word source entry;
- `relationship_type` records whether the proposal is `simple_one_to_one`, `word_entry_to_multiple_subcontracts`, `word_entry_to_contract_and_subcontract`, or another one-Word-to-many-DB relationship;
- strong secondary candidates from the same Word entry are promoted into the grouped link layer when component labels/IDs and text or structured-field evidence support them;
- each link row records narrative metrics, shared-phrase counts, and structured field-overlap evidence so every similarity claim is auditable;
- `needs_review` stays `True` for grouped links, candidate matches, or visible conflicts. It does not mean the link is wrong; it means a person should approve the relationship before field reconciliation.

Do not:

- update DB based on matching;
- force a match when folio/date/text disagree.

### Stage 4b: Build the human QA packet

Goal: make the matching diagnostics reviewable by a human, not just machine-readable.

Why this stage exists:

- match scores and short signal codes are useful for scripts but too opaque for judgment calls;
- reviewers need Word and DB narratives side by side, with the strongest signals and conflicts stated in clear language;
- the packet should focus attention on hard cases while still including a small control sample of clean matches.

Command:

```bash
uv run python workflows/word_pipeline.py qa-packet
```

Outputs:

- `data/derived/word-pipeline/06_qa_packet/word_db_match_qa_packet.csv`
- `data/derived/word-pipeline/06_qa_packet/word_db_match_qa_packet.jsonl`
- `data/derived/word-pipeline/06_qa_packet/word_db_match_qa_packet.html`
- `data/derived/word-pipeline/06_qa_packet/qa_packet_summary.md`

The full field-by-field reference is in [qa_packet_schema.md](qa_packet_schema.md)
(the documented contract between the pipeline and the review platform).

What each QA row contains:

- the review priority and recommended review bucket in plain language;
- a suggested reviewer action;
- source entry ID, register, raw Word label, date, and folio;
- top DB row ID, table/type, IDs, and score for backward-readable summaries;
- suggested DB row ID(s), suggested relationship type, and grouped DB-link explanation from `source_entry_db_link_candidates`;
- narrative similarity ratio, directional token coverage, shared-phrase metrics, and structured DB field overlap in plain language;
- alignment diagnostics explaining unresolved or suspicious cases without creating a separate review stage;
- signals and conflicts translated into prose;
- a top-candidates summary;
- optional image candidate paths and image-review notes, when `workflows/image_pipeline.py all` has been run;
- side-by-side Word entry text and suggested DB `document` text.

The generated packet intentionally includes all `word_only`, `ambiguous`, `matched_multiple`, duplicate-link-candidate, and DB-only diagnostics. It also includes targeted candidate-conflict rows, small high-confidence control samples, and a small `Word-DB-image success control` sample where the Word entry, DB row, and image candidate all align cleanly. It is a review queue, not an approval file.

### Stage 5: Extract structured field proposals

Goal: compare Word-derived fields to DB fields.

Fields to extract:

- firm name;
- registration date;
- start date;
- duration;
- renewal terms;
- event type;
- economic activity as stated;
- standardized economic activity proposal;
- place(s) of activity;
- address;
- total capital;
- currency;
- investors;
- roles (`GP` / `LP`, `accomandante` / `accomandatario`);
- titles/status;
- proxy/guardian/heirs flags;
- clauses;
- administrators/managers;
- numerical discrepancies;
- subcontracts and end dates.

Outputs:

- `field_reconciliation_proposals.jsonl`
- `economic_activity_standardization_proposals.jsonl`

Do not:

- overwrite original wording with standardized wording;
- merge people by name alone;
- infer kinship from surname alone.

### Stage 6: Human review

Goal: turn proposals into approved corrections.

The review app is split into a local Python API and a browser interface:

```bash
uv run uvicorn workflows.review_server:app --reload
cd apps/review && npm install && npm run dev
```

The API reads the QA packet and SQLite for display only. The browser interface is designed as a calm one-case-at-a-time review desk, not a dense spreadsheet.

Review UI shows:

- Word source-entry text and metadata;
- one or more suggested DB rows for that Word entry;
- source quote and DB narrative text when available;
- revision state and folio;
- image candidate;
- proposed relationship type;
- reviewer decision.

Review decisions:

- accept;
- reject;
- edit;
- needs image check;
- needs FT review;
- defer.

Outputs:

- `data/derived/word-pipeline/08_review_decisions/review_decisions.csv`
- audit trail with reviewer, timestamp, Word source entry, selected/rejected DB rows, image check status, and notes.

Code:

- `workflows/review_server.py`
- `apps/review/`

Do not:

- batch apply unreviewed proposals;
- hide uncertainty;
- discard rejected proposals without recording why.

### Stage 7: Apply approved database updates

Goal: only after review, generate targeted update scripts.

Tasks:

- create SQL update scripts from approved decisions;
- run checks before and after;
- preserve old values in audit table or audit log;
- keep snapshots/backups.

Do not:

- delete whole rows for typo-level corrections;
- apply LLM output directly;
- update the working DB without a backup and human approval.

### Stage 8: Image map

Goal: link source-entry folios to candidate manuscript images for human review.

Script:

```bash
uv run python workflows/image_pipeline.py inventory
uv run python workflows/image_pipeline.py map-folios
uv run python workflows/image_pipeline.py link-source-entries

# or run all image-link stages
uv run python workflows/image_pipeline.py all
```

Tasks:

- inventory image files per register;
- classify filename roles from patterns such as `_074.jpg`, `_074bis.jpg`, `_081a.jpg`, `000000.jpg`, and `_000A.jpg`;
- exclude photographer cards and front-matter/index images from contract linking;
- create one image-folio mapping row per page candidate in a two-page opening;
- mark special cases with `needs_review` and a plain-language `review_reason`;
- link Word source-entry `folio_start` and `folio_end` values to candidate image pages by register and folio.

Outputs:

- `data/derived/word-pipeline/07_image_links/image_inventory.csv`
- `data/derived/word-pipeline/07_image_links/image_inventory.jsonl`
- `data/derived/word-pipeline/07_image_links/image_folio_map.csv`
- `data/derived/word-pipeline/07_image_links/image_folio_map.jsonl`
- `data/derived/word-pipeline/07_image_links/source_entry_image_candidates.csv`
- `data/derived/word-pipeline/07_image_links/source_entry_image_candidates.jsonl`
- summary files for each layer.

Interpretation:

- `000000.jpg` is classified as `photographer_card` and excluded from contract linking.
- `_000A.jpg`, `_000AA.jpg`, and similar files are classified as `front_matter_or_index` and excluded from contract linking.
- Plain numeric openings infer page candidates such as left `73v` and right `74r` for `_074.jpg`.
- `bis`, lettered, skipped-numbering, and first-opening cases are included where useful but marked `needs_review`.

Do not:

- treat filename-derived folio/page candidates as confirmed manuscript citations;
- treat inferred folio/image links as confirmed.

## LLM-Assisted Extraction

LLMs may be useful, but only after deterministic extraction has created stable source entries.

Good uses:

- field extraction from a single source entry;
- identifying DB/Word differences;
- proposing standardized economic activity;
- classifying ambiguous event labels;
- summarizing why a record needs review.

Bad uses:

- parsing Word tracked-change XML;
- accepting/rejecting tracked changes;
- applying database updates;
- merging people or places without review;
- inventing missing Italian text, dates, names, or field meanings.

LLM output requirements:

- strict JSON schema;
- source quote for every extracted field;
- explicit uncertainty;
- no batch auto-apply;
- human review before any DB write.

Example LLM extraction object:

```json
{
  "source_entry_id": "...",
  "extracted_fields": [
    {
      "field": "economic_activity_raw",
      "value": "negozio d'arte di seta",
      "source_quote": "per esercitarli in negozio d'arte di seta",
      "confidence": "high",
      "notes": []
    },
    {
      "field": "economic_activity_standardized",
      "value": "silk trade",
      "source_quote": "negozio d'arte di seta",
      "confidence": "candidate",
      "needs_human_review": true
    }
  ]
}
```

## What Not To Do

- Do not edit original Word files in place.
- Do not flatten or clean tracked changes without preserving them.
- Do not make SQLite writes from extraction scripts.
- Do not treat Word-derived JSONL as reviewed truth.
- Do not treat DB values as authoritative when Word says otherwise.
- Do not infer people are identical because they share a name.
- Do not normalize economic activity without preserving the raw phrase.
- Do not assume image scan order equals folio order without calibration.
- Do not use LLM output without source quotes and human review.

## Interpretive layers requiring review

These are points where the pipeline encodes an **editorial or historical judgment**
rather than a mechanical fact. They are not bugs; they are decisions that a human
(FT for historical/legal questions) should confirm, because downstream matching,
field reconciliation, and eventual DB updates inherit them. Keep this list current
as the pipeline evolves.

### 1. Narrative label → DB `sub_type` mapping (`DB_EVENT_TYPE_MAP`)

The matcher decides whether a Word event label is "compatible" with a DB row's
`sub_type`. The DB only stores four sub_types (`balance`, `renewal`,
`termination`, `variation`), so every Italian act label is folded into one or more
of those four. Several of these groupings are interpretive and **FT review
pending**:

- `assignment` (*cessione*) → `variation` / `termination`
- `ratification` (*ratifica*) → `variation`
- `confirmation` (*conferma*) and `continuation` (*continuazione*) → `renewal` / `variation`
- `extension` (*proroga*) → `renewal` / `variation`
- `capital_return` (*restituzione capitali*) → `variation` / `termination`
- `winding_up` (*stralcio*) → `termination` / `variation`

The map is intentionally permissive (recall over precision); a wrong grouping only
weakens a +10 signal, it does not by itself force or block a match. Confirm the
historical intent before this hardens into anything authoritative. See also the
glossary entries marked **FT review pending** and `docs/data_dictionary.md`.

### 2. What a text match does and does not prove

- The matcher now scores text on `max(symmetric_ratio, text_containment_ratio)` so
  that a DB `document` that is a faithful **snippet** of the Word narrative is not
  mis-scored as dissimilar. A reviewer should still sanity-check that a
  high-containment match reflects genuine shared narrative, not coincidental
  shared vocabulary.
- The DB `document` field is a **mirror of the Word narrative, not an independent
  archival source**. A strong text match therefore supports *alignment* (this Word
  entry corresponds to this DB row); it does **not** prove the DB's structured
  fields are correct. Field-level correctness is decided against the Word narrative
  and the manuscript image, not against `document`.

### 3. Folio agreement and disagreement

- A Word source entry usually spans the **whole act** (often several folia),
  while a DB row sits on one folio inside that span. Folio comparison therefore
  treats folios as **ranges** and compares intervals, not endpoints:
  `exact` > `within` (one range contains the other) > `overlap` (ranges
  intersect) > `off_by_one` (adjacent folios) > `different`. Only a truly
  non-overlapping `different` raises the `folio_differs` conflict.
- `folio_differs` is **not** an automatic rejection. It is tolerated in the link
  layer only when the event number/main-contract id, the registration date, and
  the narrative all corroborate the link — the signature of **original vs.
  current foliation** in the same register (the DB often stores both, e.g.
  `94r[ORIG.93r]`). A `folio_differs` with weak text still blocks. Treat any
  surviving `folio_differs` or `off_by_one` (`folio_adjacent`) match as a review
  item; the original-numbering diagnostic flags the likely cases.
- Some DB folios use a different physical system entirely (page numbers such as
  `pp.20-21`) and will not reconcile to recto/verso foliation; these remain
  review items rather than matches.

### 4. Dates and the Florentine calendar

- Italian dates are parsed to ISO, including double-dated forms (e.g. `1640/41`).
- The systematic *stile fiorentino* year shift **is now modeled**: the Florentine
  year began 25 March, so a document date in [1 Jan – 24 Mar] lags the modern year
  by one. The DB stores the modern calendar; the Word narratives keep the document's
  stated date. For pre-1750 Word dates in that window the matcher also tries the
  modern-equivalent year and, on a match, records the **`registration_date_stile_fiorentino`**
  signal (a slightly softer +20 vs. the +25 literal match) instead of raising
  `registration_date_differs`. The 1750 cutoff (the Tuscan reform) protects later
  registers. This is an editorial call: a `possible_stile_fiorentino_date_alignment`
  diagnostic is emitted so a reviewer confirms the calendar-style alignment rather
  than trusting it blindly. Any *remaining* `registration_date_differs` in early
  Mercanzia registers should still be checked for other calendar/transcription causes.

### 5. Source-entry ID stability

- Entries now carry a content-stable **`source_entry_key`** alongside the
  human-readable `source_entry_id`. `source_entry_id` is `register_id` + a sequential
  ordinal and is **renumbered** whenever segmentation changes; `source_entry_key` is
  derived from the entry's stable coordinates (register, folio span, earliest ISO
  date, event label, event number), with a text content-hash suffix only for the rare
  full-coordinate collision. It is reproducible across re-segmentation. **Human review
  persistence should key on `source_entry_key`, not `source_entry_id`.** Store a snapshot
  of the reviewed content (e.g. `current_text_sha256`) with each decision so that a
  later content change can be detected and re-confirmed.

### 6. One Word entry → several DB rows

- A single Word narrative can describe several acts (e.g. a termination plus a new
  contract), which the DB splits across `contract` and `sub_contract` rows. The
  `relationship_type` assigned to a link group (`simple_one_to_one`,
  `word_entry_to_multiple_subcontracts`,
  `word_entry_to_contract_and_subcontract`, …) is a heuristic and should be
  confirmed during review.

## Open Questions

- What is the minimum human-review sample needed before scaling extraction?
- Which register should be the pilot: an early difficult one (`10831`), a clean mid-period one (`10843`), or a later tracked-change one (`10858`/`10859`)?
- Should derived revision-aware text be JSONL only, or also TEI/XML for future interchange?
- Should standardized economic activity be developed as a project-local controlled vocabulary first, or aligned with an external vocabulary later?
- What audit mechanism should be used when approved proposals are eventually applied to SQLite?

## Recommended Pilot

Start with one register and produce:

1. `register_inventory.jsonl`
2. `source_entries.jsonl`
3. `entry_db_matches.jsonl`
4. `field_reconciliation_proposals.jsonl`
5. a short review report with 30-50 sampled entries

Suggested pilot choices:

- `Mercanzia 10843`: cleaner input, manageable size, good for parser development.
- `Mercanzia 10858`: tracked-change `.docx`, good for testing revision preservation.
- `Mercanzia 10831`: historically early and important, but more complex; better after the parser is stable.
