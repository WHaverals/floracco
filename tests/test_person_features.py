"""The person spine's definitions — where historical judgement is encoded.

Each test pins one decision from `docs/person_linkage/research.md` §10 that a
future refactor could silently reverse: how missing names behave, what counts
as a career, which partners carry evidence.

    uv run --with pytest --extra linkage pytest tests/test_person_features.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workflows import db_import
from workflows.person_features import classify_entity_kind, load_person_spine, normalize_name

_TABLES_SQL = Path(__file__).resolve().parents[1] / "queries" / "schema" / "tables.sql"

# One contract per year so windows are readable; contract 9 is undated.
_SEED = """
INSERT INTO contract (contract_id, registration_date, firm_name) VALUES
 (1,'1550-01-01','Rossi e compagni'), (2,'1560-01-01','Rossi e compagni'),
 (3,'1600-01-01','Bardi e compagni'), (4,'1610-01-01','Bardi e compagni'),
 (9,'0000-00-00','Senza data');
INSERT INTO person (person_id, first_name, father_mother, grandfather, last_name, is_woman) VALUES
 (1,'Piero','Filippo','Matteo','Strozzi',0),
 (2,'Lena',NULL,NULL,'Rucellai',1),        -- no patronymic: must read as MISSING
 (3,'Ghost',NULL,NULL,'Senzatracce',0),    -- no appearances at all
 (4,'Hub','Molto',NULL,'Ovunque',0),       -- appears everywhere: a hub
 (5,'Rara','Poco',NULL,'Avis',0);
INSERT INTO investment (investment_id, contract_id, type) VALUES
 (10,1,'gp'),(11,2,'gp'),(12,3,'lp'),(13,4,'lp'),(19,9,'gp');
INSERT INTO investor (investor_id, person_id, contract_id, heirs_of) VALUES
 (100,1,1,0),(101,1,2,0),      -- Piero: alive 1550-1560
 (102,1,4,1),                  -- ...and his ESTATE acts in 1610 (posthumous)
 (103,2,9,0),                  -- Lena: only an undated contract
 (104,4,1,0),(105,4,2,0),(106,4,3,0),(107,4,4,0),   -- Hub: on everything
 (108,5,1,0);                  -- Rara: shares contract 1 with Piero and Hub
INSERT INTO investor_group (investor_id, investment_id) VALUES
 (100,10),(101,11),(102,13),(103,19),(104,10),(105,11),(106,12),(107,13),(108,10);
