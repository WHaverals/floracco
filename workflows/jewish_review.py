"""Jewish-attribution review — the curation lanes for the two provenance layers.

The investor table carries two provenance-separated Jewish flags: ``is_jewish``
(stated in the act) and ``jewish_db`` (the 2010s data-entry team's editorial
attribution, basis never recorded per row). This module surfaces the places
where the two layers and the narratives disagree, as "Needs review" flags —
computed live, is_deleted-filtered, nothing auto-applied. A reviewer resolves
each case through the normal audited editors (set ``is_jewish`` when the text
attests; the queue's refute action for an erroneous ``jewish_db``).

Investigation, lane decisions, and the full evidence trail:
docs/data_quality/jewish_db.md.

Lane sizes at the 2026-08 seed: text-attests 75 · person-drift 20 ·
stated-unattested 2 · suspect attributions 3 · convert suspects 2 · name fix 1.
Curated entries below carry their own evidence and are guarded by runtime row
checks, so a resolved case drops out of the worklist on the next load.
"""

from __future__ import annotations

import sqlite3
from typing import Any

GROUP_META: dict[str, dict[str, str]] = {
    "jewish_text_attests": {
        "label": "Jewish — text attests, flag is editorial only",
        "severity": "medium",
        "explanation": (
            "The contract's narrative (or a later act's) says “ebre…”, but this investor carries "
            "only the 2010s editorial attribution. Read the narrative — if it names this person as "
            "Jewish, set “Jewish — stated in the act”. The editorial mark stays either way."
        ),
    },
    "jewish_person_drift": {
        "label": "Jewish — same person, inconsistent flags",
        "severity": "medium",
        "explanation": (
            "This person's appearances disagree: flagged Jewish on some contracts and unflagged "
            "(or split between stated and editorial) on others. Review the appearances together "
            "and reconcile them against the narratives."
        ),
    },
    "jewish_stated_unattested": {
        "label": "Jewish — stated flag without textual attestation",
        "severity": "low",
        "explanation": (
            "“Stated in the act” is set, but no narrative on the contract or its later acts "
            "contains “ebre…”. Verify the flag against the text and the manuscript."
        ),
    },
    "jewish_suspect_attribution": {
        "label": "Jewish — editorial attribution looks erroneous",
        "severity": "medium",
        "explanation": (
            "The 2010s editorial attribution conflicts with the evidence (see the note on the "
            "flagged record). If you judge it wrong, use the refute action on the partner's "
            "“Jewish — editorial attribution” field — audited, reversible, reason required."
        ),
    },
    "jewish_convert_suspect": {
        "label": "Convert flag looks like a slipped tick",
        "severity": "low",
        "explanation": (
            "“Recorded as convert” is set with no supporting language anywhere in the corpus, on a "
            "contract whose other partners all carry the editorial Jewish attribution — likely a "
            "data-entry slip. Verify against the narrative; the field is editable in place."
        ),
    },
    "jewish_name_normalization": {
        "label": "Name normalized away from the document",
        "severity": "low",
        "explanation": (
            "The stored given name differs from the document's reading in a way that changes its "
            "signal (e.g. Sephardic “Franco” stored as “Francesco”). Correct the name from the act."
        ),
    },
    "jewish_conjecture": {
        "label": "Jewish — name-based conjecture (no textual attestation)",
        "severity": "low",
        "explanation": (
            "Machine-suggested from name and context only — the documents do not state it, and "
            "each item names its basis. Set “stated in the act” ONLY if you find textual "
            "attestation on the record; otherwise dismiss. Confirming without attestation would "
            "repeat the 2010s unrecorded-basis mistake this review exists to untangle."
        ),
    },
}

# Group 8 of the queue design: ten unflagged investors whose names/context suggest
# Jewish identity. Pure conjecture (no textual attestation) — kept OFF from the
# 2026-07 build until enabled on WH's instruction, 2026-08-27 (see
# docs/data_quality/jewish_db.md and the decisions register). Flip back to False
# to withdraw the lane; nothing else changes.
CONJECTURE_ENABLED = True

