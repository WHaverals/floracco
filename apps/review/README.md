# FlorAcco Review App

Purpose-built browser interface for reviewing Word-DB-image alignment cases.

The app is intentionally separate from the data pipelines:

- `workflows/word_pipeline.py qa-packet` creates the review queue.
- `workflows/review_server.py` serves the review queue and writes decisions.
- `apps/review/` renders a browser interface for reviewers.

The review app does **not** update SQLite, Word files, image files, or derived match outputs. It writes reviewer decisions to:

```text
data/derived/word-pipeline/08_review_decisions/review_decisions.csv
```

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

- Word source entry and metadata;
- suggested database record(s), including structured DB fields;
- manuscript image candidate;
- plain-language evidence and diagnostics;
- reviewer decision form.

The core decision is whether the Word source entry supports the selected database record(s). A single Word entry can correctly support multiple DB rows.

## Inputs

The server reads:

```text
data/derived/word-pipeline/06_qa_packet/word_db_match_qa_packet.jsonl
data/sqlite/main.db
```

Use the `.env` values documented in the repository root to point to local data paths.
