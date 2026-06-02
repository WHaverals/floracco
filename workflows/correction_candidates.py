"""Build the "possibly needs correction" candidate queue.

Candidates are *hypotheses* about which database rows/fields a reviewer should
look at — never proposals, never auto-applied, never pre-filled. Two framing
rules hold throughout:

1. The database is the truth we are perfecting; Word is evidence that helps.
   So a candidate carries the live DB value and the Word evidence side by side
   and never asserts a value to write.
2. The reconcile linker is not touched. This builder only *reads* stage 05 link
   candidates, stage 04 source entries, and the working SQLite database.

Run:
    export UV_PROJECT_ENVIRONMENT=.floracco
    uv run python workflows/correction_candidates.py build

Output (gitignored, under data/):
    data/derived/word-pipeline/10_corrections/correction_candidates.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DERIVED = PROJECT_ROOT / "data/derived/word-pipeline"
LINK_CANDIDATES_PATH = DERIVED / "05_db_candidate_matches/source_entry_db_link_candidates.jsonl"
SOURCE_ENTRIES_PATH = DERIVED / "04_source_entries/source_entries.jsonl"
CORRECTIONS_DIR = DERIVED / "10_corrections"
CANDIDATES_PATH = CORRECTIONS_DIR / "correction_candidates.jsonl"
DEFAULT_DB_PATH = PROJECT_ROOT / "data/sqlite/main.db"

BUILDER_VERSION = 1
SNIPPET_CHARS = 280

# Conflict codes we never raise as candidates (link-quality lives in reconcile).
SKIP_CONFLICTS = {"text_similarity_low"}

# Correctable field registry mirrored from review_server, kept local so the
# builder does not import the FastAPI app. (label, input_type, options)
EDITABLE_FIELDS: dict[tuple[str, str], tuple[str, str, list[str] | None]] = {
    ("contract", "registration_date"): ("Registration date", "date", None),
    ("contract", "folio"): ("Folio", "text", None),
    ("contract", "firm_name"): ("Firm name", "text", None),
    ("contract", "total"): ("Total capital", "number", None),
    ("contract", "duration_months"): ("Duration (months)", "number", None),
    ("sub_contract", "registration_date"): ("Registration date", "date", None),
    ("sub_contract", "folio"): ("Folio", "text", None),
    ("sub_contract", "renewal_months"): ("Renewal (months)", "number", None),
    ("sub_contract", "sub_type"): (
        "Type",
        "enum",
        ["balance", "renewal", "termination", "variation"],
    ),
    ("person", "last_name"): ("Last name", "text", None),
}

# reason_code -> (family, strength, base score, title)
REASON_META: dict[str, tuple[str, str, int, str]] = {
    "registration_date_differs": ("word_db_conflict", "high", 90, "Registration date disagrees with the Word source"),
    "folio_differs": ("word_db_conflict", "high", 85, "Folio disagrees with the Word source"),
    "event_type_table_differs": ("word_db_conflict", "medium", 60, "Event type may not match the Word source"),
    "db_register_differs": ("word_db_conflict", "medium", 55, "Register / provenance differs from the Word source"),
    "db_register_missing": ("word_db_conflict", "medium", 55, "Register / provenance missing relative to the Word source"),
    "db_date_missing": ("db_intrinsic", "high", 88, "Registration date is missing (0000-00-00)"),
    "orphan_main_contract": ("db_intrinsic", "high", 80, "Sub-contract points to a missing main contract"),
    "missing_sub_type": ("db_intrinsic", "medium", 58, "Sub-contract type is blank"),
    "person_no_name": ("db_intrinsic", "medium", 56, "Person record has no name"),
    "numerical_discrepancy": ("db_intrinsic", "low", 30, "Database flags a numerical discrepancy"),
    "missing_firm_name": ("db_intrinsic", "low", 25, "Firm name is missing"),
}

# Minimal mirror of word_pipeline's Italian-date parser, kept local so the builder
# stays standalone. Only used to normalise an adjudicated (inserted) date to ISO.
ITALIAN_MONTHS = {
    "gennaio": "01", "febbraio": "02", "marzo": "03", "aprile": "04",
    "maggio": "05", "giugno": "06", "luglio": "07", "agosto": "08",
    "settembre": "09", "ottobre": "10", "novembre": "11", "dicembre": "12",
}
ITALIAN_DATE_RE = re.compile(
    r"\b(?P<day>[0-3]?\d)\s+(?P<month>[A-Za-zàèéìòù]+)\s+(?P<year>1[4-8]\d{2})\b", re.IGNORECASE
)
# Digit-anchored, span-level detectors: a revision span only counts as touching a
# field if the *changed text* actually carries a date/duration/folio value — not
# merely the word "anni" or "c." somewhere in unrelated prose.
_MONTHS = "|".join(ITALIAN_MONTHS)
DATE_SPAN_RE = re.compile(rf"\b[0-3]?\d\s+(?:{_MONTHS})\b|\b(?:{_MONTHS})\s+1[4-8]\d{{2}}\b", re.IGNORECASE)
FOLIO_SPAN_RE = re.compile(r"\bc\.?\s*\d{1,3}\s*[rv]?\b|\bcart\.?\s*\d{1,3}\b", re.IGNORECASE)


def parse_italian_date(text: str | None) -> str | None:
    if not text:
        return None
    match = ITALIAN_DATE_RE.search(text)
    if not match:
        return None
    month = ITALIAN_MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    day = int(match.group("day"))
    if not 1 <= day <= 31:
        return None
    return f"{match.group('year')}-{month}-{day:02d}"


# Inline revision-marker grammar (mirrors review_server.parse_revision_segments).
REVISION_MARKER_RE = re.compile(
    r"<(?P<close>/?)(?P<tag>INS|DEL|MOVEFROM|MOVETO|COMMENT_START|COMMENT_END|COMMENT_REF|FOOTNOTE_REF|ENDNOTE_REF)\b(?P<attrs>[^>]*)/?>"
)
REVISION_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
RANGE_TAGS = {"INS": "insertion", "DEL": "deletion", "MOVEFROM": "move_from", "MOVETO": "move_to"}


def revision_spans(revision_text: str | None) -> list[dict[str, Any]]:
    """Contiguous insertion/deletion text spans with author/date, in document order."""
    text = revision_text or ""
    spans: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    cursor = 0
    for match in REVISION_MARKER_RE.finditer(text):
        seg = text[cursor : match.start()]
        if seg.strip() and stack:
            top = stack[-1]
            spans.append({"kind": top["kind"], "text": seg.strip(), "author": top.get("author"), "date": top.get("date")})
        cursor = match.end()
        tag = match.group("tag")
        if tag not in RANGE_TAGS:
            continue
        if match.group("close") == "/":
            for index in range(len(stack) - 1, -1, -1):
                if stack[index]["tag"] == tag:
                    del stack[index]
                    break
        else:
            attrs = dict(REVISION_ATTR_RE.findall(match.group("attrs") or ""))
            stack.append({"tag": tag, "kind": RANGE_TAGS[tag], "author": attrs.get("author"), "date": attrs.get("date")})
    return spans


def db_path() -> Path:
    path = Path(os.getenv("FLORACCO_DB_PATH", DEFAULT_DB_PATH))
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def candidate_key(db_row_id: str, field: str | None, reason_code: str, evidence: str) -> str:
    digest = hashlib.sha1(f"{db_row_id}|{field or ''}|{reason_code}|{evidence}".encode("utf-8")).hexdigest()
    return f"sha1:{digest}"


def folio_range(start: Any, end: Any) -> str:
    parts = [norm(part) for part in (start, end) if norm(part)]
    if len(parts) == 2 and parts[0] == parts[1]:
        return parts[0]
    return "–".join(parts)


def clean_snippet(text: Any) -> str:
    if not text:
        return ""
    flat = norm(str(text).replace("\\r\\n", " ").replace("\\n", " ").replace("\n", " ").replace("\r", " "))
    flat = " ".join(flat.split())
    return flat[:SNIPPET_CHARS]


# ---------------------------------------------------------------------------
# Evidence: the best linked Word source per DB row (for intrinsic candidates)
# ---------------------------------------------------------------------------


def best_links_by_row(links: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in links:
        db_row_id = row.get("db_row_id")
        if not db_row_id:
            continue
        score = float(row.get("score") or 0.0)
        if db_row_id not in best or score > float(best[db_row_id].get("score") or 0.0):
            best[db_row_id] = row
    return best


def word_evidence(
    link: dict[str, Any] | None, source_text: dict[str, str], folio_lookup: dict[str, str]
) -> dict[str, Any]:
    if not link:
        return {
            "source_entry_id": None,
            "source_entry_key": None,
            "register_id": None,
            "source_folio": None,
            "evidence_snippet": "",
        }
    entry_id = norm(link.get("source_entry_id")) or None
    return {
        "source_entry_id": entry_id,
        "source_entry_key": norm(link.get("source_entry_key")) or None,
        "register_id": norm(link.get("register_id")) or None,
        "source_folio": (folio_lookup.get(entry_id or "") or folio_range(link.get("entry_folio_start"), link.get("entry_folio_end")) or None),
        "evidence_snippet": clean_snippet(source_text.get(entry_id or "")),
    }


# ---------------------------------------------------------------------------
# Candidate assembly
# ---------------------------------------------------------------------------


def make_candidate(
    *,
    db_row_id: str,
    field: str | None,
    reason_code: str,
    db_value: str,
    word_value: str | None,
    explanation: str,
    evidence: dict[str, Any],
    key_extra: str = "",
    revision_evidence: dict[str, Any] | None = None,
    suggested_value: str | None = None,
) -> dict[str, Any]:
    table, raw_id = db_row_id.split(":", 1)
    family, strength, base_score, title = REASON_META[reason_code]
    label, input_type, options = (None, None, None)
    editable = False
    if field and (table, field) in EDITABLE_FIELDS:
        label, input_type, options = EDITABLE_FIELDS[(table, field)]
        editable = True
    if family == "word_db_conflict":
        key_evidence = f"{db_value}=>{norm(word_value)}"
    elif family == "tracked_change":
        key_evidence = key_extra or f"{db_value}=>{norm(suggested_value)}"
    else:
        key_evidence = db_value
    pk_col = "person_id" if table == "person" else "contract_id"
    return {
        "candidate_key": candidate_key(db_row_id, field, reason_code, key_evidence),
        "db_row_id": db_row_id,
        "db_table": table,
        "primary_key": {pk_col: raw_id},
        "field": field if editable else None,
        "field_label": label,
        "editable": editable,
        "input_type": input_type,
        "options": options,
        "family": family,
        "reason_code": reason_code,
        "title": title,
        "explanation": explanation,
        "strength": strength,
        "priority_score": base_score,
        "db_value": db_value,
        "word_value": norm(word_value) or None,
        "suggested_value": norm(suggested_value) or None,
        "revision_evidence": revision_evidence,
        "source_entry_id": evidence["source_entry_id"],
        "source_entry_key": evidence["source_entry_key"],
        "register_id": evidence["register_id"],
        "source_folio": evidence["source_folio"],
        "evidence_snippet": evidence["evidence_snippet"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_version": BUILDER_VERSION,
    }


def build_family1(
    links: list[dict[str, Any]],
    live_value: Any,
    source_text: dict[str, str],
    folio_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    """Word↔DB conflicts recorded by the matcher (stage 05)."""
    out: list[dict[str, Any]] = []
    for row in links:
        conflicts = row.get("conflicts") or []
        if isinstance(conflicts, str):
            conflicts = [conflicts]
        db_row_id = row.get("db_row_id")
        table = row.get("db_table")
        if not db_row_id or not table:
            continue
        evidence = word_evidence(row, source_text, folio_lookup)
        for code in conflicts:
            if code in SKIP_CONFLICTS or code not in REASON_META:
                continue
            field, db_value, word_value, explanation = _family1_fields(table, code, row, live_value(db_row_id, table, code))
            out.append(
                make_candidate(
                    db_row_id=db_row_id,
                    field=field,
                    reason_code=code,
                    db_value=db_value,
                    word_value=word_value,
                    explanation=explanation,
                    evidence=evidence,
                )
            )
    return out


def _family1_fields(table: str, code: str, row: dict[str, Any], live: str) -> tuple[str | None, str, str | None, str]:
    if code == "registration_date_differs":
        word = norm(row.get("entry_registration_date_raw")) or None
        return (
            "registration_date",
            live or norm(row.get("db_registration_date")),
            word,
            f"The registration date in the database (“{live or '—'}”) differs from the date written in "
            f"the linked Word source (“{word or '—'}”). Check the manuscript and decide what the database should say.",
        )
    if code == "folio_differs":
        word = folio_range(row.get("entry_folio_start"), row.get("entry_folio_end")) or None
        return (
            "folio",
            live or norm(row.get("db_folio_raw")),
            word,
            f"The folio in the database (“{live or '—'}”) differs from the folio in the linked Word source "
            f"(“{word or '—'}”). Both are shown — confirm against the manuscript image; nothing is pre-filled.",
        )
    if code == "event_type_table_differs":
        field = "sub_type" if table == "sub_contract" else None
        return (
            field,
            live or norm(row.get("db_sub_type")),
            None,
            "The Word source suggests a different event type than the database records for this row. "
            "Open the narrative to judge what the type should be.",
        )
    if code in {"db_register_differs", "db_register_missing"}:
        return (
            None,
            live or norm(row.get("register_id")),
            norm(row.get("register_id")) or None,
            "The register / provenance recorded in the database differs from (or is missing relative to) "
            "the linked Word source. Provenance fields are not directly editable yet — flag for review.",
        )
    return (None, live, None, "Word ↔ database conflict recorded by the matcher.")


def build_family3(
    connection: sqlite3.Connection,
    best_links: dict[str, dict[str, Any]],
    source_text: dict[str, str],
    folio_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    """DB-intrinsic corrupt/missing signals, computed straight from SQLite."""
    out: list[dict[str, Any]] = []

    def ev(db_row_id: str) -> dict[str, Any]:
        return word_evidence(best_links.get(db_row_id), source_text, folio_lookup)

    # contract: missing registration date
    for r in connection.execute("SELECT contract_id FROM contract WHERE registration_date='0000-00-00'"):
        rid = f"contract:{r['contract_id']}"
        e = ev(rid)
        out.append(make_candidate(
            db_row_id=rid, field="registration_date", reason_code="db_date_missing",
            db_value="0000-00-00", word_value=None,
            explanation="The registration date is missing (0000-00-00). The linked Word source may give the "
            "date — open it and verify against the manuscript before filling it in.",
            evidence=e,
        ))
    # sub_contract: missing registration date
    for r in connection.execute("SELECT contract_id FROM sub_contract WHERE registration_date='0000-00-00'"):
        rid = f"sub_contract:{r['contract_id']}"
        out.append(make_candidate(
            db_row_id=rid, field="registration_date", reason_code="db_date_missing",
            db_value="0000-00-00", word_value=None,
            explanation="The registration date is missing (0000-00-00). Verify against the linked Word source "
            "and manuscript before filling it in.",
            evidence=ev(rid),
        ))
    # contract: numerical discrepancy (low-confidence flag — often the source itself)
    for r in connection.execute("SELECT contract_id, total FROM contract WHERE numerical_discrepancy=1"):
        rid = f"contract:{r['contract_id']}"
        out.append(make_candidate(
            db_row_id=rid, field="total", reason_code="numerical_discrepancy",
            db_value=norm(r["total"]), word_value=None,
            explanation="The database flags a numerical discrepancy on this contract (recorded amounts do not "
            "reconcile). This is usually an artifact of the source contract itself, not a data-entry error — "
            "review, but it may need no change.",
            evidence=ev(rid),
        ))
    # contract: missing firm name (low-priority; Word may supply)
    for r in connection.execute("SELECT contract_id FROM contract WHERE firm_name IS NULL OR TRIM(firm_name)=''"):
        rid = f"contract:{r['contract_id']}"
        out.append(make_candidate(
            db_row_id=rid, field="firm_name", reason_code="missing_firm_name",
            db_value="", word_value=None,
            explanation="No firm name is recorded. The linked Word narrative may name the company — open it to "
            "check; the database value is not pre-filled.",
            evidence=ev(rid),
        ))
    # sub_contract: orphan main contract (flag-only — referential)
    for r in connection.execute(
        "SELECT s.contract_id, s.main_contract_id FROM sub_contract s "
        "WHERE NOT EXISTS (SELECT 1 FROM contract c WHERE c.contract_id=s.main_contract_id)"
    ):
        rid = f"sub_contract:{r['contract_id']}"
        out.append(make_candidate(
            db_row_id=rid, field=None, reason_code="orphan_main_contract",
            db_value=norm(r["main_contract_id"]), word_value=None,
            explanation=f"This sub-contract points to main contract #{norm(r['main_contract_id']) or '—'}, which "
            "does not exist in the database. The referential link is broken — needs a reviewer's eyes.",
            evidence=ev(rid),
        ))
    # sub_contract: blank type
    for r in connection.execute("SELECT contract_id FROM sub_contract WHERE sub_type IS NULL OR TRIM(sub_type)=''"):
        rid = f"sub_contract:{r['contract_id']}"
        out.append(make_candidate(
            db_row_id=rid, field="sub_type", reason_code="missing_sub_type",
            db_value="", word_value=None,
            explanation="The sub-contract type is blank. Determine the type (termination, renewal, variation, "
            "balance) from the linked Word source.",
            evidence=ev(rid),
        ))
    # person: no name at all
    for r in connection.execute(
        "SELECT person_id FROM person WHERE (first_name IS NULL OR TRIM(first_name)='') "
        "AND (last_name IS NULL OR TRIM(last_name)='')"
    ):
        rid = f"person:{r['person_id']}"
        out.append(make_candidate(
            db_row_id=rid, field="last_name", reason_code="person_no_name",
            db_value="", word_value=None,
            explanation="This person record has neither a first nor a last name. Identify the person from the "
            "contracts where they appear.",
            evidence=ev(rid),
        ))
    return out


def field_rev_ev(
    spans: list[dict[str, Any]], field_re: re.Pattern[str], author: str | None, date: str | None
) -> dict[str, Any]:
    """Revision evidence focused on one field: keep only spans that look like that
    field's value (a full match) or short numeric character-level edits (e.g. "2"→"8"),
    so the lens shows the date/folio change, not unrelated spelling fixes. Falls back to
    all spans if the focus filter leaves nothing.
    """
    def relevant(text: str) -> bool:
        return bool(field_re.search(text)) or (len(text) <= 12 and bool(re.search(r"\d", text)))

    ins = [s["text"][:120] for s in spans if s["kind"] == "insertion" and relevant(s["text"])]
    dels = [s["text"][:120] for s in spans if s["kind"] == "deletion" and relevant(s["text"])]
    if not ins and not dels:
        ins = [s["text"][:120] for s in spans if s["kind"] == "insertion"]
        dels = [s["text"][:120] for s in spans if s["kind"] == "deletion"]
    return {"insertions": ins[:8], "deletions": dels[:8], "author": author, "date": date}


def enrich_family2(
    source_entries: list[dict[str, Any]],
    ent2db: dict[str, list[dict[str, Any]]],
    family1_index: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Attach tracked-change evidence to the matcher's date/folio conflicts.

    Word is evidence, not truth, and we never guess which DB field a free-floating
    number maps to — so tracked changes do not *create* correction candidates. When a
    Family 1 date or folio conflict exists for a row and the source entry edited a
    date/folio in a tracked change, we enrich that vetted candidate with the revision
    (in-place). Dates additionally get a safe pre-filled `suggested_value`: the entry's
    own registration date, never some other date that happens to appear in the
    narrative. Names, amounts, and durations are deliberately out of scope (each needs
    field-aware extraction we cannot do reliably yet).

    Returns the number of conflicts enriched.
    """
    enriched = 0
    for entry in source_entries:
        if not entry.get("has_revisions"):
            continue
        eid = norm(entry.get("source_entry_id"))
        links = ent2db.get(eid)
        if not links:
            continue
        spans = revision_spans(entry.get("revision_aware_text"))
        if not spans:
            continue
        author = next((s.get("author") for s in spans if s.get("author")), None)
        rdate = next((s.get("date") for s in spans if s.get("date")), None)
        has_date = any(DATE_SPAN_RE.search(s["text"]) for s in spans)
        has_folio = any(FOLIO_SPAN_RE.search(s["text"]) for s in spans)
        if not (has_date or has_folio):
            continue
        seen_rows: set[str] = set()
        for link in links:
            db_row_id = link.get("db_row_id")
            if not db_row_id or db_row_id in seen_rows:
                continue
            seen_rows.add(db_row_id)
            if has_date:
                f1 = family1_index.get((db_row_id, "registration_date_differs"))
                if f1 is not None and not f1.get("revision_evidence"):
                    f1["revision_evidence"] = field_rev_ev(spans, DATE_SPAN_RE, author, rdate)
                    iso = parse_italian_date(f1.get("word_value"))
                    if iso and not f1.get("suggested_value"):
                        f1["suggested_value"] = iso
                    enriched += 1
            if has_folio:
                f1 = family1_index.get((db_row_id, "folio_differs"))
                if f1 is not None and not f1.get("revision_evidence"):
                    f1["revision_evidence"] = field_rev_ev(spans, FOLIO_SPAN_RE, author, rdate)
                    enriched += 1
    return enriched