# (investor_id, contract_id, evidence) — see jewish_db.md "Lane D" for the ranking.
CONJECTURE: list[tuple[int, int, str]] = [
    (20006, 5103, "Samuel Vita Servi — same person carries the editorial flag on two other contracts"),
    (3657, 1346, "Abramo Rodriguez Miranda — Rodrigues Miranda family flagged elsewhere"),
    (16591, 4760, "Joseph di Graziadio Castelli — Castelli: 11 flagged appearances"),
    (16490, 4727, "David Lopes Perrera — firm ‘Sabato Orvieto et David Lopes Perrera’"),
    (14789, 4318, "Leone Azzuelos — Sephardic surname; scritta made in Livorno"),
    (15529, 4520, "Jacob Berger — resident in Livorno"),
    (16461, 4719, "Salomone Brunner — resident in Livorno"),
    (3797, 1400, "Isach Genet — distinctive given-name spelling"),
    (636, 290, "Joseph della Seta — Pisan Jewish surname (early date, weaker)"),
    (18058, 4853, "David Gide — co-invests with three flagged Finzi on the same contract"),
]

# Curated suspect editorial attributions (Lane E): contract 2646's three investors
# are northern-European merchant houses (Ahrenz of Constantinople, Friez & Co. of
# Vienna, Otto Franck & Co. of Livorno) with no Jewish attestation anywhere; Otto
# Franck is unflagged on his other two contracts. Runtime guard: the flag only
# fires while jewish_db=1 still stands, so a refutation clears it from the list.
SUSPECT_ATTRIBUTIONS: list[tuple[int, int, str]] = [
    (8052, 2646, "Giovanni David Ahrenz “d'Albona dimorante in Costantinopoli”"),
    (8053, 2646, "Friez e compagni di Vienna"),
    (8054, 2646, "Otto Franck e compagni di Livorno — unflagged on contracts 2657 and 3135"),
]

# Curated convert-flag suspects: both sit on contract 4850 among six editorially
# attributed partners; “neofito/convertito” (religious sense) appears nowhere in
# the corpus. Guard: fires only while is_convert=1.
CONVERT_SUSPECTS: list[tuple[int, int, str]] = [
    (18098, 4850, "Sahadun — is_convert with no supporting text; co-partners all carry the editorial flag"),
    (18099, 4850, "Castelli — same pattern as the co-flagged Sahadun row"),
]

# Curated name normalization: the document reads “Jacob Franco de Miranda di
# Livorno”; the person row stores the Christian given form “Francesco”.
# Guard: fires only while the stored name still contains “Francesco”.
NAME_NORMALIZATION: list[tuple[int, str]] = [
    (3255, "document (contract 1532) reads “Jacob Franco de Miranda”, stored as “Jacob Francesco”"),
]

# A contract's text "attests" when its own narrative or any live later act's says
# “ebre…” (ebreo/ebrei/hebreo — the only religious vocabulary in the corpus).
_ATTESTS = (
    "(c.document LIKE '%ebre%' OR EXISTS ("
    "SELECT 1 FROM sub_contract s WHERE s.main_contract_id = c.contract_id "
    "AND s.is_deleted = 0 AND s.document LIKE '%ebre%'))"
)


def _who(first: Any, last: Any, fallback: str) -> str:
    name = " ".join(x for x in (str(first or "").strip(), str(last or "").strip()) if x)
    return name or fallback


