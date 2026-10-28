"""The backend contract behind the Reference worklist's "Not the same" action
(code-review finding D5): `distinct` is NOT transitive, so a family is resolved
only when EVERY pair is decided. The frontend therefore writes all n·(n-1)/2
pairwise links; linking only canonical↔member leaves a pair undecided and the
family resurfaces forever.

    uv run --with pytest pytest tests/test_reference_distinct_family.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workflows import corrections_db, review_server

_SCHEMA_SEED = """
CREATE TABLE currency (currency_id INTEGER PRIMARY KEY, currency TEXT);
CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, currency_id INTEGER,
    is_deleted INTEGER NOT NULL DEFAULT 0);
-- three spellings of the Portuguese cruzado (a real 3-family from the corpus),
-- each used by one contract so the worklist includes them
INSERT INTO currency VALUES (1,'cruzados'),(2,'crusadi'),(3,'cruciati');
INSERT INTO contract VALUES (10,1,0),(11,2,0),(12,3,0);
"""


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    main = str(tmp_path / "main.db")
    corr = tmp_path / "corr.db"
    c = sqlite3.connect(main)
    c.executescript(_SCHEMA_SEED)
    c.commit()
    c.close()

    def open_db():
        x = sqlite3.connect(main)
        x.row_factory = sqlite3.Row
        return x

    monkeypatch.setattr(review_server, "open_db", open_db)
    monkeypatch.setattr(review_server, "open_corrections", lambda: corrections_db.connect(corr))
    return corr


def _family_sizes():
    out = review_server.db_reference_duplicates("currency")
    fams = (out.get("families") or []) + (out.get("uncertain") or [])
    return sorted(len(f["terms"]) for f in fams)


def test_distinct_needs_every_pair_decided(wired):
    corr = wired
    assert _family_sizes() == [3]  # the cruzados/crusadi/cruciati family surfaces

    # the OLD frontend behavior: "Not the same" wrote only canonical↔member
    clog = corrections_db.connect(corr)
    corrections_db.add_reference_link(clog, kind="currency", rel="distinct", from_id=2, to_id=1, by="WH")
    corrections_db.add_reference_link(clog, kind="currency", rel="distinct", from_id=3, to_id=1, by="WH")
    clog.close()
    # pair (2,3) is still undecided → the family must resurface (the D5 bug's root)
    assert _family_sizes() == [3]

    # the fixed frontend writes ALL pairs — the last one resolves the family
    clog = corrections_db.connect(corr)
    corrections_db.add_reference_link(clog, kind="currency", rel="distinct", from_id=2, to_id=3, by="WH")
    clog.close()
    assert _family_sizes() == []


def test_same_as_star_is_transitive_and_suffices(wired):
    corr = wired
    clog = corrections_db.connect(corr)
    corrections_db.add_reference_link(clog, kind="currency", rel="same_as", from_id=2, to_id=1, by="WH")
    corrections_db.add_reference_link(clog, kind="currency", rel="same_as", from_id=3, to_id=1, by="WH")
    clog.close()
    # canonical star connects every member transitively — no pairwise links needed
    assert _family_sizes() == []
