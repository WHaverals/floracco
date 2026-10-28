"""Find, steps 1 and 2 (docs/analysis/review_2026-10-27.md, sections 11-12, build path 12.8).

One question crosses the contract and its partners: "contracts operating in
Livorno with at least one partner who is Jewish". The fixtures below are that
request in miniature, and each test fails without the part it names:
- the answer splits by provenance (on the live data 107 = 17 + 90);
- "the same partner" and "another partner" are two questions (12 against 227);
- a year range leaves the undated out, and says how many;
- "no partner who" and "every partner" leave out institutions and placeholders,
  and say so;
- a removed appearance, a hidden person, a hidden stake link, place link or
  contract count nowhere (the full sweep is in tests/test_hidden_sweep.py);
- every reading is pinned to its SQL; the page writes the readings the server
  writes; the download opens with the question; and the same-partner question
  stays fast on a live-size corpus.
Step 2 (12.8): where a partner lives and comes from; the partner's own title or
profession, never a relative's; activity, currency, register and the open
formulas; "is not"; "not recorded" as a value; the counts inside the pickers
(in this question and in the corpus, the same partner against another); and How
this was found (steps, each condition on its own, other spellings, near misses,
what rests on attribution or the data-entry team's mark).

    uv run --with pytest pytest tests/test_find.py
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from workflows import corrections_db, review_server
from workflows.review_server import (
    FIND_PARTNER_CONDITIONS,
    build_find_sql,
    find_answer,
    find_normalize,
    find_registry,
    run_readonly_sql,
)

ROOT = Path(__file__).resolve().parents[1]
_TABLES_SQL = ROOT / "queries" / "schema" / "tables.sql"

# Places: 25 Livorno, 1 Firenze, 3 Pisa, 387 terra di Livorno.
# Livorno, with a Jewish partner: 101 stated, 102 attributed, 103 both,
#   104 attributed and undated, 107 attributed (and Firenze; Livorno marked inferred).
# Livorno, none: 105 (a partner who is neither), 108 (no partners),
#   109 (its stated partner's appearance removed), 111 (its attributed partner a
#   hidden person), 113 (its stated partner's Livorno link hidden).
# Never: 110 (hidden contract), 106 (Firenze), 112 (terra di Livorno).
# Women: 201 a woman general partner; 202 a woman external investor beside a man
#   general partner; 203 a woman external investor alone; 204 a woman whose
#   general-partner link is hidden, beside a man general partner.
# Pisa: 301 the shareholders' row as general partner beside a person investing;
#   302 a person general partner.
_SEED = """
INSERT INTO place (place_id, place_name) VALUES (25,'Livorno'),(1,'Firenze'),(3,'Pisa'),(387,'terra di Livorno');
INSERT INTO contract (contract_id, registration_date, firm_name) VALUES
  (101,'1700-01-01','Uno'),(102,'1710-01-01','Due'),(103,'1720-01-01','Tre'),(104,'0000-00-00','Quattro'),
  (105,'1730-01-01','Cinque'),(106,'1740-01-01','Sei'),(107,'1750-01-01','Sette'),(108,'1760-01-01','Otto'),
  (109,'1770-01-01','Nove'),(110,'1780-01-01','Dieci'),(111,'1790-01-01','Undici'),(112,'1795-01-01','Dodici'),
  (113,'1796-01-01','Tredici'),
  (201,'1600-01-01','Donne uno'),(202,'1601-01-01','Donne due'),(203,'1602-01-01','Donne tre'),
  (204,'1603-01-01','Donne quattro'),
  (301,'1650-01-01','Azionisti'),(302,'1651-01-01','Pisani');
UPDATE contract SET is_deleted = 1 WHERE contract_id = 110;
INSERT INTO contract_place (place_id, contract_id, place_db) VALUES
  (25,101,0),(25,102,0),(25,103,0),(25,104,0),(25,105,0),(1,106,0),(25,107,1),(1,107,0),(25,108,0),
  (25,109,0),(25,110,0),(25,111,0),(387,112,0),(25,113,0),(3,301,0),(3,302,0);
UPDATE contract_place SET is_deleted = 1 WHERE contract_id = 113;
INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES
  (1,'Abram','Stated',0),(2,'Isach','Attributed',0),(3,'Moisè','Both',0),(4,'Salamone','Both',0),
  (5,'Vita','Undated',0),(6,'Piero','Neither',0),(7,'Leone','Firenze',0),(8,'Jacob','Inferred',0),
  (9,'Samuel','Removed',0),(10,'Giovanni','Removed',0),(11,'Ruben','Hidden',0),(12,'Ester','Dieci',1),
  (13,'Aron','Terra',0),(14,'Daniel','Unlinked',0),
  (21,'Maria','Prima',1),(22,'Lisa','Seconda',1),(23,'Piero','Secondo',0),(24,'Anna','Terza',1),
  (25,'Bice','Quarta',1),(26,'Ugo','Quarto',0),
  (31,'','MOLTEPLICI AZIONARI',0),(32,'Niccolò','Pisano',0),(33,'Carlo','Pisano',0);
UPDATE person SET is_deleted = 1 WHERE person_id = 11;
INSERT INTO investor (investor_id, person_id, contract_id, is_jewish, jewish_db, via_proxy) VALUES
  (1001,1,101,1,0,1),(1002,2,102,0,1,0),(1003,3,103,1,0,0),(1004,4,103,0,1,0),(1005,5,104,0,1,0),
  (1006,6,105,0,0,0),(1007,7,106,1,0,0),(1008,8,107,0,1,0),(1009,9,109,1,0,0),(1010,10,109,0,0,0),
  (1011,11,111,0,1,0),(1012,12,110,1,0,0),(1013,13,112,0,1,0),(1014,14,113,1,0,0),
  (2001,21,201,0,0,0),(2002,22,202,0,0,0),(2003,23,202,0,0,0),(2004,24,203,0,0,0),
  (2005,25,204,0,0,0),(2006,26,204,0,0,0),
  (3001,31,301,0,0,0),(3002,32,301,0,0,0),(3003,33,302,0,0,0);
