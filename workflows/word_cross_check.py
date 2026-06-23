"""Curated Word↔DB registration-date cross-check (READ-ONLY).

For each contract / sub_contract whose registration date disagrees with its linked
Word transcription, assemble a *candidate* a reviewer can verify against the manuscript.
This module never writes, and never decides who is right — it surfaces evidence only.
The reviewer adjudicates against the folio image; the database is corrected (if at all)
through the normal logged edit path.

Grounded in docs/word_cross_check/scope.md (§10–§13). Gates:
  A  reliable link  — Word entry & DB row share >= 2 person names (matcher
                      `field_overlap_count`); guards against comparing the wrong records.
  B  real diff      — both dates parse (stile-fiorentino aware) and differ on the LIVE DB
                      value (so a date already fixed in the DB drops off — self-healing).
  C  no apparatus   — the Word date context carries no `sic` / `ma <year>` / day-range /
                      `?` (the transcriber already adjudicated; the DB is usually right).
Tier:  tracked_change (T1) > clear >=2-day gap (T2) > one_day (T3).
Only T1 + T2 are *surfaced*; T3 is computed but withheld (single-minim ambiguity).

Reads the Word-pipeline derived files from the mounted data dir (FLORACCO_DATA_DIR on
Render; ./data in dev) and the live registration_date from the given DB connection.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

# Reuse the *tested* helpers (calendar-aware date parsing + revision spans) rather than
# duplicating them; we deliberately do NOT use this module's path constants.
from workflows.correction_candidates import (
    DATE_SPAN_RE,
    modern_registration_iso,
    revision_spans,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("FLORACCO_DATA_DIR") or (PROJECT_ROOT / "data")).expanduser().resolve()
DERIVED = DATA_ROOT / "derived/word-pipeline"
LINKS_PATH = DERIVED / "05_db_candidate_matches/source_entry_db_link_candidates.jsonl"
ENTRIES_PATH = DERIVED / "04_source_entries/source_entries.jsonl"
IMAGES_PATH = DERIVED / "07_image_links/source_entry_image_candidates.jsonl"

CONFLICT_CODE = "registration_date_differs"
MIN_SHARED_NAMES = 2  # Gate A

_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# Apparatus markers in the *date context* (head line + the raw Word date), NOT the body
# — entry bodies routinely carry editorial brackets like "[ha dato e messo]".
_APPARATUS_RE = re.compile(
    r"\bsic\b|senza data|\bma\s+1[4-8]\d{2}"
    r"|\d{1,2}\s*/\s*\d{1,2}\s+(?:genn|febb|marz|apr|magg|giug|lugl|agos|sett|otto|nove|dice)",
    re.IGNORECASE,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _best_links(links: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Highest-scoring link per DB row that carries the registration-date conflict."""
    best: dict[str, dict[str, Any]] = {}
    for row in links:
        conflicts = row.get("conflicts") or []
        if isinstance(conflicts, str):
            conflicts = [conflicts]
        if CONFLICT_CODE not in conflicts:
            continue
        rid = row.get("db_row_id")
        if not rid:
            continue
        if rid not in best or float(row.get("score") or 0) > float(best[rid].get("score") or 0):
            best[rid] = row
    return best


def _iso_display(iso: str | None) -> str | None:
    """1534-03-20 -> '20 Mar 1534' (calendar-normalized; stile fiorentino already resolved)."""
    if not iso or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        return None
    y, m, d = iso.split("-")
    mi = int(m)
    return f"{int(d)} {_MONTH_ABBR[mi]} {y}" if 1 <= mi <= 12 else None


def _has_apparatus(head_text: str | None, word_raw: str | None) -> bool:
    ctx = f"{head_text or ''} {word_raw or ''}"
    if _APPARATUS_RE.search(ctx):
        return True
    return "?" in (head_text or "")


