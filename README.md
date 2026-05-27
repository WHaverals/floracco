<img src="assets/logo.svg" width="126" alt="FlorAcco">

# FlorAcco

Code, docs, and workflows for stewarding the **Florentine Accomandite Corpus (1445–1808)**: ~4,800 limited-partnership contracts from 20 registers at the Archivio di Stato, Firenze. Led by Francesca Trivellato (IAS Princeton).

**This repo holds code and documentation only.** The SQLite database, Word narrative summaries, and folio images live elsewhere (IAS storage). The repo’s job is to make that material reproducible and traceable.

## What we are doing

The corpus currently sits in several places that have drifted apart:

- **SQLite** — structured fields from a web-app export
- **Word** — Italian narrative summaries (authoritative text; many with tracked changes)
- **Images** — ~4,000 folio photographs (private; not in git)

Main priorities:

1. **Stewardship** — reproducible Python environment, backups, data dictionary, project log
2. **Database quality** — issue inventory, tested SQL query cookbook, lightweight browse/edit tools
3. **Word ↔ DB reconciliation** — per-contract XML with preserved edits, stable IDs linking DB, Word, and images, human-reviewed updates with an audit trail
4. **Entity disambiguation** — person and place names are messy: typos and variants ("Bartolomeo Barbanelli" vs "Barbaneli"), and the same name can refer to different people across decades ("Niccolò Scarlatti" in 1640 ≠ 1750). OpenRefine for clustering and reconciliation; SPLINK for harder probabilistic linkage.

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

```bash
git clone <repo-url>
cd floracco
export UV_PROJECT_ENVIRONMENT=.floracco
uv sync
cp .env.example .env   # edit paths to your local DB and data
```

The Python project is named **floracco** (`pyproject.toml`). The virtualenv lives in `.floracco/` (uv default is `.venv`; we set the name above).

Run a notebook: `uv run marimo edit notebooks/` (create `notebooks/` as needed).

## What lives here (planned)

| Path | Purpose |
|------|---------|
| `LOG.md` | Running log of decisions and data changes |
| `docs/data_dictionary.md` | Field definitions for the database |
| `docs/glossary.md` | Historical and project terms |
| `queries/` | Tested, portable SQL (query cookbook) |
| `notebooks/` | Data-quality reports and exploration |
| `workflows/` | OpenRefine, reconciliation, and conversion scripts |


## People

Francesca Trivellato, Wouter Haverals, Brian Kernighan, Jonathan Betz
