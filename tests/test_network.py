"""Network view, step 1: the circle of one entry (docs/network/step1_contract.md §2, §4).

The fixture is one small co-signing graph around entry 1, Giuseppe Frescobaldi:
- ring 1 on four acts (10, 11, 12 dated; 13 undated): 2 Mario Morelli (via proxy
  on 10, his stake link on 11 hidden), 3 Pietro Rossi (heirs of, on 10), 4 Anna
  Bianchi (two rows on 11, one general partner and one external investor), 5
  Francesco Tempi (a hub: 61 acts), 14 Benedetta Tempi (a joint stake with 5 on
  12), 6 MOLTEPLICI AZIONARI (a shareholder group, on 13);
- ring 2: 7 hangs from 2 (two acts beat 3's earlier one), 8 and 9 from 3 (the
  earlier act beats 2's and 4's), 17 from 3 (one act with 3 and 4: lowest id),
  16 from the shareholder group; the hub's own co-signers 100-159 only when the
  hub is widened through or by hand;
- ring 3: 10, through 7, only when 7 is widened by hand;
- hidden: 11's row on act 10, act 14 (with 12 on it), person 13 on act 11; 15
  has no appearance at all; a second row of 1 on act 10 (1015), hidden, carries
  the editorial attribution;
- the Jewish marks: 2 on act 10 stated in the act (and attributed), 4's first
  row on act 11 attributed only;
- apart, for the kinds: 40 Tommaso Alberti on act 40 with 41 Arte della Lana (an
  institution) and the estates 42 and 43, and on act 41 with 45 Bastiano Neri.
Every test reads the fixture through the endpoint; nothing touches data/.

    UV_PROJECT_ENVIRONMENT=.floracco uv run --with pytest --with httpx python -m pytest tests/test_network.py
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_find import _live_size, _make_db
from workflows import review_server
from workflows.review_server import NETWORK_WORDS

_HUB_ACTS = ",".join(f"({100 + k},'1700-01-01','Tempi {k}')" for k in range(60))
_HUB_SIGNERS = ",".join(f"({100 + k},'Socio','Tempi{k}',0)" for k in range(60))
_HUB_ROWS = ",".join(f"({5000 + k},5,{100 + k},0,0,0),({6000 + k},{100 + k},{100 + k},0,0,0)" for k in range(60))

_SEED = f"""
INSERT INTO place (place_id, place_name) VALUES (1,'Firenze'),(25,'Livorno');
INSERT INTO currency (currency_id, currency) VALUES (1,'ducati di lire 7 per ducato');
INSERT INTO economic_activity (ec_activity_id, activity) VALUES (1,'negozio di arte di seta');
INSERT INTO contract (contract_id, registration_date, firm_name, total, currency_id, economic_sector) VALUES
  (10,'1669-10-03','Mario Morelli e compagni',50000,1,1),
  (11,'1671-10-12','Mario Morelli e compagni',60000,1,1),
  (12,'1690-09-18','Mario Morelli e compagni',70000,1,1),
  (13,'0000-00-00','Senza data',NULL,NULL,NULL),
  (14,'1695-01-01','Nascosta',NULL,NULL,NULL);
INSERT INTO contract (contract_id, registration_date, firm_name) VALUES
  (20,'1675-01-01','Neri'),(21,'1680-01-01','Neri rinnovo'),(22,'1672-01-01','Rossi'),(23,'1676-05-05','Bianchi'),
  (24,'1670-01-01','Rossi e Neri'),(25,'1685-01-01','Azionisti'),(26,'1678-01-01','Grigi'),(30,'1700-01-01','Lontano'),
  (40,'1700-03-03','Alberti'),(41,'1702-03-03','Alberti rinnovo'),
  {_HUB_ACTS};
UPDATE contract SET is_deleted = 1 WHERE contract_id = 14;
INSERT INTO contract_place (place_id, contract_id, place_db) VALUES (1,10,0),(1,11,1),(25,11,0),(1,12,0);
INSERT INTO person (person_id, first_name, father_mother, last_name, is_woman) VALUES
  (1,'Giuseppe','Lorenzo','Frescobaldi',0),(2,'Mario',NULL,'Morelli',0),(3,'Pietro',NULL,'Rossi',0),
  (4,'Anna',NULL,'Bianchi',1),(5,'Francesco',NULL,'Tempi',0),(6,'',NULL,'MOLTEPLICI AZIONARI',0),
  (7,'Carlo',NULL,'Neri',0),(8,'Luca',NULL,'Verdi',0),(9,'Marco',NULL,'Gialli',0),(10,'Dino',NULL,'Lontano',0),
  (11,'Ugo',NULL,'Rimosso',0),(12,'Teo',NULL,'Nascosto',0),(13,'Ida',NULL,'Invisibile',0),(14,'Benedetta',NULL,'Tempi',1),
  (15,'Nino',NULL,'Senza',0),(16,'Lelio',NULL,'Racach',0),(17,'Paolo',NULL,'Grigi',0),
  (40,'Tommaso',NULL,'Alberti',0),(41,'',NULL,'Arte della Lana',0),(42,'Eredi',NULL,'di Piero Rossi',0),
  (43,'Eredi',NULL,'di Anna Verdi',0),(45,'Bastiano',NULL,'Neri',0);
INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES {_HUB_SIGNERS};
UPDATE person SET is_deleted = 1 WHERE person_id = 13;
INSERT INTO investor (investor_id, person_id, contract_id, via_proxy, heirs_of, is_joint) VALUES
  (1010,1,10,0,0,0),(1020,2,10,1,0,0),(1030,3,10,0,1,0),(1110,11,10,0,0,0),
  (1011,1,11,0,0,0),(1021,2,11,0,0,0),(1041,4,11,0,0,0),(1042,4,11,0,0,0),(1131,13,11,0,0,0),
  (1012,1,12,0,0,0),(1052,5,12,0,0,0),(1142,14,12,0,0,0),
  (1013,1,13,0,0,0),(1063,6,13,0,0,0),
  (1014,1,14,0,0,0),(1124,12,14,0,0,0),
  (2020,2,20,0,0,0),(2070,7,20,0,0,0),(2080,8,20,0,0,0),
  (2121,2,21,0,0,0),(2171,7,21,0,0,0),
  (2232,3,22,0,0,0),(2282,8,22,0,0,0),(2292,9,22,0,0,0),
  (2343,4,23,0,0,0),(2393,9,23,0,0,0),
  (2434,3,24,0,0,0),(2474,7,24,0,0,0),
  (2565,6,25,0,0,0),(2616,16,25,0,0,0),
  (2636,3,26,0,0,0),(2646,4,26,0,0,0),(2676,17,26,0,0,0),
  (3070,7,30,0,0,0),(3100,10,30,0,0,0),
  (4040,40,40,0,0,0),(4041,41,40,0,0,0),(4042,42,40,0,0,0),(4043,43,40,0,0,0),(4140,40,41,0,0,0),(4145,45,41,0,0,0),
  {_HUB_ROWS};
UPDATE investor SET is_deleted = 1 WHERE investor_id = 1110;
UPDATE investor SET is_jewish = 1, jewish_db = 1 WHERE investor_id = 1020;
UPDATE investor SET jewish_db = 1 WHERE investor_id = 1041;
INSERT INTO investor (investor_id, person_id, contract_id, via_proxy, heirs_of, is_joint, jewish_db, is_deleted)
  VALUES (1015,1,10,0,0,0,1,1);
INSERT INTO investment (investment_id, contract_id, type, investment_cash, investment_non_cash) VALUES
  (1001,10,'gp',9365,NULL),(1002,10,'lp',3000,NULL),(1003,10,'lp',2000,'una bottega'),
  (1101,11,'gp',8000,NULL),(1102,11,'lp',NULL,NULL),(1104,11,'lp',1500,NULL),(1105,11,'gp',500,NULL),
  (1201,12,'gp',9365,NULL),(1202,12,'gp',20000,NULL),
  (1301,13,'lp',100,NULL),(1302,13,'gp',100,NULL),
  (2001,20,'gp',10,NULL),(2101,21,'lp',5,NULL);
INSERT INTO investor_group (investor_id, investment_id, is_deleted) VALUES
  (1010,1001,0),(1020,1002,0),(1030,1003,0),
  (1011,1101,0),(1021,1102,1),(1041,1104,0),(1042,1105,0),
  (1012,1201,0),(1052,1202,0),(1142,1202,0),
  (1013,1301,0),(1063,1302,0),
  (2070,2001,0),(2020,2101,0);