UPDATE investor SET is_deleted = 1 WHERE investor_id = 1009;
-- step 2. Live in Livorno: 1001 (101) and 1007 (106, Firenze: a near miss); also
--   1009 (removed) and 1011 (a hidden person). 1003 lives in Pisa, 2001 in Firenze,
--   1004's residence is 0 (not recorded). From Livorno: 1013 (112, terra di
--   Livorno: a near miss); 1004 from terra di Livorno.
-- Titles and professions: 2003 (202) cavaliere; 1006 (105) signor balì; 2002
--   (202) only her husband's title, barone; 3003 (302) only his father's and
--   grandfather's, cavaliere; 3002 (301) mercante; 1010 (109) Mercante di seta;
--   1011 (a hidden person, 111) cavaliere di Malta.
-- Contracts: activity 1 negozio di seta on 101 and 102 (102's marked as inferred),
--   2 arte della lana on 103 and hidden 110, 0 on 105, the lost entry 1725 on 104;
--   currency 1 scudi on 101 and 103, 2 ducati on 102 and 110; register 10831 on
--   101 and (with a trailing space) 102, 10832 on 103 and 110; an open formula
--   for the place on 101, for the activity on 103 and 110.
UPDATE investor SET place_of_residence = 25 WHERE investor_id IN (1001, 1007, 1009, 1011);
UPDATE investor SET place_of_residence = 3 WHERE investor_id = 1003;
UPDATE investor SET place_of_residence = 1 WHERE investor_id = 2001;
UPDATE investor SET place_of_residence = 0 WHERE investor_id = 1004;
UPDATE investor SET place_of_origin = 25 WHERE investor_id = 1013;
UPDATE investor SET place_of_origin = 387 WHERE investor_id = 1004;
INSERT INTO title (title_id, title_name) VALUES (1,'cavaliere'),(2,'signor balì'),(3,'barone');
UPDATE investor SET title = 1 WHERE investor_id = 2003;
UPDATE investor SET title = 2 WHERE investor_id = 1006;
UPDATE investor SET title_husband = 3 WHERE investor_id = 2002;
UPDATE investor SET title_father_mother = 1, title_grandfather = 1 WHERE investor_id = 3003;
UPDATE investor SET profession = 'mercante' WHERE investor_id = 3002;
UPDATE investor SET profession = 'Mercante di seta' WHERE investor_id = 1010;
UPDATE investor SET profession = 'cavaliere di Malta' WHERE investor_id = 1011;
INSERT INTO economic_activity (ec_activity_id, activity) VALUES (1,'negozio di seta'),(2,'arte della lana');
UPDATE contract SET economic_sector = 1 WHERE contract_id IN (101, 102);
UPDATE contract SET economic_sector = 2 WHERE contract_id IN (103, 110);
UPDATE contract SET economic_sector = 0 WHERE contract_id = 105;
UPDATE contract SET economic_sector = 1725 WHERE contract_id = 104;
UPDATE contract SET economic_sector_db = 1 WHERE contract_id = 102;
INSERT INTO currency (currency_id, currency) VALUES (1,'scudi'),(2,'ducati');
UPDATE contract SET currency_id = 1 WHERE contract_id IN (101, 103);
UPDATE contract SET currency_id = 2 WHERE contract_id IN (102, 110);
UPDATE contract SET folder = '10831' WHERE contract_id = 101;
UPDATE contract SET folder = '10831 ' WHERE contract_id = 102;
UPDATE contract SET folder = '10832' WHERE contract_id IN (103, 110);
UPDATE contract SET pl_discretion = 1 WHERE contract_id = 101;
UPDATE contract SET ec_discretion = 1 WHERE contract_id IN (103, 110);
INSERT INTO investment (investment_id, contract_id, type) VALUES
  (21,201,'gp'),(22,202,'lp'),(23,202,'gp'),(24,203,'lp'),(25,204,'gp'),(26,204,'gp'),
  (31,301,'gp'),(32,301,'lp'),(33,302,'gp');
INSERT INTO investor_group (investor_id, investment_id, is_deleted) VALUES
  (2001,21,0),(2002,22,0),(2003,23,0),(2004,24,0),(2005,25,1),(2006,26,0),(3001,31,0),(3002,32,0),(3003,33,0);
"""

PLACES = {25: "Livorno", 1: "Firenze", 3: "Pisa", 387: "terra di Livorno"}
SHAREHOLDERS = [31]


def _make_db(path: Path, seed: str = _SEED) -> Path:
    c = sqlite3.connect(path)
    c.executescript(_TABLES_SQL.read_text())
    for table in corrections_db.ALL_TABLE_PRIMARY_KEYS:
        c.execute(f"ALTER TABLE `{table}` ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    c.executescript(seed)
    c.commit()
    c.close()
    return path


@pytest.fixture(autouse=True)
def _private_stores(tmp_path, monkeypatch):
    """Saved questions and the change log live in this test's own folder, never in data/."""
    monkeypatch.setattr(review_server, "FIND_QUESTIONS_PATH", tmp_path / "find_questions.jsonl")
    monkeypatch.setattr(review_server, "_find_corrections_path", lambda: tmp_path / "corrections.db")


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """The fixture corpus, where the read-only executor and open_db read it."""
    path = _make_db(tmp_path / "main.db")
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    return path


def _lookups(spec):
    """Today's names and spellings for a question, read from the fixture."""
    connection = sqlite3.connect(review_server.db_path())
    connection.row_factory = sqlite3.Row
    try:
        return review_server.find_lookups(connection, [find_normalize(spec)])
    finally:
        connection.close()


def _find(spec, non_people=None, places=PLACES):
    """Run a question on the fixture: its sentence, answer, contract ids and List."""
    query = build_find_sql(spec, places, SHAREHOLDERS if non_people is None else non_people, _lookups(spec))
    listing = run_readonly_sql(query["list_sql"], tuple(query["list_params"]))
    counts_out = run_readonly_sql(query["counts_sql"], tuple(query["counts_params"]))
    counts = dict(zip(counts_out["columns"], counts_out["rows"][0]))
    undated = None
    if query["undated_sql"]:
        undated = run_readonly_sql(query["undated_sql"], tuple(query["undated_params"]))["rows"][0][0]
    return {
        "query": query,
        "sentence": query["sentence"],
        "answer": " ".join(find_answer(query, counts, undated)),
        "ids": [row[0] for row in listing["rows"]],
        "listing": listing,
        "counts": counts,
    }


LIVORNO = {"kind": "place", "values": [25]}


def _partner(quantifier, *conditions):
    return {"quantifier": quantifier, "conditions": [dict(zip(("kind", "choice"), c)) for c in conditions]}


# ---- her request: Livorno, a Jewish partner, split by provenance -------------

def test_her_request_counts_each_contract_once_and_splits_the_layers(corpus):
    out = _find({"contract": [LIVORNO], "partners": [_partner("any", ("jewish", "either"))]})
    assert out["sentence"] == (
        "Contracts operating in Livorno, with at least one partner who is Jewish "
        "(stated in the act or editorial attribution)"
    )
    # date order, the undated last; 103 (both layers) once
    assert out["ids"] == [101, 102, 103, 107, 104]
    assert out["answer"] == (
        "5 contracts. 2 with a partner stated Jewish in the act; 3 on editorial attribution alone. "
        "Counting contracts, each once. 1 has no registration date and is included."
    )


def test_the_list_names_each_matching_partner_in_its_own_layer(corpus):
    out = _find({"contract": [LIVORNO], "partners": [_partner("any", ("jewish", "either"))]})
    assert out["listing"]["columns"] == [
        "contract_id", "registration_date", "firm_name", "places", "stated_in_the_act", "editorial_attribution"]
    assert out["query"]["labels"][-2:] == ["Matching partners, stated in the act", "Matching partners, editorial attribution"]
    rows = {row[0]: row for row in out["listing"]["rows"]}
    assert rows[103][4:] == ["Moisè Both", "Salamone Both"]
    assert rows[102][4:] == [None, "Isach Attributed"]
    # every place of activity; the mark of the data-entry team (register 12c) said as a mark
    assert rows[107][3] == "Firenze; Livorno (marked as inferred)"


def test_each_layer_alone_says_how_many_also_have_the_other(corpus):
    stated = _find({"contract": [LIVORNO], "partners": [_partner("any", ("jewish", "stated"))]})
    assert stated["ids"] == [101, 103]
    assert "1 of them also has a partner Jewish by editorial attribution." in stated["answer"]
    assert stated["listing"]["columns"][-1] == "stated_in_the_act"
    attributed = _find({"contract": [LIVORNO], "partners": [_partner("any", ("jewish", "attributed"))]})
    assert attributed["ids"] == [102, 103, 107, 104]
    assert "1 of them also has a partner stated Jewish in the act." in attributed["answer"]


def test_the_negative_names_the_contracts_with_no_partners(corpus):
    out = _find({"contract": [LIVORNO], "partners": [_partner("no", ("jewish", "either"))]})
    # 109's stated partner was removed; 111's partner is a hidden person: neither counts
    assert out["ids"] == [105, 108, 109, 111]
    assert "2 have no partners recorded." in out["answer"]


# ---- same partner or another, never guessed ---------------------------------

def test_the_same_partner_and_another_partner_are_two_questions(corpus):
    same = _find({"partners": [_partner("any", ("woman", "yes"), ("role", "gp"))]})
    another = _find({"partners": [_partner("any", ("woman", "yes")), _partner("any", ("role", "gp"))]})
    assert same["ids"] == [201]  # 204's woman lost her general-partner link
    assert another["ids"] == [201, 202, 204]
    assert same["sentence"] == "Contracts with at least one partner who is a woman and is a general partner"
    assert another["sentence"] == (
        "Contracts with at least one partner who is a woman, "
        "and at least one partner, the same or another, who is a general partner"
    )
    assert another["listing"]["columns"][-2:] == ["matching_partners_1", "matching_partners_2"]


def test_a_later_line_after_no_partner_reads_without_the_same_or_another():
    query = build_find_sql(
        {"partners": [_partner("no", ("proxy", "yes")), _partner("any", ("woman", "yes")),
                      _partner("every", ("role", "lp"))]},
        PLACES,
    )
    assert query["sentence"] == (
        "Contracts with no partner who acts through a proxy, and at least one partner who is a woman, "
        "and in which every partner is an external investor (accomandante)"
    )


# ---- undated contracts and year ranges ---------------------------------------

def test_a_year_range_leaves_the_undated_out_and_says_how_many(corpus):
    spec = {"contract": [LIVORNO, {"kind": "years", "from": 1700, "to": 1799}],
            "partners": [_partner("any", ("jewish", "either"))]}
    out = _find(spec)
    assert out["ids"] == [101, 102, 103, 107]
    assert "registered from 1700 to 1799" in out["sentence"]
    assert "1 contract with no registration date meets the other conditions and is left out by the years." in out["answer"]
    assert "is included" not in out["answer"]
    upto = _find({"contract": [LIVORNO, {"kind": "years", "from": None, "to": 1715}]})
    assert upto["ids"] == [101, 102] and "registered up to 1715" in upto["sentence"]
    since = _find({"contract": [LIVORNO, {"kind": "years", "from": 1771}]})
    assert since["ids"] == [111] and "registered from 1771 on" in since["sentence"]


# ---- "no partner who" and "every partner" leave out non-people --------------

def test_the_shareholders_row_is_left_out_of_no_partner_who_and_said_so(corpus):
    spec = {"contract": [{"kind": "place", "values": [3]}], "partners": [_partner("no", ("role", "gp"))]}
    out = _find(spec)
    assert out["ids"] == [301]
    assert ("For “no partner”, institutions, estates, shareholder groups and placeholders are left out; "
            "1 of these contracts has one among its partners.") in out["answer"]
    assert _find(spec, non_people=[])["ids"] == []  # counted as a partner, it would be a general partner


def test_every_partner_leaves_out_non_people_and_needs_one_person(corpus):
    spec = {"contract": [{"kind": "place", "values": [3]}], "partners": [_partner("every", ("role", "lp"))]}
    assert _find(spec)["ids"] == [301]
    assert _find(spec, non_people=[])["ids"] == []
    # a contract with no partners is not one where "every partner" is anything
    assert 108 not in _find({"partners": [_partner("every", ("woman", "yes"))]})["ids"]


def test_the_endpoint_finds_the_shareholders_row_itself(corpus):
    query = review_server.find_prepare(
        {"contract": [{"kind": "place", "values": [3]}], "partners": [_partner("no", ("role", "gp"))]})
    assert 31 in query["counts_params"]


# ---- every reading pinned to its SQL -----------------------------------------

# For each partner condition, the fixture contracts whose live partners meet it.
_EXPECTED_ANY = {
    ("jewish", "either"): [101, 102, 103, 104, 106, 107, 112, 113],
    ("jewish", "stated"): [101, 103, 106, 113],
    ("jewish", "attributed"): [102, 103, 107, 112, 104],
    ("woman", "yes"): [201, 202, 203, 204],
    ("role", "gp"): [201, 202, 204, 301, 302],
    ("role", "lp"): [202, 203, 301],
    ("proxy", "yes"): [101],
    ("joint", "yes"): [],  # no shared stake here; the landing's fixture has them
}


def test_the_registry_lists_each_condition_the_engine_reads():
    registry = find_registry()
    listed = {(k["kind"], c["key"]) for k in registry["partner_conditions"] for c in k.get("choices", [])}
    assert listed == set(_EXPECTED_ANY)
    assert [k["kind"] for k in registry["partner_conditions"] if "values" in k] == ["residence", "origin", "word"]
    assert [k["kind"] for k in registry["contract_conditions"]] == [
        "place", "years", "activity", "currency", "register", "discretion", "act", "partnered"]
    assert [q["key"] for q in registry["quantifiers"]] == ["any", "no", "every"]


@pytest.mark.parametrize("kind,choice", sorted(_EXPECTED_ANY))
def test_each_partner_reading_selects_what_it_says(corpus, kind, choice):
    out = _find({"partners": [_partner("any", (kind, choice))]})
    assert sorted(out["ids"]) == sorted(_EXPECTED_ANY[(kind, choice)])
    reading = FIND_PARTNER_CONDITIONS[kind]["choices"][choice][0]
    assert out["sentence"] == f"Contracts with at least one partner who {reading}"


def test_several_places_mean_any_of_them_and_two_chips_mean_both(corpus):
    either = _find({"contract": [{"kind": "place", "values": [25, 387]}]})
    assert 112 in either["ids"] and 101 in either["ids"]
    assert either["sentence"] == "Contracts operating in Livorno or terra di Livorno"
    both = _find({"contract": [LIVORNO, {"kind": "place", "values": [1]}]})
    assert both["ids"] == [107]
    assert both["sentence"] == "Contracts operating in Livorno, operating in Firenze"


def test_no_conditions_is_the_whole_live_corpus(corpus):
    out = _find({})
    assert out["sentence"] == "Contracts"
    assert len(out["ids"]) == 18 and 110 not in out["ids"]
    assert out["answer"].startswith("18 contracts. Counting contracts, each once. 1 has no registration date")


# ---- refused in words ----------------------------------------------------------

@pytest.mark.parametrize("spec,words", [
    ({"contract": [{"kind": "registro", "values": ["1"]}]}, "Unknown condition"),
    ({"contract": [{"kind": "place", "values": ["Livorno"]}]}, "place number as a whole number"),
    ({"contract": [{"kind": "place", "values": []}]}, "Choose at least one place"),
    ({"contract": [{"kind": "years", "from": 1800, "to": 1700}]}, "The first year is after the last year"),
    ({"contract": [{"kind": "years", "from": "1700; DROP TABLE contract"}]}, "a year as a whole number"),
    ({"contract": [{"kind": "years"}]}, "a first year, a last year, or both"),
    ({"partners": [{"quantifier": "most", "conditions": [{"kind": "woman"}]}]}, "at least one, no, or every"),
    ({"partners": [{"quantifier": "any", "conditions": [{"kind": "is_woman = 1 OR 1"}]}]}, "Unknown partner condition"),
    ({"partners": [{"quantifier": "any", "conditions": [{"kind": "jewish"}]}]}, "Choose a value"),
    ({"partners": [{"quantifier": "any", "conditions": []}]}, "Say what the partner is"),
    ("contracts in Livorno", "set of conditions"),
])
def test_a_question_that_does_not_fit_is_refused_in_words(spec, words):
    with pytest.raises(HTTPException) as refused:
        build_find_sql(spec, PLACES)
    assert refused.value.status_code == 400 and words in refused.value.detail


def test_a_place_that_is_not_in_the_database_is_refused():
    with pytest.raises(HTTPException) as refused:
        build_find_sql({"contract": [{"kind": "place", "values": [999]}]}, PLACES)
    assert "no place number 999" in refused.value.detail


def test_values_reach_the_database_as_parameters_only():
    query = build_find_sql(
        {"contract": [{"kind": "place", "values": [25, 387]}, {"kind": "years", "from": "1700", "to": 1799}],
         "partners": [_partner("no", ("jewish", "either"))]},
        PLACES, SHAREHOLDERS,
    )
    for number in ("25", "387", "1700", "1799"):
        assert not re.search(rf"\b{number}\b", query["list_sql"])
    assert query["list_params"].count(25) == 1 and 1700 in query["list_params"]
    assert ";" not in query["list_sql"] + query["counts_sql"]  # the executor takes one statement


# ---- the endpoint, the download and the picker --------------------------------

def _download(response) -> bytes:
    async def read():
        return b"".join([c if isinstance(c, bytes) else c.encode() async for c in response.body_iterator])

    return asyncio.run(read())


def test_the_endpoint_returns_the_list_with_its_words(corpus):
    out = review_server.analysis_find(review_server.FindRequest(
        spec={"contract": [LIVORNO], "partners": [_partner("any", ("jewish", "either"))]}))
    assert out["total"] == 5 and out["row_count"] == 5
    assert out["answer"][:2] == ["5 contracts.", "2 with a partner stated Jewish in the act; 3 on editorial attribution alone."]
    assert len(out["labels"]) == len(out["columns"]) and out["names"] == {"place": {"25": "Livorno"}}


def test_the_download_opens_with_the_question_and_holds_every_row(corpus):
    spec = {"contract": [LIVORNO], "partners": [_partner("any", ("jewish", "either"))]}
    link = "https://review.example/analysis/find?place=25&partner=any.jewish-either"
    raw = _download(review_server.analysis_find_export(review_server.FindExport(spec=spec, link=link)))
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0] == ('"Contracts operating in Livorno, with at least one partner who is Jewish '
                        '(stated in the act or editorial attribution)"')
    assert lines[1].startswith('"5 contracts. 2 with a partner stated Jewish in the act; 3 on editorial')
    assert re.fullmatch(r"Downloaded on \d{1,2} \w+ \d{4} from " + re.escape(link), lines[2])
    assert lines[3] == ""
    assert lines[4].startswith("Contract,Registration date,Firm name,Places of activity,")
    assert lines[5] == "contract_id,registration_date,firm_name,places,stated_in_the_act,editorial_attribution"
    assert [line.split(",")[0] for line in lines[6:]] == ["101", "102", "103", "107", "104"]


def test_a_link_that_is_not_a_web_address_stays_out_of_the_file(corpus):
    raw = _download(review_server.analysis_find_export(review_server.FindExport(spec={}, link="=HYPERLINK(1)")))
    assert raw.decode("utf-8-sig").splitlines()[2].startswith("Downloaded on ")
    assert "HYPERLINK" not in raw.decode("utf-8-sig")


