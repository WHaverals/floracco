"""E3 — the two untested importers (docs/code_review/findings.md E3).

Part 1: the MySQL-dump parser trio in db_import (extract_insert_statements,
_find_statement_end, mysql_insert_to_sqlite). NOT one-shot code: it runs again
on every reseed with a fresh archive export, and a subtle escape bug silently
mangles corpus text at the source. Every assertion here round-trips through a
real SQLite execute, so the test proves end-to-end character fidelity.

Part 2: the manuscript-folio parsers in word_pipeline (strip_db_folio_annotations,
parse_db_folio, folio_sort_key) — LIVE server code choosing which scan is shown
next to each record. The wrong-opening bug already shipped once (LOG 2026-06:
a record on 26v also showed the previous opening until side-aware tokens landed);
the four hand-verified cases from that fix are pinned here at last.

    uv run --with pytest pytest tests/test_import_and_folio.py
"""

from __future__ import annotations

import sqlite3

import pytest

from workflows.db_import import (
    _find_statement_end,
    _norm,
    blank_placeholder_text,
    extract_insert_statements,
    mysql_insert_to_sqlite,
)
from workflows.word_pipeline import folio_sort_key, parse_db_folio, strip_db_folio_annotations


# ---------------------------------------------------------------------------
# Part 1 — the dump parsers
# ---------------------------------------------------------------------------

def _dump_around(statements: str, table: str = "contract") -> str:
    """Wrap INSERT statements in the dump structure extract_… expects."""
    return (
        f"-- Dumping data for table `{table}`\n--\n\n"
        f"{statements}\n"
        f"\n-- --------------------------------------------------------\n\n--\n"
        f"-- Table structure for table `next_table`\n"
    )


def _roundtrip(values_sql: str) -> list[tuple]:
    """Extract → convert → execute in real SQLite → return the stored rows."""
    dump = _dump_around(f"INSERT INTO `contract` (`contract_id`, `document`) VALUES {values_sql};")
    stmts = extract_insert_statements(dump, "contract")
    assert len(stmts) == 1
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, document TEXT)")
    conn.execute(mysql_insert_to_sqlite(stmts[0]))
    return conn.execute("SELECT contract_id, document FROM contract ORDER BY contract_id").fetchall()


def test_escaped_apostrophe_roundtrips():
    # the single most common shape in this corpus: elided Italian articles
    assert _roundtrip(r"(1, 'trafficare nell\'arte della seta')") == [
        (1, "trafficare nell'arte della seta")
    ]


def test_every_mysql_escape_roundtrips():
    rows = _roundtrip(
        r"(1, 'detto \"il Rosso\"'), (2, 'line1\nline2'), (3, 'tab\there'), "
        r"(4, 'back\\slash'), (5, 'cr\rhere')"
    )
    assert rows == [
        (1, 'detto "il Rosso"'),
        (2, "line1\nline2"),
        (3, "tab\there"),
        (4, "back\\slash"),
        (5, "cr\rhere"),
    ]


def test_nul_escape_fails_loudly_not_silently():
    # Discovered when this suite first ran (2026-08-27): a MySQL \0 escape
    # becomes a literal NUL, which sqlite3 REFUSES to execute. The real dump
    # contains zero \0 escapes (verified), so this is a documented limitation
    # pinned as-is: if one ever appears, the reseed must crash loudly (dump
    # corruption a human should look at), never silently alter the text.
    with pytest.raises(sqlite3.ProgrammingError, match="null character"):
        _roundtrip(r"(1, 'nul\0char')")


def test_doubled_quote_and_semicolon_inside_literal():
    # a '' escape and a ; inside the string must not terminate anything early
    rows = _roundtrip(r"(1, 'l''arte; e mestiere')")
    assert rows == [(1, "l'arte; e mestiere")]


def test_semicolon_in_literal_does_not_end_statement():
    text = r"INSERT INTO `t` VALUES (1, 'a; b\'; c');"
    end = _find_statement_end(text, 0)
    assert end == len(text) - 1  # the real terminator, not the ones inside the string


def test_two_statements_back_to_back():
    dump = _dump_around(
        "INSERT INTO `contract` (`contract_id`, `document`) VALUES (1, 'primo');\n"
        "INSERT INTO `contract` (`contract_id`, `document`) VALUES (2, 'secondo');"
    )
    stmts = extract_insert_statements(dump, "contract")
    assert len(stmts) == 2
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE contract (contract_id INTEGER PRIMARY KEY, document TEXT)")
    for s in stmts:
        conn.execute(mysql_insert_to_sqlite(s))
    assert conn.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 2