"""

RING1 = {2, 3, 4, 5, 6, 14}
RING2 = {7, 8, 9, 16, 17}
HUB_SIGNERS = set(range(100, 160))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = _make_db(tmp_path / "main.db", _SEED)
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    return TestClient(review_server.app)


def _circle(client, entry=1, **params):
    response = client.get(f"/api/network/circle/{entry}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _ids(circle, **where):
    return {n["id"] for n in circle["nodes"] if all(n[k] == v for k, v in where.items())}


def _node(circle, entry):
    return next(n for n in circle["nodes"] if n["id"] == entry)


def _appearance(circle, entry, act):
    return next(a for a in circle["appearances"] if a["person"] == entry and a["act"] == act)


def _act(circle, act):
    return next(a for a in circle["acts"] if a["id"] == act)


def _ties(circle):
    return {(t["a"], t["b"]): t["acts"] for t in circle["ties"]}


# ---- the circle at one and two steps ---------------------------------------------

def test_two_steps_reach_the_co_signers_and_theirs_but_not_through_the_hub(client):
    circle = _circle(client)
    assert circle["centre"] == 1 and circle["steps"] == 2 and circle["cap"] == 300
    assert (circle["hubs"], circle["others"], circle["widen"]) == ("stop", "on", [])
    assert _ids(circle, step=0) == {1}
    assert _ids(circle, step=1) == RING1
    assert _ids(circle, step=2) == RING2
    assert circle["counts"] == {
        "reached": 12, "drawn": 12, "persons": 11, "others": 1, "kinds": {"collective": 1}, "hubs": 1, "acts": 11, "ties": 21,
    }
    assert circle["cut"] is None
    assert {a["id"] for a in circle["acts"]} == {10, 11, 12, 13, 20, 21, 22, 23, 24, 25, 26}
    assert [a["id"] for a in circle["acts"]] == [10, 24, 11, 22, 20, 23, 26, 21, 25, 12, 13]  # by date, undated last


def test_one_step_draws_the_co_signers_and_every_act_two_of_them_share(client):
    circle = _circle(client, steps=1)
    assert {n["id"] for n in circle["nodes"]} == {1} | RING1
    assert {a["id"] for a in circle["acts"]} == {10, 11, 12, 13, 26}  # 26: 3 and 4 signed it without the centre
    assert _ties(circle)[(3, 4)] == [26]
    assert circle["counts"]["ties"] == 10
    assert "within one step" in circle["answer"]


def test_ties_are_distinct_pairs_of_drawn_entries_with_their_shared_acts(client):
    ties = _ties(_circle(client))
    assert ties[(1, 2)] == [10, 11]
    assert ties[(2, 7)] == [20, 21]
    assert ties[(5, 14)] == [12]
    assert (7, 10) not in ties  # 10 is not drawn at two steps
    assert all(a < b for a, b in ties)
    assert len(ties) == 21


def test_each_ring_two_entry_hangs_from_its_parent_by_acts_then_date_then_id(client):
    circle = _circle(client)
    assert _node(circle, 7)["parent"] == 2  # two acts with 2 beat the earlier single act with 3
    assert _node(circle, 8)["parent"] == 3  # one act each: 3's act of 1672 before 2's of 1675
    assert _node(circle, 9)["parent"] == 3  # 1672 before 4's 1676
    assert _node(circle, 17)["parent"] == 3  # one act with both 3 and 4: the lowest id
    assert _node(circle, 16)["parent"] == 6
    assert all(_node(circle, e)["parent"] == 1 for e in RING1)
    assert _node(circle, 1)["parent"] is None


def test_nodes_carry_the_stored_name_fields_the_kind_and_the_years_in_view(client):
    circle = _circle(client)
    centre = circle["nodes"][0]
    assert centre["id"] == 1 and centre["name"] == "Giuseppe Frescobaldi"
    assert centre["name_fields"] == {
        "first_name": "Giuseppe", "father_mother": "Lorenzo", "grandfather": None,
        "last_name": "Frescobaldi", "nickname": None,
    }
    assert centre["kind"] == "person" and centre["hub"] is False and centre["woman"] is False
    assert (centre["acts_in_view"], centre["acts_total"]) == (4, 4)  # the hidden act 14 counts nowhere
    assert centre["roles"] == ["gp", "lp"]
    assert (centre["first_year"], centre["last_year"], centre["undated_acts"]) == (1669, 1690, 1)
    assert centre["widened"] is False
    hub = _node(circle, 5)
    assert hub["hub"] is True and (hub["acts_in_view"], hub["acts_total"]) == (1, 61)
    assert _node(circle, 14)["woman"] is True
    assert _node(circle, 6)["kind"] == "collective"
    assert _node(circle, 6)["name_fields"]["first_name"] == ""  # as stored: empty, not null


def test_nodes_are_ordered_by_step_then_acts_shared_then_earliest_act_then_id(client):
    ids = [n["id"] for n in _circle(client)["nodes"]]
    assert ids[0] == 1
    assert ids[1] == 2  # two acts with the centre
    assert ids[1:7] == [2, 3, 4, 5, 14, 6]  # 3 (1669), 4 (1671), 5 and 14 (1690), 6 (undated last)
    assert ids[7:] == [7, 8, 9, 17, 16]


# ---- acts and appearances --------------------------------------------------------

def test_acts_carry_their_stored_fields_places_and_signer_counts(client):
    circle = _circle(client)
    act = _act(circle, 10)
    assert act["date"] == "1669-10-03" and act["year"] == 1669
    assert act["firm_name"] == "Mario Morelli e compagni" and act["total"] == 50000
    assert act["currency"] == "ducati di lire 7 per ducato" and act["activity"] == "negozio di arte di seta"
    assert act["places"] == ["Firenze"] and act["places_inferred"] is False
    assert (act["signers_live"], act["signers_in_view"]) == (3, 3)  # 11's hidden row counts nowhere
    assert _act(circle, 11)["places"] == ["Firenze", "Livorno"] and _act(circle, 11)["places_inferred"] is True
    assert (_act(circle, 11)["signers_live"], _act(circle, 11)["signers_in_view"]) == (3, 3)  # person 13 is hidden
    assert _act(circle, 26)["signers_in_view"] == 3 and _act(circle, 26)["signers_live"] == 3
    undated = _act(circle, 13)
    assert undated["date"] == "0000-00-00" and undated["year"] is None
    assert undated["activity"] is None and undated["currency"] is None and undated["total"] is None


def test_appearances_read_role_and_stake_through_the_live_junction_on_the_same_act(client):
    circle = _circle(client)
    gp = _appearance(circle, 1, 10)
    assert gp == {"person": 1, "act": 10, "role": "gp", "cash": 9365, "non_cash": None,
                  "heirs_of": False, "proxy": False, "joint": False, "jewish": None}
    assert _appearance(circle, 2, 10)["proxy"] is True
    heirs = _appearance(circle, 3, 10)
    assert heirs["heirs_of"] is True and heirs["non_cash"] == "una bottega" and heirs["cash"] == 2000
    # 2's stake link on act 11 is hidden: no role, no cash
    assert _appearance(circle, 2, 11)["role"] is None and _appearance(circle, 2, 11)["cash"] is None
    # 2's stake on act 20 points at a stake of act 21: it reads as none on 20, and 21 has no link
    assert _appearance(circle, 2, 20)["role"] is None and _appearance(circle, 2, 21)["role"] is None
    assert _node(circle, 2)["roles"] == ["lp"]
    assert len(circle["appearances"]) == sum(a["signers_in_view"] for a in circle["acts"])


def test_two_rows_on_one_act_yield_one_appearance_with_both_roles_and_the_summed_cash(client):
    circle = _circle(client)
    both = _appearance(circle, 4, 11)
    assert both["rows"] == 2 and both["role"] == "both" and both["cash"] == 2000
    assert _node(circle, 4)["roles"] == ["gp", "lp"]
    assert "rows" not in _appearance(circle, 1, 11)
    assert sum(1 for a in circle["appearances"] if a["person"] == 4 and a["act"] == 11) == 1


def test_the_jewish_marks_come_through_as_two_layers_and_a_hidden_row_carries_none(client):
    """The appearance's `jewish` is "stated" when is_jewish = 1 (whatever jewish_db
    says), "attributed" when jewish_db = 1 alone, else null; the two layers are
    never merged (docs/network/step2a_contract.md §2). A hidden row's mark is left
    out, as the row is: 1's hidden second row on act 10 carries the attribution and
    the live appearance stays unmarked."""
    circle = _circle(client)
    assert _appearance(circle, 2, 10)["jewish"] == "stated"
    assert _appearance(circle, 4, 11)["jewish"] == "attributed"  # one of the two rows, merged like the roles
    assert _appearance(circle, 1, 10)["jewish"] is None
    assert "rows" not in _appearance(circle, 1, 10) and _act(circle, 10)["signers_live"] == 3
    assert {a["jewish"] for a in circle["appearances"]} == {"stated", "attributed", None}
    assert sum(1 for a in circle["appearances"] if a["jewish"]) == 2


def test_joint_is_derived_from_the_live_holders_of_the_stake(client):
    circle = _circle(client)
    assert _appearance(circle, 5, 12)["joint"] is True and _appearance(circle, 5, 12)["cash"] == 20000
    assert _appearance(circle, 14, 12)["joint"] is True and _appearance(circle, 14, 12)["cash"] == 20000
    assert _appearance(circle, 1, 12)["joint"] is False


def test_an_undated_act_counts_as_undated_and_never_as_a_year(client):
    circle = _circle(client)
    group = _node(circle, 6)
    assert (group["first_year"], group["last_year"], group["undated_acts"]) == (1685, 1685, 1)
    assert "1669–1690" in circle["answer"] and "0000" not in circle["answer"]
    assert all(a["year"] != 0 for a in circle["acts"])


# ---- hubs, others, widen, cap ---------------------------------------------------------

def test_hubs_through_adds_the_hub_s_co_signers(client):
    circle = _circle(client, hubs="through")
    assert _ids(circle, step=2) == RING2 | HUB_SIGNERS
    assert all(_node(circle, e)["parent"] == 5 and _node(circle, e)["widened"] is False for e in HUB_SIGNERS)
    assert circle["counts"]["reached"] == 72 and circle["counts"]["hubs"] == 1
    assert {a["id"] for a in circle["acts"]} >= set(range(100, 160))
    assert (
        "Entries on more than 20 acts (1 here; 1 in the corpus) are drawn, and the second step goes through them. "
        "The cut at 20 is for readability."
    ) in circle["how"]


def test_hubs_off_leaves_the_hubs_out_before_the_expansion(client):
    circle = _circle(client, hubs="off")
    assert 5 not in {n["id"] for n in circle["nodes"]}
    assert _ids(circle, step=1) == RING1 - {5}
    assert _ids(circle, step=2) == RING2
    assert circle["counts"]["hubs"] == 0 and circle["counts"]["reached"] == 11
    act = _act(circle, 12)
    assert (act["signers_live"], act["signers_in_view"]) == (3, 2)  # the hub is live, only left out of the drawing
    assert _appearance(circle, 14, 12)["joint"] is True
    assert "Entries on more than 20 acts (1 in the corpus) are left out. The cut at 20 is for readability." in circle["how"]


def test_a_hub_centre_with_hubs_off_is_refused_in_words(client):
    response = client.get("/api/network/circle/5", params={"hubs": "off"})
    assert response.status_code == 400
    assert response.json()["detail"] == "This entry is itself on more than 20 acts; choose “drawn” to draw it."
    assert _circle(client, 5)["centre"] == 5  # with hubs=stop the hub's own circle is drawn
    assert _ids(_circle(client, 5), step=1) == {1, 14} | HUB_SIGNERS


def test_others_off_removes_the_non_person_rows_and_their_ties(client):
    circle = _circle(client, others="off")
    assert 6 not in {n["id"] for n in circle["nodes"]} and 16 not in {n["id"] for n in circle["nodes"]}
    assert circle["counts"]["others"] == 0 and circle["counts"]["reached"] == 10
    assert (1, 6) not in _ties(circle) and 25 not in {a["id"] for a in circle["acts"]}
    act = _act(circle, 13)  # the centre's act stays in view, with the group among its live signers
    assert (act["signers_live"], act["signers_in_view"]) == (2, 1)
    assert circle["counts"]["kinds"] == {}
    assert "5 co-signers on 4 acts, 1669–1690; 10 entries within two steps." in circle["answer"]
    assert not any("left out" in line and "nstitution" in line for line in circle["how"])  # the rail's ticked box says it
    response = client.get("/api/network/circle/6", params={"others": "off"})
    assert response.status_code == 400 and response.json()["detail"] == (
        "This entry is itself an institution, estate, shareholder group or placeholder; "
        "untick “Leave out institutions, estates, shareholder groups and placeholders” to draw it."
    )


def test_widen_adds_exactly_the_named_entry_s_co_signers_one_step_further(client):
    circle = _circle(client, widen="7")
    assert circle["widen"] == [7]
    assert {n["id"] for n in circle["nodes"]} == {1} | RING1 | RING2 | {10}
    far = _node(circle, 10)
    assert (far["step"], far["parent"], far["widened"]) == (3, 7, True)
    assert _ties(circle)[(7, 10)] == [30] and 30 in {a["id"] for a in circle["acts"]}
    assert circle["how"][:2] == [
        "Giuseppe Frescobaldi, entry 1, is on 4 acts; 6 co-signers are drawn, and 5 more entries that signed with those. "
        "Among the 13, 22 pairs signed an act together.",
        "Widened from entry 7: 1 more entry on the next ring.",
    ]
    assert circle["how"][-1] == "The co-signers of an entry widened from stand on a third ring, in its sector."


def test_widening_a_hub_by_hand_passes_through_it_and_the_hub_line_says_so(client):
    circle = _circle(client, widen="5.999")
    assert circle["widen"] == [5]  # 999 is not in the circle
    assert _ids(circle, widened=True) == HUB_SIGNERS
    assert all(_node(circle, e)["step"] == 2 and _node(circle, e)["parent"] == 5 for e in HUB_SIGNERS)
    assert (
        "Entries on more than 20 acts (1 here; 1 in the corpus) are drawn, and the second step stops at them, "
        "except entry 5, widened. The cut at 20 is for readability."
    ) in circle["how"]
    assert "Widened from entry 5: 60 more entries on the next ring." in circle["how"]
    # the hub's co-signers stand on ring 2, so no third-ring sentence; with 7 widened too, 10 stands on a third
    assert not any(line.startswith("The co-signers of an entry widened from") for line in circle["how"])
    assert _circle(client, widen="7.5")["widen"] == [7, 5]
    assert _circle(client, widen="7.5")["how"][-1] == "The co-signers of an entry widened from stand on a third ring, in its sector."
    assert _circle(client, steps=1, widen="5")["counts"]["reached"] == 67


def test_the_cap_keeps_the_first_two_rings_whole_and_cuts_the_rest_in_the_stated_order(client):
    circle = _circle(client, hubs="through", cap=50)
    assert circle["cut"] == {"reached": 72, "drawn": 50}
    assert circle["counts"]["reached"] == 72 and circle["counts"]["drawn"] == 50 and len(circle["nodes"]) == 50
    assert _ids(circle, step=1) == RING1
    kept = [n["id"] for n in circle["nodes"] if n["step"] == 2]
    assert kept[:5] == [7, 8, 9, 17, 16]  # 7 shares two acts; then by earliest act, then id
    assert kept[5:] == list(range(100, 138))  # one act each on the same date: lowest ids
    assert "showing 50 of 72 entries (49 person entries and one shareholder group) within two steps." in circle["answer"]
    assert circle["how"][1] == (
        "The 50 drawn are the centre, its co-signers and the entries widened from, kept whole; "
        "then the rest by acts shared with the entry they hang from, most first, earliest act first."
    )
    assert _circle(client, hubs="through", cap=72)["cut"] is None


def test_the_cap_keeps_an_entry_widened_by_hand_so_nothing_hangs_from_an_undrawn_parent(client):
    circle = _circle(client, widen="5", cap=50)
    assert circle["cut"] == {"reached": 72, "drawn": 50}
    drawn = {n["id"] for n in circle["nodes"]}
    assert 5 in drawn and all(_node(circle, n["id"])["parent"] in drawn | {None} for n in circle["nodes"])
    assert _ids(circle, widened=True) == set(range(100, 138))


# ---- hidden rows -----------------------------------------------------------------------

def test_a_hidden_investor_row_a_hidden_act_and_a_hidden_person_vanish_from_the_circle(client):
    circle = _circle(client)
    drawn = {n["id"] for n in circle["nodes"]}
    assert 11 not in drawn and 12 not in drawn and 13 not in drawn
    assert 14 not in {a["id"] for a in circle["acts"]}
    assert not any(a["person"] in (11, 12, 13) for a in circle["appearances"])
    assert NETWORK_WORDS["how_hidden"] in circle["how"]


@pytest.mark.parametrize("entry", [12, 13, 15, 99999], ids=["only-a-hidden-act", "hidden-person", "no-appearance", "unknown"])
def test_an_entry_without_a_live_appearance_is_404_in_words(client, entry):
    response = client.get(f"/api/network/circle/{entry}")
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"Entry {entry} has no acts to draw: there is no such entry, or the entry or its acts are hidden."
    )


# ---- words --------------------------------------------------------------------------------

HIDDEN_LINE = "Hidden entries, appearances and acts are left out."


def test_the_sentence_answer_and_how_lines_read_as_the_review_writes_them(client):
    circle = _circle(client)
    assert circle["sentence"] == "Who signed acts with Giuseppe Frescobaldi, entry 1?"
    assert circle["answer"] == (
        "6 co-signers on 4 acts, 1669–1690; 12 entries (11 person entries and one shareholder group) within two steps. "
        "Person entries are counted as entered; one person may have several."
    )
    assert circle["how"] == [
        "Giuseppe Frescobaldi, entry 1, is on 4 acts; 6 co-signers are drawn, and 5 more entries that signed with those. "
        "Among the 12, 21 pairs signed an act together.",
        "Entries on more than 20 acts (1 here; 1 in the corpus) are drawn, and the second step stops at them. "
        "The cut at 20 is for readability.",
        HIDDEN_LINE,
        "Ring 1, clockwise from the top: the co-signers, in the order of their first act with Giuseppe Frescobaldi; "
        "a co-signer's room grows with the entries hanging from it. "
        "Ring 2: the entries that signed with those, each beside the one it signed most acts with.",
    ]


def test_at_one_step_the_how_lines_say_what_is_drawn_and_the_clockwise_order(client):
    circle = _circle(client, steps=1)
    assert circle["answer"].startswith("6 co-signers on 4 acts, 1669–1690; 7 entries (6 person entries and one shareholder group) within one step. ")
    assert circle["how"] == [
        "Giuseppe Frescobaldi, entry 1, is on 4 acts; 6 co-signers are drawn. Among the 7, 10 pairs signed an act together.",
        "Entries on more than 20 acts (1 here; 1 in the corpus) are drawn.",
        HIDDEN_LINE,
        "Clockwise from the top, the co-signers stand in the order of their first act with Giuseppe Frescobaldi.",
    ]
    # at one step hubs=through draws the same picture and says the same
    assert _circle(client, steps=1, hubs="through")["how"] == circle["how"]
    # with the hub left out, the one-step hub line is the "left out" line
    assert _circle(client, steps=1, hubs="off")["how"][1] == (
        "Entries on more than 20 acts (1 in the corpus) are left out. The cut at 20 is for readability."
    )


def test_a_hub_centre_is_never_counted_among_the_hubs(client):
    two = _circle(client, 5)
    assert two["counts"]["hubs"] == 1 and _node(two, 5)["hub"] is True  # the key still shows its ring
    assert two["how"] == [
        "Francesco Tempi, entry 5, is on 61 acts; 62 co-signers are drawn, and 4 more entries that signed with those. "
        f"Among the 67, {two['counts']['ties']:,} pairs signed an act together.",
        "Entries on more than 20 acts (0 here; 1 in the corpus) are drawn, and the second step stops at them. "
        "The cut at 20 is for readability.",
        HIDDEN_LINE,
        "Ring 1, clockwise from the top: the co-signers, in the order of their first act with Francesco Tempi; "
        "a co-signer's room grows with the entries hanging from it. "
        "Ring 2: the entries that signed with those, each beside the one it signed most acts with.",
    ]
    one = _circle(client, 5, steps=1)
    assert one["how"][1] == HIDDEN_LINE  # no hub in the circle but the centre: the line is omitted
    # the centre is always widened, so widening it by hand applies to nothing
    assert _circle(client, 5, widen="5")["widen"] == [] and _circle(client, 5, widen="5")["how"] == two["how"]
    # with another hub drawn, it is the one counted
    assert _circle(client, 1)["how"][1].startswith("Entries on more than 20 acts (1 here; 1 in the corpus)")


@pytest.mark.parametrize("entry,params,hub_line", [
    (16, {"others": "off"}, "Entries on more than 20 acts (0 here; 1 in the corpus) are drawn, and the second step stops at them. "
                            "The cut at 20 is for readability."),
    (100, {"hubs": "off", "steps": "1"}, "Entries on more than 20 acts (1 in the corpus) are left out. The cut at 20 is for readability."),
], ids=["only-with-a-shareholder-group", "only-with-a-hub"])
def test_zero_co_signers_say_so_and_have_no_position_line(client, entry, params, hub_line):
    circle = _circle(client, entry, **params)
    assert [n["id"] for n in circle["nodes"]] == [entry] and circle["ties"] == []
    assert circle["counts"]["ties"] == 0 and circle["counts"]["acts"] == 1  # the centre's act stays in view
    act = circle["acts"][0]
    assert (act["signers_live"], act["signers_in_view"]) == (2, 1)
    assert circle["answer"].startswith("0 co-signers on 1 act, ")
    assert circle["how"] == ["No other entry on its acts is drawn.", hub_line, HIDDEN_LINE]


def test_the_answer_names_the_kinds_present_and_counts_carries_them(client):
    circle = _circle(client, 40)
    assert circle["counts"]["kinds"] == {"institution": 1, "estate": 2}
    assert circle["counts"]["persons"] == 2 and circle["counts"]["others"] == 3
    assert circle["answer"] == (
        "4 co-signers on 2 acts, 1700–1702; 5 entries (2 person entries and one institution and 2 estates) within two steps. "
        "Person entries are counted as entered; one person may have several."
    )
    assert {n["id"]: n["kind"] for n in circle["nodes"] if n["kind"] != "person"} == {
        41: "institution", 42: "estate", 43: "estate",
    }
    assert _circle(client)["counts"]["kinds"] == {"collective": 1}
    assert _circle(client, 40, others="off")["counts"]["kinds"] == {}
    assert _circle(client, 40, others="off")["answer"].startswith("1 co-signer on 2 acts, 1700–1702; 2 entries within two steps. ")


def test_the_three_refusals_read_as_the_review_writes_them(client):
    missing = client.get("/api/network/circle/15")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Entry 15 has no acts to draw: there is no such entry, or the entry or its acts are hidden."
    hub = client.get("/api/network/circle/5", params={"hubs": "off"})
    assert hub.status_code == 400
    assert hub.json()["detail"] == "This entry is itself on more than 20 acts; choose “drawn” to draw it."
    assert hub.json()["detail"].startswith("This entry is itself on more than ")  # the page matches these words
    other = client.get("/api/network/circle/6", params={"others": "off"})
    assert other.status_code == 400
    assert other.json()["detail"] == (
        "This entry is itself an institution, estate, shareholder group or placeholder; "
        "untick “Leave out institutions, estates, shareholder groups and placeholders” to draw it."
    )
    assert other.json()["detail"].startswith("This entry is itself an institution")
    assert all(line[0].isupper() and line.endswith(".") for line in (missing.json()["detail"], hub.json()["detail"], other.json()["detail"]))


def _templates():
    for key, value in NETWORK_WORDS.items():
        if isinstance(value, str):
            yield key, re.compile("^" + re.sub(r"\\\{[a-z_0-9]+\\\}", "(.*?)", re.escape(value)) + "$")


def _from_registry(line: str) -> bool:
    return any(pattern.match(line) for _key, pattern in _templates())


@pytest.mark.parametrize("entry,params", [
    (1, {}), (1, {"steps": "1"}), (1, {"hubs": "through"}), (1, {"hubs": "off"}), (1, {"others": "off"}),
    (1, {"widen": "5.7"}), (1, {"hubs": "through", "cap": "50"}), (1, {"widen": "5", "cap": "50", "others": "off"}),
    (5, {}), (16, {"others": "off"}), (100, {"hubs": "off", "steps": "1"}), (40, {}),
], ids=["default", "one-step", "through", "off", "no-others", "widened", "cut", "all",
        "hub-centre", "no-co-signers", "no-co-signers-one-step", "kinds"])
def test_every_how_line_and_the_sentence_and_answer_come_from_the_registry(client, entry, params):
    circle = _circle(client, entry, **params)
    assert _from_registry(circle["sentence"])
    first, standing = circle["answer"].rsplit(". ", 1)
    assert _from_registry(first + ".") and standing == NETWORK_WORDS["standing"]
    for line in circle["how"]:
        assert _from_registry(line), line
    assert all(line[0].isupper() and line.endswith(".") for line in circle["how"])


@pytest.mark.parametrize("entry,params,sentence", [
    ("abc", {}, NETWORK_WORDS["bad_entry"]),
    ("1", {"steps": "3"}, NETWORK_WORDS["bad_steps"]),
    ("1", {"cap": "10"}, NETWORK_WORDS["bad_cap"]),
    ("1", {"cap": "601"}, NETWORK_WORDS["bad_cap"]),
    ("1", {"cap": "many"}, NETWORK_WORDS["bad_cap"]),
    ("1", {"hubs": "maybe"}, NETWORK_WORDS["bad_hubs"]),
    ("1", {"others": "some"}, NETWORK_WORDS["bad_others"]),
    ("1", {"widen": "5.x"}, NETWORK_WORDS["bad_widen"]),
], ids=["entry", "steps", "cap-low", "cap-high", "cap-word", "hubs", "others", "widen"])
def test_a_bad_parameter_is_refused_with_a_plain_sentence(client, entry, params, sentence):
    response = client.get(f"/api/network/circle/{entry}", params=params)
    assert response.status_code == 400 and response.json()["detail"] == sentence


def test_the_endpoint_writes_nothing(client, tmp_path):
    path = tmp_path / "main.db"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    _circle(client, hubs="through", widen="7")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not (tmp_path / "main.db-wal").exists()


# ---- speed: the worst centre on a live-size corpus -------------------------------------------

def _hub_cluster(path: Path) -> int:
    """On top of the Find live-size corpus: a centre on 60 acts with four new
    co-signers each (240 on ring 1), every one of them on three more acts with
    three new co-signers (2,160 on ring 2): wider than entry 11308 (822)."""
    c = sqlite3.connect(path)
    centre = 20001
    persons, contracts, investors, stakes, links = [(centre, "Centro", "Grande", 0)], [], [], [], []
    next_person, next_contract, next_investor = 20002, 6001, 30001
    ring1 = []
    for k in range(60):
        contracts.append((next_contract, f"{1600 + k}-01-01"))
        investors.append((next_investor, centre, next_contract))
        next_investor += 1
        for _ in range(4):
            persons.append((next_person, "Nome", f"Primo{next_person}", 0))
            investors.append((next_investor, next_person, next_contract))
            ring1.append(next_person)
            next_person += 1
            next_investor += 1
        next_contract += 1
    for entry in ring1:
        for j in range(3):
            contracts.append((next_contract, f"{1650 + j}-06-01"))
            investors.append((next_investor, entry, next_contract))
            next_investor += 1
            for _ in range(3):
                persons.append((next_person, "Nome", f"Secondo{next_person}", 0))
                investors.append((next_investor, next_person, next_contract))
                next_person += 1
                next_investor += 1
            next_contract += 1
    for investor_id, _person, contract in investors:
        stakes.append((investor_id, contract, "gp" if investor_id % 2 else "lp", 100))
        links.append((investor_id, investor_id))
    c.executemany("INSERT INTO person (person_id, first_name, last_name, is_woman) VALUES (?,?,?,?)", persons)
    c.executemany("INSERT INTO contract (contract_id, registration_date) VALUES (?,?)", contracts)
    c.executemany("INSERT INTO investor (investor_id, person_id, contract_id) VALUES (?,?,?)", investors)
    c.executemany("INSERT INTO investment (investment_id, contract_id, type, investment_cash) VALUES (?,?,?,?)", stakes)
    c.executemany("INSERT INTO investor_group (investor_id, investment_id) VALUES (?,?)", links)
    c.commit()
    c.close()
    return centre


def test_the_worst_centre_stays_under_half_a_second_on_a_live_size_corpus(tmp_path, monkeypatch):
    path = tmp_path / "live.db"
    _live_size(path)
    centre = _hub_cluster(path)
    monkeypatch.setattr(review_server, "db_path", lambda: path)
    client = TestClient(review_server.app)
    for params in ({}, {"hubs": "through"}, {"steps": "1"}):
        started = time.monotonic()
        circle = _circle(client, centre, **params)
        assert time.monotonic() - started < 0.5, params
    assert circle["counts"]["reached"] == 241 and circle["cut"] is None  # one step
    two = _circle(client, centre)
    assert two["counts"]["reached"] == 2401 and two["cut"] == {"reached": 2401, "drawn": 300}
    assert two["counts"]["acts"] >= 60 and two["counts"]["ties"] >= 240