def test_the_place_picker_counts_live_contracts_by_place_of_activity(corpus):
    places = review_server.analysis_find_values("place", "LIV")["values"]
    # case-blind; Livorno's hidden contract 110 and hidden link on 113 do not count
    assert [(p["value"], p["name"], p["contracts"], p["inferred"]) for p in places] == [
        (25, "Livorno", 9, 1), (387, "terra di Livorno", 1, 0)]
    assert review_server.analysis_find_values("place", "")["values"][0]["name"] == "Livorno"


def test_the_normal_form_is_its_own_normal_form():
    spec = {"contract": [{"kind": "place", "values": ["387", 25, 25]}, {"kind": "years", "from": "1700", "to": ""}],
            "partners": [{"quantifier": "every", "conditions": [{"kind": "woman"}, {"kind": "jewish", "choice": "stated"}]}]}
    once = find_normalize(spec)
    assert once["contract"][0]["values"] == [25, 387] and once["contract"][1] == {"kind": "years", "from": 1700, "to": None}
    assert find_normalize(once) == once


# ---- step 2: where a partner lives and comes from --------------------------------

def _line(quantifier, *conditions):
    return {"quantifier": quantifier, "conditions": list(conditions)}


def _values(kind, *values, negated=False):
    return {"kind": kind, "not": negated, "values": list(values)}


@pytest.mark.parametrize("line,ids,sentence", [
    # 1009 (removed) and 1011 (a hidden person) also live in Livorno and count nowhere
    (_line("any", _values("residence", 25)), [101, 106], "with at least one partner who lives in Livorno"),
    (_line("any", _values("residence", 3)), [103], "with at least one partner who lives in Pisa"),
    (_line("any", _values("origin", 25)), [112], "with at least one partner who comes from Livorno"),
    (_line("any", _values("origin", 387)), [103], "with at least one partner who comes from terra di Livorno"),
    (_line("every", _values("residence", 25)), [101, 106], "in which every partner lives in Livorno"),
    (_line("any", _values("residence", 25, negated=True)),
     [102, 103, 104, 105, 107, 109, 112, 113, 201, 202, 203, 204, 301, 302],
     "with at least one partner who does not live in Livorno"),
    (_line("any", _values("residence", None)), [102, 103, 104, 105, 107, 109, 112, 113, 202, 203, 204, 301, 302],
     "with at least one partner who lives in an unrecorded place"),
    (_line("any", {"kind": "jewish", "choice": "either"}, _values("residence", 3, None)), [102, 103, 104, 107, 112, 113],
     "with at least one partner who is Jewish (stated in the act or editorial attribution) "
     "and lives in Pisa or an unrecorded place"),
])
def test_lives_in_and_comes_from_are_the_partners_own_places(corpus, line, ids, sentence):
    out = _find({"partners": [line]})
    assert sorted(out["ids"]) == ids
    assert out["sentence"] == f"Contracts {sentence}"


def test_no_partner_who_lives_there_is_the_complement(corpus):
    some = _find({"partners": [_line("any", _values("residence", 25))]})
    none = _find({"partners": [_line("no", _values("residence", 25))]})
    assert len(some["ids"]) + len(none["ids"]) == 18 and 108 in none["ids"]


# ---- step 2: the partner's own title or profession, never a relative's --------

@pytest.mark.parametrize("words,ids", [
    (["cavalier"], [202]),  # 302 has it only as his father's and grandfather's title; 111's is a hidden person's
    (["barone"], []),  # 202's barone is her husband's title
    (["bali"], [105]),  # accents ignored: signor balì
    (["MERCANT"], [109, 301]),  # case ignored, the profession as written
    (["cavalier", "mercant"], [109, 202, 301]),  # several words mean any of them
])
def test_a_word_searches_the_partners_own_title_and_profession(corpus, words, ids):
    out = _find({"partners": [_line("any", _values("word", *words))]})
    assert sorted(out["ids"]) == ids
    shown = " or ".join(f"“{w}”" for w in sorted(words, key=str.lower))
    assert out["sentence"] == f"Contracts with at least one partner who has a title or profession containing {shown}"


def test_a_word_lists_the_stored_spellings_it_matched(corpus):
    found = _find({"partners": [_line("any", _values("word", "cavalier", "mercant"))]})["query"]["searched"]
    assert found == [
        {"word": "cavalier", "titles": ["cavaliere"], "professions": []},  # not the hidden person's "cavaliere di Malta"
        {"word": "mercant", "titles": [], "professions": ["Mercante di seta", "mercante"]},
    ]
    assert _find({"partners": [_line("any", _values("word", "bali"))]})["query"]["searched"][0]["titles"] == ["signor balì"]


def test_no_title_or_profession_reads_as_its_negation(corpus):
    out = _find({"partners": [_line("every", _values("word", "cavalier", negated=True))]})
    assert 202 not in out["ids"] and 302 in out["ids"]
    assert out["sentence"] == "Contracts in which every partner has no title or profession containing “cavalier”"


# ---- step 2: the contract's activity, currency, register and open formulas ------

@pytest.mark.parametrize("condition,ids,reading", [
    (_values("activity", 1), [101, 102], "whose activity is “negozio di seta”"),
    (_values("activity", 2), [103], "whose activity is “arte della lana”"),  # 110 is hidden
    (_values("activity", 1725), [104], "whose activity is entry 1725, missing from the activity list"),
    (_values("activity", 1, 2, negated=True), [104, 105, 106, 107, 108, 109, 111, 112, 113, 201, 202, 203, 204, 301, 302],
     "whose activity is not “negozio di seta” or “arte della lana”"),
    (_values("currency", 1), [101, 103], "whose currency is “scudi”"),
    (_values("currency", 2, None), [102, 104, 105, 106, 107, 108, 109, 111, 112, 113, 201, 202, 203, 204, 301, 302],
     "whose currency is “ducati” or not recorded"),
    (_values("register", "10831"), [101, 102], "whose register is 10831"),  # 102's has a trailing space
    (_values("register", "10832", negated=True), [101, 102, 104, 105, 106, 107, 108, 109, 111, 112, 113, 201, 202, 203,
                                                  204, 301, 302], "whose register is not 10832"),
    ({"kind": "discretion", "choice": "place"}, [101], "with an open formula for the place (such as “e in ogni altro luogo”)"),
    ({"kind": "discretion", "choice": "activity"}, [103], "with an open formula for the activity (such as “et altre cose”)"),
    ({"kind": "place", "values": [None]}, [113, 201, 202, 203, 204], "operating in an unrecorded place"),  # 113's link is hidden
    (_values("place", 25, negated=True), [106, 112, 113, 201, 202, 203, 204, 301, 302], "not operating in Livorno"),
])
def test_each_contract_condition_selects_what_it_says(corpus, condition, ids, reading):
    out = _find({"contract": [condition]})
    assert sorted(out["ids"]) == ids
    assert out["sentence"] == f"Contracts {reading}"


@pytest.mark.parametrize("condition", [
    _values("place", 25), _values("place", None), _values("activity", 1, None), _values("currency", 2),
    _values("register", "10831", None), {"kind": "discretion", "choice": "activity"},
])
def test_is_and_is_not_split_the_corpus_in_two(corpus, condition):
    flipped = {**condition, "not": True}
    yes, no = _find({"contract": [condition]})["ids"], _find({"contract": [flipped]})["ids"]
    assert not set(yes) & set(no) and len(yes) + len(no) == 18


@pytest.mark.parametrize("kind,choice,negated_ids", [
    ("woman", "yes", [101, 102, 103, 104, 105, 106, 107, 109, 112, 113, 202, 204, 301, 302]),
    ("jewish", "stated", [102, 103, 104, 105, 107, 109, 112, 201, 202, 203, 204, 301, 302]),
])
def test_is_not_flips_a_partner_condition(corpus, kind, choice, negated_ids):
    out = _find({"partners": [_line("any", {"kind": kind, "choice": choice, "not": True})]})
    assert sorted(out["ids"]) == negated_ids
    reading = FIND_PARTNER_CONDITIONS[kind]["choices"][choice].reading_not
    assert out["sentence"] == f"Contracts with at least one partner who {reading}"


def test_the_endpoint_names_every_value_it_uses(corpus):
    spec = {"contract": [_values("activity", 1725, 1), _values("register", "10831")],
            "partners": [_line("any", _values("residence", 25, None))]}
    out = review_server.analysis_find(review_server.FindRequest(spec=spec))
    assert out["names"] == {"activity": {"1": "negozio di seta", "1725": "entry 1725, missing from the activity list"},
                            "register": {"10831": "10831"}, "place": {"25": "Livorno"}}
    assert out["total"] == 2  # 101 (lives in Livorno) and 102 (residence not recorded)


@pytest.mark.parametrize("spec,words", [
    ({"contract": [{"kind": "activity", "values": [9]}]}, "There is no activity number 9 in the database"),
    ({"contract": [{"kind": "register", "values": ["99999"]}]}, "There is no register 99999 in the database"),
    ({"partners": [_line("any", _values("residence", 999))]}, "There is no place number 999 in the database"),
])
def test_a_value_that_is_not_in_the_database_is_refused(corpus, spec, words):
    with pytest.raises(HTTPException) as refused:
        review_server.find_prepare(spec)
    assert words in refused.value.detail


@pytest.mark.parametrize("spec,words", [
    ({"contract": [{"kind": "place", "values": [25], "not": "yes"}]}, "either “is” or “is not”"),
    ({"contract": [{"kind": "activity", "values": ["seta"]}]}, "activity number as a whole number"),
    ({"contract": [{"kind": "register", "values": ["10831; DROP TABLE contract"]}]}, "Enter a register as it is written"),
    ({"contract": [{"kind": "discretion", "choice": "luogo"}]}, "Choose a value for"),
    ({"partners": [_line("any", _values("word", "a;b"))]}, "Enter one word"),
    ({"partners": [_line("any", _values("word", None))]}, "Choose a word"),
    ({"partners": [_line("any", _values("residence"))]}, "Choose at least one place"),
    ({"partners": [_line("any", *[{"kind": "woman"}] * 9)]}, "up to 8 conditions"),
])
def test_a_step_two_condition_that_does_not_fit_is_refused_in_words(spec, words):
    with pytest.raises(HTTPException) as refused:
        find_normalize(spec)
    assert refused.value.status_code == 400 and words in refused.value.detail


def test_words_and_registers_reach_the_database_as_parameters_only(corpus):
    spec = {"contract": [_values("register", "10831")], "partners": [_line("any", _values("word", "mercant"))]}
    query = _find(spec)["query"]
    for sql in (query["list_sql"], query["counts_sql"], query["total_sql"]):
        assert "10831" not in sql and "ercant" not in sql
    assert "mercante" in query["list_params"] and "10831" in query["list_params"]


# ---- step 2: the pickers, with counts in this question and in the corpus -------

def _connection():
    connection = sqlite3.connect(review_server.db_path())
    connection.row_factory = sqlite3.Row
    return connection


def _count(spec):
    out = review_server.analysis_find_counts(review_server.FindCounts(specs=[spec]))["counts"][0]
    return out and out["total"]


def test_each_picker_counts_in_the_corpus_as_the_question_itself_would(corpus):
    connection = _connection()
    try:
        for kind in ("place", "residence", "origin", "activity", "currency", "register"):
            values = review_server.find_values(connection, kind, "", limit=50)
            assert values, kind
            partner = kind in ("residence", "origin")
            for value in values:
                condition = _values(kind, value["value"])
                spec = {"partners": [_line("any", condition)]} if partner else {"contract": [condition]}
                assert value["contracts"] == _count(spec), (kind, value)
    finally:
        connection.close()


def test_not_recorded_is_a_value_with_its_count(corpus):
    connection = _connection()
    try:
        last = {kind: review_server.find_values(connection, kind, "")[-1]
                for kind in ("place", "residence", "activity", "currency", "register")}
        # typing narrows the list and leaves it out
        assert all(v["value"] is not None for v in review_server.find_values(connection, "currency", "scu"))
    finally:
        connection.close()
    assert {k: (v["name"], v["contracts"]) for k, v in last.items()} == {
        "place": ("not recorded", 5), "residence": ("not recorded", 13), "activity": ("not recorded", 14),
        "currency": ("not recorded", 15), "register": ("not recorded", 15)}


