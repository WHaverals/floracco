"""READ-ONLY audit of the Word↔DB conflict candidates, for designing the curated
"Word cross-check" signal. Writes nothing to the database; emits an audit table +
a precision funnel so design decisions rest on real numbers, not guesses.

Scope (LOCKED 2026-06-22): Word↔DB *conflicts* only (date / folio / type / register).
DB-intrinsic families (firm/numerical/orphan/no_name/sub_type/date_missing) are out —
superseded by data_quality or opaque. See docs/word_cross_check/scope.md.

    uv run python -m workflows.word_cross_check_audit
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workflows"))
import correction_candidates as cc  # the date parser + paths

DERIVED = ROOT / "data/derived/word-pipeline"
CANDS = DERIVED / "10_corrections/correction_candidates.jsonl"
LINKS = DERIVED / "05_db_candidate_matches/source_entry_db_link_candidates.jsonl"
OUT = DERIVED / "10_corrections/word_cross_check_audit.jsonl"

CONFLICT_CODES = {
    "registration_date_differs", "folio_differs",
    "event_type_table_differs", "db_register_differs", "db_register_missing",
}
# Transcriber apparatus that means they ALREADY adjudicated the DATE → don't flag.
# Deliberately specific: NOT the ubiquitous "[Nuova]" label bracket, NOT the bare
# word "ma", NOT the stile-fiorentino year slash "1503/04" (the parser handles it).
_MONTHS = "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
APPARATUS = re.compile(
    r"\bsic\b"                                   # explicit transcription-error marker
    r"|senza data"                               # "[senza data, dunque inserito come …]"
    r"|cassat|depennat|recte|cio[eè]\b|ovvero"   # struck/erased/"rather"
    r"|\([^)]*\b\d{4}\b[^)]*\b(?:sic|ma|recte)\b[^)]*\)"  # "(sic, ma 1745)" parenthetical
    rf"|\b\d{{1,2}}/\d{{1,2}}\s+(?:{_MONTHS})",   # ambiguous day range "21/29 ottobre"
    re.IGNORECASE,
)


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def best_link_by_row(links: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for r in links:
        rid = r.get("db_row_id")
        if not rid:
            continue
        score = float(r.get("score") or 0)
        if rid not in best or score > float(best[rid].get("score") or 0):
            best[rid] = r
    return best


def day_gap(db_iso: str, word_iso: str | None) -> int | None:
    if not word_iso:
        return None
    try:
        return abs((date.fromisoformat(db_iso) - date.fromisoformat(word_iso)).days)
    except ValueError:
        return None


def audit() -> list[dict]:
    cands = [c for c in _load(CANDS) if c["reason_code"] in CONFLICT_CODES]
    links = best_link_by_row(_load(LINKS))
    rows = []
    for c in cands:
        rid = c["db_row_id"]
        link = links.get(rid, {})
        snippet = (c.get("evidence_snippet") or "")
        cid = rid.split(":", 1)[1]
        word_iso = cc.modern_registration_iso(c.get("word_value")) if c["reason_code"] == "registration_date_differs" else None
        rows.append({
            "db_row_id": rid,
            "reason": c["reason_code"],
            "db_value": c.get("db_value"),
            "word_value": c.get("word_value"),
            "word_iso": word_iso,
            "day_gap": day_gap(str(c.get("db_value") or ""), word_iso),
            # link-quality signals (automatic; human link_confirmed is 0 everywhere)
            "id_in_text": bool(re.search(rf"\b{re.escape(cid)}\b", snippet[:90])),
            "token_coverage": round(float(link.get("db_token_coverage_in_word") or 0), 3),
            "n_field_overlap": len(link.get("field_overlap") or []),
            "event_type_relation": link.get("event_type_relation"),
            "score": float(link.get("score") or 0),
            # apparatus + tracked change
            "has_apparatus": bool(APPARATUS.search(snippet[:160])),
            "tracked_change": bool(c.get("revision_evidence")),
        })
    return rows


def funnel(rows: list[dict]) -> None:
    date_rows = [r for r in rows if r["reason"] == "registration_date_differs"]
    reliable = lambda r: r["id_in_text"] or r["token_coverage"] >= 0.6 or r["n_field_overlap"] >= 3
    clean = lambda r: r["word_iso"] and r["day_gap"] is not None
    no_app = lambda r: not r["has_apparatus"]

    print("=== population by reason ===")
    from collections import Counter
    for k, v in Counter(r["reason"] for r in rows).most_common():
        print(f"  {v:4}  {k}")
    print(f"\n=== DATE-CONFLICT FUNNEL (of {len(date_rows)} registration_date_differs) ===")
    g_link = [r for r in date_rows if reliable(r)]
    g_clean = [r for r in g_link if clean(r)]
    g_noapp = [r for r in g_clean if no_app(r)]
    g_track = [r for r in g_noapp if r["tracked_change"]]
    g_small = [r for r in g_noapp if (r["day_gap"] or 99) <= 7]
    print(f"  reliable link (id-in-text / coverage>=.6 / >=3 overlap): {len(g_link)}")
    print(f"  + parseable, genuinely differs:                          {len(g_clean)}")
    print(f"  + no transcriber apparatus (sic/ma/?/[):                 {len(g_noapp)}  <-- curated candidates")
    print(f"      of which tracked-change-backed (highest conf):       {len(g_track)}")
    print(f"      of which small gap <=7d (no tracked change):         {len([r for r in g_small if not r['tracked_change']])}")
    print(f"  human-confirmed links across ALL conflicts:              {sum(1 for r in rows if r.get('link_confirmed'))}")


if __name__ == "__main__":
    rows = audit()
    OUT.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    funnel(rows)
    print(f"\naudit table → {OUT.relative_to(ROOT)} ({len(rows)} rows)")
