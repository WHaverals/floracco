"""DB-intrinsic "Needs review" flags — the data-quality worklist.

Pure SQL over main.db (live rows only, is_deleted = 0); NO Word-pipeline
dependency, so it runs in the pilot. Each flag is a *hypothesis* for a reviewer,
carrying a stable key, a plain-language explanation, and a `fix` descriptor the
UI uses to deep-link to the editor. Flags are computed live (no build file), so
fixing a record removes it on the next load; `dismiss` is only for "reviewed,
not an error".

Scope = Tier 1 (high precision, per-row, fixable in-app). Tier 2 (structural /
backfill: is_joint drift, missing lookup rows 1725/place 116, cruft links,
cross-contract) is maintainer work, handled elsewhere — not here. Tier 3
(firm_name blank, numerical_discrepancy, start>registration, …) is cry-wolf and
deliberately excluded. See docs/data_quality + LOG.md for the grounding.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# Strings that mean "nothing here" but were saved as text. The archive export
# leaked exactly one (`'NULL'` in contract 69's firm name, docs/multi_user_safety.md
# D8); the rest are what a person types when they mean blank. Three places share
# this set so the corpus never holds placeholder text: the importer blanks it at
# seed time, the editors refuse it, and the flag below catches whatever slips by.
PLACEHOLDER_TEXT: frozenset[str] = frozenset({"null", "none", "n/a", "-", "—", "–"})

# Correctable free-text columns the placeholder check sweeps, per table, with the
# fix descriptor the UI needs to open the right editor. Investor columns live on
# the partner row of the contract page, hence the partner_field kind.
PLACEHOLDER_COLUMNS: dict[str, list[str]] = {
    "contract": ["firm_name", "folio", "document"],
    "sub_contract": ["sub_firm_name", "folio", "document"],
    "person": ["first_name", "father_mother", "grandfather", "last_name", "nickname"],
    "investor": ["profession", "husband_first_name", "husband_last_name", "guardian_of"],
}


def is_placeholder_text(value: Any) -> bool:
    """True when a text value is a stand-in for nothing ("NULL", "none", a dash)."""
    if value is None:
        return False
    return str(value).strip().lower() in PLACEHOLDER_TEXT


def _existing_columns(connection: sqlite3.Connection, table: str, wanted: list[str]) -> list[str]:
    """The subset of `wanted` that the table actually has (schemas differ between
    the corpus and test fixtures; a missing column is simply not checked)."""
    try:
        present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return []
    return [col for col in wanted if col in present]


def _placeholder_sql() -> str:
    """SQL predicate for a column holding placeholder text (bind the column name)."""
    tokens = ", ".join("?" for _ in PLACEHOLDER_TEXT)
    return f"LOWER(TRIM(COALESCE({{col}}, ''))) IN ({tokens})"


# group id -> reviewer-facing label, severity, and the one-line explanation.
GROUP_META: dict[str, dict[str, str]] = {
    "placeholder_text": {
        "label": "Placeholder text",
        "severity": "medium",
        "explanation": "A field holds the word NULL (or a dash) where the source has nothing — an export artefact or a slip at the keyboard, not information. Open the field and clear it; if the act does record a value, enter that instead.",
    },
    "broken_economic_sector": {
        "label": "Economic activity not in the list",
        "severity": "high",
        "explanation": "The economic activity points to an entry that no longer exists, so it shows blank. Set it from the act.",
    },
    "missing_reg_date": {
        "label": "Missing registration date",
        "severity": "high",
        "explanation": "No registration date is recorded (shows 0000-00-00). Set it from the act.",
    },
    "no_partners": {
        "label": "No partners recorded",
        "severity": "high",
        "explanation": "This accomandita has no partners entered — it needs at least a general (accomandatario) and a limited (accomandante) partner. Add them from the act.",
    },
    "dup_partner": {
        "label": "Possible duplicate partner",
        "severity": "medium",
        "explanation": "This person appears more than once on the contract without a second distinct stake — either an extra appearance with no investment attached, or two links to the same single investment. Likely entered twice. Check the act; if so, remove the redundant appearance (the stake's capital is unaffected — it's one investment).",
    },
    "person_no_name": {
        "label": "Unnamed person",
        "severity": "high",
        "explanation": "This person has neither a first nor a last name. Add their name from the act.",
    },
    "broken_place": {
        "label": "Place not in the list",
        "severity": "high",
        "explanation": "A residence/origin points to a place entry that no longer exists. Re-point it from the act, or clear it.",
    },
    "guardian_no_ward": {
        "label": "Guardian — ward not recorded",
        "severity": "medium",
        "explanation": "Marked as acting as a guardian, but for whom isn't recorded. Name the ward from the act — or clear the guardian mark if the act shows no guardianship.",
    },
    "no_gp": {
        "label": "No general partner",
        "severity": "medium",
        "explanation": "No accomandatario (general partner) is recorded. Check the act and add or correct a partner's role.",
    },
    "missing_sub_type": {
        "label": "Sub-contract type missing",
        "severity": "medium",
        "explanation": "This sub-contract has no type (balance / renewal / termination / variation). Set it from the act.",
    },
    "orphan_stake": {
        "label": "Stake with no partner",
        "severity": "medium",
        "explanation": "A role and a sum are recorded on this contract without a person attached — left over from data entry, or a partner who was removed. Open the Partners block and attach the right person, or remove the stake if it was entered in error.",
    },
    "widow_not_woman": {
        "label": "Marked widow, not a woman",
        "severity": "medium",
        "explanation": "Recorded as a widow on a contract but not marked as a woman — one of the two is likely wrong. Check the act and fix whichever it is.",
    },
}


def _firm(name: Any, cid: Any) -> str:
    name = (name or "").strip()
    return name or f"Contract {cid}"


def flags(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Run every Tier-1 check and return the flags (un-dismissed filtering is the
    caller's job). Live rows only."""
    out: list[dict[str, Any]] = []

    def add(group: str, table: str, pk: Any, title: str, fix: dict[str, Any], key_suffix: str = "") -> None:
        meta = GROUP_META[group]
        key = f"{table}:{pk}:{group}" + (f":{key_suffix}" if key_suffix else "")
        out.append({
            "key": key, "group": group, "table": table, "pk": str(pk), "title": title,
            "severity": meta["severity"], "explanation": meta["explanation"], "fix": fix,
        })

    # 1. broken economic sector — FK points to a missing activity (incl. the 38 on id 1725)
    for r in connection.execute(
        """SELECT c.contract_id AS cid, c.firm_name AS firm FROM contract c
           WHERE c.is_deleted=0 AND c.economic_sector NOT IN (0) AND c.economic_sector IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM economic_activity e WHERE e.ec_activity_id=c.economic_sector)"""
    ):
        add("broken_economic_sector", "contract", r["cid"], _firm(r["firm"], r["cid"]),
            {"kind": "relink", "field": "economic_sector"})

    # 2. missing registration date — on a *real* record only (has partners or a narrative).
    # Empty-stub contracts (no partners AND no narrative) are not reviewer-fixable from the
    # app; they go to the maintainer integrity report (incomplete_contract), not here.
    for r in connection.execute(
        """SELECT contract_id AS cid, firm_name AS firm FROM contract c
           WHERE c.is_deleted=0 AND (c.registration_date IS NULL OR c.registration_date IN ('0000-00-00',''))
             AND (COALESCE(c.document,'')<>''
                  OR EXISTS (SELECT 1 FROM investor i WHERE i.contract_id=c.contract_id AND i.is_deleted=0))"""
    ):
        add("missing_reg_date", "contract", r["cid"], _firm(r["firm"], r["cid"]),
            {"kind": "edit", "field": "registration_date"})

    # 3. contract with no live partners but a recorded narrative to add them from. A
    # contract with neither partners NOR narrative is an empty stub → integrity report.
    for r in connection.execute(
        """SELECT c.contract_id AS cid, c.firm_name AS firm FROM contract c
           LEFT JOIN (SELECT contract_id FROM investor WHERE is_deleted=0 GROUP BY contract_id) has
             ON has.contract_id=c.contract_id
           WHERE c.is_deleted=0 AND COALESCE(c.document,'')<>'' AND has.contract_id IS NULL"""
    ):
        add("no_partners", "contract", r["cid"], _firm(r["firm"], r["cid"]), {"kind": "add_investor", "field": None})

    # 4. possible duplicate partner — same person+contract, one role, one shared investment, one cash
    for r in connection.execute(
        """WITH d AS (
             SELECT person_id, contract_id FROM investor
             WHERE is_deleted=0 AND person_id NOT IN (0) GROUP BY person_id, contract_id HAVING COUNT(*)>1)
           SELECT i.person_id AS pid, i.contract_id AS cid, c.firm_name AS firm,
                  p.first_name AS fn, p.last_name AS ln,
                  COUNT(DISTINCT v.type) AS roles, COUNT(DISTINCT ig.investment_id) AS invs,
                  COUNT(DISTINCT v.investment_cash) AS cashes
           FROM investor i JOIN d ON d.person_id=i.person_id AND d.contract_id=i.contract_id
           JOIN contract c ON c.contract_id=i.contract_id
           LEFT JOIN person p ON p.person_id=i.person_id
           LEFT JOIN investor_group ig ON ig.investor_id=i.investor_id AND ig.is_deleted=0
           LEFT JOIN investment v ON v.investment_id=ig.investment_id AND v.is_deleted=0
           WHERE i.is_deleted=0
           GROUP BY i.person_id, i.contract_id
           HAVING roles<=1 AND invs<=1 AND cashes<=1"""
    ):
        who = " ".join(x for x in ((r["fn"] or "").strip(), (r["ln"] or "").strip()) if x) or f"person {r['pid']}"
        add("dup_partner", "contract", r["cid"], f"{who} · {_firm(r['firm'], r['cid'])}",
            {"kind": "review_partners", "field": None}, key_suffix=str(r["pid"]))

    # 5. unnamed person — no first AND last, referenced by a live investor (visible)
    for r in connection.execute(
        """SELECT p.person_id AS pid FROM person p
           WHERE p.is_deleted=0 AND COALESCE(p.first_name,'')='' AND COALESCE(p.last_name,'')=''
             AND EXISTS (SELECT 1 FROM investor i WHERE i.person_id=p.person_id AND i.is_deleted=0)"""
    ):
        add("person_no_name", "person", r["pid"], f"Person #{r['pid']} (unnamed)", {"kind": "edit", "field": "last_name"})

    # 6. broken place — residence/origin FK points to a missing place (per investor)
    for r in connection.execute(
        """SELECT i.investor_id AS iid, i.contract_id AS cid, c.firm_name AS firm,
                  p.first_name AS fn, p.last_name AS ln,
                  CASE WHEN i.place_of_residence NOT IN (0) AND NOT EXISTS(SELECT 1 FROM place pl WHERE pl.place_id=i.place_of_residence)
                       THEN 'place_of_residence' ELSE 'place_of_origin' END AS field
           FROM investor i JOIN contract c ON c.contract_id=i.contract_id LEFT JOIN person p ON p.person_id=i.person_id
           WHERE i.is_deleted=0 AND (
             (i.place_of_residence NOT IN (0) AND i.place_of_residence IS NOT NULL AND NOT EXISTS(SELECT 1 FROM place pl WHERE pl.place_id=i.place_of_residence))
             OR (i.place_of_origin NOT IN (0) AND i.place_of_origin IS NOT NULL AND NOT EXISTS(SELECT 1 FROM place pl WHERE pl.place_id=i.place_of_origin)))"""
    ):
        who = " ".join(x for x in ((r["fn"] or "").strip(), (r["ln"] or "").strip()) if x) or f"investor {r['iid']}"
        add("broken_place", "contract", r["cid"], f"{who} · {_firm(r['firm'], r['cid'])}",
            {"kind": "partner_field", "field": r["field"], "investor_id": str(r["iid"])}, key_suffix=str(r["iid"]))

    # 7. guardian with no ward recorded (per investor)
    for r in connection.execute(
        """SELECT i.investor_id AS iid, i.contract_id AS cid, c.firm_name AS firm, p.first_name AS fn, p.last_name AS ln
           FROM investor i JOIN contract c ON c.contract_id=i.contract_id LEFT JOIN person p ON p.person_id=i.person_id
           WHERE i.is_deleted=0 AND i.is_guardian=1 AND COALESCE(i.guardian_of,'')=''"""
    ):
        who = " ".join(x for x in ((r["fn"] or "").strip(), (r["ln"] or "").strip()) if x) or f"investor {r['iid']}"
        add("guardian_no_ward", "contract", r["cid"], f"{who} · {_firm(r['firm'], r['cid'])}",
            {"kind": "partner_field", "field": "guardian_of", "investor_id": str(r["iid"])}, key_suffix=str(r["iid"]))

    # 8. contract with partners but no general partner
    for r in connection.execute(
        """WITH roles AS (
             SELECT i.contract_id AS cid, SUM(CASE WHEN v.type='gp' THEN 1 ELSE 0 END) gp
             FROM investor i JOIN investor_group ig ON ig.investor_id=i.investor_id AND ig.is_deleted=0
             JOIN investment v ON v.investment_id=ig.investment_id AND v.is_deleted=0
             WHERE i.is_deleted=0 GROUP BY i.contract_id)
           SELECT roles.cid AS cid, c.firm_name AS firm FROM roles JOIN contract c ON c.contract_id=roles.cid
           WHERE roles.gp=0 AND c.is_deleted=0"""
    ):
        add("no_gp", "contract", r["cid"], _firm(r["firm"], r["cid"]), {"kind": "review_partners", "field": None})

    # 9. sub-contract missing its type — but NOT a fully-blank placeholder row (no parent,
    # no date, no firm): that is cruft, surfaced in the maintainer integrity report for a
    # human to complete or delete, not a reviewer "set the type" task.
    for r in connection.execute(
        """SELECT contract_id AS scid, sub_firm_name AS firm FROM sub_contract
           WHERE is_deleted=0 AND COALESCE(sub_type,'')=''
             AND NOT (COALESCE(main_contract_id,0)=0 AND COALESCE(registration_date,'') IN ('0000-00-00','')
                      AND COALESCE(sub_firm_name,'')='')"""
    ):
        add("missing_sub_type", "sub_contract", r["scid"], _firm(r["firm"], r["scid"]), {"kind": "edit", "field": "sub_type"})

    # 10. recorded as a widow but the person isn't marked a woman — a human decides
    # which is wrong (we deliberately do NOT auto-backfill this). The fix deep-links
    # to the person's "Recorded as woman" toggle; if instead the widow flag is the
    # error, the reviewer corrects that on the contract.
    for r in connection.execute(
        """SELECT p.person_id AS pid, p.first_name AS fn, p.last_name AS ln
           FROM person p
           JOIN (SELECT DISTINCT person_id FROM investor WHERE is_widow=1 AND is_deleted=0) w
             ON w.person_id=p.person_id
           WHERE p.is_deleted=0 AND p.is_woman=0"""
    ):
        who = " ".join(x for x in ((r["fn"] or "").strip(), (r["ln"] or "").strip()) if x) or f"Person #{r['pid']}"
        add("widow_not_woman", "person", r["pid"], who, {"kind": "edit", "field": "is_woman"})

    # 11. stake with no partner — an investment row nobody is linked to (ten in
    # the seed; the remove-partner cascade also leaves one behind by design).
    # The set of held investments is materialised once: investor_group has no
    # index on investment_id, so a correlated NOT EXISTS here cost 4 s per call.
    for r in connection.execute(
        """SELECT v.investment_id AS iid, v.contract_id AS cid, c.firm_name AS firm, v.type AS role,
                  v.investment_cash AS cash
           FROM investment v JOIN contract c ON c.contract_id=v.contract_id
           LEFT JOIN (SELECT ig.investment_id AS iid FROM investor_group ig
                      JOIN investor i ON i.investor_id=ig.investor_id
                      WHERE ig.is_deleted=0 AND i.is_deleted=0 GROUP BY ig.investment_id) held
             ON held.iid=v.investment_id
           WHERE v.is_deleted=0 AND c.is_deleted=0 AND held.iid IS NULL"""
    ):
        stake = " · ".join(x for x in ((r["role"] or "").strip(), "" if r["cash"] is None else str(r["cash"])) if x)
        add("orphan_stake", "contract", r["cid"], f"{_firm(r['firm'], r['cid'])} · {stake or 'stake'}",
            {"kind": "review_partners", "field": None}, key_suffix=str(r["iid"]))

    # 12. placeholder text — "NULL", "none", a dash — saved where the source has nothing.
    # One flag per (record, column); the fix opens that column's editor so the
    # reviewer clears it (or enters the real value if the act has one).
    tokens = sorted(PLACEHOLDER_TEXT)
    for col in _existing_columns(connection, "contract", PLACEHOLDER_COLUMNS["contract"]):
        for r in connection.execute(
            f"SELECT contract_id AS cid, firm_name AS firm, {col} AS bad FROM contract "
            f"WHERE is_deleted=0 AND {_placeholder_sql().format(col=col)}", tokens
        ):
            add("placeholder_text", "contract", r["cid"], f"{_firm(r['firm'], r['cid'])} · {col} = “{r['bad']}”",
                {"kind": "edit", "field": col}, key_suffix=col)
    for col in _existing_columns(connection, "sub_contract", PLACEHOLDER_COLUMNS["sub_contract"]):
        for r in connection.execute(
            f"SELECT contract_id AS scid, sub_firm_name AS firm, {col} AS bad FROM sub_contract "
            f"WHERE is_deleted=0 AND {_placeholder_sql().format(col=col)}", tokens
        ):
            sub_name = (r["firm"] or "").strip() or f"Sub-contract {r['scid']}"
            add("placeholder_text", "sub_contract", r["scid"], f"{sub_name} · {col} = “{r['bad']}”",
                {"kind": "edit", "field": col}, key_suffix=col)
    for col in _existing_columns(connection, "person", PLACEHOLDER_COLUMNS["person"]):
        for r in connection.execute(
            f"SELECT person_id AS pid, first_name AS fn, last_name AS ln, {col} AS bad FROM person "
            f"WHERE is_deleted=0 AND {_placeholder_sql().format(col=col)}", tokens
        ):
            who = " ".join(x for x in ((r["fn"] or "").strip(), (r["ln"] or "").strip()) if x) or f"Person #{r['pid']}"
            add("placeholder_text", "person", r["pid"], f"{who} · {col} = “{r['bad']}”",
                {"kind": "edit", "field": col}, key_suffix=col)
    for col in _existing_columns(connection, "investor", PLACEHOLDER_COLUMNS["investor"]):
        for r in connection.execute(
            f"""SELECT i.investor_id AS iid, i.contract_id AS cid, c.firm_name AS firm,
                       p.first_name AS fn, p.last_name AS ln, i.{col} AS bad
                FROM investor i JOIN contract c ON c.contract_id=i.contract_id
                LEFT JOIN person p ON p.person_id=i.person_id
                WHERE i.is_deleted=0 AND c.is_deleted=0 AND {_placeholder_sql().format(col='i.' + col)}""",
            tokens,
        ):
            who = " ".join(x for x in ((r["fn"] or "").strip(), (r["ln"] or "").strip()) if x) or f"investor {r['iid']}"
            add("placeholder_text", "contract", r["cid"], f"{who} · {_firm(r['firm'], r['cid'])} · {col} = “{r['bad']}”",
                {"kind": "partner_field", "field": col, "investor_id": str(r["iid"])}, key_suffix=f"{r['iid']}:{col}")

    return out