def test_the_pickers_offer_the_stored_spellings_and_marks(corpus):
    connection = _connection()

    def offered(kind, q):
        return [(v["name"], v["contracts"], v["inferred"]) for v in review_server.find_values(connection, kind, q)]

    try:
        assert offered("residence", "LIV") == [("Livorno", 2, 0)]
        assert offered("origin", "liv") == [("Livorno", 1, 0), ("terra di Livorno", 1, 0)]
        assert offered("activity", "seta") == [("negozio di seta", 2, 1)]  # 102's activity carries the mark
        assert offered("activity", "1725") == [("entry 1725, missing from the activity list", 1, 0)]
        assert [name for name, *_n in offered("register", "")] == ["10831", "10832", "not recorded"]
    finally:
        connection.close()


def test_a_word_picker_counts_the_word_and_lists_its_spellings(corpus):
    out = review_server.analysis_find_values("word", "Cavalier")["values"]
    assert out == [{"value": "Cavalier", "name": "Cavalier", "contracts": 1, "inferred": 0,
                    "titles": ["cavaliere"], "professions": []}]
    assert review_server.analysis_find_values("word", "two words")["values"] == []


def test_the_jewish_choices_count_within_livorno_and_in_the_corpus(corpus):
    # On the live data: 17 / 100 / 107 within Livorno, 183 for "either" in the corpus (12.2, 12.6).
    def jewish(choice, place=True):
        return {"contract": [LIVORNO] if place else [], "partners": [_line("any", {"kind": "jewish", "choice": choice})]}

    specs = [jewish(c) for c in ("stated", "attributed", "either")] + [jewish(c, False) for c in ("stated", "attributed", "either")]
    out = review_server.analysis_find_counts(review_server.FindCounts(specs=specs))["counts"]
    assert [c["total"] for c in out] == [2, 4, 5, 4, 5, 8]
    assert out[2]["parts"] == ["operating in Livorno",
                               "with at least one partner who is Jewish (stated in the act or editorial attribution)"]


def test_the_same_partner_and_another_are_both_counted_before_she_chooses(corpus):
    # On the live data: 12 against 227 (12.2).
    woman = _line("any", {"kind": "woman"})
    same = {"partners": [{**woman, "conditions": woman["conditions"] + [{"kind": "role", "choice": "gp"}]}]}
    another = {"partners": [woman, _line("any", {"kind": "role", "choice": "gp"})]}
    out = review_server.analysis_find_counts(review_server.FindCounts(specs=[same, another, {"contract": "x"}]))["counts"]
    assert [c and c["total"] for c in out] == [1, 3, None]
    assert out[1]["parts"][-1] == "and at least one partner, the same or another, who is a general partner"


def test_the_counts_endpoint_takes_a_bounded_number_of_questions(corpus):
    with pytest.raises(HTTPException) as refused:
        review_server.analysis_find_counts(review_server.FindCounts(specs=[{}] * (review_server.FIND_MAX_COUNTS + 1)))
    assert "at once" in refused.value.detail


# ---- step 2: How this was found -------------------------------------------------

HER_REQUEST = {"contract": [LIVORNO], "partners": [_partner("any", ("jewish", "either"))]}


def test_how_this_was_found_gives_the_steps_and_each_condition_alone(corpus):
    # On the live data: 4,866 > 578 > 107; on its own, the partner condition finds 183 (12.4).
    how = review_server.find_how(HER_REQUEST)
    assert how["steps"] == [
        {"reading": "All contracts", "count": 18},
        {"reading": "operating in Livorno", "count": 9, "alone": 9},
        {"reading": "with at least one partner who is Jewish (stated in the act or editorial attribution)",
         "count": 5, "alone": 8},
    ]
    assert how["counted"] == ["Contracts, each counted once however many of its partners match.",
                              "A partner is one person's appearance on one contract, as entered."]
    assert how["sql"].startswith("SELECT c.contract_id") and 25 in how["params"]


def test_near_misses_are_offered_as_questions_of_their_own(corpus):
    # On the live data: 16 contracts with a Jewish partner living in Livorno while the firm operated elsewhere.
    near = review_server.find_how(HER_REQUEST)["near_misses"]
    assert [(n["sentence"], n["count"]) for n in near] == [
        ("Contracts not operating in Livorno, with at least one partner who is Jewish (stated in the act or "
         "editorial attribution) and lives in Livorno", 1),  # 106, in Firenze
        ("Contracts not operating in Livorno, with at least one partner who is Jewish (stated in the act or "
         "editorial attribution) and comes from Livorno", 1),  # 112, in terra di Livorno
    ]
    # each is a question the page can open as it is
    assert _find(near[0]["spec"])["ids"] == [106]
    # with no partner line, the near miss adds one
    alone = review_server.find_how({"contract": [LIVORNO]})["near_misses"]
    assert alone[0]["sentence"] == "Contracts not operating in Livorno, with at least one partner who lives in Livorno"


def test_other_spellings_are_left_out_and_say_what_they_would_add(corpus):
    left = review_server.find_how(HER_REQUEST)["left_out"]
    assert left == [{"chosen": "Livorno", "name": "terra di Livorno", "contracts": 1, "adds": 1}]  # 112
    origin = review_server.find_how({"partners": [_line("any", _values("origin", 25))]})["left_out"]
    assert origin == [{"chosen": "Livorno", "name": "terra di Livorno", "contracts": 1, "adds": 1}]  # 103


def test_what_rests_on_attribution_and_on_the_marks_is_said(corpus):
    # On the live data: 90 of 107 on attribution alone, 60 of them in 1750-1799; 12 marked as inferred.
    assert review_server.find_how(HER_REQUEST)["inference"] == [
        "3 of the 5 rest on editorial attribution alone; 1 of them was registered in 1700–1749.",
        "1 of the 5 names Livorno with the data-entry team's mark “inferred”.",
    ]
    activity = review_server.find_how({"contract": [_values("activity", 1)]})["inference"]
    assert activity == ["1 of the 2 has its activity marked as inferred by the data-entry team."]
    stated = review_server.find_how({"partners": [_partner("any", ("jewish", "stated"))]})["inference"]
    assert stated == []


def test_how_this_was_found_lists_the_word_and_its_rule(corpus):
    how = review_server.find_how({"partners": [_line("any", _values("word", "cavalier"))]})
    assert how["searched"] == [{"word": "cavalier", "titles": ["cavaliere"], "professions": []}]
    assert how["counted"][-1] == FIND_PARTNER_CONDITIONS["word"]["note"]


# ---- step 3: Count -----------------------------------------------------------------

def _by(spec, by, by2=None):
    return review_server.analysis_find_count(review_server.FindCountRequest(spec=spec, by=by, by2=by2))


def _rows(count):
    return [(row["name"], row["ids"]) for row in count["rows"]]


def test_the_groupings_follow_the_question(corpus):
    def keys(spec):
        return [g["key"] for g in review_server.analysis_find(review_server.FindRequest(spec=spec))["groupings"]]

    plain = ["decade", "half", "place", "activity", "currency", "register"]
    assert keys({"contract": [LIVORNO]}) == plain
    assert keys(HER_REQUEST) == plain + ["provenance", "role"]
    assert keys({"partners": [_partner("any", ("jewish", "stated"))]}) == plain + ["role"]  # nothing to split
    assert keys({"partners": [_partner("no", ("woman", "yes"))]}) == plain  # no partner matches


def test_count_by_period_keeps_the_undated_as_a_row(corpus):
    assert _rows(_by(HER_REQUEST, "half")) == [
        ("1700–1749", [101, 102, 103]), ("1750–1799", [107]), ("no registration date", [104])]
    assert _rows(_by(HER_REQUEST, "decade"))[:2] == [("1700–1709", [101]), ("1710–1719", [102])]


def test_count_by_place_counts_a_contract_under_each_of_its_places_and_says_so(corpus):
    out = _by({}, "place")
    assert out["total"] == 18
    assert _rows(out) == [
        ("Livorno", [101, 102, 103, 104, 105, 107, 108, 109, 111]), ("Firenze", [106, 107]), ("Pisa", [301, 302]),
        ("terra di Livorno", [112]), ("not recorded", [113, 201, 202, 203, 204]),  # 113's only link is hidden
    ]
    assert out["notes"] == ["1 of the 18 name more than one place and is counted under each."]


def test_count_shows_the_largest_and_gathers_all_others(corpus, monkeypatch):
    monkeypatch.setattr(review_server, "FIND_TOP_ROWS", 2)
    out = _by({}, "place")
    assert [name for name, _ids in _rows(out)] == ["Livorno", "Firenze", "all others (2 values)", "not recorded"]
    assert out["rows"][2]["ids"] == [112, 301, 302]
    assert "The 2 largest of 4 values are shown; “all others” counts the 3 contracts of the other 2, each once." in out["notes"]


def test_count_by_two_groupings_gives_each_cell_its_contracts(corpus):
    out = _by(HER_REQUEST, "provenance", "half")
    assert [c["name"] for c in out["columns"]] == ["1700–1749", "1750–1799", "no registration date"]
    assert [(row["name"], [cell["ids"] for cell in row["cells"]]) for row in out["rows"]] == [
        ("editorial attribution alone", [[102], [107], [104]]),
        ("a matching partner stated Jewish in the act", [[101, 103], [], []]),
    ]
    assert out["by"]["label"] == "how the matching partner is known to be Jewish"


def test_count_by_the_matching_partners_role(corpus):
    # 204's woman lost her general-partner link: her stake is not recorded
    out = _by({"partners": [_partner("any", ("woman", "yes"))]}, "role")
    assert _rows(out) == [("external investor (accomandante)", [202, 203]), ("general partner", [201]),
                          ("no stake recorded", [204])]


def test_count_by_activity_names_the_lost_entry_and_not_recorded(corpus):
    rows = _rows(_by({}, "activity"))
    assert rows[:3] == [("negozio di seta", [101, 102]), ("arte della lana", [103]),
                        ("entry 1725, missing from the activity list", [104])]
    assert rows[-1][0] == "not recorded" and len(rows[-1][1]) == 14


@pytest.mark.parametrize("spec,by,by2,words", [
    ({}, "role", None, "cannot be grouped by 'role'"),
    ({}, "provenance", None, "cannot be grouped by 'provenance'"),
    ({}, "place", "place", "Choose two different groupings"),
    ({}, "sector", None, "cannot be grouped by 'sector'"),
])
def test_a_count_that_does_not_fit_is_refused_in_words(corpus, spec, by, by2, words):
    with pytest.raises(HTTPException) as refused:
        _by(spec, by, by2)
    assert words in refused.value.detail


# ---- step 3: Over time and Compare ---------------------------------------------------

def _time(spec, period="decade", split=None, beside=()):
    return review_server.analysis_find_time(review_server.FindTimeRequest(
        spec=spec, period=period, split=split, beside=list(beside)))


def test_over_time_draws_every_period_and_names_the_partial_last_one(corpus):
    out = _time(HER_REQUEST)
    assert out["periods"] == list(range(1600, 1800, 10))  # the fixture's first and last years: 1600, 1796
    assert out["partial"] == {"last": "1790–1796"}
    (series,) = out["series"]
    assert series["counts"][10:16] == [1, 1, 1, 0, 0, 1] and sum(series["counts"]) == 4 and series["undated"] == 1
    # stated and attributed as split bars
    assert [p["label"] for p in series["parts"]] == ["a matching partner stated Jewish in the act", "editorial attribution alone"]
    assert series["parts"][0]["counts"][10:13] == [1, 0, 1] and series["parts"][1]["counts"][10:16] == [0, 1, 0, 0, 0, 1]


def test_each_series_carries_its_bases_period_by_period(corpus):
    bases = _time(HER_REQUEST)["series"][0]["bases"]
    assert [b["label"] for b in bases] == ["All contracts", "operating in Livorno"]
    every, livorno = bases[0]["counts"], bases[1]["counts"]
    assert every[0] == 4 and every[5] == 2 and every[18] == 0 and every[19] == 3  # 1780s: 110 is hidden
    assert livorno[10:20] == [1, 1, 1, 1, 0, 1, 1, 1, 0, 1]
    half = _time(HER_REQUEST, "half")
    assert half["periods"] == [1600, 1650, 1700, 1750] and half["partial"] == {"last": "1750–1796"}


def test_compare_sets_the_same_question_beside_it_and_states_the_overlap(corpus):
    out = _time({"contract": [LIVORNO]}, beside=[{"contract": [{"kind": "place", "values": [1]}]}])
    assert [s["label"] for s in out["series"]] == ["operating in Livorno", "operating in Firenze"]
    assert [s["total"] for s in out["series"]] == [9, 2]
    assert out["notes"] == ["1 contract is in more than one of these and drawn in each."]  # 107
    assert out["names"] == {"place": {"25": "Livorno", "1": "Firenze"}}
    assert [b["label"] for b in out["series"][1]["bases"]] == ["All contracts"]  # each its own question's earlier steps


