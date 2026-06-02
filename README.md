<img src="assets/logo.svg" width="126" alt="FlorAcco">

# FlorAcco

Code, docs, and workflows for stewarding the **Florentine Accomandite Corpus (1445–1808)**: ~4,800 limited-partnership contracts from 20 registers at the Archivio di Stato, Firenze. Led by Francesca Trivellato (IAS Princeton).

**This repo holds code and documentation only.** The SQLite database, Word files, and images live elsewhere (IAS storage). This repo supports inspection and analysis of the data, and provides the framework (workflows, linking, audit trail) to work with the files.

## What we are doing

The corpus currently sits in several places that have drifted apart (all private, not in git):

- **SQLite** — structured fields from a web-app export
- **Word** — Italian narrative summaries (authoritative text; many with tracked changes)
- **Images** — ~4,000 folio photographs

Main priorities:

1. **Stewardship** — reproducible Python environment, backups, data dictionary, project log
2. **Database quality** — issue inventory, tested SQL query cookbook, lightweight browse/edit tools
3. **Improved interface** — better webapp for working with and exploring the data (no longer handrolling queries)
4. **Word ↔ DB reconciliation** — Word-derived source entries and contract bundles with preserved edits, stable IDs linking DB, Word, and images, human-reviewed updates with an audit trail
5. **Entity disambiguation** — person and place names are messy: typos and variants ("Bartolomeo Barbanelli" vs "Barbaneli"), and the same name can refer to different people across decades ("Niccolò Scarlatti" in 1640 ≠ 1750). OpenRefine for clustering and reconciliation; SPLINK for harder probabilistic linkage.

The staged plan for working with the authoritative Word files, matching them to SQLite, and producing reviewable JSONL is in [`docs/workflows/README.md`](docs/workflows/README.md).

## Research objectives

With a reconciled, trustable database, the corpus supports questions such as:

- How did partnerships diversify over time — did contracts bring together people who had not previously co-invested?
- Distribution of firms and investors by location, sector, and economic activity
- Roles of women; kinship and social status (titles) between general and limited partners
- Contracts with both Jewish and non-Jewish investors; patriciate, clergy, and Medici household members via titles
- Contract duration (where termination is recorded); share-buying after late-18th-century reforms
- Network analysis of repeat investors vs. one-off participants

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

For Word reconciliation workflows, also install [LibreOffice](https://www.libreoffice.org/) so legacy `.doc` files can be normalized to `.docx` without touching the originals. The command-line executable should be available as `soffice`:

```bash
soffice --version
```

On macOS, LibreOffice may live at `/Applications/LibreOffice.app/Contents/MacOS/soffice`; the workflow scripts can use that path if `soffice` is not on `PATH`.

```bash
git clone https://github.com/WHaverals/floracco.git
cd floracco
export UV_PROJECT_ENVIRONMENT=.floracco
uv sync
cp .env.example .env   # configure local paths
```

The Python project is named **floracco** (`pyproject.toml`). The virtualenv lives in `.floracco/` (uv default is `.venv`; we set the name above).

Run the first safe Word pipeline stages:

```bash
uv run python workflows/word_pipeline.py inventory
uv run python workflows/word_pipeline.py normalize
uv run python workflows/word_pipeline.py validate-normalized
uv run python workflows/word_pipeline.py extract-registers
uv run python workflows/word_pipeline.py segment-entries
uv run python workflows/word_pipeline.py match-db
uv run python workflows/image_pipeline.py all
uv run python workflows/word_pipeline.py qa-packet
```

The Word and image pipelines write only to `data/derived/`. The Word pipeline converts legacy `.doc` files into derived `.docx` copies because `.docx` exposes inspectable Word XML; originals remain untouched and authoritative. Extraction preserves paragraph order, tracked changes, comments, and footnotes; segmentation groups the paragraph evidence into candidate source entries; DB matching ranks candidate SQLite alignments and writes an explicit link-candidate layer so one Word entry can point to one or more DB rows. Match suggestions include auditable text metrics and structured DB-field overlap, such as names, places, firm names, and amounts. The same matching stage also writes alignment diagnostics for unresolved or suspicious cases; these guide review without making Word segmentation DB-driven. The image pipeline classifies manuscript photographs, excludes front matter and photographer-card images from contract linking, and creates provisional folio/image candidates for review. The QA packet turns those ranked candidates into CSV/HTML review material for human judgment calls.

Run the human review app:

```bash
uv run uvicorn workflows.review_server:app --reload
cd apps/review && npm install && npm run dev
```

The review app is a React/Vite browser interface backed by a local FastAPI server. It writes decisions to `data/derived/word-pipeline/08_review_decisions/review_decisions.csv`, including selected and rejected DB-row links for each Word entry; it does not update SQLite.

## What lives here

| Path | Purpose |
|------|---------|
| `apps/review/` | Browser review app for Word-DB-image alignment decisions |
| `notebooks/` | Data-quality reports and exploration |
| `workflows/` | OpenRefine, reconciliation, conversion scripts |
| `docs/data_dictionary.md` | Field definitions (in progress) |
| `docs/glossary.md` | Historical and project terms |
| `docs/workflows/` | Workflow plans, including Word ↔ DB reconciliation |
| `LOG.md` | Running log of decisions and data changes |


## People

Francesca Trivellato (FT), Wouter Haverals (WH), Brian Kernighan (BK), Jonathan Betz (JB)
