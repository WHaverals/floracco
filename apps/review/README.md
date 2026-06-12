# FlorAcco Review App

Purpose-built browser interface for reviewing Word-DB-image alignment cases.

The app is intentionally separate from the data pipelines:

- `workflows/word_pipeline.py qa-packet` creates the review queue.
- `workflows/review_server.py` serves the review queue and writes decisions.
- `apps/review/` renders a browser interface for reviewers.

Write scope — what touches what:

- **Reconcile** never updates SQLite, Word files, image files, or derived match
  outputs. It writes reviewer decisions to
  `data/derived/word-pipeline/08_review_decisions/review_decisions.csv`.
- **Corrections** is the platform's only path that writes to
  `data/sqlite/main.db` — deliberate, human-triggered, drift-guarded `UPDATE`s
  with pre/post images (see `docs/corrections_workflow.md`).
- **Database** hide/restore actions write through the audited operation log in
  `data/sqlite/corrections.db` (see `docs/workflows/db_corrections_design.md`).
- Word files, images, and pipeline outputs are never written by any tool.

## Run

From the repository root:

```bash
uv sync
uv run uvicorn workflows.review_server:app --reload
```

In a second terminal:

```bash
cd apps/review
npm install
npm run dev
```

Open the Vite URL, usually <http://127.0.0.1:5173>.

## Review Model

The interface is organized around one case at a time:

- Word source entry and metadata (with a manuscript thumbnail + lightbox);
- suggested database record(s), each with a per-row `Supported` toggle and
  text-strength chip;
- plain-language evidence backed by recorded metrics, behind one disclosure;
- three actions: **Confirm · next**, **None match**, **Not sure**.

The core decision is whether the Word source entry supports the selected database record(s). A single Word entry can correctly support multiple DB rows. Decision columns and semantics: `docs/workflows/qa_packet_schema.md`.

## Inputs

The server reads (all read-only except the decision CSV and the Corrections stores):

```text
data/derived/word-pipeline/06_qa_packet/word_db_match_qa_packet.jsonl
data/derived/word-pipeline/05_db_candidate_matches/source_entry_db_link_candidates.jsonl
data/derived/word-pipeline/04_source_entries/source_entries.jsonl
data/derived/word-pipeline/07_image_links/source_entry_image_candidates.jsonl
data/derived/word-pipeline/10_corrections/*           (candidates + proposals)
data/sqlite/main.db
data/sqlite/corrections.db                            (change history)
```

Use the `.env` values documented in the repository root to point to local data paths.