def test_compare_splits_the_set_and_gathers_all_others(corpus, monkeypatch):
    monkeypatch.setattr(review_server, "FIND_SERIES", 2)
    out = _time({}, split="place")
    assert [(s["label"], s["total"]) for s in out["series"]] == [
        ("Livorno", 9), ("not recorded", 5), ("all others (3 values)", 5)]
    assert out["notes"] == ["1 contract is in more than one of these and drawn in each.",
                            "The 2 largest values are drawn; “all others” gathers the other 3."]
    assert sum(out["series"][0]["counts"]) + out["series"][0]["undated"] == 9


@pytest.mark.parametrize("kwargs,words", [
    ({"split": "decade"}, "a grouping other than time"),
    ({"split": "place", "beside": [{}]}, "a grouping other than time"),
    ({"period": "century"}, "decades or half-centuries"),
    ({"beside": [{}] * 4}, "Up to 3 questions"),
])
def test_a_time_question_that_does_not_fit_is_refused_in_words(corpus, kwargs, words):
    with pytest.raises(HTTPException) as refused:
        _time({"contract": [LIVORNO]}, **kwargs)
    assert words in refused.value.detail


# ---- step 4: two sets combined -------------------------------------------------------

FIRENZE = {"kind": "place", "values": [1]}


def test_two_sets_combine_as_both_either_or_but_not(corpus):
    both = _find({"combine": "and", "sets": [HER_REQUEST, {"contract": [FIRENZE]}]})
    assert both["ids"] == [107]
    assert both["sentence"] == (
        "Contracts that are both “operating in Livorno, with at least one partner who is Jewish (stated in the act "
        "or editorial attribution)” and “operating in Firenze”")
    either = _find({"combine": "or", "sets": [HER_REQUEST, {"contract": [FIRENZE]}]})
    assert either["ids"] == [101, 102, 103, 106, 107, 104]
    # in either, the List says which set each contract is in
    rows = {row[0]: row[4:6] for row in either["listing"]["rows"]}
    assert rows[106] == ["", "yes"] and rows[107] == ["yes", "yes"] and rows[101] == ["yes", ""]
    assert either["query"]["labels"][4:6] == ["In set 1", "In set 2"]
    but_not = _find({"combine": "not", "sets": [HER_REQUEST, {"contract": [FIRENZE]}]})
    assert but_not["ids"] == [101, 102, 103, 104]
    assert but_not["sentence"].endswith("” but not “operating in Firenze”")


def test_conditions_on_top_of_a_combination_narrow_it(corpus):
    out = _find({"combine": "or", "sets": [HER_REQUEST, {"contract": [FIRENZE]}],
                 "contract": [{"kind": "years", "from": 1710}]})
    assert out["ids"] == [102, 103, 106, 107]
    assert out["sentence"].endswith("or “operating in Firenze”, registered from 1710 on")


def test_combined_sets_nest_up_to_three_deep(corpus):
    either = {"combine": "or", "sets": [HER_REQUEST, {"contract": [FIRENZE]}]}
    nested = _find({"combine": "not", "sets": [either, {"contract": [{"kind": "years", "to": 1715}]}]})
    assert nested["ids"] == [103, 106, 107, 104]
    deep = {"contract": [LIVORNO]}
    for _ in range(4):
        deep = {"combine": "and", "sets": [deep, {}]}
    with pytest.raises(HTTPException) as refused:
        find_normalize(deep)
    assert "up to 3 deep" in refused.value.detail


@pytest.mark.parametrize("spec,words", [
    ({"combine": "xor", "sets": [{}, {}]}, "“and”, “or” or “but not”"),
    ({"combine": "and", "sets": [{}]}, "exactly two sets"),
    ({"sets": [{}, {}]}, "“and”, “or” or “but not”"),
    ({"combine": "and", "sets": [{}, {"contract": [{"kind": "sector"}]}]}, "Unknown condition"),
])
def test_a_combination_that_does_not_fit_is_refused_in_words(spec, words):
    with pytest.raises(HTTPException) as refused:
        find_normalize(spec)
    assert words in refused.value.detail


def test_how_this_was_found_names_the_two_sets(corpus):
    how = review_server.find_how({"combine": "and", "sets": [HER_REQUEST, {"contract": [FIRENZE]}]})
    assert [(s["sentence"].split(",")[0], s["count"]) for s in how["sets"]] == [
        ("Contracts operating in Livorno", 5), ("Contracts operating in Firenze", 2)]
    assert [s["count"] for s in how["steps"]] == [18, 1]


def test_a_combined_question_names_the_values_of_both_sets(corpus):
    out = review_server.analysis_find(review_server.FindRequest(
        spec={"combine": "or", "sets": [{"contract": [LIVORNO]}, {"contract": [FIRENZE]}]}))
    assert out["names"] == {"place": {"25": "Livorno", "1": "Firenze"}} and out["total"] == 10
    assert out["groupings"][-1]["key"] == "register"  # a combination has no partner line of its own


# ---- step 4: See their partners --------------------------------------------------------

WOMEN = {"partners": [_partner("any", ("woman", "yes"))]}


def _partners(spec, which="every"):
    return review_server.analysis_find_partners(review_server.FindPartnersRequest(spec=spec, which=which))


def test_their_partners_are_every_partner_or_only_those_who_matched(corpus):
    out = _partners(WOMEN, "matched")
    assert out["counts"] == {
        "every": {"reading": "every partner on these contracts", "appearances": 6, "people": 6},
        "matched": {"reading": "only the partners who matched", "appearances": 4, "people": 4},
    }
    assert out["line"] == ("Partners: only the partners who matched. 4 appearances of 4 person entries, "
                           "as entered, not yet reviewed as people.")
    rows = [(r[0], r[1], r[4], r[7], r[13]) for r in out["rows"]]
    # Bice's general-partner link is hidden, so no stake is shown for her
    assert rows == [("Maria Prima", 201, "general partner", "yes", 21),
                    ("Lisa Seconda", 202, "external investor (accomandante)", "yes", 22),
                    ("Anna Terza", 203, "external investor (accomandante)", "yes", 24),
                    ("Bice Quarta", 204, None, "yes", 25)]
    assert out["labels"][0] == "Partner" and len(out["labels"]) == len(out["columns"])


def test_every_partner_leaves_out_removed_appearances_and_hidden_people(corpus):
    out = _partners({"contract": [LIVORNO]})
    assert out["counts"] == {"every": {"reading": "every partner on these contracts", "appearances": 8, "people": 8}}
    names = [r[0] for r in out["rows"]]
    assert "Samuel Removed" not in names and "Ruben Hidden" not in names and "Giovanni Removed" in names
    abram = next(r for r in out["rows"] if r[0] == "Abram Stated")
    assert abram[5:10] == ["yes", "", "", "yes", "Livorno"]  # stated, not attributed, not a woman, proxy, lives in


def test_only_those_who_matched_needs_a_partner_line(corpus):
    with pytest.raises(HTTPException) as refused:
        _partners({"contract": [LIVORNO]}, "matched")
    assert "No partner line" in refused.value.detail
    with pytest.raises(HTTPException):
        _partners(WOMEN, "some")


def test_the_partners_download_opens_with_the_question_and_which_partners(corpus):
    raw = _download(review_server.analysis_find_partners_export(review_server.FindPartnersRequest(
        spec=WOMEN, which="matched", link="https://review.example/analysis/find?partner=any.woman")))
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0] == "Contracts with at least one partner who is a woman"
    assert lines[1].startswith('"Partners: only the partners who matched. 4 appearances of 4 person entries, as entered')
    assert lines[4].startswith("Partner,Contract,Registration date,") and len(lines) == 6 + 4


# ---- step 4: saved questions, and what changed since ------------------------------------

from starlette.requests import Request  # noqa: E402


def _as(email=""):
    headers = [(b"cf-access-authenticated-user-email", email.encode())] if email else []
    return Request({"type": "http", "headers": headers})


def _save(name, spec=HER_REQUEST, search="place=25&partner=any.jewish-either&view=count", email="FT@example.org"):
    return review_server.analysis_find_save(review_server.FindSave(name=name, search=search, spec=spec), _as(email))


def _mine(email="ft@example.org"):
    return review_server.analysis_find_questions(_as(email))["questions"]


def test_a_saved_question_is_asked_again_today(corpus):
    saved = _save("  Livorno   firms with a Jewish partner ")
    assert saved["name"] == "Livorno firms with a Jewish partner" and saved["count"] == 5
    (question,) = _mine()
    assert question["search"] == "place=25&partner=any.jewish-either&view=count"
    assert question["changed"] is False and question["today"] == 5
    assert re.fullmatch(r"Saved \d{1,2} \w+ \d{4}: 5 contracts\.", question["lines"][0])
    assert question["lines"][1:] == ["Today: 5, no change.",
                                     "No edits have been logged since to the records and fields it selects by."]
    assert _mine("someone.else@example.org") == [] and _mine("") == []  # each person their own


def test_a_saved_question_names_what_changed(corpus):
    _save("Livorno firms with a Jewish partner")
    c = sqlite3.connect(corpus)
    c.executescript("""
        UPDATE contract SET is_deleted = 1 WHERE contract_id = 101;
        UPDATE investor SET is_jewish = 1 WHERE investor_id = 1006;
        UPDATE place SET place_name = 'Livorno (città)' WHERE place_id = 25;
    """)
    c.commit()
    c.close()
    (question,) = _mine()
    assert question["changed"] is True and question["today"] == 5
    assert question["lines"][1:3] == [
        "Today: 5 (105 entered; 101 left).",
        "Its words now read: Contracts operating in Livorno (città), with at least one partner who is Jewish "
        "(stated in the act or editorial attribution).",
    ]


def _log(path, *edits):
    """A change log holding these applied edits: (table, field, when)."""
    c = corrections_db.connect(path)
    for k, (table, field, at) in enumerate(edits):
        c.execute("INSERT INTO change_request (request_id, op, db_table, pk, field, status, origin, created_by, created_at) "
                  "VALUES (?, 'update', ?, '{}', ?, 'applied', 'ui', 'WH', ?)", (f"r{k}", table, field, at))
        c.execute("INSERT INTO change_event (request_id, event, at, by) VALUES (?, 'applied', ?, 'WH')", (f"r{k}", at))
    c.commit()
    c.close()


def test_the_edits_logged_since_count_only_what_the_question_selects_by(corpus, tmp_path):
    _save("Livorno firms with a Jewish partner")
    _log(tmp_path / "corrections.db",
         ("contract", "currency_id", "2999-01-01T00:00:00+00:00"),  # not read by this question
         ("investor", "is_jewish", "2999-02-01T00:00:00+00:00"),
         ("investor", "jewish_db", "2000-01-01T00:00:00+00:00"))  # before it was saved
    (question,) = _mine()
    assert question["lines"][-1] == (
        "1 edit has been logged since to the records and fields it selects by, the last on 1 February 2999.")
    assert review_server.find_how(HER_REQUEST)["as_of"] == (
        "The last edit logged to the records and fields this question selects by was on 1 February 2999 (2 in all).")
    assert review_server.find_how({"contract": [{"kind": "years", "from": 1700}]})["as_of"] == (
        "No edit has been logged to the records and fields this question selects by.")


def test_the_fields_a_question_selects_by(corpus):
    reads = review_server._find_reads(find_normalize({
        "contract": [LIVORNO, {"kind": "currency", "values": [1]}],
        "partners": [_partner("any", ("woman", "yes")), {"quantifier": "no", "conditions": [_values("word", "cavalier")]}]}))
    assert reads == {"contract": {None, "currency_id"}, "contract_place": {"*"}, "investor": {None, "title", "profession"},
                     "person": {None, "is_woman"}}


def test_saved_questions_are_renamed_and_removed_in_an_append_only_log(corpus):
    qid = _save("first name")["id"]
    review_server.analysis_find_rename(qid, review_server.FindRename(name="second name"), _as("FT@example.org"))
    assert [q["name"] for q in _mine()] == ["second name"]
    with pytest.raises(HTTPException) as refused:  # someone else's
        review_server.analysis_find_remove(qid, _as("someone.else@example.org"))
    assert refused.value.status_code == 404
    review_server.analysis_find_remove(qid, _as("FT@example.org"))
    assert _mine() == []
    events = [json.loads(line)["event"] for line in review_server.FIND_QUESTIONS_PATH.read_text().splitlines()]
    assert events == ["saved", "renamed", "removed"]  # nothing is erased


@pytest.mark.parametrize("name,search,words", [
    ("   ", "place=25", "Give the question a name"),
    ("x" * 121, "place=25", "Give the question a name"),
    ("fine", "place=25\nview=list", "too long to save"),
    ("fine", "p" * 8001, "too long to save"),
])
def test_a_save_that_does_not_fit_is_refused_in_words(corpus, name, search, words):
    with pytest.raises(HTTPException) as refused:
        _save(name, search=search)
    assert words in refused.value.detail