def flags(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every lane's open items, computed live. Same shape as data_quality.flags."""
    out: list[dict[str, Any]] = []

    def add(group: str, table: str, pk: Any, title: str, fix: dict[str, Any], key_suffix: str = "") -> None:
        meta = GROUP_META[group]
        key = f"{table}:{pk}:{group}" + (f":{key_suffix}" if key_suffix else "")
        out.append({
            "key": key, "group": group, "table": table, "pk": str(pk), "title": title,
            "severity": meta["severity"], "explanation": meta["explanation"], "fix": fix,
        })

    # Lane A — narrative attests, investor carries only the editorial flag.
    for r in connection.execute(
        f"""SELECT i.investor_id AS iid, i.contract_id AS cid, c.firm_name AS firm,
                   p.first_name AS fn, p.last_name AS ln
            FROM investor i JOIN contract c ON c.contract_id = i.contract_id
            LEFT JOIN person p ON p.person_id = i.person_id
            WHERE i.is_deleted = 0 AND c.is_deleted = 0
              AND i.jewish_db = 1 AND i.is_jewish = 0 AND {_ATTESTS}"""
    ):
        who = _who(r["fn"], r["ln"], f"investor {r['iid']}")
        firm = (r["firm"] or "").strip() or f"Contract {r['cid']}"
        add("jewish_text_attests", "contract", r["cid"], f"{who} · {firm}",
            {"kind": "partner_field", "field": "is_jewish", "investor_id": str(r["iid"])},
            key_suffix=str(r["iid"]))

    # Lane C — the same person flagged inconsistently across live appearances.
    # Two drift shapes: flagged-on-some/unflagged-on-others, and a split between
    # the stated and the editorial layer. A row with BOTH flags (a confirmed
    # attribution) is consistent, not drift — the conditions below respect that.
    for r in connection.execute(
        """SELECT p.person_id AS pid, p.first_name AS fn, p.last_name AS ln,
                  COUNT(*) AS n,
                  SUM(CASE WHEN i.is_jewish = 1 OR i.jewish_db = 1 THEN 1 ELSE 0 END) AS flagged
           FROM investor i JOIN person p ON p.person_id = i.person_id
           WHERE i.is_deleted = 0 AND p.is_deleted = 0
           GROUP BY i.person_id
           HAVING COUNT(*) >= 2 AND (
             (flagged > 0 AND flagged < COUNT(*))
             OR (SUM(CASE WHEN i.is_jewish = 1 AND i.jewish_db = 0 THEN 1 ELSE 0 END) > 0
                 AND SUM(CASE WHEN i.jewish_db = 1 AND i.is_jewish = 0 THEN 1 ELSE 0 END) > 0)
           )"""
    ):
        who = _who(r["fn"], r["ln"], f"Person #{r['pid']}")
        add("jewish_person_drift", "person", r["pid"],
            f"{who} — flagged on {r['flagged']} of {r['n']} appearances",
            {"kind": "person_flags", "field": None})

    # Lane 6 — the stated flag with no textual attestation anywhere on the contract.
    for r in connection.execute(
        f"""SELECT i.investor_id AS iid, i.contract_id AS cid, c.firm_name AS firm,
                   p.first_name AS fn, p.last_name AS ln
            FROM investor i JOIN contract c ON c.contract_id = i.contract_id
            LEFT JOIN person p ON p.person_id = i.person_id
            WHERE i.is_deleted = 0 AND c.is_deleted = 0
              AND i.is_jewish = 1 AND NOT {_ATTESTS}"""
    ):
        who = _who(r["fn"], r["ln"], f"investor {r['iid']}")
        firm = (r["firm"] or "").strip() or f"Contract {r['cid']}"
        add("jewish_stated_unattested", "contract", r["cid"], f"{who} · {firm}",
            {"kind": "partner_field", "field": "is_jewish", "investor_id": str(r["iid"])},
            key_suffix=str(r["iid"]))

    # Lane E — curated suspects, self-cleaning via runtime guards.
    for iid, cid, evidence in SUSPECT_ATTRIBUTIONS:
        row = connection.execute(
            "SELECT jewish_db FROM investor WHERE investor_id = ? AND is_deleted = 0", (iid,)
        ).fetchone()
        if row and row["jewish_db"] in (1, "1"):
            add("jewish_suspect_attribution", "contract", cid, evidence,
                {"kind": "partner_field", "field": "jewish_db", "investor_id": str(iid)},
                key_suffix=str(iid))

    for iid, cid, evidence in CONVERT_SUSPECTS:
        row = connection.execute(
            "SELECT is_convert FROM investor WHERE investor_id = ? AND is_deleted = 0", (iid,)
        ).fetchone()
        if row and row["is_convert"] in (1, "1"):
            add("jewish_convert_suspect", "contract", cid, evidence,
                {"kind": "partner_field", "field": "is_convert", "investor_id": str(iid)},
                key_suffix=str(iid))

    for pid, evidence in NAME_NORMALIZATION:
        row = connection.execute(
            "SELECT first_name FROM person WHERE person_id = ? AND is_deleted = 0", (pid,)
        ).fetchone()
        if row and "francesco" in (row["first_name"] or "").lower():
            add("jewish_name_normalization", "person", pid, evidence,
                {"kind": "edit", "field": "first_name"})

    if CONJECTURE_ENABLED:
        for iid, cid, evidence in CONJECTURE:
            row = connection.execute(
                "SELECT is_jewish, jewish_db FROM investor WHERE investor_id = ? AND is_deleted = 0", (iid,)
            ).fetchone()
            if row and row["is_jewish"] not in (1, "1") and row["jewish_db"] not in (1, "1"):
                add("jewish_conjecture", "contract", cid, evidence,
                    {"kind": "partner_field", "field": "is_jewish", "investor_id": str(iid)},
                    key_suffix=str(iid))

    return out