"""


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_TABLES_SQL.read_text() + _SEED)
    db_import.add_soft_delete_columns(c)
    return c


def _row(spine, person_id):
    return spine[spine.person_id == person_id].iloc[0]


# ---------------------------------------------------------------------------
# Name normalization: what it folds, and what it must never fold
# ---------------------------------------------------------------------------

def test_missing_names_stay_missing():
    # The bug this pins (found 2026-08-28 by the memo checkpoints): a NULL
    # arriving as NaN became the literal string "nan", so two people who BOTH
    # lacked a patronymic would have appeared to agree on it — a false-positive
    # generator dressed as a missing value.
    import math
    for empty in (None, "", "   ", float("nan"), math.nan):
        assert normalize_name(empty) == ""


def test_encoding_variants_fold_together():
    # the three stored apostrophes (a MySQL-escape artefact splits the Medici block)
    assert normalize_name("de\\' Medici") == normalize_name("de' Medici") == normalize_name("de’ Medici")
    assert normalize_name("  Niccolò   ROSSI ") == "niccolo rossi"


def test_semantic_differences_are_preserved():
    # gender vowels, family -i/-o, and gemination are MEANING, not spelling
    assert normalize_name("Francesco") != normalize_name("Francesca")
    assert normalize_name("Rossi") != normalize_name("Rosso")
    assert normalize_name("Capponi") != normalize_name("Caponi")


# ---------------------------------------------------------------------------
# What counts as a career
# ---------------------------------------------------------------------------

def test_career_window_ignores_posthumous_appearances(conn):
    # Piero invests 1550-1560; his estate acts in 1610. A career is what
    # someone did while alive — otherwise his window stretches 60 years and he
    # becomes a spurious over-merge candidate (the person 3460 shape).
    piero = _row(load_person_spine(conn), 1)
    assert (piero.first_year, piero.last_year) == (1550, 1560)
    assert piero.span_years == 10
    assert piero.n_posthumous == 1
    assert piero.first_posthumous_year == 1610   # kept: a terminus ante quem


def test_undated_contracts_do_not_create_a_career(conn):
    # Lena appears only on the '0000-00-00' contract: no window at all, rather
    # than a window starting in year 0.
    lena = _row(load_person_spine(conn), 2)
    assert lena.n_appearances == 1
    assert lena.n_dated == 0
    assert pd_isna(lena.first_year) and pd_isna(lena.span_years)


def test_zero_appearance_persons_are_kept_with_empty_arrays(conn):
    # The 856 batch-entry ghosts are candidates too — they must survive the
    # spine as real rows, with empty (not missing) arrays.
    ghost = _row(load_person_spine(conn), 3)
    assert ghost.n_appearances == 0
    assert ghost.contracts == [] and ghost.partners == [] and ghost.firm_tokens == []
    assert ghost.dominant_role == "unknown"


# ---------------------------------------------------------------------------
# What counts as network evidence
# ---------------------------------------------------------------------------

def test_hub_partners_are_dropped(conn):
    # Sharing a partner who appears on everything is nearly no evidence;
    # sharing a rare one is strong. With the threshold at 2, 'Hub' (4
    # contracts) drops out of everyone's partner set, 'Rara' stays.
    spine = load_person_spine(conn, hub_threshold=2)
    assert 4 not in _row(spine, 1).partners      # Hub excluded
    assert 5 in _row(spine, 1).partners          # Rara kept
    # and with a permissive threshold the hub returns — the filter, not the data
    assert 4 in _row(load_person_spine(conn, hub_threshold=99), 1).partners


def test_firm_tokens_drop_legal_boilerplate(conn):
    tokens = _row(load_person_spine(conn), 1).firm_tokens
    assert "rossi" in tokens          # a PARTNER's name: real network evidence
    assert "compagni" not in tokens and "e" not in tokens


def test_firm_tokens_never_hand_a_person_their_own_name_back():
    # A Florentine firm is named after its partners, so tokenising the firm name
    # puts the person's own name straight back into their own firm_tokens. Two
    # rows of the same name then intersect on those tokens automatically and the
    # network comparison scores it as independent evidence of identity — but it
    # is the name, counted a second time, once by `name` and again by `network`.
    # Measured before this guard, persons 11820/12009 shared NO partners at all
    # yet scored the firm-token level on 'gianfigliazzi' and 'leonardo'.
    seed = """
    INSERT INTO contract (contract_id, registration_date, firm_name) VALUES
     (1,'1550-01-01','Gianfigliazzi e Tornabuoni');
    INSERT INTO person (person_id, first_name, father_mother, grandfather, last_name, is_woman)
     VALUES (1,'Leonardo','Luigi',NULL,'Gianfigliazzi',0);
    INSERT INTO investment (investment_id, contract_id, type) VALUES (10,1,'gp');
    INSERT INTO investor (investor_id, person_id, contract_id, heirs_of) VALUES (100,1,1,0);
    INSERT INTO investor_group (investor_id, investment_id) VALUES (100,10);
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_TABLES_SQL.read_text() + seed)
    db_import.add_soft_delete_columns(c)

    tokens = _row(load_person_spine(c), 1).firm_tokens
    assert "gianfigliazzi" not in tokens, "the person's own surname came back as evidence"
    assert "leonardo" not in tokens, "the person's own given name came back as evidence"
    assert "tornabuoni" in tokens, "a partner's name is exactly what this feature is for"