def test_extraction_stops_at_next_table_and_missing_table_is_empty():
    dump = _dump_around("INSERT INTO `contract` (`contract_id`, `document`) VALUES (1, 'x');")
    dump += "INSERT INTO `contract` (`contract_id`, `document`) VALUES (99, 'beyond');\n"
    assert len(extract_insert_statements(dump, "contract")) == 1  # the one before the divider
    assert extract_insert_statements(dump, "no_such_table") == []


def test_unterminated_insert_raises_loudly():
    dump = _dump_around("INSERT INTO `contract` (`contract_id`) VALUES (1, 'never closed")
    with pytest.raises(ValueError, match="Unterminated"):
        extract_insert_statements(dump, "contract")


# ---------------------------------------------------------------------------
# Part 2 — the folio parsers (all expected values ground-truthed 2026-08-27)
# ---------------------------------------------------------------------------

def test_annotation_stripping():
    assert strip_db_folio_annotations("94r[ORIG.93r]") == "94r"
    assert strip_db_folio_annotations("118v-119r/117v-118r") == "118v-119r"
    assert strip_db_folio_annotations("76v=774") == "76v"
    assert strip_db_folio_annotations(None) == ""


def test_parse_db_folio_hand_verified_cases():
    # the four cases hand-verified when the wrong-opening bug was fixed (LOG)
    assert parse_db_folio("26v") == ("26v", "26v")
    assert parse_db_folio("21r") == ("21r", "21r")
    assert parse_db_folio("160v [ORIG.159v]") == ("160v", "160v")
    assert parse_db_folio("9r-v") == ("9r", "9v")


def test_parse_db_folio_shapes():
    assert parse_db_folio("24rv") == ("24r", "24v")     # sideless recto-verso
    assert parse_db_folio("109vr") == ("109r", "109v")  # ...in either order
    assert parse_db_folio("24r-25v") == ("24r", "25v")  # a true span
    assert parse_db_folio("118v-119r/117v-118r") == ("118v", "119r")
    assert parse_db_folio("12") == ("12", "12")         # bare leaf number
    assert parse_db_folio(None) == (None, None)
    assert parse_db_folio("") == (None, None)
    assert parse_db_folio("n.n") == ("n.n", "n.n")      # unparseable stays verbatim


def test_folio_sort_key_side_awareness():
    # recto before verso before the next leaf — THE property whose loss caused
    # the shipped wrong-opening bug
    assert folio_sort_key("26r") < folio_sort_key("26v") < folio_sort_key("27r")
    assert folio_sort_key("26r") < folio_sort_key("26bis")   # bis after the plain leaf
    assert folio_sort_key("26r") < folio_sort_key("26(a)r")  # lettered after plain
    assert folio_sort_key("n.n") is None and folio_sort_key(None) is None


# ---------------------------------------------------------------------------
# Part 3 — seed hygiene: placeholder strings become SQL NULL (LOG 2026-09-10)
# ---------------------------------------------------------------------------

def test_blank_placeholder_text_only_touches_placeholder_cells():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE contract (contract_id int(11), firm_name varchar(255), folio text, total int(11), document text);
        INSERT INTO contract VALUES (69, 'NULL', '10r', 150, 'a narrative');
        INSERT INTO contract VALUES (70, 'Nullo Nulli e compagni', ' none ', NULL, '-');
        INSERT INTO contract VALUES (71, NULL, '', 0, 'n.n');
        """
    )
    blanked = blank_placeholder_text(c, ["contract"])
    assert blanked == {"contract.firm_name": 1, "contract.folio": 1, "contract.document": 1}
    rows = c.execute("SELECT contract_id, firm_name, folio, total, document FROM contract ORDER BY 1").fetchall()
    assert rows == [
        (69, None, "10r", 150, "a narrative"),           # the export's one quoted 'NULL'
        (70, "Nullo Nulli e compagni", None, None, None),  # a real name survives; ' none ' and '-' do not
        (71, None, "", 0, "n.n"),                         # '' and integer 0 are untouched
    ]
    # idempotent: a second pass finds nothing
    assert blank_placeholder_text(c, ["contract"]) == {}


def test_replay_norm_treats_literal_null_as_blank():
    # the June 2026 op-log recorded 'NULL' as the pre-image of contract 69's firm
    # name; against the cleaned seed (SQL NULL) that history must still match
    assert _norm("NULL") == _norm(None) == _norm("  ") == ""
    assert _norm(" Nulli ") == "Nulli"
