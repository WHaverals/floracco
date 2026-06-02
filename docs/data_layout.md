# Data and database artifacts

Local data lives under `data/` (entire directory gitignored). This document describes that layout. Operational rules for agents: [AGENTS.md](../AGENTS.md).

## Local data (`data/` — not in git)

```
data/
├── corpus/                 # Documentary corpus (read-only)
│   ├── word/               # Authoritative contract narratives (.doc/.docx)
│   └── img/                # Folio photographs, one folder per register
├── sqlite/                 # Structured database
│   ├── main.db             # Working SQLite database (~4,866 contracts)
│   ├── projects_at.sql     # Live IAS MySQL export (phpMyAdmin; canonical source)
│   └── archive/            # Optional retired dumps (e.g. old `new.sql`)
├── derived/                # Regenerable outputs (normalized Word, extracted JSONL)
└── reference/              # Local reference copies (optional)
    ├── sql-formulas/       # Legacy analytical SQL in Word (.docx); MariaDB syntax
    └── Accomandite.pdf     # Background lecture (optional local copy)
```

Configure paths in `.env` (see `.env.example`).

### Authority

| Content | Location | Notes |
|---------|----------|--------|
| Contract narratives | `data/corpus/word/` | Authoritative text; tracked changes |
| Manuscript images | `data/corpus/img/` | Folio evidence |
| Structured fields | `data/sqlite/main.db` | May lag Word; known errors |
| Analytical queries | `data/reference/sql-formulas/` | Port to `queries/` as SQLite |

Linking: DB fields `archive`, `series`, `folder`, `folio` ↔ Word file ↔ image folder (use a register map).

Workflow plan: [workflows/README.md](workflows/README.md) describes the staged Word → extracted entries → DB matching → reviewable JSONL process. Original Word files remain untouched; derived copies and JSONL outputs belong under a gitignored derived-data area.

### Rebuild database

From the live IAS MySQL export (`projects_at.sql` — 11 research tables only; skips `admin` and MySQL views):

```bash
uv run python workflows/db_import.py build
```

Optional: set `FLORACCO_DB_DUMP_PATH` if the dump is not at `data/sqlite/projects_at.sql`. The script backs up the existing `main.db` to `main.db.bak`, validates row counts, and exits non-zero on failure.

After import, rebuild derived Word-DB artifacts (do not re-run Word stages 00–04 unless the corpus changed):

```bash
uv run python workflows/word_pipeline.py match-db
uv run python workflows/word_pipeline.py qa-packet
uv run python workflows/correction_candidates.py build
```

## Database schema (in git)

| Path | Role |
|------|------|
| `queries/schema/tables.sql` | Eleven base tables (readable schema, no data) |
| `docs/schema/*.xls` | Field definitions and input rules (source for data dictionary) |

### Core tables

- **`contract`** — main accomandita record
- **`sub_contract`** — renewals / dissolutions (`main_contract_id`)
- **`person`** — identity across contracts
- **`investor`** — person on one contract (role, titles, flags)
- **`investment`** — capital tranche
- **`investor_group`** — links investors to investments
- **`contract_place`** — operating locations
- Lookups: **`place`**, **`title`**, **`currency`**, **`economic_activity`**

**Key IDs:** `person_id` = same human across contracts; `investor_id` = one appearance on one contract.

## Project documentation (in git, `docs/`)

| Path | Role |
|------|------|
| `project-charter.pdf` | Scope, phases, deliverables |
| `2025.12.10 Memo Kernighan.pdf` | Project background and goals |
| `schema/` | Database field specs (Excel) |
| `data_dictionary.md` | Field glossary (in progress) |
| `data_layout.md` | This file |
| `workflows/README.md` | Word ↔ DB reconciliation plan |

## Query cookbook (in git)

| Path | Role |
|------|------|
| `queries/schema/tables.sql` | Schema reference |
| `queries/*.sql` | Tested SQLite queries (to be added) |

Legacy MariaDB queries in `data/reference/sql-formulas/` — do not run verbatim in SQLite.