def test_a_saved_question_that_no_longer_fits_says_so(corpus):
    review_server._find_append({"event": "saved", "id": "abcdefabcdef", "owner": "", "name": "old", "search": "",
                                "spec": {"contract": [{"kind": "sector"}]}, "sentence": "Contracts", "count": 3, "ids": []})
    (question,) = _mine("")
    assert question["changed"] is True and question["today"] is None
    assert question["lines"][1] == "It no longer fits the conditions: Unknown condition 'sector'."


def test_the_saved_questions_travel_with_every_backup():
    from workflows import backup_verify

    assert "find_questions.jsonl" in backup_verify.HUMAN_WORK and "find_questions.jsonl" in backup_verify.FILE_ROLES
    for script in ("deploy/backup_remote.sh", "deploy/backup_corrections.sh"):
        assert "find_questions.jsonl" in (ROOT / script).read_text(), script


# ---- the page writes what the server writes -----------------------------------

_NODE = shutil.which("node")
_FIND_SPEC = ROOT / "apps/review/src/utils/findSpec.ts"
_FIND_VIEW = ROOT / "apps/review/src/utils/findView.ts"


def _node_version() -> tuple[int, ...]:
    if _NODE is None:
        return ()
    out = subprocess.run([_NODE, "--version"], capture_output=True, text=True, check=False).stdout
    return tuple(int(part) for part in re.findall(r"\d+", out)[:2])


needs_node = pytest.mark.skipif(
    _node_version() < (22, 6),
    reason="needs Node 22.6 or newer to run apps/review/src/utils/findSpec.ts directly",
)