def test_firms_carry_other_peoples_names_but_never_your_own():
    # An accomandita is renewed, so the same firm recurs across contracts and two
    # records inside it are in the same business — evidence `partners` cannot
    # give, because partner sets are keyed on person_id and a duplicated person's
    # partners are duplicated too. But a firm named after the person themselves
    # is the NAME counted twice, so it must be stripped.
    #
    # This test builds the SPINE rather than calling the helper directly: an
    # earlier version called `_drop_self_referential_firms` itself and therefore
    # survived a mutation that removed the call site entirely.
    seed = """
    INSERT INTO contract (contract_id, registration_date, firm_name) VALUES
     (1,'1550-01-01','Mazzeo Mazzei e compagni'),
     (2,'1552-01-01','Zanobi Lippi e compagni');
    INSERT INTO person (person_id, first_name, father_mother, grandfather, last_name, is_woman)
     VALUES (1,'Mazzeo','Piero',NULL,'Mazzei',0);
    INSERT INTO investment (investment_id, contract_id, type) VALUES (10,1,'gp'),(11,2,'lp');
    INSERT INTO investor (investor_id, person_id, contract_id, heirs_of) VALUES (100,1,1,0),(101,1,2,0);
    INSERT INTO investor_group (investor_id, investment_id) VALUES (100,10),(101,11);
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_TABLES_SQL.read_text() + seed)
    db_import.add_soft_delete_columns(c)

    firms = _row(load_person_spine(c), 1).firms
    assert any("lippi" in f for f in firms), (
        "a firm named for somebody ELSE is exactly the evidence this feature is for")
    assert not any("mazzei" in f for f in firms), (
        "a firm named for the person is their own name, not independent evidence")


def test_dominant_role(conn):
    spine = load_person_spine(conn)
    assert _row(spine, 1).dominant_role == "gp"     # two gp tranches
    assert _row(spine, 4).dominant_role == "mixed"  # two gp, two lp


# ---------------------------------------------------------------------------
# What counts as a person at all
# ---------------------------------------------------------------------------

def kind(first=None, patronymic=None, grandfather=None, last=None) -> str:
    return classify_entity_kind(first, patronymic, grandfather, last)


def test_institutions_are_not_people():
    assert kind(last="Arte della Lana") == "institution"
    assert kind(first="Arte della lana") == "institution"          # name wanders fields
    assert kind(first="Monte di Pietà di Firenze") == "institution"
    assert kind(first="Regio Spedale degli Innocenti") == "institution"
    assert kind(first="erario imperiale di Sua Maestà Cesarea") == "institution"
    assert kind(first="Congregazione della Grascia di Firenze") == "institution"


def test_real_families_are_not_mistaken_for_institutions():
    # THE trap this classifier exists to avoid: Florentine surnames collide
    # with institutional vocabulary. A bare-keyword list ("badia", "camera",
    # "compagni") reclassifies real people as buildings — the same false-friend
    # lesson as the activity matcher's gemination fold.
    assert kind(first="Benedetto Michele", last="del Badia") == "person"
    assert kind(first="Giovanni Bonaventura", last="Neri Badia") == "person"
    assert kind(first="Francesco", last="Camerati") == "person"
    assert kind(first="Bartolomeo", patronymic="Neri", last="Compagni") == "person"


def test_estates_are_not_people():
    assert kind(first="eredi", patronymic="Neri", last="Corsini") == "estate"
    assert kind(first="heredi", patronymic="Neri", last="Corsini") == "estate"
    # ...but a man whose given name merely starts with those letters is a person
    assert kind(first="Eredino", last="Rossi") == "person"


def test_collectives_are_many_people_in_one_row():
    # Shareholders recorded as a single row: 19 contracts, 1767-1807, all of
    # whose narratives speak of azioni/azionisti. Kept distinct from
    # 'placeholder' because a placeholder is ONE unidentified person while a
    # collective is many — they distort counts differently, and these rows mark
    # the corpus's share companies (docs/data_quality/non_person_rows.md).
    assert kind(last="MOLTEPLICI AZIONARI") == "collective"   # 18 contracts, in last_name
    assert kind(first="azionisti vari") == "collective"       # lowercase, in first_name
    assert kind(first="caratisti") == "collective"
    # ...but the role words must not swallow real surnames that merely rhyme
    assert kind(first="Gaspero", last="Diversi") == "person"
    assert kind(first="Sebastiano", last="Corvari") == "person"
    assert kind(first="Francesco", last="Giavarini") == "person"


def test_placeholders_are_not_people():
    assert kind(first="C.M") == "placeholder"
    assert kind(first="G.B.") == "placeholder"
    assert kind() == "placeholder"                       # nothing recorded at all
    assert kind(first="amico di Pagolo Guardi") == "placeholder"


def test_ordinary_people_are_people():
    assert kind(first="Alfonso", patronymic="Filippo", grandfather="Matteo", last="Strozzi") == "person"
    assert kind(first="Lisabetta", patronymic="Giovanni", last="Corradini") == "person"
    assert kind(last="Strozzi") == "person"              # bare surname: thin, but a person


def pd_isna(value) -> bool:
    import pandas as pd
    return bool(pd.isna(value))