def _revision_for_date(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The tracked-change (deletion -> insertion) that touches a *date*, if any → T1 evidence.
    Keep only date-bearing spans (a full date match, or a short numeric edit like "2"→"8"),
    so the lens shows the date change — not unrelated name/spelling edits in the same entry."""
    if not entry.get("has_revisions"):
        return None
    spans = revision_spans(entry.get("revision_aware_text"))
    if not spans or not any(DATE_SPAN_RE.search(s["text"]) for s in spans):
        return None

    def date_relevant(text: str) -> bool:
        return bool(DATE_SPAN_RE.search(text)) or (len(text) <= 12 and any(c.isdigit() for c in text))

    removed = [s["text"][:60] for s in spans if s["kind"] == "deletion" and date_relevant(s["text"])]
    added = [s["text"][:60] for s in spans if s["kind"] == "insertion" and date_relevant(s["text"])]
    if not (removed or added):
        return None
    author = next((s.get("author") for s in spans if s.get("author")), None)
    return {"removed": removed[:4], "added": added[:4], "author": author}


def _page_side(folio_start: Any, folio_raw: Any) -> str | None:
    """recto / verso from the folio reference — tells the UI which spread page to open."""
    txt = str(folio_start or folio_raw or "").lower()
    if re.search(r"\d\s*v\b", txt):
        return "verso"
    if re.search(r"\d\s*r\b", txt):
        return "recto"
    return None


def _adjacent_images(primary: str | None) -> dict[str, str | None]:
    """Primary image + the previous/next leaf (dual-numbering off-by-one safety net)."""
    out: dict[str, str | None] = {"primary": primary, "prev": None, "next": None}
    if not primary:
        return out
    m = re.search(r"(.*_)(\d+)(\.jpg)$", primary)
    if not m:
        return out
    head, num, ext = m.group(1), m.group(2), m.group(3)
    width = len(num)
    n = int(num)
    for key, delta in (("prev", -1), ("next", 1)):
        if n + delta >= 0:
            cand = f"{head}{str(n + delta).zfill(width)}{ext}"
            if Path(cand).exists():
                out[key] = cand
    return out


def _tier(revision: dict[str, Any] | None, gap_days: int) -> str:
    if revision is not None:
        return "tracked_change"
    return "clear" if gap_days >= 2 else "one_day"


# mtime-aware cache of the parsed derived files (static on the disk), so per-record
# loads don't re-parse thousands of lines on every request.
_REF_CACHE: dict[tuple[str, str, str], tuple[Any, dict[str, dict[str, Any]]]] = {}


def _reference(links_path: Path, entries_path: Path, images_path: Path):
    key = (str(links_path), str(entries_path), str(images_path))
    try:
        sig = tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in (links_path, entries_path, images_path))
    except OSError:
        sig = None
    cached = _REF_CACHE.get(key)
    if cached and cached[0] == sig:
        return cached[1]
    links = _best_links(_load_jsonl(links_path))
    entries = {e["source_entry_id"]: e for e in _load_jsonl(entries_path)}
    images: dict[str, list[str]] = {}
    for row in _load_jsonl(images_path):
        eid = row.get("source_entry_id")
        if eid and row.get("image_path"):
            images.setdefault(eid, []).append(row["image_path"])
    ref = {"links": links, "entries": entries, "images": images}
    _REF_CACHE[key] = (sig, ref)
    return ref


def _package(rid: str, link: dict[str, Any], entry: dict[str, Any], images: list[str], live_date: str) -> dict[str, Any] | None:
    """Apply gates A–C to one row's link + entry + live DB date → a check, or None.
    Shared by `build_checks` (all rows) and `check_for` (one row)."""
    # Gate A — reliable link (>= 2 shared person names).
    if int(link.get("field_overlap_count") or 0) < MIN_SHARED_NAMES:
        return None
    table = rid.partition(":")[0]
    if table not in ("contract", "sub_contract"):
        return None
    # Gate B — live DB date parses, Word date parses, and they differ.
    db_val = (live_date or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", db_val):  # missing/0000 is a different flag
        return None
    word_raw = (link.get("entry_registration_date_raw") or "").strip()
    word_iso = modern_registration_iso(word_raw)
    if not word_iso or word_iso == db_val:
        return None
    # Gate C — no transcriber apparatus in the date context.
    if _has_apparatus(entry.get("head_text"), word_raw):
        return None
    gap = abs((date.fromisoformat(db_val) - date.fromisoformat(word_iso)).days)
    revision = _revision_for_date(entry)
    tier = _tier(revision, gap)
    return {
        "db_row_id": rid,
        "table": table,
        "field": "registration_date",
        "tier": tier,
        "surfaced": tier in ("tracked_change", "clear"),
        "db_value": db_val,
        "db_display": _iso_display(db_val),
        "word_iso": word_iso,
        "word_display": _iso_display(word_iso),
        "word_raw": word_raw,
        "gap_days": gap,
        "revision": revision,
        "source_entry_id": link.get("source_entry_id"),
        "field_overlap_count": int(link.get("field_overlap_count") or 0),
        "folio": (link.get("entry_folio_start") or entry.get("folio_raw") or None),
        "page_side": _page_side(link.get("entry_folio_start"), entry.get("folio_raw")),
        "images": _adjacent_images(images[0] if images else None),
    }


def _live_date(connection: sqlite3.Connection, table: str, raw_id: str) -> str:
    row = connection.execute(
        f"SELECT registration_date FROM {table} WHERE contract_id = ?", (raw_id,)
    ).fetchone()
    return (row[0] if row else None) or ""


def build_checks(
    connection: sqlite3.Connection,
    *,
    links_path: Path = LINKS_PATH,
    entries_path: Path = ENTRIES_PATH,
    images_path: Path = IMAGES_PATH,
) -> dict[str, dict[str, Any]]:
    """Return {db_row_id: check} for every live registration-date conflict passing gates A–C.
    `tier` ∈ tracked_change / clear / one_day; `surfaced` True for the first two. Read-only:
    the live DB value decides whether a conflict still exists (self-healing)."""
    ref = _reference(links_path, entries_path, images_path)
    out: dict[str, dict[str, Any]] = {}
    for rid, link in ref["links"].items():
        table, _, raw_id = rid.partition(":")
        if table not in ("contract", "sub_contract"):
            continue
        entry = ref["entries"].get(link.get("source_entry_id"), {})
        images = ref["images"].get(link.get("source_entry_id"), [])
        pkg = _package(rid, link, entry, images, _live_date(connection, table, raw_id))
        if pkg:
            out[rid] = pkg
    return out


def check_for(
    connection: sqlite3.Connection,
    db_row_id: str,
    *,
    links_path: Path = LINKS_PATH,
    entries_path: Path = ENTRIES_PATH,
    images_path: Path = IMAGES_PATH,
) -> dict[str, Any] | None:
    """The check for a single record (e.g. "contract:184"), or None. Uses the cached
    reference data + one live-DB read — cheap enough to call on every record load."""
    table, _, raw_id = db_row_id.partition(":")
    if table not in ("contract", "sub_contract"):
        return None
    ref = _reference(links_path, entries_path, images_path)
    link = ref["links"].get(db_row_id)
    if not link:
        return None
    entry = ref["entries"].get(link.get("source_entry_id"), {})
    images = ref["images"].get(link.get("source_entry_id"), [])
    return _package(db_row_id, link, entry, images, _live_date(connection, table, raw_id))


def surfaced_checks(checks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """T1 + T2 only (the held-back one-day tier removed)."""
    return {k: v for k, v in checks.items() if v["surfaced"]}