def _ts(expression: str):
    script = (
        f"import * as f from {json.dumps(_FIND_SPEC.as_uri())};\n"
        f"import * as v from {json.dumps(_FIND_VIEW.as_uri())};\n"
        f"console.log(JSON.stringify({expression}));\n"
    )
    out = subprocess.run(
        [_NODE, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return json.loads(out.stdout)


def _kinds() -> str:
    return f"f.kindsFrom({json.dumps(find_registry())})"


def _not(c):
    return {**c, "not": False} if c.get("kind") != "years" else c


@needs_node
def test_the_address_round_trips_and_names_what_it_drops():
    state = {"contract": [_not({"kind": "place", "values": [25, 387]}), {"kind": "years", "from": "1700", "to": ""}],
             "partners": [{"quantifier": "any", "conditions": [_not({"kind": "jewish", "choice": "either"}),
                                                               _not({"kind": "woman", "choice": "yes"})]},
                          {"quantifier": "no", "conditions": [_not({"kind": "role", "choice": "gp"})]}]}
    search = _ts(f"f.findSearch({json.dumps(state)}, {_kinds()})")
    assert search == "place=25.387&years=1700-&partner=any.jewish-either.woman&partner=no.role-gp"
    back = _ts(f"f.parseFind({json.dumps(search)}, {_kinds()})")
    assert back == {"state": state, "dropped": []}
    odd = _ts(f"f.parseFind('place=liv&partner=any.sex-f&years=x&partner=some.woman&page=2&not-years=1-2', {_kinds()})")
    assert odd["state"] == {"contract": [], "partners": []}
    assert odd["dropped"] == ["place “liv”", "partner line “any.sex-f”", "years “x”", "partner line “some.woman”",
                              "“page”", "years “1-2”"]
    # a condition still being filled in stays out of the address
    unfinished = {"contract": [_not({"kind": "place", "values": []}), {"kind": "years", "from": "", "to": " "}],
                  "partners": [{"quantifier": "any", "conditions": [_not({"kind": "residence", "values": []})]}]}
    assert _ts(f"f.findSearch({json.dumps(unfinished)}, {_kinds()})") == ""


@needs_node
def test_every_step_two_condition_round_trips_through_the_address():
    state = {
        "contract": [
            {"kind": "place", "not": False, "values": [25, None]},
            {"kind": "place", "not": True, "values": [1]},
            {"kind": "activity", "not": False, "values": [1, 1725, None]},
            {"kind": "currency", "not": True, "values": [2]},
            {"kind": "register", "not": False, "values": ["10831", "1263bis", None]},
            {"kind": "discretion", "not": True, "choice": "activity"},
        ],
        "partners": [{"quantifier": "every", "conditions": [
            {"kind": "woman", "not": True, "choice": "yes"},
            {"kind": "residence", "not": False, "values": [25, None]},
            {"kind": "origin", "not": True, "values": [387]},
            {"kind": "word", "not": False, "values": ["mercant", "d’arme", "balì"]},
        ]}],
    }
    search = _ts(f"f.findSearch({json.dumps(state)}, {_kinds()})")
    assert search.startswith("place=25.none&not-place=1&activity=1.1725.none&not-currency=2&register=10831.1263bis.none"
                             "&not-discretion=activity&partner=every.not-woman.residence-25_none.not-origin-387.word-")
    assert _ts(f"f.parseFind({json.dumps(search)}, {_kinds()})") == {"state": state, "dropped": []}
    # and the server reads what the page sends, in the same order
    spec = _ts(f"f.toSpec({json.dumps(state)})")
    normal = find_normalize(spec)
    assert [c["kind"] for c in normal["contract"]] == ["place", "place", "activity", "currency", "register", "discretion"]
    assert normal["partners"][0]["conditions"][3]["values"] == ["balì", "d’arme", "mercant"]
    # values that no longer fit are named
    odd = _ts(f"f.parseFind('activity=seta&partner=any.word-none&register=10831%3B&partner=any.residence', {_kinds()})")
    assert odd["dropped"] == ["activity “seta”", "partner line “any.word-none”", "register “10831;”",
                              "partner line “any.residence”"]


@needs_node
def test_the_page_sends_what_the_server_reads():
    state = {"contract": [{"kind": "years", "from": " 1700", "to": ""}], "partners": []}
    spec = _ts(f"f.toSpec({json.dumps(state)})")
    assert spec == {"contract": [{"kind": "years", "from": 1700, "to": None}], "partners": []}
    assert find_normalize(spec)["contract"] == [{"kind": "years", "from": 1700, "to": None}]
    reasons = _ts(f"[f.incompleteReason({{contract: [{{kind: 'years', from: '1800', to: '1700'}}], partners: []}}, {_kinds()}),"
                  f" f.incompleteReason({{contract: [{{kind: 'place', not: false, values: []}}], partners: []}}, {_kinds()}),"
                  f" f.incompleteReason({{contract: [{{kind: 'years', from: '17oo', to: ''}}], partners: []}}, {_kinds()}),"
                  f" f.incompleteReason({{contract: [], partners: [{{quantifier: 'any', conditions: "
                  f"[{{kind: 'word', not: false, values: []}}]}}]}}, {_kinds()})]")
    assert reasons == ["The first year is after the last year.", "Choose a place.",
                       "Enter years as whole numbers, such as 1700.", "Type a word."]


@needs_node
def test_the_pickers_ask_the_counts_the_server_gives(corpus):
    # The Jewish choice within Livorno: 2 / 4 / 5 here, 4 / 5 / 8 in the corpus (live: 17 / 100 / 107, 183).
    state = {"contract": [_not(LIVORNO)],
             "partners": [{"quantifier": "any", "conditions": [_not({"kind": "jewish", "choice": "either"})]}]}
    options = [_not({"kind": "jewish", "choice": c}) for c in ("stated", "attributed", "either")]
    target = {"level": "partner", "line": 0, "index": 0}
    pairs = _ts(f"f.optionSpecs({json.dumps(state)}, {json.dumps(target)}, {json.dumps(options)})")
    specs = [p["here"] for p in pairs] + [p["alone"] for p in pairs]
    counts = review_server.analysis_find_counts(review_server.FindCounts(specs=specs))["counts"]
    assert [c["total"] for c in counts] == [2, 4, 5, 4, 5, 8]
    # same partner or another (live: 12 against 227)
    women = {"contract": [], "partners": [{"quantifier": "any", "conditions": [_not({"kind": "woman", "choice": "yes"})]}]}
    placed = _ts(f"f.placementSpecs({json.dumps(women)}, {json.dumps(_not({'kind': 'role', 'choice': 'gp'}))})")
    assert [p["line"] for p in placed] == [0, None]
    counts = review_server.analysis_find_counts(review_server.FindCounts(specs=[p["spec"] for p in placed]))["counts"]
    assert [c["total"] for c in counts] == [1, 3]
    # a value picker for a condition still empty counts each value in its place, the rest left as it is
    half = {"contract": [_not(LIVORNO), _not({"kind": "currency", "values": []})], "partners": []}
    pairs = _ts(f"f.optionSpecs({json.dumps(half)}, {{level: 'contract', index: 1}}, "
                f"[{{kind: 'currency', not: false, values: [1]}}, {{kind: 'currency', not: false, values: [null]}}])")
    counts = review_server.analysis_find_counts(
        review_server.FindCounts(specs=[p[k] for p in pairs for k in ("here", "alone")]))["counts"]
    assert [c["total"] for c in counts] == [2, 2, 6, 15]  # 101, 103 / in Livorno with none recorded: 104 105 107 108 109 111


@needs_node
def test_a_near_miss_opens_as_the_question_the_server_wrote(corpus):
    near = review_server.find_how(HER_REQUEST)["near_misses"][0]["spec"]
    state = _ts(f"f.fromSpec({json.dumps(near)})")
    search = _ts(f"f.findSearch({json.dumps(state)}, {_kinds()})")
    assert search == "not-place=25&partner=any.jewish-either.residence-25"
    assert find_normalize(_ts(f"f.toSpec({json.dumps(state)})")) == near


@needs_node
@pytest.mark.parametrize("quantifiers", [
    ["any"], ["no"], ["every"], ["any", "any"], ["every", "any"], ["no", "any"], ["no", "any", "any"],
    ["any", "no", "every"],
])
def test_each_partner_line_opens_with_the_words_of_the_sentence(quantifiers):
    registry = find_registry()
    words = {q["key"]: {"reading": q["reading"], "later": q["later"]} for q in registry["quantifiers"]}
    lines = [{"quantifier": q} for q in quantifiers]
    openings = _ts(f"f.lineOpenings({json.dumps(lines)}, {json.dumps(words)}, {json.dumps(registry['same_or_another'])})")
    reading = FIND_PARTNER_CONDITIONS["proxy"]["choices"]["yes"][0]
    sentence = build_find_sql({"partners": [_partner(q, ("proxy", "yes")) for q in quantifiers]}, PLACES)["sentence"]
    assert sentence == "Contracts " + ", ".join(f"{opening} {reading}" for opening in openings)


@needs_node
def test_the_view_rides_in_the_address_beside_the_question():
    view = {"view": "compare", "by": "place", "by2": "half", "period": "half", "show": "share", "base": 1,
            "split": "", "beside": [{"target": "c0", "token": "place-1"}, {"target": "p0.1", "token": "not-role-lp"}],
            "partners": "every", "saved": "", "all": False}
    search = _ts(f"v.viewSearch({json.dumps(view)})")
    assert search == ("view=compare&by=place&by2=half&period=half&show=share&base=1"
                      "&beside=c0%7Eplace-1&beside=p0.1%7Enot-role-lp")
    assert _ts(f"v.parseView({json.dumps(search)})") == view
    assert _ts("v.viewSearch(v.DEFAULT_VIEW)") == ""
    odd = _ts("v.parseView('view=map&by=a%3Bb&base=-2&period=century&beside=x9~place-1&beside=c0~place-1')")
    assert odd == {**_ts("v.DEFAULT_VIEW"), "beside": [{"target": "c0", "token": "place-1"}]}
    # the question's reader passes the view's keys over rather than naming them as dropped
    both = _ts(f"f.parseFind('place=25&view=time&period=half', {_kinds()}, v.VIEW_KEYS)")
    assert both["dropped"] == [] and both["state"]["contract"] == [{"kind": "place", "not": False, "values": [25]}]
    assert _ts("v.joinSearch('place=25', 'view=count')") == "place=25&view=count"
    # the whole corpus as a set, in place of the landing
    assert _ts("v.viewSearch(v.viewOf({all: true, view: 'time'}))") == "view=time&all=1"
    assert _ts("v.parseView('all=1')") == {**_ts("v.DEFAULT_VIEW"), "all": True}
    assert _ts("v.questionPart('all=1&view=count')") == ""


@needs_node
def test_the_charts_scale_and_name_their_periods():
    assert _ts("[v.niceTicks(27), v.niceTicks(107), v.niceTicks(0.6), v.niceTicks(0)]") == [
        [0, 10, 20, 30], [0, 50, 100, 150], [0, 0.2, 0.4, 0.6], [0, 1]]
    assert _ts("[v.share(17, 107), v.share(3, 0), v.percentText(v.share(60, 90)), v.percentText(null), v.percentText(100)]") == [
        100 * 17 / 107, None, "66.7%", "–", "100%"]
    labels = _ts("[1440, 1450, 1800].map((p) => v.periodLabel(p, 10, {first: '1445–1449', last: '1800–1808'}, [1440, 1450, 1800]))")
    assert labels == ["1445–1449", "1450–1459", "1800–1808"]


@needs_node
def test_a_question_set_beside_is_the_same_question_with_one_chip_changed(corpus):
    state = {"contract": [_not(LIVORNO)], "partners": []}
    other = _ts(f"f.toSpec(f.complete(f.replaceAt({json.dumps(state)}, v.readTarget('c0'), "
                f"f.conditionOf('place-1', {_kinds()}.contract))))")
    assert other == {"contract": [{"kind": "place", "not": False, "values": [1]}], "partners": []}
    out = _time({"contract": [LIVORNO]}, beside=[other])
    assert [s["total"] for s in out["series"]] == [9, 2]
    token = _ts(f"f.conditionToken({{kind: 'role', not: true, choice: 'lp'}}, {_kinds()}.partner)")
    assert token == "not-role-lp"


@needs_node
def test_two_sets_combined_ride_in_the_address(corpus):
    first = {"contract": [_not(LIVORNO)],
             "partners": [{"quantifier": "any", "conditions": [_not({"kind": "jewish", "choice": "either"})]}]}
    second = {"contract": [_not(FIRENZE)], "partners": []}
    state = {"contract": [{"kind": "years", "from": "1710", "to": ""}], "partners": [],
             "combine": {"op": "or", "sets": [first, second]}}
    search = _ts(f"f.findSearch({json.dumps(state)}, {_kinds()})")
    assert search == "combine=or&set=place%3D25%26partner%3Dany.jewish-either&set=place%3D1&years=1710-"
    assert _ts(f"f.parseFind({json.dumps(search)}, {_kinds()})") == {"state": state, "dropped": []}
    spec = _ts(f"f.toSpec({json.dumps(state)})")
    assert spec["combine"] == "or" and len(spec["sets"]) == 2
    assert _find(spec)["ids"] == [102, 103, 106, 107]  # the server reads what the page writes
    assert _ts(f"f.toSpec(f.fromSpec({json.dumps(spec)}))") == spec
    # a set still being filled in holds the whole question back; a lone set is named as dropped
    unfinished = {**state, "combine": {"op": "and", "sets": [{"contract": [_not({"kind": "place", "values": []})],
                                                              "partners": []}, second]}}
    assert _ts(f"f.incompleteReason({json.dumps(unfinished)}, {_kinds()})") == "Choose a place."
    lone = _ts(f"f.parseFind('combine=and&set=place%3D25&place=1', {_kinds()})")
    assert lone["dropped"] == ["the combined sets"] and lone["state"]["contract"] == [_not(FIRENZE)]
    assert _ts("v.questionPart('place=25&view=count&partners=matched&saved=abcdef012345')") == "place=25"


def test_the_chips_take_their_words_from_the_registry():
    # Readings live on the server; the page may not keep its own copy of them.
    source = "".join((ROOT / "apps/review/src" / name).read_text() for name in (
        "components/FindPane.tsx", "components/FindChips.tsx", "components/FindViews.tsx",
        "components/FindQuestions.tsx", "components/FindLanding.tsx", "utils/findSpec.ts", "utils/findView.ts"))
    words = []
    for registry in (FIND_PARTNER_CONDITIONS, review_server.FIND_CONTRACT_CONDITIONS):
        for entry in registry.values():
            # "not recorded" is also how the List writes a missing date, so only longer "none" words count
            words += [entry["menu"]] + [entry[k] for k in ("label", "label_not") if entry.get(k)]
            words += [entry["none"]] if entry.get("none") not in (None, "not recorded") else []
            for choice in entry.get("choices", {}).values():
                words += [choice.reading, choice.reading_not]
    for first, later in review_server.FIND_QUANTIFIERS.values():
        words += [first, later]
    for opening, between in review_server.FIND_COMBINE.values():
        words += [opening, between]
    words += list(review_server.FIND_PLACEMENTS.values()) + [review_server._FIND_SAME_OR_ANOTHER]
    words += list(review_server.FIND_PARTNER_CHOICES.values())
    # the landing's figures and the starting questions (step 5); of the figures'
    # templates, the stretches of three words or more between the numbers
    for entry in review_server.FIND_FIGURES.values():
        words += [entry["label"], entry["definition"]]
        for key in ("value", "detail", "orphans", "other_type"):
            words += [w.strip() for w in re.split(r"\{\w+\}", entry.get(key, "")) if len(w.split()) >= 3]
    words += [w.strip() for t in review_server.FIND_FIGURE_AS_OF for w in re.split(r"\{\w+\}", t) if len(w.split()) >= 3]
    words += list(review_server.FIND_STARTING_LABELS.values())
    for group in review_server.FIND_STARTING:
        words.append(group["group"])
        words += [item[k] for item in group["items"] for k in ("title", "why", "open", "waits") if item.get(k)]
    # whole phrases only: a one-word label such as "registered" may be ordinary page copy
    for phrase in (w for w in words if " " in w):
        assert phrase not in source, phrase


# ---- speed: uncorrelated IN subqueries on a live-size corpus -------------------

def _live_size(path: Path) -> None:
    """4,866 contracts, 11,400 people, 17,495 appearances, 15,400 stakes, no
    secondary indexes: the shape of main.db (review 12.5). One partner in six has
    a residence and one in twenty an origin; 377 titles; professions on one in six."""
    rng = random.Random(1)
    c = sqlite3.connect(path)
    c.executescript(_TABLES_SQL.read_text())
    for table in corrections_db.ALL_TABLE_PRIMARY_KEYS:
        c.execute(f"ALTER TABLE `{table}` ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
    c.executemany("INSERT INTO place (place_id, place_name) VALUES (?,?)", [(k, f"Luogo {k}") for k in range(1, 400)])
    c.executemany("INSERT INTO contract (contract_id, registration_date) VALUES (?,?)",
                  [(k, f"{1445 + k % 360}-01-01") for k in range(1, 4867)])
    c.executemany("INSERT INTO contract_place (place_id, contract_id) VALUES (?,?)",
                  [(1 + rng.randrange(399) if k % 5 else 25, k) for k in range(1, 4867)])
    c.executemany("INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES (?,?,?,?)",
                  [(k, "Nome", f"Cognome{k}", 1 if k % 50 == 0 else 0) for k in range(1, 11401)])
    c.executemany("INSERT INTO title (title_id, title_name) VALUES (?,?)",
                  [(k, f"{'cavaliere' if k % 7 == 0 else 'signore'} {k}") for k in range(1, 378)])
    investors, stakes, links = [], [], []
    for k in range(1, 17496):
        contract = 1 + (k * 7) % 4866
        investors.append((k, 1 + rng.randrange(11400), contract, int(k % 40 == 0), int(k % 9 == 0), int(k % 4 == 0),
                          (1 + rng.randrange(399) if k % 3 else 25) if k % 6 == 0 else 0,
                          1 + rng.randrange(399) if k % 20 == 0 else 0,
                          1 + rng.randrange(377) if k % 3 == 0 else 0,
                          f"mercante {k % 300}" if k % 6 == 1 else None))
        if k <= 15400:
            stakes.append((k, contract, "gp" if k % 2 else "lp"))
            links.append((k, k))
    c.executemany("INSERT INTO investor (investor_id, person_id, contract_id, is_jewish, jewish_db, via_proxy, "
                  "place_of_residence, place_of_origin, title, profession) VALUES (?,?,?,?,?,?,?,?,?,?)", investors)
    c.executemany("INSERT INTO investment (investment_id, contract_id, type) VALUES (?,?,?)", stakes)
    c.executemany("INSERT INTO investor_group (investor_id, investment_id) VALUES (?,?)", links)
    c.commit()
    c.close()


@pytest.mark.parametrize("spec", [
    {"partners": [_partner("any", ("woman", "yes"), ("role", "gp"))]},
    {"partners": [_partner("every", ("jewish", "either"), ("role", "lp"))]},
    {"contract": [LIVORNO], "partners": [_partner("no", ("jewish", "either"), ("proxy", "yes"))]},
], ids=["same-partner", "every", "no"])
def test_partner_questions_stay_fast_on_a_live_size_corpus(tmp_path, monkeypatch, spec):
    # The correlated EXISTS form took 15.2 s on the first of these on main.db.
    path = tmp_path / "live.db"
    _live_size(path)
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    query = build_find_sql(spec, {25: "Livorno"}, list(range(1, 62)))
    for sql, params in ((query["list_sql"], query["list_params"]), (query["counts_sql"], query["counts_params"])):
        started = time.monotonic()
        run_readonly_sql(sql, tuple(params))
        assert time.monotonic() - started < 0.5


_LIVE_RESIDENCE = {"kind": "residence", "not": False, "values": [25]}


@pytest.mark.parametrize("spec", [
    {"partners": [_line("any", {"kind": "woman"}, _LIVE_RESIDENCE)]},
    {"contract": [_values("place", 25, negated=True)],
     "partners": [_line("every", _values("word", "cavalier"), {"kind": "jewish", "choice": "either", "not": True})]},
    {"contract": [LIVORNO], "partners": [_line("no", _values("origin", 25, None), _values("word", "mercant"))]},
], ids=["same-partner-residence", "every-word-not", "no-origin-word"])
def test_step_two_questions_stay_fast_on_a_live_size_corpus(tmp_path, monkeypatch, spec):
    path = tmp_path / "live.db"
    _live_size(path)
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    query = review_server.find_prepare(spec)
    for sql, params in ((query["list_sql"], query["list_params"]), (query["counts_sql"], query["counts_params"])):
        started = time.monotonic()
        run_readonly_sql(sql, tuple(params))
        assert time.monotonic() - started < 0.5


def test_the_pickers_and_how_this_was_found_stay_fast_on_a_live_size_corpus(tmp_path, monkeypatch):
    # A picker asks up to 13 values twice (here and on their own): one request.
    path = tmp_path / "live.db"
    _live_size(path)
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    base = {"contract": [LIVORNO], "partners": [_line("any", {"kind": "jewish", "choice": "either"})]}
    specs = []
    for place in range(20, 33):
        here = json.loads(json.dumps(base))
        here["partners"][0]["conditions"].append(_values("residence", place))
        specs += [here, {"partners": [_line("any", _values("residence", place))]}]
    started = time.monotonic()
    counts = review_server.analysis_find_counts(review_server.FindCounts(specs=specs))["counts"]
    assert all(c is not None for c in counts) and time.monotonic() - started < 1.0
    started = time.monotonic()
    how = review_server.find_how(base)
    assert how["steps"][-1]["count"] > 0 and time.monotonic() - started < 1.0


def test_the_views_stay_fast_on_a_live_size_corpus(tmp_path, monkeypatch):
    # Count, Over time and Compare each answer in one request under a second.
    path = tmp_path / "live.db"
    _live_size(path)
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    every = {}
    jewish = {"contract": [LIVORNO], "partners": [_line("any", {"kind": "jewish", "choice": "either"})]}
    asks = [
        lambda: _by(every, "place", "decade"),
        lambda: _by(jewish, "role", "provenance"),
        lambda: _time(jewish),
        lambda: _time(every, split="place"),
        lambda: _time(jewish, "half", beside=[{"contract": [{"kind": "place", "values": [26]}],
                                               "partners": jewish["partners"]}]),
        lambda: _partners(every),  # all 17,495 appearances counted, 5,000 shown
        lambda: _partners(jewish, "matched"),
        lambda: _by({"combine": "or", "sets": [jewish, {"contract": [{"kind": "place", "values": [26]}]}]}, "half"),
    ]
    for ask in asks:
        started = time.monotonic()
        ask()
        assert time.monotonic() - started < 1.0


# ---- step 5: the landing and the starting questions (12.1, section 7) -----------
# Later acts: 101 a termination and a variation, 103 a balance, 105 a renewal;
#   102's only termination is hidden, 110's hangs on a hidden contract, and 507 and
#   508 point to contracts that do not exist (508 with no type).
# Stakes: 1006 and the new 1015 share 40 on 105; 1008 shares 41 on 107 only with
#   an appearance of the hidden person 11; 1013 is marked joint and holds 42 on
#   112; 1002 is marked joint and holds no stake (the mark alone counts for nothing).
# 101 gains a partner in neither Jewish layer (a mixed contract); 114, in Livorno,
#   has a Jewish partner beside the shareholders' row.
_LANDING_SEED = """
INSERT INTO sub_contract (contract_id, archive, series, folder, folio, registration_date, sub_type,
  main_contract_id, is_deleted) VALUES
  (501,'ASF','M','10831','1r','1701-01-01','termination',101,0),(502,'ASF','M','10831','2r','1702-01-01','variation',101,0),
  (503,'ASF','M','10831','3r','1711-01-01','termination',102,1),(504,'ASF','M','10831','4r','1721-01-01','balance',103,0),
  (505,'ASF','M','10831','5r','1731-01-01','renewal',105,0),(506,'ASF','M','10831','6r','1781-01-01','termination',110,0),
  (507,'ASF','M','10831','7r','1791-01-01','variation',999,0),(508,'ASF','M','10831','8r','1792-01-01',NULL,998,0);
INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES (15,'Neri','Socio',0),(16,'Lapo','Misto',0),
  (17,'Elia','Ebreo',0);
INSERT INTO contract (contract_id, registration_date, firm_name) VALUES (114,'1797-01-01','Quattordici');
INSERT INTO contract_place (place_id, contract_id, place_db) VALUES (25,114,0);
INSERT INTO investor (investor_id, person_id, contract_id, is_jewish, jewish_db, via_proxy) VALUES
  (1015,15,105,0,0,0),(1016,11,107,0,0,0),(1017,16,101,0,0,0),(1018,17,114,1,0,0),(1019,31,114,0,0,0);
UPDATE investor SET is_joint = 1 WHERE investor_id IN (1013, 1002);
INSERT INTO investment (investment_id, contract_id, type) VALUES (40,105,'lp'),(41,107,'lp'),(42,112,'gp');
INSERT INTO investor_group (investor_id, investment_id) VALUES (1006,40),(1015,40),(1008,41),(1016,41),(1013,42);
"""
_LANDING_ALL = [101, 102, 103, 104, 105, 106, 107, 108, 109, 111, 112, 113, 114, 201, 202, 203, 204, 301, 302]


@pytest.fixture()
def landing(tmp_path, monkeypatch):
    """The fixture corpus with the landing's rows, where the server reads it."""
    path = _make_db(tmp_path / "main.db", _SEED + _LANDING_SEED)
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    return path


def _read_only():
    """A plain read-only connection, as the endpoints hand the landing."""
    return sqlite3.connect(f"file:{review_server.db_path()}?mode=ro", uri=True)


def _words(segments):
    return "".join(s["text"] for s in segments)


@pytest.mark.parametrize("condition,ids,reading", [
    ({"kind": "act", "choice": "any"}, [101, 103, 105], "with at least one later act (sub-contract)"),
    ({"kind": "act", "choice": "termination"}, [101], "with a recorded termination"),  # 102's is hidden
    ({"kind": "act", "choice": "variation"}, [101], "with a recorded variation"),
    ({"kind": "act", "choice": "balance"}, [103], "with a recorded balance"),
    ({"kind": "act", "choice": "renewal"}, [105], "with a recorded renewal"),
    ({"kind": "act", "not": True, "choice": "termination"}, [i for i in _LANDING_ALL if i != 101],
     "with no recorded termination"),
    ({"kind": "partnered", "choice": "no"}, [108, 111], "with no partners recorded"),  # 111's only partner is hidden
    ({"kind": "partnered", "not": True, "choice": "no"}, [i for i in _LANDING_ALL if i not in (108, 111)],
     "with at least one partner recorded"),
])
def test_the_landing_conditions_select_what_they_say(landing, condition, ids, reading):
    out = _find({"contract": [condition]})
    assert sorted(out["ids"]) == ids
    assert out["sentence"] == f"Contracts {reading}"


def test_a_joint_stake_is_shared_with_a_live_person_or_marked_and_held(landing):
    # 105: 1006 and 1015 share a stake; 112: 1013's mark, on a stake it holds. Not
    # 107 (its stake is shared only with a hidden person), nor 102 (a mark, no stake).
    out = _find({"partners": [_partner("any", ("joint", "yes"))]})
    assert out["ids"] == [105, 112]
    assert out["sentence"] == ("Contracts with at least one partner who holds a stake jointly "
                               "(shared with another person, or marked as held jointly)")
    every = _find({"partners": [_partner("every", ("joint", "yes"))]})
    assert every["ids"] == [105, 112]  # both of 105's partners share its stake


def _appearances():
    with _read_only() as c:
        return c.execute("SELECT COUNT(*) FROM investor i JOIN person pe ON pe.person_id = i.person_id "
                         "AND pe.is_deleted = 0 JOIN contract c ON c.contract_id = i.contract_id AND c.is_deleted = 0 "
                         "WHERE i.is_deleted = 0").fetchone()[0]


def test_the_standing_figures_count_what_they_say(landing):
    landing_ = review_server.find_figures(_read_only())
    assert landing_["answer"] == ["19 contracts.", "Counting contracts, each once.",
                                  "1 has no registration date and is included."]
    chart = landing_["chart"]
    assert (_words(chart["value"]), _words(chart["detail"])) == ("20 decades", "most in 1600–1609 (4)")
    figures = {f["key"]: (_words(f["value"]), _words(f["detail"])) for f in landing_["figures"]}
    every = _appearances()
    assert figures == {
        "contracts": ("19", "18 dated; 1 not"),
        "acts": ("4", "on 3 contracts: 1 termination, 1 variation, 1 balance, 1 renewal; "
                      "2 more point to a missing contract"),
        "termination": ("1", "5.3% of the 19 contracts"),
        "woman": ("4", "of the 17 with partners recorded (23.5%); as general partner on 1, as external investor on 2"),
        "places": ("Livorno 10, Firenze 2, Pisa 2",
                   "of these 1, 0 and 0 marked as inferred by the data-entry team (meaning to be confirmed)"),
        "proxy": ("1", f"of {every} ({100 / every:.1f}%)"),
        "joint": ("3", f"of {every} ({300 / every:.1f}%)"),
    }
    assert [f["key"] for f in landing_["figures"]] == list(figures)


def test_every_number_on_the_landing_opens_the_question_it_counts(landing):
    # Each linked number reruns as the Find it opens and finds as many: contracts,
    # or, where it opens See their partners, the matching appearances.
    landing_ = review_server.find_figures(_read_only())
    linked = [s for f in [landing_["chart"], *landing_["figures"]] for s in f["value"] + f["detail"] if "spec" in s]
    assert len(linked) == 12  # the decades; contracts, acts, termination; woman 3; places 3; proxy; joint
    for segment in linked:
        query = review_server.find_prepare(segment["spec"])
        shown = int(re.sub(r"\D", "", segment["text"].rsplit(" ", 1)[-1] if segment["view"].get("view") != "time" else "0") or 0)
        if segment["view"].get("view") == "partners":
            built = review_server.find_partners_sql(query, "matched")
            found = run_readonly_sql(built["counts_sql"], tuple(built["params"]))["rows"][0][0]
        elif segment["view"].get("view") == "time":
            continue  # the decades: the axis of the whole corpus
        else:
            found = len(review_server._find_members(query))
        assert found == shown, segment


def test_the_landing_says_when_what_each_figure_counts_was_last_edited(landing, tmp_path):
    out = review_server.analysis_find_landing()
    assert {f["as_of"] for f in [out["chart"], *out["figures"]]} == {"No edit logged to what it counts."}
    assert not any("reads" in f for f in [out["chart"], *out["figures"]])
    _log(tmp_path / "corrections.db", ("sub_contract", "end_date", "2999-03-01T00:00:00+00:00"),
         ("person", "is_woman", "2999-04-01T00:00:00+00:00"))
    out = review_server.analysis_find_landing()
    as_of = {f["key"]: f["as_of"] for f in [out["chart"], *out["figures"]]}
    assert as_of["acts"] == as_of["termination"] == "Last edit logged to what it counts: 1 March 2999."
    assert as_of["woman"] == "Last edit logged to what it counts: 1 April 2999."
    assert as_of["contracts"] == as_of["places"] == "No edit logged to what it counts."


_STARTING_FINDS = [item for group in review_server.FIND_STARTING for item in group["items"] if "same" in item]


@pytest.mark.parametrize("item", _STARTING_FINDS, ids=[item["library"] for item in _STARTING_FINDS])
def test_each_starting_question_counts_what_its_library_query_counts(landing, item):
    library, found = review_server.find_starting_check(_read_only(), item)
    assert library == found and library


def test_every_library_entry_is_a_starting_question_once():
    items = [item for group in review_server.FIND_STARTING for item in group["items"]]
    placed = [item["library"] for item in items if item.get("library")]
    assert sorted(placed) == sorted(q["id"] for q in review_server.ANALYSIS_LIBRARY)  # all 25, each once
    for item in items:
        if "spec" in item:
            assert "same" in item or "library" not in item  # a Find standing for a library entry is held to it
            review_server.find_normalize(item["spec"])
        elif "library" in item:
            assert item["why"]
        else:
            # a question that waits for a decision names it and carries no number
            assert re.search(r"decision 12[a-f]|§\d", item["waits"]) and "spec" not in item
    assert [g["group"] for g in review_server.FIND_STARTING] == [
        "When and for how long", "Where", "For what business", "With what money", "Who invested",
        "Who came back and who was related", "Checking the data"]


def test_all_jewish_stays_a_library_query_because_every_partner_leaves_out_placeholders(landing):
    # 114 has a Jewish partner beside the shareholders' row: the library list counts
    # the row as a partner who is neither, Find's "every partner" leaves it out.
    with _read_only() as c:
        library = sorted(r[1] for r in c.execute(_lib_sql("jewish_all")))
    every = _find({"partners": [_partner("every", ("jewish", "either"))]})["ids"]
    assert sorted(set(every) - set(library)) == [114] and set(library) <= set(every)


def _lib_sql(query_id):
    return next(q["sql"] for q in review_server.ANALYSIS_LIBRARY if q["id"] == query_id)


def test_the_starting_questions_carry_their_sentences_and_counts(landing):
    out = review_server.analysis_find_starting()
    assert out["labels"] == {"library": "Library query"}
    items = {i["title"]: i for g in out["groups"] for i in g["items"]}
    assert items["Contracts with no partners"] == {
        "kind": "find", "title": "Contracts with no partners", "says": "Contracts with no partners recorded",
        "spec": {"contract": [{"kind": "partnered", "not": False, "choice": "no"}], "partners": []}, "view": {},
        "count": 2, "library": "no_investors"}
    assert items["Women partners, by role"]["says"] == (
        "Contracts with at least one partner who is a woman, counted by role of the matching partner")
    assert items["Most-used currencies"]["count"] == 19
    assert items["Sub-contracts by type"] == {"kind": "library", "title": "Sub-contracts by type",
                                              "says": "Counts later acts, not contracts.", "library": "later_acts_by_type"}
    open_ = [i for g in out["groups"] for i in g["items"] if i["kind"] == "open"]
    assert len(open_) == 6 and all(set(i) == {"kind", "title", "waits"} for i in open_)


@needs_node
def test_each_starting_question_and_figure_opens_as_the_question_the_server_wrote(landing):
    starting = review_server.analysis_find_starting()
    figures = review_server.find_figures(_read_only())
    links = [{"spec": i["spec"], "view": i["view"]} for g in starting["groups"] for i in g["items"] if i["kind"] == "find"]
    links += [{"spec": s["spec"], "view": s["view"]} for f in [figures["chart"], *figures["figures"]]
              for s in f["value"] + f["detail"] if "spec" in s]
    out = _ts(f"{json.dumps(links)}.map((l) => {{"
              f"const search = v.joinSearch(f.findSearch(f.fromSpec(l.spec), {_kinds()}), v.viewSearch(v.viewOf(l.view)));"
              f"return {{search, spec: f.toSpec(f.parseFind(search, {_kinds()}, v.VIEW_KEYS).state), view: v.parseView(search)}};}})")
    default = _ts("v.DEFAULT_VIEW")
    for link, back in zip(links, out):
        assert find_normalize(back["spec"]) == link["spec"], back["search"]
        assert back["view"] == {**default, **link["view"]}, back["search"]
    searches = [back["search"] for back in out]
    assert "all=1" in searches and "view=partners&partners=matched" in searches[-1]
    assert _ts("[f.isEmpty(f.EMPTY_FIND), f.isEmpty({contract: [{kind: 'place', not: false, values: []}], partners: []})]") == [
        True, False]


def test_the_landing_and_the_starting_questions_stay_fast_on_a_live_size_corpus(tmp_path, monkeypatch):
    path = tmp_path / "live.db"
    _live_size(path)
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    for ask in (review_server.analysis_find_landing, review_server.analysis_find_starting):
        started = time.monotonic()
        ask()
        assert time.monotonic() - started < 1.0