def dedupe(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One candidate per candidate_key; keep the highest base score."""
    by_key: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        key = cand["candidate_key"]
        if key not in by_key or cand["priority_score"] > by_key[key]["priority_score"]:
            by_key[key] = cand
    return list(by_key.values())


def build() -> dict[str, Any]:
    links = load_jsonl(LINK_CANDIDATES_PATH)
    source_entries = load_jsonl(SOURCE_ENTRIES_PATH)
    source_text = {norm(e.get("source_entry_id")): e.get("current_text") or "" for e in source_entries}
    folio_lookup = {norm(e.get("source_entry_id")): norm(e.get("folio_raw")) for e in source_entries if e.get("folio_raw")}
    best_links = best_links_by_row(links)

    connection = sqlite3.connect(db_path())
    connection.row_factory = sqlite3.Row

    # live DB value cache so conflict candidates show the *current* truth
    live_cache: dict[tuple[str, str], str] = {}

    def live_field(db_row_id: str, field: str) -> str:
        if ":" not in db_row_id:
            return ""
        table, raw_id = db_row_id.split(":", 1)
        if table not in {"contract", "sub_contract"}:
            return ""
        cache_key = (db_row_id, field)
        if cache_key in live_cache:
            return live_cache[cache_key]
        try:
            row = connection.execute(
                f"SELECT {field} AS v FROM {table} WHERE contract_id = ?", (raw_id,)
            ).fetchone()
            value = norm(row["v"]) if row else ""
        except sqlite3.OperationalError:
            value = ""
        live_cache[cache_key] = value
        return value

    def live_value(db_row_id: str, table: str, code: str) -> str:
        field = {
            "registration_date_differs": "registration_date",
            "folio_differs": "folio",
            "event_type_table_differs": "sub_type",
        }.get(code)
        if not field:
            return ""
        return live_field(db_row_id, field)

    ent2db: dict[str, list[dict[str, Any]]] = {}
    for row in links:
        ent2db.setdefault(norm(row.get("source_entry_id")), []).append(row)

    try:
        family1 = build_family1(links, live_value, source_text, folio_lookup)
        family1_index = {(c["db_row_id"], c["reason_code"]): c for c in family1}
        enriched_count = enrich_family2(source_entries, ent2db, family1_index)
        family3 = build_family3(connection, best_links, source_text, folio_lookup)
    finally:
        connection.close()

    candidates = dedupe(family1 + family3)
    # stable ordering for a clean diff: score desc, then table/field/row
    candidates.sort(
        key=lambda c: (-c["priority_score"], c["db_table"], c["reason_code"], c["db_row_id"])
    )

    CORRECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with CANDIDATES_PATH.open("w", encoding="utf-8") as handle:
        for cand in candidates:
            handle.write(json.dumps(cand, ensure_ascii=False) + "\n")

    by_reason = Counter(c["reason_code"] for c in candidates)
    by_strength = Counter(c["strength"] for c in candidates)
    by_family = Counter(c["family"] for c in candidates)
    return {
        "total": len(candidates),
        "by_reason": dict(by_reason.most_common()),
        "by_strength": dict(by_strength),
        "by_family": dict(by_family),
        "conflicts_enriched_with_revision": enriched_count,
        "path": str(CANDIDATES_PATH.relative_to(PROJECT_ROOT)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build"], help="build the candidate queue")
    args = parser.parse_args()
    if args.command == "build":
        summary = build()
        print(f"Wrote {summary['total']} candidates → {summary['path']}")
        print("By family:", summary["by_family"])
        print("By strength:", summary["by_strength"])
        print(f"Conflicts enriched with tracked-change evidence: {summary['conflicts_enriched_with_revision']}")
        print("By reason:")
        for reason, count in summary["by_reason"].items():
            print(f"  {reason:28s} {count}")


if __name__ == "__main__":
    main()
