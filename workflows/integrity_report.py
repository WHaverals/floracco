"""Tier-2 structural integrity report — READ-ONLY.

Surfaces the structural faults a *maintainer* repairs in a controlled rebuild: the
broken internal links and orphan rows a reviewer can't fix from reading an act.
It is **not** the in-app reviewer queue (see ``workflows/data_quality.py`` for the
"Needs review" flags), and it **never writes** to the database.

Two buckets (see ``docs/data_quality/integrity.md``):
  * B — visible in the app but not reviewer-fixable (cross-contract links, orphan
    amending acts).
  * C — invisible: the broken record can't even be opened (dangling junction rows,
    rows on a phantom contract).

Excluded on purpose: broken economic sector / investor place / empty contract
place-links go to the reviewer queue; the ``economic_activity 1725`` / ``place 116``
holes are unrestorable and routed through the queue's relink path.

Run:
    uv run python -m workflows.integrity_report
"""

from __future__ import annotations

import sqlite3
from typing import Any

from workflows.review_server import open_db

# (issue key, bucket, one-line meaning, recommended repair, SQL → rows of (ref, detail))
CHECKS: list[dict[str, Any]] = [
    {
        "key": "cross_contract_link",
        "bucket": "B",
        "meaning": "Investor on one contract is wired to a *different* contract's capital.",
        "repair": "Almost always an entry slip onto a neighbouring contract — re-point to the correct investment on the investor's own contract (or remove + re-add the partner). Confirm against the act.",
        "sql": """
            SELECT 'investor ' || ig.investor_id AS ref,
                   'on contract ' || i.contract_id || ', but investment ' || ig.investment_id
                     || ' belongs to contract ' || v.contract_id
                     || ' (' || v.type || ' / ' || v.investment_cash || ')' AS detail
            FROM investor_group ig
            JOIN investor i ON i.investor_id = ig.investor_id
            JOIN investment v ON v.investment_id = ig.investment_id
            WHERE ig.is_deleted = 0 AND i.contract_id <> v.contract_id
            ORDER BY ig.investor_id""",
    },
    {
        "key": "subcontract_missing_parent",
        "bucket": "B",
        "meaning": "An amending act (termination/variation) whose parent contract is missing.",
        "repair": "Find the foundational accomandita (the main_contract_id may be wrong), re-point it, or delete the row if it's a placeholder.",
        "sql": """
            SELECT 'sub_contract ' || s.contract_id AS ref,
                   COALESCE(NULLIF(s.sub_type, ''), '(no type)')
                     || ' -> missing parent contract ' || s.main_contract_id AS detail
            FROM sub_contract s
            WHERE s.is_deleted = 0
              AND NOT EXISTS (SELECT 1 FROM contract c WHERE c.contract_id = s.main_contract_id)
            ORDER BY s.contract_id""",
    },
    {
        "key": "orphan_link_missing_investor",
        "bucket": "C",
        "meaning": "A junction row pointing at an investor that doesn't exist.",
        "repair": "Cruft — delete the dangling investor_group row in the rebuild.",
        "sql": """
            SELECT 'investor_group(inv ' || ig.investor_id || ', invm ' || ig.investment_id || ')' AS ref,
                   'no investor row ' || ig.investor_id AS detail
            FROM investor_group ig
            WHERE ig.is_deleted = 0
              AND NOT EXISTS (SELECT 1 FROM investor i WHERE i.investor_id = ig.investor_id)
            ORDER BY ig.investor_id""",
    },
    {
        "key": "orphan_link_missing_investment",
        "bucket": "C",
        "meaning": "A junction row pointing at an investment that doesn't exist.",
        "repair": "Cruft — delete the dangling investor_group row in the rebuild.",
        "sql": """
            SELECT 'investor_group(inv ' || ig.investor_id || ', invm ' || ig.investment_id || ')' AS ref,
                   'no investment row ' || ig.investment_id AS detail
            FROM investor_group ig
            WHERE ig.is_deleted = 0
              AND NOT EXISTS (SELECT 1 FROM investment v WHERE v.investment_id = ig.investment_id)
            ORDER BY ig.investor_id""",
    },
    {
        "key": "investment_missing_contract",
        "bucket": "C",
        "meaning": "A capital row attached to a contract that doesn't exist.",
        "repair": "Re-attach to the correct contract, or delete if it's an orphan.",
        "sql": """
            SELECT 'investment ' || v.investment_id AS ref,
                   v.type || ' / cash ' || v.investment_cash || ' -> missing contract ' || v.contract_id AS detail
            FROM investment v
            WHERE v.is_deleted = 0
              AND NOT EXISTS (SELECT 1 FROM contract c WHERE c.contract_id = v.contract_id)
            ORDER BY v.investment_id""",
    },
    {
        "key": "contract_place_missing_contract",
        "bucket": "C",
        "meaning": "A place-link attached to a contract that doesn't exist.",
        "repair": "Cruft — delete the dangling contract_place row in the rebuild.",
        "sql": """
            SELECT 'contract_place(place ' || cp.place_id || ', contract ' || cp.contract_id || ')' AS ref,
                   'missing contract ' || cp.contract_id AS detail
            FROM contract_place cp
            WHERE cp.is_deleted = 0
              AND NOT EXISTS (SELECT 1 FROM contract c WHERE c.contract_id = cp.contract_id)
            ORDER BY cp.contract_id""",
    },
]


def collect(connection: sqlite3.Connection, examples: int | None = None) -> list[dict[str, Any]]:
    """Run every check (read-only) and return [{key, bucket, meaning, repair, count, rows}].
    `examples` caps the rows kept per check (None = all)."""
    out: list[dict[str, Any]] = []
    for chk in CHECKS:
        rows = [(r["ref"], r["detail"]) for r in connection.execute(chk["sql"])]
        out.append({
            "key": chk["key"], "bucket": chk["bucket"], "meaning": chk["meaning"],
            "repair": chk["repair"], "count": len(rows),
            "rows": rows if examples is None else rows[:examples],
        })
    return out


def render(results: list[dict[str, Any]]) -> str:
    lines = ["Tier-2 structural integrity report (read-only — nothing is changed)", ""]
    total = sum(r["count"] for r in results)
    for r in sorted(results, key=lambda x: (x["bucket"], x["key"])):
        if not r["count"]:
            continue
        lines.append(f"[{r['bucket']}] {r['key']} — {r['count']}")
        lines.append(f"     {r['meaning']}")
        lines.append(f"     fix: {r['repair']}")
        for ref, detail in r["rows"]:
            lines.append(f"       · {ref}: {detail}")
        lines.append("")
    clean = [r["key"] for r in results if not r["count"]]
    if clean:
        lines.append("clean (0): " + ", ".join(clean))
    lines.append("")
    lines.append(f"TOTAL structural faults: {total}")
    lines.append("(B = visible in app, not reviewer-fixable · C = invisible cruft)")
    return "\n".join(lines)


def main() -> None:
    connection = open_db()
    try:
        print(render(collect(connection)))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
