"""Local API server for the FlorAcco review platform.

Run:
    uv run uvicorn workflows.review_server:app --reload

Write scope: review decisions (CSV), correction proposals (JSONL), and —
exclusively through the audited corrections.db op-log — governed updates,
hide/restore, and creation of DB-native rows in main.db. Word files, images,
and pipeline outputs are never written.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from workflows import corrections_db, data_quality, search_index, word_cross_check
from workflows.word_pipeline import act_components_for_review, folio_sort_key, parse_db_folio


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# All corpus data (SQLite, derived pipeline outputs, corrections, the search
# index) lives under one root. Locally that is <repo>/data; in deployment
# FLORACCO_DATA_DIR points at the mounted persistent disk's working copy, making
# the entire data location a single relocatable knob — which is also what lets
# the resettable demo swap the working tree wholesale from a pristine snapshot.
DATA_ROOT = Path(os.getenv("FLORACCO_DATA_DIR") or (PROJECT_ROOT / "data")).expanduser().resolve()
DERIVED_ROOT = DATA_ROOT / "derived/word-pipeline"

QA_PACKET_PATH = DERIVED_ROOT / "06_qa_packet/word_db_match_qa_packet.jsonl"
LINK_CANDIDATES_PATH = DERIVED_ROOT / "05_db_candidate_matches/source_entry_db_link_candidates.jsonl"
SOURCE_ENTRIES_PATH = DERIVED_ROOT / "04_source_entries/source_entries.jsonl"
IMAGE_CANDIDATES_PATH = DERIVED_ROOT / "07_image_links/source_entry_image_candidates.jsonl"
WORD_COMMENTS_PATH = DERIVED_ROOT / "03_extracted_registers/comments.jsonl"
WORD_NOTES_PATH = DERIVED_ROOT / "03_extracted_registers/footnotes.jsonl"
IMAGE_FOLIO_MAP_PATH = DERIVED_ROOT / "07_image_links/image_folio_map.jsonl"
REVIEW_DIR = DERIVED_ROOT / "08_review_decisions"
DECISIONS_PATH = REVIEW_DIR / "review_decisions.csv"
CORRECTIONS_DIR = DERIVED_ROOT / "10_corrections"
PROPOSALS_PATH = CORRECTIONS_DIR / "corrections_proposals.jsonl"
EVENTS_PATH = CORRECTIONS_DIR / "corrections_events.jsonl"
# Derived "possibly needs correction" queue (built by workflows/correction_candidates.py)
# plus its append-only human dismissal log. Candidates are hypotheses, never writes.
CANDIDATES_PATH = CORRECTIONS_DIR / "correction_candidates.jsonl"
CANDIDATE_DISMISSALS_PATH = CORRECTIONS_DIR / "correction_candidate_dismissals.jsonl"
# DB-intrinsic "Needs review" flags (workflows/data_quality.py, computed live) +
# their append-only dismissal log ("reviewed, not an error").
FLAG_DISMISSALS_PATH = CORRECTIONS_DIR / "flag_dismissals.jsonl"
DEFAULT_DB_PATH = DATA_ROOT / "sqlite/main.db"

# Fields a reviewer may correct in v1: scalar values only, safe to edit as plain
# text/date/number/enum. Foreign keys (title/currency/place ids, person_id) are
# deliberately excluded — they need entity pickers + a `relink` replay path and a
# different UX (deferred). The primary key column per table is also fixed here.
# investor/investment are single-PK child rows reachable from a contract; editing
# their scalar columns runs through the same propose→approve→apply machinery.
PRIMARY_KEY_COLUMN = {
    "contract": "contract_id",
    "sub_contract": "contract_id",
    "person": "person_id",
    "investor": "investor_id",
    "investment": "investment_id",
}
CORRECTABLE_FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "contract": {
        "firm_name": {"label": "Firm name", "input_type": "text"},
        "registration_date": {"label": "Registration date", "input_type": "date"},
        "start_date": {"label": "Start date", "input_type": "date"},
        "folio": {"label": "Folio", "input_type": "text"},
        "total": {"label": "Total capital", "input_type": "number"},
        "duration_months": {"label": "Duration (months)", "input_type": "number"},
        # Boolean source facts about the contract (stored 0/1, no NULLs → Yes/No).
        "automatic_renewal": {"label": "Automatic renewal", "input_type": "bool"},
        "automatic_renewal_months": {"label": "Renewal period (months)", "input_type": "number"},
        "clauses": {"label": "Special clauses", "input_type": "bool"},
        "pl_discretion": {"label": "Place at discretion", "input_type": "bool"},
        "ec_discretion": {"label": "Economic activity at discretion", "input_type": "bool"},
        "administrators": {"label": "Administrators named", "input_type": "bool"},
        "additional_docs": {"label": "Additional documents", "input_type": "bool"},
        # The DB's own narrative text. Editable by decision (2026-06-11): Word
        # summaries are frozen provenance shown alongside; the document field is
        # the living text of record and may diverge from Word over time.
        "document": {"label": "Narrative (document)", "input_type": "textarea"},
    },
    "sub_contract": {
        "sub_firm_name": {"label": "Sub-firm name", "input_type": "text"},
        "sub_type": {
            "label": "Type",
            "input_type": "enum",
            "options": ["balance", "renewal", "termination", "variation"],
        },
        "registration_date": {"label": "Registration date", "input_type": "date"},
        "end_date": {"label": "End date", "input_type": "date"},
        "renewal_months": {"label": "Renewal (months)", "input_type": "number"},
        "folio": {"label": "Folio", "input_type": "text"},
        "document": {"label": "Narrative (document)", "input_type": "textarea"},
    },
    "person": {
        "first_name": {"label": "First name", "input_type": "text"},
        "father_mother": {"label": "Father / mother", "input_type": "text"},
        "grandfather": {"label": "Grandfather", "input_type": "text"},
        "last_name": {"label": "Last name", "input_type": "text"},
        "nickname": {"label": "Nickname", "input_type": "text"},
        "is_woman": {"label": "Recorded as woman", "input_type": "bool"},
    },
    # An investor is one person's appearance on one contract. Only the scalar,
    # free-text columns are correctable here; the FK columns (title, place_of_*)
    # and the derived `is_joint` flag are excluded (FKs need a relink UX; is_joint
    # is computed from the investment's group structure, not edited directly).
    "investor": {
        "profession": {"label": "Profession", "input_type": "text"},
        "husband_first_name": {"label": "Husband — first name", "input_type": "text"},
        "husband_last_name": {"label": "Husband — last name", "input_type": "text"},
        "guardian_of": {"label": "Guardian of", "input_type": "text"},
        # Boolean source facts about this investor's appearance (all 0/1, no NULLs).
        # FK columns (title*, place_of_*) stay read-only here — relink is deferred.
        "citizen_florence": {"label": "Citizen of Florence", "input_type": "bool"},
        "via_proxy": {"label": "Acted via proxy", "input_type": "bool"},
        "is_widow": {"label": "Recorded as widow", "input_type": "bool"},
        "is_guardian": {"label": "Acting as guardian", "input_type": "bool"},
        "is_jewish": {"label": "Recorded as Jewish", "input_type": "bool"},
        "is_convert": {"label": "Recorded as convert", "input_type": "bool"},
        "heirs": {"label": "Heirs", "input_type": "bool"},
        "heirs_of": {"label": "Heirs of", "input_type": "bool"},
        "and_c": {"label": "“& company” (e compagni)", "input_type": "bool"},
    },
    # The money side of an investor's stake. `type` (gp/lp) is the real partnership
    # role. A joint investment is one shared by several investors, so editing its
    # cash edits the shared figure — by design, not per-person.
    "investment": {
        "type": {"label": "Role", "input_type": "enum", "options": ["gp", "lp"]},
        "partnership_name": {"label": "Partnership name", "input_type": "text"},
        "investment_cash": {"label": "Cash", "input_type": "number"},
        "investment_non_cash": {"label": "Non-cash", "input_type": "text"},
    },
}
CHANGE_TYPES = {"correct", "fill_missing", "flag_uncertain"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DECISION_FIELDNAMES = [
    "review_id",
    "updated_at",
    "reviewer",
    "source_entry_key",
    "source_entry_id",
    "suggested_db_row_id",
    "register_id",
    "recommended_review_bucket",
    "main_judgment",
    "image_judgment",
    "field_correction_needed",
    "next_action",
    "review_note",
    "image_candidate_paths",
    "selected_db_row_ids",
    "rejected_db_row_ids",
    "unassessed_db_row_ids",
    "suggested_relationship_type",
    "reviewed_text_sha256",
    "packet_section",
]


class ReviewDecision(BaseModel):
    reviewer: str = Field(min_length=1)
    # Content-stable entry identity; review decisions are keyed on this so they
    # survive re-segmentation. source_entry_id is kept for display only.
    source_entry_key: str = ""
    source_entry_id: str = ""
    suggested_db_row_id: str = ""
    # "Word entry review" (default) or "DB-only review"; selects the review_id
    # scheme so the case identity stays stable across match-db re-runs.
    packet_section: str = ""
    register_id: str = ""
    recommended_review_bucket: str = ""
    main_judgment: str
    image_judgment: str
    field_correction_needed: str
    next_action: str
    review_note: str = ""
    image_candidate_paths: str = ""
    selected_db_row_ids: list[str] = Field(default_factory=list)
    rejected_db_row_ids: list[str] = Field(default_factory=list)
    # Demoted `alternative` rows the reviewer neither selected nor rejected.
    # Recorded for audit but given no decision status: an unticked alternative
    # was never the question being asked, so it must not surface as "rejected"
    # evidence on the DB record (it stays "proposed").
    unassessed_db_row_ids: list[str] = Field(default_factory=list)
    suggested_relationship_type: str = ""


app = FastAPI(title="FlorAcco Review API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def split_semicolon_values(text: Any) -> list[str]:
    return [value.strip() for value in str(text or "").split(";") if value.strip()]


def blank_or_none_recorded(text: Any) -> bool:
    clean = str(text or "").strip().lower()
    return clean in {"", "none recorded.", "none recorded", "no conflict recorded.", "no conflict recorded"}


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evidence_status_for_metric(value: float | None, strong: float, partial: float) -> str:
    if value is None or value <= 0:
        return "neutral"
    if value >= strong:
        return "strong"
    if value >= partial:
        return "partial"
    return "weak"


def metric_evidence(label: str, value: Any, *, strong: float, partial: float, detail_suffix: str = "") -> dict[str, Any]:
    metric = as_float(value)
    status = evidence_status_for_metric(metric, strong, partial)
    if metric is None:
        detail = "No metric was recorded."
    else:
        detail = f"{metric:g}{detail_suffix}"
    return {
        "kind": "metric",
        "label": label,
        "status": status,
        "detail": detail,
        "metric": metric,
        "highlight_values": [],
    }


def humanize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("_", " ")).strip().capitalize()


def parse_field_overlap_items(text: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for part in split_semicolon_values(text):
        if ":" not in part:
            continue
        raw_label, raw_value = part.split(":", 1)
        value = raw_value.strip()
        if not value:
            continue
        items.append(
            {
                "kind": "field_overlap",
                "label": humanize_label(raw_label),
                "status": "match",
                "detail": value,
                "metric": None,
                "highlight_values": [value],
            }
        )
    return items


def parse_signal_items(text: Any) -> list[dict[str, Any]]:
    if blank_or_none_recorded(text):
        return []
    return [
        {
            "kind": "signal",
            "label": signal,
            "status": "match",
            "detail": "Recorded as positive match evidence.",
            "metric": None,
            "highlight_values": [],
        }
        for signal in split_semicolon_values(text)
    ]


def parse_conflict_items(text: Any) -> list[dict[str, Any]]:
    if blank_or_none_recorded(text):
        return [
            {
                "kind": "conflict",
                "label": "Direct conflicts",
                "status": "match",
                "detail": "No direct conflict was recorded for date, register, folio, or event type.",
                "metric": None,
                "highlight_values": [],
            }
        ]
    return [
        {
            "kind": "conflict",
            "label": conflict,
            "status": "conflict",
            "detail": "Review this discrepancy before approving the link.",
            "metric": None,
            "highlight_values": [],
        }
        for conflict in split_semicolon_values(text)
    ]


def evidence_items_for_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        metric_evidence("Narrative similarity", row.get("narrative_similarity_ratio"), strong=0.75, partial=0.5),
        metric_evidence(
            "Text containment (DB text inside Word)",
            row.get("text_containment_ratio"),
            strong=0.75,
            partial=0.5,
        ),
        metric_evidence("Word terms found in DB text", row.get("word_token_coverage_in_db"), strong=0.75, partial=0.5),
        metric_evidence("DB terms found in Word text", row.get("db_token_coverage_in_word"), strong=0.75, partial=0.5),
        metric_evidence(
            "Longest shared phrase",
            row.get("longest_shared_phrase_words"),
            strong=6,
            partial=3,
            detail_suffix=" words",
        ),
    ]
    items.extend(parse_signal_items(row.get("top_match_signals_plain_language")))
    items.extend(parse_field_overlap_items(row.get("field_overlap_plain_language")))
    items.extend(parse_conflict_items(row.get("top_match_conflicts_plain_language")))
    if not blank_or_none_recorded(row.get("alignment_diagnostics_plain_language")):
        items.append(
            {
                "kind": "diagnostic",
                "label": "Alignment diagnostic",
                "status": "review",
                "detail": row.get("alignment_diagnostics_plain_language"),
                "metric": None,
                "highlight_values": [],
            }
        )
    return items


def highlight_values_for_evidence(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    highlights: list[dict[str, str]] = []
    for item in items:
        status = str(item.get("status") or "match")
        for value in item.get("highlight_values") or []:
            clean = str(value or "").strip()
            if len(clean) < 3 or clean.lower() in seen:
                continue
            seen.add(clean.lower())
            highlights.append({"value": clean, "status": status, "label": str(item.get("label") or "")})
    return highlights


def normalize_empty(value: Any) -> Any:
    if value is None:
        return ""
    return value


def entry_identity(row: dict[str, Any]) -> str:
    """Stable identity for a Word entry: prefer the content key, fall back to the id."""
    return str(row.get("source_entry_key") or row.get("source_entry_id") or "")


def review_key(row: dict[str, Any]) -> tuple[str, str]:
    # Anchor on the single stable DB primary key (top_db_row_id), never the
    # volatile multi-id suggested-link list — see review_id_for.
    db_anchor = str(row.get("top_db_row_id") or row.get("suggested_db_row_ids") or "")
    return entry_identity(row), db_anchor


def review_id_for(entry_identity_value: str, db_anchor: str, is_db_only: bool = False) -> str:
    """Stable review identity for a case.

    A **Word-entry** review case is 1:1 with its content-stable ``source_entry_key``
    (``entry_identity``), so it is keyed on that alone — it must survive a
    ``match-db`` re-run that re-prunes, adds, drops, or reorders the suggested DB
    links (that link list is *not* stable across runs; the key is). The reviewer's
    actual choice lives in ``selected_db_row_ids`` / ``rejected_db_row_ids``
    regardless of what the matcher now proposes, and ``reviewed_text_sha256`` flags
    when the reviewed Word text itself drifted.

    A **DB-only** review case is a specific DB row surfaced for review (often
    alongside the same Word entry, so it shares its ``source_entry_key``). It is
    keyed on identity **plus** that single stable DB row id, both to distinguish it
    from the entry's Word-review case and to keep several DB-only rows for one
    entry apart.
    """
    if is_db_only:
        return f"{entry_identity_value}__{db_anchor}" if entry_identity_value else f"__{db_anchor}"
    return entry_identity_value or f"__{db_anchor}"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_qa_rows() -> list[dict[str, Any]]:
    rows = [{key: normalize_empty(value) for key, value in row.items()} for row in load_jsonl(QA_PACKET_PATH)]
    for index, row in enumerate(rows):
        identity, db_anchor = review_key(row)
        row["case_index"] = index
        row["review_id"] = review_id_for(
            identity, db_anchor, is_db_only=(row.get("packet_section") == "DB-only review")
        )
    return rows


def load_decisions() -> list[dict[str, Any]]:
    if not DECISIONS_PATH.exists():
        return []
    with DECISIONS_PATH.open(encoding="utf-8", newline="") as handle:
        return [{key: normalize_empty(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def decisions_by_review_id() -> dict[str, dict[str, Any]]:
    return {row["review_id"]: row for row in load_decisions() if row.get("review_id")}


def db_path() -> Path:
    path = Path(os.getenv("FLORACCO_DB_PATH", DEFAULT_DB_PATH))
    return path if path.is_absolute() else PROJECT_ROOT / path


def db_row_for_id(db_row_id: str) -> dict[str, Any]:
    if not db_row_id or ":" not in db_row_id:
        return {}
    table, raw_id = db_row_id.split(":", 1)
    if table not in {"contract", "sub_contract"}:
        return {}
    path = db_path()
    if not path.exists():
        return {}
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(f"SELECT * FROM {table} WHERE contract_id = ?", (raw_id,))
        row = cursor.fetchone()
    return dict(row) if row else {}


def image_path_from_request(path_text: str) -> Path:
    """Resolve a requested image path to a file, restricted to the images root.

    Internet-facing (behind Cloudflare Access, but defense-in-depth): the result
    must live under the images root only — NOT anywhere in the repo — so this
    endpoint can never be used to read source files. Stored paths are repo-
    relative (e.g. ``data/corpus/img/…``); a leading ``data/`` is stripped and the
    remainder resolved against ``DATA_ROOT`` so images relocate with the data dir.
    """
    if not path_text:
        raise HTTPException(status_code=404, detail="No image path provided.")
    images_root = Path(
        os.getenv("FLORACCO_IMAGES_ROOT") or (DATA_ROOT / "corpus/img")
    ).expanduser().resolve()
    raw = Path(path_text)
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        parts = raw.parts
        relative = Path(*parts[1:]) if parts and parts[0] == "data" else raw
        resolved = (DATA_ROOT / relative).resolve()
    if not (resolved == images_root or images_root in resolved.parents):
        raise HTTPException(status_code=403, detail="Image path is outside the images root.")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Image file not found.")
    return resolved


REVISION_MARKER_RE = re.compile(
    r"<(?P<close>/?)(?P<tag>INS|DEL|MOVEFROM|MOVETO|COMMENT_START|COMMENT_END|COMMENT_REF|FOOTNOTE_REF|ENDNOTE_REF)\b(?P<attrs>[^>]*)/?>"
)
REVISION_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
RANGE_CHANGE_TAGS = {
    "INS": "insertion",
    "DEL": "deletion",
    "MOVEFROM": "move_from",
    "MOVETO": "move_to",
}


def _parse_marker_attrs(attrs: str) -> dict[str, str]:
    return {key: value for key, value in REVISION_ATTR_RE.findall(attrs or "")}


def _emit_text(
    tokens: list[dict[str, Any]],
    text: str,
    changes: list[dict[str, Any]],
    comment_ids: list[str],
) -> None:
    """Split a literal text span on newlines/tabs into renderable tokens.

    Each text token carries a snapshot of the currently open changes
    (outermost first) and open comment ids so the frontend can style without
    re-deriving nesting.
    """
    if not text:
        return
    for piece in re.split(r"(\n|\t)", text):
        if piece == "":
            continue
        if piece == "\n":
            tokens.append({"type": "break"})
        elif piece == "\t":
            tokens.append({"type": "tab"})
        else:
            tokens.append(
                {
                    "type": "text",
                    "text": piece,
                    "changes": [dict(change) for change in changes],
                    "comment_ids": list(comment_ids),
                }
            )


def parse_revision_segments(revision_text: str) -> dict[str, Any]:
    """Parse `revision_aware_text` inline markers into a render-ready token stream.

    Marker grammar (see docs/workflows/tracked_changes_word_panel.md):
    paired range markers `<INS|DEL|MOVEFROM|MOVETO ...>...</...>`, paired comment
    ranges `<COMMENT_START id=..>..<COMMENT_END id=..>`, and point markers
    `<COMMENT_REF|FOOTNOTE_REF|ENDNOTE_REF id=..>`.

    The walk is defensive: orphan close tags and unbalanced ranges are tolerated
    so a single malformed marker never drops the rest of the entry text.
    """
    text = revision_text or ""
    tokens: list[dict[str, Any]] = []
    change_stack: list[dict[str, Any]] = []
    open_comment_ids: list[str] = []
    summary = {"insertions": 0, "deletions": 0, "moves": 0, "comments": 0, "notes": 0}
    counted_comment_ids: set[str] = set()

    cursor = 0
    for match in REVISION_MARKER_RE.finditer(text):
        _emit_text(tokens, text[cursor : match.start()], change_stack, open_comment_ids)
        cursor = match.end()
        is_close = match.group("close") == "/"
        tag = match.group("tag")
        attrs = _parse_marker_attrs(match.group("attrs"))
        marker_id = attrs.get("id")

        if tag in RANGE_CHANGE_TAGS:
            if is_close:
                for index in range(len(change_stack) - 1, -1, -1):
                    if change_stack[index]["tag"] == tag:
                        del change_stack[index]
                        break
            else:
                kind = RANGE_CHANGE_TAGS[tag]
                change_stack.append(
                    {
                        "tag": tag,
                        "kind": kind,
                        "id": marker_id,
                        "author": attrs.get("author") or None,
                        "date": attrs.get("date") or None,
                    }
                )
                if kind == "insertion":
                    summary["insertions"] += 1
                elif kind == "deletion":
                    summary["deletions"] += 1
                else:
                    summary["moves"] += 1
        elif tag == "COMMENT_START":
            if marker_id and marker_id not in open_comment_ids:
                open_comment_ids.append(marker_id)
            if marker_id and marker_id not in counted_comment_ids:
                counted_comment_ids.add(marker_id)
                summary["comments"] += 1
        elif tag == "COMMENT_END":
            if marker_id in open_comment_ids:
                open_comment_ids.remove(marker_id)
        elif tag == "COMMENT_REF":
            if marker_id and marker_id not in counted_comment_ids:
                counted_comment_ids.add(marker_id)
                summary["comments"] += 1
            tokens.append({"type": "comment_ref", "id": marker_id})
        elif tag in {"FOOTNOTE_REF", "ENDNOTE_REF"}:
            summary["notes"] += 1
            tokens.append(
                {
                    "type": "note_ref",
                    "id": marker_id,
                    "kind": "footnote" if tag == "FOOTNOTE_REF" else "endnote",
                }
            )

    _emit_text(tokens, text[cursor:], change_stack, open_comment_ids)
    has_revisions = bool(summary["insertions"] or summary["deletions"] or summary["moves"])
    return {"tokens": tokens, "summary": summary, "has_revisions": has_revisions}


def _as_list(value: Any) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []


def build_word_entry_rich(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble the token stream plus comment/note bodies for the Word panel."""
    parsed = parse_revision_segments(str(row.get("word_entry_revision_text") or ""))
    comments = _as_list(row.get("word_entry_comments"))
    notes = _as_list(row.get("word_entry_notes"))
    summary = row.get("word_entry_revision_summary")
    if not isinstance(summary, dict):
        summary = parsed["summary"]
    return {
        "has_revisions": bool(row.get("word_entry_has_revisions")) or parsed["has_revisions"],
        "summary": summary,
        "tokens": parsed["tokens"],
        "comments": comments,
        "notes": notes,
        "clean_text": str(row.get("word_entry_text") or ""),
    }


_link_candidates_by_entry: dict[str, list[dict[str, Any]]] | None = None
_source_entries_by_id: dict[str, dict[str, Any]] | None = None


def link_candidates_by_entry() -> dict[str, list[dict[str, Any]]]:
    global _link_candidates_by_entry
    if _link_candidates_by_entry is None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for link_row in load_jsonl(LINK_CANDIDATES_PATH):
            grouped.setdefault(str(link_row["source_entry_id"]), []).append(link_row)
        for links in grouped.values():
            links.sort(key=lambda item: int(item.get("link_ordinal") or 0))
        _link_candidates_by_entry = grouped
    return _link_candidates_by_entry


def source_entries_by_id() -> dict[str, dict[str, Any]]:
    global _source_entries_by_id
    if _source_entries_by_id is None:
        _source_entries_by_id = {
            str(entry["source_entry_id"]): entry for entry in load_jsonl(SOURCE_ENTRIES_PATH)
        }
    return _source_entries_by_id


def entry_stub_for_act_components(row: dict[str, Any]) -> dict[str, Any]:
    source_entry_id = str(row.get("source_entry_id") or "")
    source_entry = source_entries_by_id().get(source_entry_id)
    if source_entry:
        return source_entry
    return {
        "current_text": row.get("word_entry_text"),
        "event_label_raw": row.get("entry_label"),
        "event_label_guess": row.get("entry_type_interpretation"),
        "event_number_raw": row.get("entry_number"),
        "referenced_event_number_raw": row.get("referenced_entry_number"),
    }


def case_payload(row: dict[str, Any], decision: dict[str, Any] | None = None) -> dict[str, Any]:
    suggested_db_row_ids = split_semicolon_values(row.get("suggested_db_row_ids")) or split_semicolon_values(
        row.get("top_db_row_id")
    )
    image_paths = split_semicolon_values(row.get("image_candidate_paths"))
    evidence_items = evidence_items_for_row(row)
    source_entry_id = str(row.get("source_entry_id") or "")
    link_candidates = link_candidates_by_entry().get(source_entry_id, [])
    act_components = act_components_for_review(entry_stub_for_act_components(row), link_candidates)
    return {
        "row": row,
        "suggested_db_row_ids": suggested_db_row_ids,
        "db_rows": [db_row_for_id(db_row_id) for db_row_id in suggested_db_row_ids],
        "image_paths": image_paths,
        # Grouped by physical scan (an opening spread links two folio sides to one
        # photo) so the UI shows each scan once with merged folio captions — same
        # shape /api/word-entry uses. Avoids the panel double-counting one scan.
        "image_candidates": group_word_entry_images(image_candidates_by_entry().get(source_entry_id, [])),
        "evidence_items": evidence_items,
        "highlight_values": highlight_values_for_evidence(evidence_items),
        "word_entry_rich": build_word_entry_rich(row),
        "act_components": act_components,
        "link_metrics": link_metrics_for_candidates(link_candidates),
        "decision": decision,
    }


def link_metrics_for_candidates(link_candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-candidate match signals keyed by db_row_id, for the review UI.

    These come from the alignment layer (``source_entry_db_link_candidates``) so a
    reviewer can see how strongly *each* suggested row matches the Word narrative —
    the signal that separates a real twin from a sibling that merely shares
    boilerplate. ``match_strength`` mirrors the headline rule
    ``max(narrative_similarity_ratio, text_containment_ratio)``.
    """
    metrics: dict[str, dict[str, Any]] = {}
    for link in link_candidates:
        db_row_id = str(link.get("db_row_id") or "")
        if not db_row_id or db_row_id in metrics:
            continue
        similarity = as_float(link.get("narrative_similarity_ratio"))
        containment = as_float(link.get("text_containment_ratio"))
        strengths = [value for value in (similarity, containment) if value is not None]
        metrics[db_row_id] = {
            "narrative_similarity_ratio": similarity,
            "text_containment_ratio": containment,
            "match_strength": max(strengths) if strengths else None,
            "longest_shared_phrase_words": link.get("longest_shared_phrase_words"),
            "score": as_float(link.get("score")),
            "relationship_type": link.get("relationship_type"),
            "link_role": link.get("link_role"),
            "link_ordinal": link.get("link_ordinal"),
            # Pipeline-owned verdict on Word label vs DB table/type (exact /
            # interpretive / mismatch / unknown). The UI renders this and keeps
            # no label→type map of its own (qa_packet_schema.md v4).
            "event_type_relation": link.get("event_type_relation"),
        }
    return metrics


@app.get("/api/me")
def whoami(request: Request) -> dict[str, Any]:
    """The signed-in reviewer's identity, from the Cloudflare Access header.

    When the platform sits behind Cloudflare Access, every authenticated request
    carries the verified user email in `Cf-Access-Authenticated-User-Email`. The
    front-end seeds the reviewer field from this so the audit trail records a
    *verified* identity instead of self-typed initials. Locally (no Access) the
    header is absent and the field stays manually editable.
    """
    email = request.headers.get("cf-access-authenticated-user-email", "").strip()
    return {"authenticated": bool(email), "email": email}


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    rows = load_qa_rows()
    decisions = decisions_by_review_id()
    return {
        # Displayed relative to the data root (these live under it); using
        # PROJECT_ROOT broke when FLORACCO_DATA_DIR relocates data outside the repo.
        "qa_packet_path": str(QA_PACKET_PATH.relative_to(DATA_ROOT)),
        "decisions_path": str(DECISIONS_PATH.relative_to(DATA_ROOT)),
        "total_cases": len(rows),
        "reviewed_cases": sum(1 for row in rows if row["review_id"] in decisions),
        "buckets": sorted(
            {str(row.get("recommended_review_bucket") or "") for row in rows if row.get("recommended_review_bucket")}
        ),
        "registers": sorted({str(row.get("register_id") or "") for row in rows if row.get("register_id")}),
    }


def mtime_iso(path: Path) -> str | None:
    """File modification time as ISO-8601 UTC, or None if the file is absent."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


@app.get("/api/cases")
def cases(
    bucket: str = "All",
    register: str = "All",
    reviewed: str = Query(default="unreviewed", pattern="^(all|reviewed|unreviewed)$"),
    search: str = "",
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, Any]:
    rows = load_qa_rows()
    decisions = decisions_by_review_id()
    search_lower = search.lower().strip()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        is_reviewed = row["review_id"] in decisions
        if bucket != "All" and row.get("recommended_review_bucket") != bucket:
            continue
        if register != "All" and row.get("register_id") != register:
            continue
        if reviewed == "reviewed" and not is_reviewed:
            continue
        if reviewed == "unreviewed" and is_reviewed:
            continue
        if search_lower:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "source_entry_id",
                    "source_entry_key",
                    "suggested_db_row_ids",
                    "top_db_row_id",
                    "word_entry_text",
                    "suggested_db_documents_text",
                    "field_overlap_plain_language",
                )
            ).lower()
            if search_lower not in haystack:
                continue
        preview = {
            "review_id": row["review_id"],
            "source_entry_id": row.get("source_entry_id"),
            "source_entry_key": row.get("source_entry_key"),
            "register_id": row.get("register_id"),
            "recommended_review_bucket": row.get("recommended_review_bucket"),
            "word_registration_date": row.get("word_registration_date"),
            "word_folio_range": row.get("word_folio_range"),
            "suggested_db_row_ids": row.get("suggested_db_row_ids") or row.get("top_db_row_id"),
            "is_reviewed": is_reviewed,
        }
        filtered.append(preview)
    return {"total": len(filtered), "cases": filtered[:limit]}


@app.get("/api/cases/{review_id}")
def case_detail(review_id: str) -> dict[str, Any]:
    decisions = decisions_by_review_id()
    for row in load_qa_rows():
        if row["review_id"] == review_id:
            return case_payload(row, decisions.get(review_id))
    raise HTTPException(status_code=404, detail="Review case not found.")


@app.get("/api/images")
def image(path: str) -> FileResponse:
    return FileResponse(image_path_from_request(path))


@app.post("/api/decisions")
def save_decision(decision: ReviewDecision) -> dict[str, Any]:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    decision_row = decision.model_dump()
    decision_row["selected_db_row_ids"] = "; ".join(decision.selected_db_row_ids)
    decision_row["rejected_db_row_ids"] = "; ".join(decision.rejected_db_row_ids)
    decision_row["unassessed_db_row_ids"] = "; ".join(decision.unassessed_db_row_ids)
    decision_row["updated_at"] = datetime.now(timezone.utc).isoformat()
    entry_identity_value = decision.source_entry_key or decision.source_entry_id
    decision_row["review_id"] = review_id_for(
        entry_identity_value,
        decision.suggested_db_row_id,
        is_db_only=(decision.packet_section == "DB-only review"),
    )
    # Snapshot the reviewed Word text so a later content change can be detected
    # (README §5). Read from the case the decision was made on, not the payload.
    case_row = next((row for row in load_qa_rows() if row["review_id"] == decision_row["review_id"]), None)
    decision_row["reviewed_text_sha256"] = (
        hashlib.sha256(str(case_row.get("word_entry_text") or "").encode("utf-8")).hexdigest()
        if case_row
        else ""
    )

    existing_rows = load_decisions()
    kept_rows = [row for row in existing_rows if row.get("review_id") != decision_row["review_id"]]
    kept_rows.append(decision_row)
    with DECISIONS_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DECISION_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept_rows)
    return {"ok": True, "review_id": decision_row["review_id"], "decisions_path": str(DECISIONS_PATH)}


# ---------------------------------------------------------------------------
# Read-only database browser (Stage: /database tool)
#
# Lets a reviewer inspect the structured SQLite mirror entity-first (contracts,
# sub-contracts, people) with foreign keys resolved to human-readable values and
# the linked Word source(s) surfaced. This endpoint never writes to SQLite.
# ---------------------------------------------------------------------------

DB_BROWSE_TABLES = {"contract", "sub_contract", "person"}


def open_db() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise HTTPException(status_code=503, detail="SQLite database not found.")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def open_corrections() -> sqlite3.Connection:
    """Connection to the authoritative human-change log (corrections.db)."""
    return corrections_db.connect(corrections_db.default_path())


def hidden_clause(where: str, include_hidden: bool) -> str:
    """Append the soft-delete filter to a (possibly empty) WHERE clause."""
    if include_hidden:
        return where
    return f"{where} AND is_deleted = 0" if where else "WHERE is_deleted = 0"


def contract_dependents(connection: sqlite3.Connection, contract_id: str) -> dict[str, int]:
    """Counts of the rows that hang off a contract (the cascade subtree)."""
    n = lambda sql: connection.execute(sql, (contract_id,)).fetchone()[0]  # noqa: E731
    return {
        "sub_contract": n("SELECT COUNT(*) FROM sub_contract WHERE main_contract_id = ? AND is_deleted = 0"),
        "investor": n("SELECT COUNT(*) FROM investor WHERE contract_id = ? AND is_deleted = 0"),
        "investment": n("SELECT COUNT(*) FROM investment WHERE contract_id = ? AND is_deleted = 0"),
        "contract_place": n("SELECT COUNT(*) FROM contract_place WHERE contract_id = ? AND is_deleted = 0"),
    }


def lookup_value(
    connection: sqlite3.Connection, table: str, key_col: str, key: Any, value_col: str
) -> str | None:
    if key in (None, ""):
        return None
    cursor = connection.execute(
        f"SELECT {value_col} AS v FROM {table} WHERE {key_col} = ?", (key,)
    )
    row = cursor.fetchone()
    return row["v"] if row and row["v"] not in (None, "") else None


def display_text(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return str(value)


def yes_no(value: Any) -> str:
    return "Yes" if value in (1, "1", True) else "No"


def person_display_name(
    first: Any, last: Any, nickname: Any, father: Any = None, grandfather: Any = None
) -> str:
    core = " ".join(str(part) for part in (first, last) if part)
    if not core and father:
        core = f"di {father}"
    name = core or "Unnamed person"
    if nickname:
        name = f"{name} (detto {nickname})"
    return name


def clean_document(text: Any) -> str | None:
    """Render the SQLite ``document`` field for display.

    Almost every stored narrative carries literal ``\\r\\n`` escape sequences
    (the backslash characters, not real newlines). Convert those — and any real
    control characters — into clean line breaks. This is display-only; the
    database is never modified.
    """
    if text in (None, ""):
        return None
    out = str(text)
    out = (
        out.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .replace("\\t", "\t")
    )
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() or None


@lru_cache(maxsize=1)
def source_entries_by_id() -> dict[str, dict[str, Any]]:
    return {
        str(row.get("source_entry_id")): row for row in load_jsonl(SOURCE_ENTRIES_PATH)
    }


@lru_cache(maxsize=1)
def source_entries_by_key() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(SOURCE_ENTRIES_PATH):
        key = row.get("source_entry_key")
        if key and key not in index:
            index[str(key)] = row
    return index


@lru_cache(maxsize=1)
def image_candidates_by_entry() -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for row in load_jsonl(IMAGE_CANDIDATES_PATH):
        index.setdefault(str(row.get("source_entry_id")), []).append(row)
    return index


@lru_cache(maxsize=1)
def word_comments_by_key() -> dict[tuple[str, str], dict[str, Any]]:
    """Comment bodies from extraction, keyed (register_id, comment_id)."""
    return {
        (str(row.get("register_id")), str(row.get("comment_id"))): row
        for row in load_jsonl(WORD_COMMENTS_PATH)
    }


@lru_cache(maxsize=1)
def word_notes_by_key() -> dict[tuple[str, str], dict[str, Any]]:
    """Footnote/endnote bodies from extraction, keyed (register_id, note_id)."""
    return {
        (str(row.get("register_id")), str(row.get("note_id"))): row
        for row in load_jsonl(WORD_NOTES_PATH)
    }


@lru_cache(maxsize=1)
def folio_map_by_folder() -> dict[str, list[dict[str, Any]]]:
    """Image-folio map rows usable for contract linking, indexed by folder.

    Keyed on the archival folder (``10838``, ``1262``) — the column DB rows carry
    natively — so a manuscript page can be found for ANY record by (folder,
    folio), including records with no Word entry at all.
    """
    index: dict[str, list[dict[str, Any]]] = {}
    for row in load_jsonl(IMAGE_FOLIO_MAP_PATH):
        if not row.get("use_for_contract_linking"):
            continue
        folder = str(row.get("folder") or "").strip()
        if folder:
            index.setdefault(folder, []).append(row)
    return index


def manuscript_images_for(folder: Any, folio_raw: Any) -> list[dict[str, Any]]:
    """Manuscript page candidates for a DB record, by (folder, folio).

    Folio sides matter: a record on ``26v`` must show only the scan whose page
    candidate is ``26v`` (the opening 26v|27r), not also the previous opening
    that ends on ``26r`` — so the DB folio span is compared as proper folio
    TOKENS (number + bis/letter + recto/verso, via the pipeline's
    ``parse_db_folio``/``folio_sort_key``), not bare numbers. A token without a
    side (``26``, page-number styles) covers both sides of the leaf. Inline
    annotations (``160v [ORIG. 159v]``) are stripped; runaway spans are clamped
    so a malformed folio value cannot pull in half a register. Grouped per
    physical scan, same shape as the Word-entry images. Provisional map —
    ``needs_review`` travels with each image.
    """
    rows = folio_map_by_folder().get(str(folder or "").strip())
    if not rows:
        return []
    start_token, end_token = parse_db_folio(folio_raw)
    start_key = folio_sort_key(start_token)
    if start_key is None:
        return []
    end_key = folio_sort_key(end_token) or start_key
    if end_key < start_key:
        start_key, end_key = end_key, start_key
    has_side = lambda token: bool(re.search(r"[rv]\s*$", str(token or ""), re.IGNORECASE))  # noqa: E731
    if not has_side(start_token):
        start_key = (start_key[0], start_key[1], start_key[2], 0)
    if not has_side(end_token):
        end_key = (end_key[0], end_key[1], end_key[2], 1)
    if end_key[0] - start_key[0] > 3:
        end_key = (start_key[0] + 3, 99, 99, 1)
    hits = []
    for row in rows:
        key = folio_sort_key(row.get("folio_candidate"))
        if key is not None and start_key <= key <= end_key:
            hits.append(
                {
                    "image_path": row.get("image_path"),
                    "image_file": row.get("image_file"),
                    "image_role": row.get("image_role"),
                    "needs_review": row.get("needs_review"),
                    "review_reason": row.get("review_reason"),
                    "matched_folio": row.get("folio_candidate"),
                    "page_position": row.get("page_position"),
                    "entry_folio_role": None,
                }
            )
    return group_word_entry_images(hits)


def decision_link_status(db_row_id: str) -> tuple[set[str], set[str]]:
    """Reviewer verdicts for one DB row, keyed by content-stable entry key.

    Returns (confirmed_keys, rejected_keys) — the ``source_entry_key`` values a
    human in /reconcile marked as supporting (selected) or not supporting
    (rejected) this DB row.
    """
    confirmed: set[str] = set()
    rejected: set[str] = set()
    for decision in load_decisions():
        key = decision.get("source_entry_key") or decision.get("source_entry_id")
        if not key:
            continue
        if db_row_id in split_semicolon_values(decision.get("selected_db_row_ids")):
            confirmed.add(str(key))
        if db_row_id in split_semicolon_values(decision.get("rejected_db_row_ids")):
            rejected.add(str(key))
    return confirmed, rejected


def _entry_folio(entry: dict[str, Any]) -> str:
    raw = entry.get("folio_raw")
    if raw:
        return str(raw)
    return "–".join(
        str(part)
        for part in (entry.get("folio_start"), entry.get("folio_end"))
        if part
    )


def linked_word_sources(db_row_id: str) -> list[dict[str, Any]]:
    confirmed_keys, rejected_keys = decision_link_status(db_row_id)
    seen_entries: set[str] = set()
    covered_keys: set[str] = set()
    sources: list[dict[str, Any]] = []

    for row in load_jsonl(LINK_CANDIDATES_PATH):
        if row.get("db_row_id") != db_row_id:
            continue
        entry_id = str(row.get("source_entry_id") or "")
        if entry_id in seen_entries:
            continue
        seen_entries.add(entry_id)
        key = str(row.get("source_entry_key") or "")
        if key:
            covered_keys.add(key)
        try:
            strength = max(
                float(row.get("narrative_similarity_ratio") or 0.0),
                float(row.get("text_containment_ratio") or 0.0),
            )
        except (TypeError, ValueError):
            strength = 0.0
        if key in confirmed_keys:
            status = "confirmed"
        elif key in rejected_keys:
            status = "rejected"
        else:
            status = "proposed"
        folio = "–".join(
            str(part)
            for part in (row.get("entry_folio_start"), row.get("entry_folio_end"))
            if part
        )
        entry_for_counts = source_entries_by_id().get(str(entry_id)) or {}
        sources.append(
            {
                "source_entry_id": entry_id,
                "source_entry_key": key or None,
                "register_id": row.get("register_id"),
                "label": row.get("entry_label_raw") or row.get("entry_label_guess"),
                "date": row.get("entry_registration_date_raw"),
                "folio": folio,
                "relationship": row.get("relationship_type"),
                "strength": round(strength, 2),
                "status": status,
                # Paleographic doubts ("ricontrollare la foto") are rare (≈2% of
                # attached summaries) and high-value — surfaced as a badge on the
                # collapsed strip without expanding.
                "comment_count": len(entry_for_counts.get("comment_ids") or []),
            }
        )

    # Surface reviewer-confirmed links even when the matcher never proposed them.
    for key in confirmed_keys - covered_keys:
        entry = source_entries_by_key().get(key)
        if not entry:
            continue
        sources.append(
            {
                "source_entry_id": entry.get("source_entry_id"),
                "source_entry_key": key,
                "register_id": entry.get("register_id"),
                "label": entry.get("event_label_raw") or entry.get("event_label_guess"),
                "date": entry.get("registration_date_raw"),
                "folio": _entry_folio(entry),
                "relationship": "reviewer_confirmed",
                "strength": None,
                "status": "confirmed",
                "comment_count": len(entry.get("comment_ids") or []),
            }
        )

    status_rank = {"confirmed": 0, "proposed": 1, "rejected": 2}
    sources.sort(
        key=lambda item: (
            status_rank.get(item["status"], 3),
            -(item["strength"] or 0.0),
        )
    )
    return sources


def decision_link_maps() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """selected/rejected ``source_entry_key`` sets grouped by ``db_row_id``.

    One pass over the decision log so a record touching many DB rows (e.g. a
    person across several contracts) does not re-read it per row.
    """
    confirmed: dict[str, set[str]] = {}
    rejected: dict[str, set[str]] = {}
    for decision in load_decisions():
        key = decision.get("source_entry_key") or decision.get("source_entry_id")
        if not key:
            continue
        for db_row_id in split_semicolon_values(decision.get("selected_db_row_ids")):
            confirmed.setdefault(db_row_id, set()).add(str(key))
        for db_row_id in split_semicolon_values(decision.get("rejected_db_row_ids")):
            rejected.setdefault(db_row_id, set()).add(str(key))
    return confirmed, rejected


def word_sources_via_contracts(
    targets: list[tuple[str, str]], cap: int = 24
) -> tuple[list[dict[str, Any]], int]:
    """Aggregate linked Word sources for a set of (db_row_id, via_label) targets.

    A person has no direct Word link — the matcher links narratives to
    contract / sub_contract rows, not to person identities. So a person's
    manuscript context is surfaced *indirectly*, from the contracts where the
    person is recorded as an investor. Each returned source carries a ``via``
    label naming that contract, so the provenance stays explicit. Returns
    (sources_up_to_cap, total_before_cap); one pass over the link file.
    """
    target_label = {db_row_id: label for db_row_id, label in targets}
    if not target_label:
        return [], 0
    confirmed_map, rejected_map = decision_link_maps()
    seen: set[tuple[str, str]] = set()
    sources: list[dict[str, Any]] = []
    for row in load_jsonl(LINK_CANDIDATES_PATH):
        db_row_id = row.get("db_row_id")
        if db_row_id not in target_label:
            continue
        entry_id = str(row.get("source_entry_id") or "")
        dedupe = (str(db_row_id), entry_id)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        key = str(row.get("source_entry_key") or "")
        if key in confirmed_map.get(db_row_id, set()):
            status = "confirmed"
        elif key in rejected_map.get(db_row_id, set()):
            status = "rejected"
        else:
            status = "proposed"
        try:
            strength = max(
                float(row.get("narrative_similarity_ratio") or 0.0),
                float(row.get("text_containment_ratio") or 0.0),
            )
        except (TypeError, ValueError):
            strength = 0.0
        folio = "–".join(
            str(part)
            for part in (row.get("entry_folio_start"), row.get("entry_folio_end"))
            if part
        )
        sources.append(
            {
                "source_entry_id": entry_id,
                "source_entry_key": key or None,
                "register_id": row.get("register_id"),
                "label": row.get("entry_label_raw") or row.get("entry_label_guess"),
                "date": row.get("entry_registration_date_raw"),
                "folio": folio,
                "relationship": row.get("relationship_type"),
                "strength": round(strength, 2),
                "status": status,
                "via": target_label[db_row_id],
                "via_row_id": db_row_id,
            }
        )
    status_rank = {"confirmed": 0, "proposed": 1, "rejected": 2}
    sources.sort(
        key=lambda item: (status_rank.get(item["status"], 3), -(item["strength"] or 0.0))
    )
    return sources[:cap], len(sources)


# ---------------------------------------------------------------------------
# Corrections: schema/registry helpers, proposal store, validation
# ---------------------------------------------------------------------------


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def primary_key_for(db_row_id: str) -> tuple[str, str] | None:
    if not db_row_id or ":" not in db_row_id:
        return None
    table, raw_id = db_row_id.split(":", 1)
    if table not in PRIMARY_KEY_COLUMN:
        return None
    return table, raw_id


def normalize_value(value: Any) -> str:
    return "" if value is None else str(value)


def evidence_fingerprint(current_value: Any, source_quote: Any) -> str:
    digest = hashlib.sha1(
        (normalize_value(current_value) + "||" + normalize_value(source_quote)).encode("utf-8")
    ).hexdigest()
    return f"sha1:{digest}"


def validate_correction(table: str, field: str, change_type: str, proposed_value: str) -> str:
    if table not in CORRECTABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"Table '{table}' is not correctable.")
    meta = CORRECTABLE_FIELDS[table].get(field)
    if not meta:
        raise HTTPException(
            status_code=400, detail=f"Field '{field}' is not correctable on {table}."
        )
    if change_type not in CHANGE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown change_type '{change_type}'.")
    value = (proposed_value or "").strip()
    if change_type == "flag_uncertain":
        return value  # annotation only — never written to the DB
    if not value:
        raise HTTPException(status_code=400, detail="A proposed value is required.")
    input_type = meta["input_type"]
    if input_type == "date" and not DATE_PATTERN.match(value):
        raise HTTPException(status_code=400, detail="Date must be in YYYY-MM-DD form.")
    if input_type == "number":
        try:
            int(value)
        except ValueError:
            raise HTTPException(status_code=400, detail="Value must be a whole number.")
    if input_type == "enum" and value not in (meta.get("options") or []):
        raise HTTPException(
            status_code=400,
            detail=f"Value must be one of: {', '.join(meta.get('options') or [])}.",
        )
    # bool is stored as 0/1 (the corpus has no NULL booleans — Yes/No is exact).
    if input_type == "bool" and value not in ("0", "1"):
        raise HTTPException(status_code=400, detail="Value must be Yes (1) or No (0).")
    return value


def read_db_value(connection: sqlite3.Connection, table: str, pk_value: str, field: str) -> Any:
    pk_col = PRIMARY_KEY_COLUMN[table]
    row = connection.execute(
        f"SELECT {field} AS value FROM {table} WHERE {pk_col} = ?", (pk_value,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Database row not found.")
    return row["value"]


def load_proposals() -> list[dict[str, Any]]:
    return load_jsonl(PROPOSALS_PATH)


def proposal_by_id(proposal_id: str) -> dict[str, Any] | None:
    for proposal in load_proposals():
        if proposal.get("proposal_id") == proposal_id:
            return proposal
    return None


def save_proposal(proposal: dict[str, Any]) -> None:
    CORRECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    rows = [p for p in load_proposals() if p.get("proposal_id") != proposal["proposal_id"]]
    rows.append(proposal)
    with PROPOSALS_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_correction_event(event: dict[str, Any]) -> None:
    CORRECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def proposals_for_row(db_row_id: str) -> dict[str, dict[str, Any]]:
    """Latest non-rejected proposal per field for one DB row (for record chips)."""
    by_field: dict[str, dict[str, Any]] = {}
    for proposal in load_proposals():
        if proposal.get("db_row_id") != db_row_id or proposal.get("status") == "rejected":
            continue
        field = proposal.get("field")
        existing = by_field.get(field)
        if not existing or proposal.get("created_at", "") >= existing.get("created_at", ""):
            by_field[field] = proposal
    return by_field


def record_field(
    label: str,
    table: str,
    column: str | None,
    raw_value: Any,
    display: str,
    corrections: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """A record-detail field, annotated with edit affordance + correction state."""
    field: dict[str, Any] = {"label": label, "value": display, "column": column, "editable": False}
    meta = CORRECTABLE_FIELDS.get(table, {}).get(column) if column else None
    if meta:
        field["editable"] = True
        field["input_type"] = meta["input_type"]
        field["options"] = meta.get("options")
        field["current"] = normalize_value(raw_value)
    proposal = corrections.get(column) if column else None
    if proposal:
        field["correction"] = {
            "proposal_id": proposal.get("proposal_id"),
            "status": proposal.get("status"),
            "change_type": proposal.get("change_type"),
            "proposed_value": proposal.get("proposed_value"),
            "applied_at": proposal.get("applied_at"),
            "applied_by": proposal.get("applied_by"),
            "reviewed_by": proposal.get("reviewed_by"),
        }
    return field


def _attach_word_check(connection: sqlite3.Connection, fields: list[dict[str, Any]], db_row_id: str) -> None:
    """Attach the curated Word↔DB registration-date cross-check (if any) to the date field.
    Read-only evidence for the reviewer; never a write. Degrades gracefully — if the Word
    derived files are absent, the record still loads."""
    try:
        check = word_cross_check.check_for(connection, db_row_id)
    except Exception:
        check = None
    if not check or not check.get("surfaced"):  # T3 (one-day) is computed but held back
        return
    for field in fields:
        if field.get("column") == "registration_date":
            field["word_check"] = check
            break


def relink_field(
    connection: sqlite3.Connection,
    label: str,
    table: str,
    pk: Any,
    column: str,
    kind: str,
    current_id: Any,
) -> dict[str, Any]:
    """An FK field that re-points to a lookup row (title/place/currency/activity).
    Shows the resolved phrase + a `relink` descriptor the UI uses to pick an
    existing phrase, create one verbatim, or clear it — via /api/db/relink. The
    phrase itself is never edited in place (verbatim rows stay immutable)."""
    meta = LOOKUP_KINDS[kind]
    current_text = lookup_value(connection, meta["table"], meta["id"], current_id, meta["value"]) or ""
    return {
        "label": label,
        "value": display_text(current_text),
        "relink": {
            "table": table,
            "pk": str(pk),
            "field": column,
            "kind": kind,
            "current": current_text,
        },
    }


def _correction_chip(proposal: dict[str, Any] | None) -> dict[str, Any] | None:
    """The compact correction state a record cell carries, or None."""
    if not proposal:
        return None
    return {
        "proposal_id": proposal.get("proposal_id"),
        "status": proposal.get("status"),
        "change_type": proposal.get("change_type"),
        "proposed_value": proposal.get("proposed_value"),
        "applied_at": proposal.get("applied_at"),
        "applied_by": proposal.get("applied_by"),
        "reviewed_by": proposal.get("reviewed_by"),
    }


def editable_cell(
    table: str,
    row_pk: Any,
    column: str,
    raw_value: Any,
    display: str,
    proposals: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    """A single editable cell of a child row (investor/investment) for the
    Partners block. Mirrors ``record_field`` but carries its own ``db_row_id``
    (since each partner row addresses a different SQLite row), and reads its
    correction chip from a prebuilt per-row index (one JSONL pass, not one per
    cell). Columns absent from CORRECTABLE_FIELDS come back non-editable."""
    db_row_id = f"{table}:{row_pk}"
    meta = CORRECTABLE_FIELDS.get(table, {}).get(column)
    cell: dict[str, Any] = {
        "db_row_id": db_row_id,
        "column": column,
        "value": display,
        "editable": bool(meta),
        "current": normalize_value(raw_value),
        "input_type": (meta or {}).get("input_type", "text"),
        "options": (meta or {}).get("options"),
    }
    chip = _correction_chip(proposals.get(db_row_id, {}).get(column))
    if chip:
        cell["correction"] = chip
    return cell


def proposals_by_row() -> dict[str, dict[str, dict[str, Any]]]:
    """Latest non-rejected proposal per (db_row_id, field), in one JSONL pass —
    so a record with many child rows costs a single read, not one per cell."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for proposal in load_proposals():
        if proposal.get("status") == "rejected":
            continue
        rid = proposal.get("db_row_id")
        field = proposal.get("field")
        if not rid or not field:
            continue
        row = out.setdefault(rid, {})
        existing = row.get(field)
        if not existing or proposal.get("created_at", "") >= existing.get("created_at", ""):
            row[field] = proposal
    return out


def build_partners(
    connection: sqlite3.Connection, raw_id: str, include_hidden: bool = False
) -> dict[str, Any]:
    """The contract's people-and-money, as one joined 'Partners' block.

    An investor (one person's appearance on this contract) is linked to its
    investment — the money + the gp/lp role — through the ``investor_group``
    junction. A *joint* investment is one shared by several investors, so its
    cash is the shared figure and must not be read as per-person. Investors with
    no investment (role unknown) and investments with no investor (unattached)
    are both surfaced rather than dropped. Live rows only (is_deleted = 0)."""
    proposals = proposals_by_row()

    # How many *distinct people* share each investment on this contract → joint.
    # COUNT(DISTINCT person_id), NOT COUNT(*): a person double-linked to one
    # investment (a real data fault, separately flagged as dup_partner) must not
    # inflate the "joint · N" badge — that would over-report co-investment, which is
    # exactly what the project studies. Two real co-holders still count as 2; one
    # person entered twice counts as 1 (so the stake is correctly not joint).
    joint_counts = {
        r["investment_id"]: r["c"]
        for r in connection.execute(
            """
            SELECT ig.investment_id AS investment_id, COUNT(DISTINCT i.person_id) AS c
            FROM investor_group ig
            JOIN investor i ON i.investor_id = ig.investor_id
            WHERE i.contract_id = ? AND ig.is_deleted = 0 AND i.is_deleted = 0
            GROUP BY ig.investment_id
            """,
            (raw_id,),
        ).fetchall()
    }

    investor_rows = connection.execute(
        """
        SELECT i.investor_id AS investor_id, i.person_id AS person_id,
               i.profession AS profession, i.place_of_residence AS place_of_residence,
               i.place_of_origin AS place_of_origin,
               i.is_widow AS is_widow, i.is_guardian AS is_guardian, i.is_joint AS is_joint,
               i.citizen_florence AS citizen_florence, i.via_proxy AS via_proxy,
               i.is_jewish AS is_jewish, i.is_convert AS is_convert,
               i.heirs AS heirs, i.heirs_of AS heirs_of, i.and_c AS and_c,
               i.husband_first_name AS husband_first_name, i.husband_last_name AS husband_last_name,
               i.guardian_of AS guardian_of,
               i.title AS title, i.title_husband AS title_husband,
               i.title_grandfather AS title_grandfather, i.title_father_mother AS title_father_mother,
               p.first_name AS first_name, p.last_name AS last_name, p.nickname AS nickname,
               inv.investment_id AS investment_id, inv.type AS inv_type,
               inv.investment_cash AS investment_cash, inv.investment_non_cash AS investment_non_cash
        FROM investor i
        LEFT JOIN person p ON p.person_id = i.person_id
        LEFT JOIN investor_group ig ON ig.investor_id = i.investor_id AND ig.is_deleted = 0
        LEFT JOIN investment inv ON inv.investment_id = ig.investment_id AND inv.is_deleted = 0
        WHERE i.contract_id = ? AND i.is_deleted = 0
        ORDER BY i.investor_id
        """,
        (raw_id,),
    ).fetchall()

    def status_flags(inv: sqlite3.Row) -> str:
        # Status carries only the investor's *personal standing* (widow/guardian).
        # "joint" deliberately lives on the Capital cell instead — it is a property
        # of the *stake*, not the person, and showing it here too produced
        # on-screen contradictions with the derived joint badge (see the joint
        # handling in cash_cell + docs/data_quality/is_joint.md).
        return (
            ", ".join(
                flag
                for flag, present in (
                    ("widow", inv["is_widow"]),
                    ("guardian", inv["is_guardian"]),
                )
                if present in (1, "1", True)
            )
            or "—"
        )

    def cash_cell(inv_id: Any, cash: Any, non_cash: Any, is_joint: Any = 0) -> dict[str, Any]:
        # "Joint" is encoded two structurally-incompatible ways in this corpus:
        #   1. one investment shared by several investors (derivable: joint_counts
        #      gives the live share count → the authoritative "joint · N" badge);
        #   2. parallel investments of the same role+amount on one contract, marked
        #      only by the per-investor `is_joint` flag (no shared tranche to count).
        # The count sees (1) and is blind to (2); the flag is the only signal for
        # (2). We mark joint on EITHER, so we never hide real co-investment, and we
        # show "· N" only when N is structurally known (case 1). The stored flag is
        # noisy (stale/lonely cases) — those are surfaced for review, not trusted
        # silently. See docs/data_quality/is_joint.md.
        count = joint_counts.get(inv_id, 1) if inv_id is not None else 0
        joint = inv_id is not None and (count > 1 or is_joint in (1, "1", True))
        return {
            "display": display_text(cash),
            "non_cash": display_text(non_cash),
            "joint": joint,
            "joint_count": count,  # frontend shows "· N" only when count > 1
            "field": editable_cell("investment", inv_id, "investment_cash", cash, display_text(cash), proposals)
            if inv_id is not None
            else None,
        }

    def partner_attributes(inv: sqlite3.Row) -> dict[str, Any]:
        """The investor's full per-appearance record, grouped for the expand panel.
        Bool + free-text fields are editable cells; title/place FKs re-point to a
        lookup row (relink). `notable` counts the *sparse* meaningful attrs
        (excludes the ubiquitous title) to drive the row's expand cue."""
        iid = inv["investor_id"]

        def boolf(label: str, col: str) -> dict[str, Any]:
            return {"label": label, "cell": editable_cell("investor", iid, col, inv[col], yes_no(inv[col]), proposals)}

        def textf(label: str, col: str) -> dict[str, Any]:
            return {"label": label, "cell": editable_cell("investor", iid, col, inv[col], display_text(inv[col]), proposals)}

        def relinkf(label: str, col: str, kind: str) -> dict[str, Any]:
            return relink_field(connection, label, "investor", iid, col, kind, inv[col])

        groups = [
            {"label": "Origin & citizenship", "fields": [
                relinkf("Place of residence", "place_of_residence", "place"),
                relinkf("Place of origin", "place_of_origin", "place"),
                boolf("Citizen of Florence", "citizen_florence"),
            ]},
            {"label": "Religion", "fields": [
                boolf("Recorded as Jewish", "is_jewish"),
                boolf("Recorded as convert", "is_convert"),
            ]},
            {"label": "Capacity — how they participate", "fields": [
                boolf("Acted via proxy", "via_proxy"),
                boolf("Recorded as widow", "is_widow"),
                textf("Husband — first name", "husband_first_name"),
                textf("Husband — last name", "husband_last_name"),
                boolf("Acting as guardian", "is_guardian"),
                textf("Guardian of", "guardian_of"),
                boolf("Heirs", "heirs"),
                boolf("Heirs of", "heirs_of"),
                boolf("“& company” (e compagni)", "and_c"),
            ]},
            {"label": "Titles", "fields": [
                relinkf("Title", "title", "title"),
                relinkf("Father/mother's title", "title_father_mother", "title"),
                relinkf("Grandfather's title", "title_grandfather", "title"),
                relinkf("Husband's title", "title_husband", "title"),
            ]},
        ]

        notable = sum(
            1 for col in ("citizen_florence", "via_proxy", "is_widow", "is_guardian",
                          "is_jewish", "is_convert", "heirs", "heirs_of", "and_c")
            if inv[col] in (1, "1", True)
        )
        if (inv["husband_first_name"] or "").strip() or (inv["husband_last_name"] or "").strip():
            notable += 1
        if (inv["guardian_of"] or "").strip():
            notable += 1
        if inv["place_of_origin"] not in (0, None):
            notable += 1
        return {"notable": notable, "groups": groups}

    def make_partner_row(inv: sqlite3.Row, *, removed: bool = False) -> dict[str, Any]:
        inv_id = inv["investment_id"]
        return {
            "key": f"investor:{inv['investor_id']}",
            "person": {
                "id": str(inv["person_id"]),
                "name": person_display_name(inv["first_name"], inv["last_name"], inv["nickname"]),
            }
            if inv["person_id"] is not None
            else None,
            "role": editable_cell("investment", inv_id, "type", inv["inv_type"], display_text(inv["inv_type"]), proposals)
            if inv_id is not None
            else None,
            # A removed row shows no joint badge (it's greyed and read-only anyway).
            "cash": cash_cell(inv_id, inv["investment_cash"], inv["investment_non_cash"], 0 if removed else inv["is_joint"]),
            "profession": editable_cell(
                "investor", inv["investor_id"], "profession", inv["profession"], display_text(inv["profession"]), proposals
            ),
            "residence": display_text(
                lookup_value(connection, "place", "place_id", inv["place_of_residence"], "place_name")
            ),
            "status": "removed" if removed else status_flags(inv),
            "removed": removed,
            # The expand panel's full attribute set — live rows only (removed rows
            # are greyed/read-only and don't carry the extra columns).
            "attributes": None if removed else partner_attributes(inv),
        }

    rows: list[dict[str, Any]] = [make_partner_row(inv) for inv in investor_rows]

    # Unattached investments: money recorded on the contract with no investor
    # linked through the junction. Rare, but surfacing beats silently dropping —
    # and it's exactly the state left when the sole holder of a stake is removed.
    orphan_investments = connection.execute(
        """
        SELECT inv.investment_id AS investment_id, inv.type AS inv_type,
               inv.investment_cash AS investment_cash, inv.investment_non_cash AS investment_non_cash
        FROM investment inv
        WHERE inv.contract_id = ? AND inv.is_deleted = 0
          AND NOT EXISTS (
            SELECT 1 FROM investor_group ig
            JOIN investor i ON i.investor_id = ig.investor_id
            WHERE ig.investment_id = inv.investment_id AND ig.is_deleted = 0 AND i.is_deleted = 0
          )
        ORDER BY inv.investment_id
        """,
        (raw_id,),
    ).fetchall()
    for iv in orphan_investments:
        inv_id = iv["investment_id"]
        rows.append(
            {
                "key": f"investment:{inv_id}",
                "person": None,
                "role": editable_cell("investment", inv_id, "type", iv["inv_type"], display_text(iv["inv_type"]), proposals),
                "cash": cash_cell(inv_id, iv["investment_cash"], iv["investment_non_cash"]),
                "profession": None,
                "residence": "—",
                "status": "unattached",
                "removed": False,
            }
        )

    live_count = len(rows)

    # Removed (soft-deleted) partners — only on request; rendered greyed with a
    # Restore action. Joins their now-deleted links so we can still show who/what
    # was removed (the investment row itself is left in place, decision 2026-06-17).
    if include_hidden:
        removed_rows = connection.execute(
            """
            SELECT i.investor_id AS investor_id, i.person_id AS person_id,
                   i.profession AS profession, i.place_of_residence AS place_of_residence,
                   i.is_widow AS is_widow, i.is_guardian AS is_guardian, i.is_joint AS is_joint,
                   p.first_name AS first_name, p.last_name AS last_name, p.nickname AS nickname,
                   inv.investment_id AS investment_id, inv.type AS inv_type,
                   inv.investment_cash AS investment_cash, inv.investment_non_cash AS investment_non_cash
            FROM investor i
            LEFT JOIN person p ON p.person_id = i.person_id
            LEFT JOIN investor_group ig ON ig.investor_id = i.investor_id
            LEFT JOIN investment inv ON inv.investment_id = ig.investment_id
            WHERE i.contract_id = ? AND i.is_deleted = 1
            ORDER BY i.investor_id
            """,
            (raw_id,),
        ).fetchall()
        rows.extend(make_partner_row(inv, removed=True) for inv in removed_rows)

    return {"count": live_count, "rows": rows, "removed_count": len(rows) - live_count}


def build_places(connection: sqlite3.Connection, raw_id: str, include_hidden: bool = False) -> dict[str, Any]:
    """The contract's place(s) — where the firm operated — as an editable block.
    `address` is free text (editable); the place itself is re-pointed by remove +
    add (its place_id is part of the composite PK). Live rows only by default."""
    def make(pl: sqlite3.Row, *, removed: bool = False) -> dict[str, Any]:
        return {
            "key": f"place:{pl['place_id']}",
            "place_id": str(pl["place_id"]),
            "place": display_text(pl["place_name"]),
            "address": pl["address"] or "",
            "removed": removed,
        }

    sql = (
        "SELECT cp.place_id AS place_id, cp.address AS address, p.place_name AS place_name "
        "FROM contract_place cp LEFT JOIN place p ON p.place_id = cp.place_id "
        "WHERE cp.contract_id = ? AND cp.is_deleted = ? ORDER BY cp.place_id"
    )
    rows = [make(pl) for pl in connection.execute(sql, (raw_id, 0)).fetchall()]
    live_count = len(rows)
    if include_hidden:
        rows.extend(make(pl, removed=True) for pl in connection.execute(sql, (raw_id, 1)).fetchall())
    return {"count": live_count, "rows": rows, "removed_count": len(rows) - live_count}


def contract_detail(connection: sqlite3.Connection, raw_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM contract WHERE contract_id = ?", (raw_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contract not found.")
    data = dict(row)
    currency = lookup_value(connection, "currency", "currency_id", data.get("currency_id"), "currency")
    archive_ref = " / ".join(
        str(part) for part in (data.get("archive"), data.get("series"), data.get("folder")) if part
    )
    total = data.get("total")
    total_text = f"{total} {currency}".strip() if total not in (None, "") else None
    corrections = proposals_for_row(f"contract:{raw_id}")
    fields = [
        record_field("Firm name", "contract", "firm_name", data.get("firm_name"), display_text(data.get("firm_name")), corrections),
        record_field("Registration date", "contract", "registration_date", data.get("registration_date"), display_text(data.get("registration_date")), corrections),
        record_field("Start date", "contract", "start_date", data.get("start_date"), display_text(data.get("start_date")), corrections),
        record_field("Folio", "contract", "folio", data.get("folio"), display_text(data.get("folio")), corrections),
        record_field("Archive / series / folder", "contract", None, None, display_text(archive_ref), corrections),
        record_field("Total capital", "contract", "total", data.get("total"), display_text(total_text), corrections),
        record_field("Duration (months)", "contract", "duration_months", data.get("duration_months"), display_text(data.get("duration_months")), corrections),
        record_field("Automatic renewal", "contract", "automatic_renewal", data.get("automatic_renewal"), yes_no(data.get("automatic_renewal")), corrections),
        record_field("Renewal period (months)", "contract", "automatic_renewal_months", data.get("automatic_renewal_months"), display_text(data.get("automatic_renewal_months")), corrections),
        record_field("Special clauses", "contract", "clauses", data.get("clauses"), yes_no(data.get("clauses")), corrections),
        record_field("Place at discretion", "contract", "pl_discretion", data.get("pl_discretion"), yes_no(data.get("pl_discretion")), corrections),
        record_field("Economic activity at discretion", "contract", "ec_discretion", data.get("ec_discretion"), yes_no(data.get("ec_discretion")), corrections),
        record_field("Administrators named", "contract", "administrators", data.get("administrators"), yes_no(data.get("administrators")), corrections),
        record_field("Additional documents", "contract", "additional_docs", data.get("additional_docs"), yes_no(data.get("additional_docs")), corrections),
        relink_field(connection, "Economic sector", "contract", raw_id, "economic_sector", "economic_activity", data.get("economic_sector")),
        relink_field(connection, "Currency", "contract", raw_id, "currency_id", "currency", data.get("currency_id")),
    ]
    _attach_word_check(connection, fields, f"contract:{raw_id}")

    places = build_places(connection, raw_id)
    subs = connection.execute(
        """
        SELECT contract_id, sub_type, registration_date, folio, sub_firm_name
        FROM sub_contract WHERE main_contract_id = ? AND is_deleted = 0
        ORDER BY registration_date
        """,
        (raw_id,),
    ).fetchall()

    # Investors + investments are merged into one editable Partners block
    # (people + role + capital), keyed through investor_group — see build_partners.
    partners = build_partners(connection, raw_id)

    sections: list[dict[str, Any]] = []
    if subs:
        sections.append(
            {
                "title": f"Sub-contracts ({len(subs)})",
                "columns": ["Type", "Date", "Folio", "Firm"],
                "link_table": "sub_contract",
                "rows": [
                    {
                        "id": str(s["contract_id"]),
                        "cells": [
                            display_text(s["sub_type"]),
                            display_text(s["registration_date"]),
                            display_text(s["folio"]),
                            display_text(s["sub_firm_name"]),
                        ],
                    }
                    for s in subs
                ],
            }
        )
    return {
        "table": "contract",
        "id": str(raw_id),
        "row_id": f"contract:{raw_id}",
        "title": data.get("firm_name") or f"Contract {raw_id}",
        "subtitle": f"Main contract · {raw_id}",
        "fields": fields,
        "partners": partners,
        "places": places,
        "sections": sections,
        "document": clean_document(data.get("document")),
        "document_correction": corrections.get("document"),
        "manuscript_images": manuscript_images_for(data.get("folder"), data.get("folio")),
        "word_sources": linked_word_sources(f"contract:{raw_id}"),
        "word_sources_note": None,
    }


def sub_contract_detail(connection: sqlite3.Connection, raw_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM sub_contract WHERE contract_id = ?", (raw_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sub-contract not found.")
    data = dict(row)
    archive_ref = " / ".join(
        str(part) for part in (data.get("archive"), data.get("series"), data.get("folder")) if part
    )
    corrections = proposals_for_row(f"sub_contract:{raw_id}")
    fields = [
        record_field("Sub-firm name", "sub_contract", "sub_firm_name", data.get("sub_firm_name"), display_text(data.get("sub_firm_name")), corrections),
        record_field("Type", "sub_contract", "sub_type", data.get("sub_type"), display_text(data.get("sub_type")), corrections),
        record_field("Registration date", "sub_contract", "registration_date", data.get("registration_date"), display_text(data.get("registration_date")), corrections),
        record_field("End date", "sub_contract", "end_date", data.get("end_date"), display_text(data.get("end_date")), corrections),
        record_field("Renewal (months)", "sub_contract", "renewal_months", data.get("renewal_months"), display_text(data.get("renewal_months")), corrections),
        record_field("Folio", "sub_contract", "folio", data.get("folio"), display_text(data.get("folio")), corrections),
        record_field("Archive / series / folder", "sub_contract", None, None, display_text(archive_ref), corrections),
    ]
    _attach_word_check(connection, fields, f"sub_contract:{raw_id}")

    sections: list[dict[str, Any]] = []
    main_id = data.get("main_contract_id")
    if main_id not in (None, ""):
        main = connection.execute(
            "SELECT contract_id, firm_name, registration_date, folio FROM contract WHERE contract_id = ?",
            (main_id,),
        ).fetchone()
        if main:
            sections.append(
                {
                    "title": "Parent contract",
                    "columns": ["Firm", "Date", "Folio"],
                    "link_table": "contract",
                    "rows": [
                        {
                            "id": str(main["contract_id"]),
                            "cells": [
                                display_text(main["firm_name"]),
                                display_text(main["registration_date"]),
                                display_text(main["folio"]),
                            ],
                        }
                    ],
                }
            )

    return {
        "table": "sub_contract",
        "id": str(raw_id),
        "row_id": f"sub_contract:{raw_id}",
        "title": data.get("sub_firm_name") or f"Sub-contract {raw_id}",
        "subtitle": f"{display_text(data.get('sub_type'))} · {raw_id}",
        "fields": fields,
        "sections": sections,
        "document": clean_document(data.get("document")),
        "document_correction": corrections.get("document"),
        "manuscript_images": manuscript_images_for(data.get("folder"), data.get("folio")),
        "word_sources": linked_word_sources(f"sub_contract:{raw_id}"),
        "word_sources_note": None,
    }


def person_detail(connection: sqlite3.Connection, raw_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM person WHERE person_id = ?", (raw_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Person not found.")
    data = dict(row)
    corrections = proposals_for_row(f"person:{raw_id}")
    fields = [
        record_field("First name", "person", "first_name", data.get("first_name"), display_text(data.get("first_name")), corrections),
        record_field("Father / mother", "person", "father_mother", data.get("father_mother"), display_text(data.get("father_mother")), corrections),
        record_field("Grandfather", "person", "grandfather", data.get("grandfather"), display_text(data.get("grandfather")), corrections),
        record_field("Last name", "person", "last_name", data.get("last_name"), display_text(data.get("last_name")), corrections),
        record_field("Nickname", "person", "nickname", data.get("nickname"), display_text(data.get("nickname")), corrections),
        record_field("Recorded as woman", "person", "is_woman", data.get("is_woman"), yes_no(data.get("is_woman")), corrections),
    ]

    contracts = connection.execute(
        """
        SELECT i.contract_id AS contract_id, i.profession AS profession,
               c.firm_name AS firm_name, c.registration_date AS registration_date
        FROM investor i
        LEFT JOIN contract c ON c.contract_id = i.contract_id
        WHERE i.person_id = ? AND i.is_deleted = 0
        ORDER BY c.registration_date
        """,
        (raw_id,),
    ).fetchall()
    sections: list[dict[str, Any]] = []
    if contracts:
        sections.append(
            {
                "title": f"Appears in contracts ({len(contracts)})",
                "columns": ["Firm", "Date", "Profession"],
                "link_table": "contract",
                "rows": [
                    {
                        "id": str(c["contract_id"]),
                        "cells": [
                            display_text(c["firm_name"]),
                            display_text(c["registration_date"]),
                            display_text(c["profession"]),
                        ],
                    }
                    for c in contracts
                ],
            }
        )

    # A person has no direct Word link. Surface manuscript context indirectly,
    # from the contracts where this person is recorded as an investor — each
    # source labelled with the contract it came from.
    targets: dict[str, str] = {}
    for c in contracts:
        cid = c["contract_id"]
        if cid in (None, ""):
            continue
        targets.setdefault(
            f"contract:{cid}", c["firm_name"] or f"Contract {cid}"
        )
    cap = 24
    word_sources, total = word_sources_via_contracts(list(targets.items()), cap=cap)
    word_sources_note: str | None
    if not contracts:
        word_sources_note = None
    elif total == 0:
        word_sources_note = (
            "No Word entry is linked to the contracts this person appears in."
        )
    else:
        word_sources_note = (
            "Indirect: these are the Word entries (and folio images) for the "
            f"{len(targets)} contract(s) where this person is recorded as an "
            "investor."
        )
        if total > cap:
            word_sources_note += f" Showing the first {cap} of {total}."

    return {
        "table": "person",
        "id": str(raw_id),
        "row_id": f"person:{raw_id}",
        "title": person_display_name(
            data.get("first_name"),
            data.get("last_name"),
            data.get("nickname"),
            data.get("father_mother"),
            data.get("grandfather"),
        ),
        "subtitle": f"Person · {raw_id}",
        "fields": fields,
        "sections": sections,
        "document": None,
        "word_sources": word_sources,
        "word_sources_note": word_sources_note,
    }


def search_meta(date: Any, folio: Any, folder: Any, sub_type: Any = None) -> str:
    """Readable one-line meta for a search result.

    Placeholder dates (``0000-00-00``) are suppressed — they are a known
    data-quality issue, not information — and the register folder is shown so
    a result can be located archivally even when the firm name is blank.
    """
    date_text = str(date or "").strip()
    if date_text == "0000-00-00":
        date_text = "no date"
    parts = [
        str(sub_type or "").strip(),
        date_text,
        f"c. {str(folio).strip()}" if str(folio or "").strip() else "",
        f"reg. {str(folder).strip()}" if str(folder or "").strip() else "",
    ]
    return " · ".join(part for part in parts if part)


# Whitelisted ORDER BY bodies (after the exact-id-float lead key). No user text is
# interpolated — the `sort` param only selects a key. Folio is intentionally absent:
# its values are unparsed strings ("154r-v", "97r [ORIG. 96r]") and sort unreliably.
_DATE_PLACEHOLDER_LAST = (
    "CASE WHEN registration_date IS NULL OR registration_date IN ('', '0000-00-00') "
    "THEN 1 ELSE 0 END"
)
SEARCH_SORTS: dict[str, dict[str, str]] = {
    "contract": {
        "date_asc": f"{_DATE_PLACEHOLDER_LAST}, registration_date ASC",
        "date_desc": f"{_DATE_PLACEHOLDER_LAST}, registration_date DESC",
        "id_asc": "CAST(contract_id AS INTEGER) ASC",
        "id_desc": "CAST(contract_id AS INTEGER) DESC",
    },
    "person": {
        "name_asc": "CASE WHEN trim(coalesce(last_name, '')) = '' THEN 1 ELSE 0 END, last_name, first_name",
        "id_asc": "CAST(person_id AS INTEGER) ASC",
        "id_desc": "CAST(person_id AS INTEGER) DESC",
    },
}
SEARCH_SORT_DEFAULT = {"contract": "date_asc", "sub_contract": "date_asc", "person": "name_asc"}


def search_order_by(table: str, sort: str) -> str:
    """ORDER BY body for the chosen sort, falling back to the table default."""
    options = SEARCH_SORTS["person" if table == "person" else "contract"]
    return options.get(sort) or options[SEARCH_SORT_DEFAULT[table]]


@app.get("/api/db/search")
def db_search(
    table: str,
    q: str = "",
    limit: int = Query(default=60, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    sort: str = "",
    register: str = "",
    year_from: str = "",
    year_to: str = "",
    sub_type: str = "",
    gender: str = "",
    include_hidden: bool = False,
) -> dict[str, Any]:
    if table not in DB_BROWSE_TABLES:
        raise HTTPException(status_code=400, detail="Unknown table.")
    order_body = search_order_by(table, sort)
    term = q.strip()
    like = f"%{term}%"

    # WHERE = a free-text group AND the structured facets. Register matches the
    # TRIMMED folder so trailing-space duplicates ('10848' / '10848 ') collapse to
    # one register; year filters compare the ISO date's leading 4 chars.
    conditions: list[str] = []
    params: list[Any] = []
    if term:
        if table == "contract":
            # Contracts also match a partner's name (investor → person) and the
            # economic-activity text, so a historian can find a firm by who is in it
            # or what trade it ran — not only the firm_name string.
            conditions.append(
                "(firm_name LIKE ? OR folio LIKE ? OR CAST(contract_id AS TEXT) LIKE ? "
                "OR EXISTS (SELECT 1 FROM investor iv JOIN person p ON p.person_id = iv.person_id "
                "  WHERE iv.contract_id = contract.contract_id AND iv.is_deleted = 0 "
                "  AND (p.first_name LIKE ? OR p.last_name LIKE ? OR p.nickname LIKE ?)) "
                "OR EXISTS (SELECT 1 FROM economic_activity ea "
                "  WHERE CAST(ea.ec_activity_id AS TEXT) = CAST(contract.economic_sector AS TEXT) "
                "  AND ea.activity LIKE ?))"
            )
            params += [like, like, like, like, like, like, like]
        elif table == "sub_contract":
            conditions.append("(sub_firm_name LIKE ? OR folio LIKE ? OR CAST(contract_id AS TEXT) LIKE ?)")
            params += [like, like, like]
        else:
            conditions.append(
                "(first_name LIKE ? OR last_name LIKE ? OR nickname LIKE ? OR CAST(person_id AS TEXT) LIKE ?)"
            )
            params += [like, like, like, like]
    if table in ("contract", "sub_contract"):
        if register:
            conditions.append("TRIM(folder) = ?")
            params.append(register)
        if year_from:
            conditions.append("substr(registration_date, 1, 4) >= ?")
            params.append(year_from)
        if year_to:
            conditions.append("substr(registration_date, 1, 4) <= ?")
            params.append(year_to)
    if table == "sub_contract" and sub_type:
        conditions.append("sub_type = ?")
        params.append(sub_type)
    if table == "person" and gender in ("woman", "man"):
        conditions.append("is_woman = ?")
        params.append(1 if gender == "woman" else 0)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    where = hidden_clause(where, include_hidden)

    connection = open_db()
    try:
        if table == "contract":
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM contract {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"SELECT contract_id, registration_date, folio, firm_name, folder FROM contract {where} "
                # A purely numeric query floats the exact id to the top; the chosen
                # sort (placeholder dates last) follows.
                f"ORDER BY CASE WHEN CAST(contract_id AS TEXT) = ? THEN 0 ELSE 1 END, {order_body} "
                "LIMIT ? OFFSET ?",
                (*params, term, limit, offset),
            ).fetchall()
            results = [
                {
                    "id": str(r["contract_id"]),
                    "row_id": f"contract:{r['contract_id']}",
                    "title": (r["firm_name"] or "").strip() or f"Contract {r['contract_id']}",
                    "meta": search_meta(
                        r["registration_date"], r["folio"], r["folder"]
                    ),
                }
                for r in rows
            ]
        elif table == "sub_contract":
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM sub_contract {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"SELECT contract_id, registration_date, folio, sub_firm_name, sub_type, folder FROM sub_contract {where} "
                f"ORDER BY CASE WHEN CAST(contract_id AS TEXT) = ? THEN 0 ELSE 1 END, {order_body} "
                "LIMIT ? OFFSET ?",
                (*params, term, limit, offset),
            ).fetchall()
            results = [
                {
                    "id": str(r["contract_id"]),
                    "row_id": f"sub_contract:{r['contract_id']}",
                    "title": (r["sub_firm_name"] or "").strip() or f"Sub-contract {r['contract_id']}",
                    "meta": search_meta(
                        r["registration_date"], r["folio"], r["folder"], r["sub_type"]
                    ),
                }
                for r in rows
            ]
        else:  # person
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM person {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"SELECT person_id, first_name, last_name, nickname FROM person {where} "
                # Mononyms (blank surname) sort last, not first (the default name sort).
                f"ORDER BY CASE WHEN CAST(person_id AS TEXT) = ? THEN 0 ELSE 1 END, {order_body} "
                "LIMIT ? OFFSET ?",
                (*params, term, limit, offset),
            ).fetchall()
            results = [
                {
                    "id": str(r["person_id"]),
                    "row_id": f"person:{r['person_id']}",
                    "title": person_display_name(
                        r["first_name"], r["last_name"], r["nickname"]
                    ),
                    "meta": f"#{r['person_id']}",
                }
                for r in rows
            ]
        return {
            "table": table,
            "total": total,
            "shown": len(results),
            "offset": offset,
            "results": results,
        }
    finally:
        connection.close()


def register_label(series: str | None, folder: str | None) -> str:
    """Human register label from the (case-varying) series + (trimmed) folder.

    The corpus stores the series two ways for the Camera di Commercio register and
    leaves trailing spaces on some folders; this collapses both to one clean label
    at read time (no data writes — the stored values stay verbatim)."""
    folder = (folder or "").strip()
    series_lc = (series or "").lower()
    if not folder:
        return "(no register)"
    if "mercanzia" in series_lc:
        return f"Mercanzia {folder}"
    if "commercio" in series_lc or "camera" in series_lc:
        return f"Camera di Commercio {folder}"
    return folder


@app.get("/api/db/facets")
def db_facets(table: str) -> dict[str, Any]:
    """Facet values for the browse filters: registers (trimmed folder + count),
    a by-decade year histogram, the actual year span, and sub-types. Registers /
    dates apply only to contracts & sub-contracts; people carry neither."""
    if table not in DB_BROWSE_TABLES:
        raise HTTPException(status_code=400, detail="Unknown table.")
    empty: dict[str, Any] = {
        "registers": [],
        "year_histogram": [],
        "year_min": None,
        "year_max": None,
        "sub_types": [],
        "genders": [],
    }
    if table == "person":
        connection = open_db()
        try:
            counts = {
                int(r["is_woman"]): r["c"]
                for r in connection.execute(
                    "SELECT is_woman, COUNT(*) AS c FROM person WHERE is_deleted = 0 "
                    "AND is_woman IS NOT NULL GROUP BY is_woman"
                ).fetchall()
            }
        finally:
            connection.close()
        genders = [
            {"value": value, "label": label, "count": counts.get(flag, 0)}
            for value, label, flag in (("woman", "Women", 1), ("man", "Men", 0))
            if counts.get(flag, 0)
        ]
        return {**empty, "genders": genders}
    dated = (
        "is_deleted = 0 AND registration_date IS NOT NULL "
        "AND registration_date NOT IN ('', '0000-00-00')"
    )
    connection = open_db()
    try:
        registers = [
            {
                "folder": (r["folder"] or "").strip(),
                "label": register_label(r["series"], r["folder"]),
                "count": r["c"],
            }
            for r in connection.execute(
                f"SELECT TRIM(folder) AS folder, MIN(series) AS series, COUNT(*) AS c "
                f"FROM {table} WHERE is_deleted = 0 GROUP BY TRIM(folder) ORDER BY c DESC"
            ).fetchall()
        ]
        histogram = [
            {"decade": int(r["decade"]), "count": r["c"]}
            for r in connection.execute(
                f"SELECT substr(registration_date, 1, 3) || '0' AS decade, COUNT(*) AS c "
                f"FROM {table} WHERE {dated} GROUP BY decade ORDER BY decade"
            ).fetchall()
            if str(r["decade"]).isdigit()
        ]
        span = connection.execute(
            f"SELECT MIN(substr(registration_date, 1, 4)) AS lo, "
            f"MAX(substr(registration_date, 1, 4)) AS hi FROM {table} WHERE {dated}"
        ).fetchone()
        sub_types = []
        if table == "sub_contract":
            sub_types = [
                {"value": r["sub_type"], "count": r["c"]}
                for r in connection.execute(
                    "SELECT sub_type, COUNT(*) AS c FROM sub_contract "
                    "WHERE is_deleted = 0 AND sub_type IS NOT NULL AND sub_type <> '' "
                    "GROUP BY sub_type ORDER BY c DESC"
                ).fetchall()
            ]
        return {
            "registers": registers,
            "year_histogram": histogram,
            "year_min": int(span["lo"]) if span and span["lo"] else None,
            "year_max": int(span["hi"]) if span and span["hi"] else None,
            "sub_types": sub_types,
            "genders": [],
        }
    finally:
        connection.close()


@app.get("/api/db/record/{table}/{record_id}")
def db_record(table: str, record_id: str, include_hidden: bool = False) -> dict[str, Any]:
    if table not in DB_BROWSE_TABLES:
        raise HTTPException(status_code=400, detail="Unknown table.")
    connection = open_db()
    try:
        if table == "contract":
            record = contract_detail(connection, record_id)
            record["dependents"] = contract_dependents(connection, record_id)
            # With removed partners requested, rebuild the block to include them.
            if include_hidden:
                record["partners"] = build_partners(connection, record_id, include_hidden=True)
                record["places"] = build_places(connection, record_id, include_hidden=True)
        elif table == "sub_contract":
            record = sub_contract_detail(connection, record_id)
        else:
            record = person_detail(connection, record_id)
        col = PRIMARY_KEY_COLUMN[table]
        flag = connection.execute(
            f"SELECT is_deleted FROM {table} WHERE {col} = ?", (record_id,)
        ).fetchone()
        record["is_deleted"] = bool(flag and flag["is_deleted"])
    finally:
        connection.close()
    clog = open_corrections()
    try:
        record["change_history"] = corrections_db.history_for_row(
            clog, table, {col: int(record_id)}
        )
    finally:
        clog.close()
    return record


class RecordAction(BaseModel):
    reviewer: str = Field(min_length=1)
    reason: str = ""


def _set_hidden(table: str, record_id: str, *, hidden: bool, action: RecordAction) -> dict[str, Any]:
    """Soft-delete (hide) or restore a record: flip `is_deleted` in main.db and log
    the operation to the authoritative change log. Reversible; nothing is purged."""
    if table not in DB_BROWSE_TABLES:
        raise HTTPException(status_code=400, detail="Unknown table.")
    if hidden and not action.reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to hide a record.")
    col = PRIMARY_KEY_COLUMN[table]
    connection = open_db()
    try:
        row = connection.execute(f"SELECT {col} FROM {table} WHERE {col} = ?", (record_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Record not found.")
        with connection:
            connection.execute(
                f"UPDATE {table} SET is_deleted = ? WHERE {col} = ?", (1 if hidden else 0, record_id)
            )
    finally:
        connection.close()
    clog = open_corrections()
    try:
        corrections_db.record_operation(
            clog,
            op="delete" if hidden else "restore",
            db_table=table,
            pk={col: int(record_id)},
            by=action.reviewer,
            reason=action.reason.strip() or None,
            note=action.reason.strip() or None,
        )
    finally:
        clog.close()
    return {"ok": True, "is_deleted": hidden}


@app.post("/api/db/record/{table}/{record_id}/hide")
def hide_record(table: str, record_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_hidden(table, record_id, hidden=True, action=action)


@app.post("/api/db/record/{table}/{record_id}/restore")
def restore_record(table: str, record_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_hidden(table, record_id, hidden=False, action=action)


# ---------------------------------------------------------------------------
# Remove / restore a PARTNER (an investor's appearance on a contract).
#
# Unlike record hide (one row), this is a small audited cascade: soft-delete the
# investor + its investor_group link(s), and re-derive `is_joint` on a tranche
# that drops to a single survivor. The investment row is LEFT in place — if it
# loses its last investor it simply shows as "unattached" (decision 2026-06-17).
# Never touches `person` (identity, may appear elsewhere) or lookups. Cross-
# contract links are unlinked but their (foreign) investment is never re-derived.
# Reversible + fully logged; see docs/remove_partner_design.md.
# ---------------------------------------------------------------------------


def _rederive_is_joint(
    connection: sqlite3.Connection,
    clog: sqlite3.Connection,
    investment_id: Any,
    *,
    reviewer: str,
    reason: str,
) -> None:
    """Set `is_joint` to match the live share structure of one investment:
    1 if ≥2 live investors share it, else 0. Idempotent — writes (and logs) only
    the rows that actually change, so a normal solo remove is a no-op here."""
    members = connection.execute(
        """SELECT i.investor_id AS investor_id, i.is_joint AS is_joint
           FROM investor_group g JOIN investor i ON i.investor_id = g.investor_id
           WHERE g.investment_id = ? AND g.is_deleted = 0 AND i.is_deleted = 0""",
        (investment_id,),
    ).fetchall()
    target = 1 if len(members) >= 2 else 0
    for m in members:
        current = 1 if m["is_joint"] in (1, "1", True) else 0
        if current == target:
            continue
        with connection:
            connection.execute(
                "UPDATE investor SET is_joint = ? WHERE investor_id = ?", (target, m["investor_id"])
            )
        corrections_db.record_operation(
            clog,
            op="update",
            db_table="investor",
            pk={"investor_id": int(m["investor_id"])},
            field="is_joint",
            before_value=str(current),
            after_value=str(target),
            by=reviewer,
            reason=f"is_joint re-derived ({reason})",
        )


def _set_partner_removed(cid: str, investor_id: str, *, removed: bool, action: RecordAction) -> dict[str, Any]:
    if removed and not action.reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to remove a partner.")
    connection = open_db()
    clog = open_corrections()
    try:
        inv = connection.execute(
            "SELECT investor_id, contract_id, is_deleted FROM investor WHERE investor_id = ?",
            (investor_id,),
        ).fetchone()
        if not inv or str(inv["contract_id"]) != str(cid):
            raise HTTPException(status_code=404, detail="Partner not found on this contract.")
        if bool(inv["is_deleted"]) == removed:
            raise HTTPException(
                status_code=409,
                detail="Partner is already removed." if removed else "Partner is not removed.",
            )
        reason = action.reason.strip() or None
        # Which links to act on: live ones when removing, soft-deleted ones when restoring.
        links = connection.execute(
            "SELECT investment_id FROM investor_group WHERE investor_id = ? AND is_deleted = ?",
            (investor_id, 0 if removed else 1),
        ).fetchall()

        with connection:
            connection.execute(
                "UPDATE investor SET is_deleted = ? WHERE investor_id = ?", (1 if removed else 0, investor_id)
            )
        corrections_db.record_operation(
            clog, op="delete" if removed else "restore", db_table="investor",
            pk={"investor_id": int(investor_id)}, by=action.reviewer, reason=reason, note=reason,
        )
        for link in links:
            with connection:
                connection.execute(
                    "UPDATE investor_group SET is_deleted = ? WHERE investor_id = ? AND investment_id = ?",
                    (1 if removed else 0, investor_id, link["investment_id"]),
                )
            corrections_db.record_operation(
                clog, op="delete" if removed else "restore", db_table="investor_group",
                pk={"investor_id": int(investor_id), "investment_id": int(link["investment_id"])},
                by=action.reviewer, reason=reason, note=reason,
            )

        unattached = False
        for link in links:
            investment = connection.execute(
                "SELECT contract_id FROM investment WHERE investment_id = ?", (link["investment_id"],)
            ).fetchone()
            # Cross-contract guard: never re-derive a foreign contract's tranche.
            if not investment or str(investment["contract_id"]) != str(cid):
                continue
            _rederive_is_joint(
                connection, clog, link["investment_id"],
                reviewer=action.reviewer, reason=reason or ("removed" if removed else "restored"),
            )
            remaining = connection.execute(
                """SELECT COUNT(*) AS c FROM investor_group g JOIN investor i ON i.investor_id = g.investor_id
                   WHERE g.investment_id = ? AND g.is_deleted = 0 AND i.is_deleted = 0""",
                (link["investment_id"],),
            ).fetchone()["c"]
            if removed and remaining == 0:
                unattached = True
    finally:
        clog.close()
        connection.close()
    # search.db refreshes on demand (search_index.ensure_fresh) on the next query.
    return {"ok": True, "removed": removed, "left_unattached": unattached}


@app.post("/api/db/contract/{cid}/partner/{investor_id}/remove")
def remove_partner(cid: str, investor_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_partner_removed(cid, investor_id, removed=True, action=action)


@app.post("/api/db/contract/{cid}/partner/{investor_id}/restore")
def restore_partner(cid: str, investor_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_partner_removed(cid, investor_id, removed=False, action=action)


# ---------------------------------------------------------------------------
# Creating records (DB-native rows)
#
# From the Word-corpus freeze onward, new contracts and acts are added directly
# to the database — no Word summary exists for them, by design. Every create is
# one audited `create` op in corrections.db (full-row snapshot → replayed onto
# a reseed by db_import), with a REQUIRED source line as its provenance, since
# there is no Word regest to point at. Lookup values (place/activity/currency/
# title) are raw, interpretive phrases: the platform reuses one only on an EXACT
# match of the stored text and otherwise stores the new phrase verbatim — it
# never normalizes, merges, or "corrects" them.
# ---------------------------------------------------------------------------

DATE_OR_BLANK = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUB_TYPES = ["balance", "renewal", "termination", "variation"]
LOOKUP_KINDS: dict[str, dict[str, str]] = {
    "economic_activity": {"table": "economic_activity", "id": "ec_activity_id", "value": "activity"},
    "place": {"table": "place", "id": "place_id", "value": "place_name"},
    "currency": {"table": "currency", "id": "currency_id", "value": "currency"},
    "title": {"table": "title", "id": "title_id", "value": "title_name"},
}

# FK columns a reviewer may re-point to a lookup row (→ kind). Gate for the relink
# endpoint; the matching `relink` descriptors are emitted by `relink_field`.
# (contract_place.place_id — the "Places" section — is deferred: composite PK.)
RELINK_FIELDS: dict[tuple[str, str], str] = {
    ("contract", "economic_sector"): "economic_activity",
    ("contract", "currency_id"): "currency",
    ("investor", "title"): "title",
    ("investor", "title_husband"): "title",
    ("investor", "title_grandfather"): "title",
    ("investor", "title_father_mother"): "title",
    ("investor", "place_of_residence"): "place",
    ("investor", "place_of_origin"): "place",
}


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


def _lookup_norm(text: Any) -> str:
    """Normalization for FINDING existing lookup values only — never for storing."""
    return re.sub(r"\s+", " ", _strip_accents(str(text or "").lower())).strip()


@app.get("/api/search")
def global_search(q: str = "", expand: str = "") -> dict[str, Any]:
    """Global FTS search over the database (contracts, acts, people).

    The derived search.db is rebuilt on demand when main.db has changed (an
    applied correction, hide, or created row) — a full rebuild is ~0.5 s, so
    freshness is checked per request at negligible cost. A purely numeric query
    additionally returns direct id-jump cards (historians think in act numbers).
    """
    term = q.strip()
    if len(term) < 2:
        return {"total": 0, "groups": [], "term_counts": None, "id_jumps": []}

    id_jumps: list[dict[str, Any]] = []
    if term.isdigit():
        connection = open_db()
        try:
            row = connection.execute(
                "SELECT contract_id, firm_name, registration_date, folio, folder FROM contract WHERE contract_id = ? AND is_deleted = 0",
                (int(term),),
            ).fetchone()
            if row:
                id_jumps.append(
                    {
                        "kind": "contract",
                        "ref": str(row["contract_id"]),
                        "title": (row["firm_name"] or "").strip() or f"Contract {row['contract_id']}",
                        "meta": search_meta(row["registration_date"], row["folio"], row["folder"]),
                    }
                )
            row = connection.execute(
                "SELECT contract_id, sub_firm_name, sub_type, registration_date, folio, folder FROM sub_contract WHERE contract_id = ? AND is_deleted = 0",
                (int(term),),
            ).fetchone()
            if row:
                id_jumps.append(
                    {
                        "kind": "sub_contract",
                        "ref": str(row["contract_id"]),
                        "title": (row["sub_firm_name"] or "").strip() or f"Sub-contract {row['contract_id']}",
                        "meta": search_meta(row["registration_date"], row["folio"], row["folder"], row["sub_type"]),
                    }
                )
            row = connection.execute(
                "SELECT person_id, first_name, last_name, nickname FROM person WHERE person_id = ? AND is_deleted = 0",
                (int(term),),
            ).fetchone()
            if row:
                id_jumps.append(
                    {
                        "kind": "person",
                        "ref": str(row["person_id"]),
                        "title": person_display_name(row["first_name"], row["last_name"], row["nickname"]),
                        "meta": "person record",
                    }
                )
        finally:
            connection.close()

    search_path = search_index.ensure_fresh(db_path())
    payload = search_index.search(
        search_path, term, expand_kind=expand if expand in search_index.KIND_ORDER else None
    )
    payload["id_jumps"] = id_jumps
    return payload


@app.get("/api/db/registers")
def db_registers() -> dict[str, Any]:
    """Canonical register options for the create forms.

    Derived from the data: one option per clean folder, labeled with the
    majority (archive, series) spelling among its rows — choosing a *default*
    for NEW rows, never rewriting existing ones. The 750-odd rows with blank or
    malformed register metadata are a separate repair worklist.
    """
    connection = open_db()
    try:
        rows = connection.execute(
            "SELECT trim(archive) a, trim(series) s, trim(folder) f, count(*) n FROM contract "
            "WHERE is_deleted = 0 GROUP BY trim(archive), trim(series), trim(folder)"
        ).fetchall()
    finally:
        connection.close()
    by_folder: dict[str, list[Any]] = {}
    for row in rows:
        folder = row["f"]
        if not folder or not re.fullmatch(r"\d+(bis)?", folder):
            continue
        by_folder.setdefault(folder, []).append(row)
    options = []
    for folder, variants in by_folder.items():
        best = max(variants, key=lambda r: r["n"])
        options.append(
            {
                "archive": best["a"] or "ASF",
                "series": best["s"] or "",
                "folder": folder,
                "contracts": sum(r["n"] for r in variants),
            }
        )
    options.sort(key=lambda o: (0 if o["series"].lower().startswith("mercanzia") else 1, o["folder"]))
    return {"registers": options}


@app.get("/api/db/check-number/{number}")
def db_check_number(number: int) -> dict[str, Any]:
    """Is a register act number free to become a new contract's id?

    A TAKEN number is usually not a conflict but a signal: the act being added
    is a later act ON that contract (verified on the word-only backlog: all six
    taken-number cases were modifiche/disdette of the existing contract) — the
    UI offers "add as act on contract N" instead.
    """
    connection = open_db()
    try:
        row = connection.execute(
            "SELECT contract_id, firm_name, registration_date, folio, folder FROM contract WHERE contract_id = ?",
            (number,),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return {"free": True, "existing": None}
    return {
        "free": False,
        "existing": {
            "id": str(row["contract_id"]),
            "title": (row["firm_name"] or "").strip() or f"Contract {row['contract_id']}",
            "date": row["registration_date"],
            "folio": row["folio"],
            "folder": row["folder"],
        },
    }


@app.get("/api/db/similar")
def db_similar(folder: str, folio: str = "", date: str = "") -> dict[str, Any]:
    """Possible duplicates for the create-form warning: rows in the same register
    sharing the folio and/or the date. (folder, folio, date) is a near-unique key
    in this corpus (14 collision groups in 4,866 contracts), so hits are worth
    reading, not noise."""
    folder = folder.strip()
    folio = folio.strip()
    date = date.strip()
    if not folder or (not folio and not date):
        return {"rows": []}
    connection = open_db()
    out: list[dict[str, Any]] = []
    try:
        for table, name_col in (("contract", "firm_name"), ("sub_contract", "sub_firm_name")):
            rows = connection.execute(
                f"SELECT contract_id, {name_col} AS name, registration_date, folio FROM {table} "
                "WHERE trim(folder) = ? AND (trim(folio) = ? OR registration_date = ?) "
                "AND is_deleted = 0 LIMIT 4",
                (folder, folio or "<none>", date or "<none>"),
            ).fetchall()
            for row in rows:
                same_folio = folio and str(row["folio"] or "").strip() == folio
                same_date = date and row["registration_date"] == date
                out.append(
                    {
                        "row_id": f"{table}:{row['contract_id']}",
                        "table": table,
                        "id": str(row["contract_id"]),
                        "title": (row["name"] or "").strip() or f"{table.replace('_', '-')} {row['contract_id']}",
                        "date": row["registration_date"],
                        "folio": row["folio"],
                        "match": "folio + date" if (same_folio and same_date) else ("folio" if same_folio else "date"),
                    }
                )
    finally:
        connection.close()
    return {"rows": out[:6]}


@app.get("/api/db/lookup/{kind}")
def db_lookup(kind: str, q: str = "") -> dict[str, Any]:
    """Typeahead over a lookup list. Matching is diacritic/case-insensitive for
    FINDING only; the returned values are the raw stored phrases, verbatim.
    Usage counts let the encoder prefer an existing identical phrase without the
    platform ever suggesting a merge."""
    meta = LOOKUP_KINDS.get(kind)
    if not meta:
        raise HTTPException(status_code=400, detail="Unknown lookup kind.")
    usage_sql = {
        "economic_activity": "SELECT economic_sector k, count(*) n FROM contract WHERE economic_sector IS NOT NULL AND is_deleted = 0 GROUP BY economic_sector",
        "currency": "SELECT currency_id k, count(*) n FROM contract WHERE currency_id IS NOT NULL AND is_deleted = 0 GROUP BY currency_id",
        "place": (
            "SELECT k, sum(n) n FROM ("
            "SELECT place_id k, count(*) n FROM contract_place WHERE is_deleted = 0 GROUP BY place_id "
            "UNION ALL SELECT place_of_residence k, count(*) n FROM investor WHERE place_of_residence IS NOT NULL AND is_deleted = 0 GROUP BY place_of_residence "
            "UNION ALL SELECT place_of_origin k, count(*) n FROM investor WHERE place_of_origin IS NOT NULL AND is_deleted = 0 GROUP BY place_of_origin"
            ") GROUP BY k"
        ),
        "title": "SELECT title k, count(*) n FROM investor WHERE title IS NOT NULL AND is_deleted = 0 GROUP BY title",
    }[kind]
    needle = _lookup_norm(q)
    connection = open_db()
    try:
        usage = {row["k"]: row["n"] for row in connection.execute(usage_sql)}
        rows = connection.execute(f"SELECT {meta['id']} AS id, {meta['value']} AS value FROM {meta['table']}").fetchall()
    finally:
        connection.close()
    scored = []
    exact = None
    for row in rows:
        value = str(row["value"] or "")
        norm = _lookup_norm(value)
        if needle and needle not in norm:
            continue
        rank = 0 if norm == needle else (1 if norm.startswith(needle) else 2)
        item = {"id": row["id"], "value": value, "used": usage.get(row["id"], 0)}
        if norm == needle:
            exact = item
        scored.append((rank, -item["used"], value, item))
    scored.sort(key=lambda t: t[:3])
    return {"values": [t[3] for t in scored[:8]], "exact": exact}


def _next_id(connection: sqlite3.Connection, table: str, column: str) -> int:
    row = connection.execute(f"SELECT coalesce(max({column}), 0) + 1 FROM {table}").fetchone()
    return int(row[0])


def _insert_and_log(
    connection: sqlite3.Connection,
    *,
    table: str,
    pk: dict[str, Any],
    data: dict[str, Any],
    reviewer: str,
    reason: str,
) -> dict[str, Any]:
    """INSERT into main.db, snapshot the full row, log the create op.

    main.db first, log second; if logging fails the inserted row is removed
    again (compensating delete), so no row can exist without its audit entry.
    Composite primary keys (investor_group, contract_place) are supported.
    """
    names = ", ".join(f"`{c}`" for c in data)
    marks = ", ".join("?" for _ in data)
    where = " AND ".join(f"`{c}` = ?" for c in pk)
    pk_params = [data[c] for c in pk]
    with connection:
        connection.execute(f"INSERT INTO `{table}` ({names}) VALUES ({marks})", list(data.values()))
    row = connection.execute(f"SELECT * FROM `{table}` WHERE {where}", pk_params).fetchone()
    snapshot = {key: row[key] for key in row.keys()}
    clog = open_corrections()
    try:
        corrections_db.record_operation(
            clog,
            op="create",
            db_table=table,
            pk={c: int(data[c]) for c in pk},
            by=reviewer,
            after_value=snapshot,
            reason=reason,
            note=reason,
        )
    except Exception:
        with connection:
            connection.execute(f"DELETE FROM `{table}` WHERE {where}", pk_params)
        raise
    finally:
        clog.close()
    return snapshot


def _resolve_lookup_id(
    connection: sqlite3.Connection, kind: str, raw_value: str, *, reviewer: str, reason: str
) -> int | None:
    """Reuse a lookup row only on an EXACT raw-text match (trimmed); otherwise
    create a new row storing the phrase verbatim. Interpretive phrases are data —
    no case-folding, no diacritic-stripping, no merging on the write path."""
    value = raw_value.strip()
    if not value:
        return None
    meta = LOOKUP_KINDS[kind]
    row = connection.execute(
        f"SELECT {meta['id']} AS id FROM {meta['table']} WHERE trim({meta['value']}) = ?", (value,)
    ).fetchone()
    if row:
        return int(row["id"])
    new_id = _next_id(connection, meta["table"], meta["id"])
    _insert_and_log(
        connection,
        table=meta["table"],
        pk={meta["id"]: new_id},
        data={meta["id"]: new_id, meta["value"]: value},
        reviewer=reviewer,
        reason=reason,
    )
    return new_id


class RelinkAction(BaseModel):
    reviewer: str = Field(min_length=1)
    field: str
    value: str = ""          # the chosen/typed phrase; "" → clear to none
    reason: str = ""


@app.post("/api/db/relink/{table}/{record_id}")
def relink_record(table: str, record_id: str, action: RelinkAction) -> dict[str, Any]:
    """Re-point an FK column (title/place/currency/economic_sector) to a lookup
    row: reuse an existing phrase, create one verbatim, or clear to none. Audited
    as create?(new phrase) + update(FK) — both replay-safe; the deferred `relink`
    op is not needed. The phrase itself is never edited in place."""
    kind = RELINK_FIELDS.get((table, action.field))
    if not kind:
        raise HTTPException(status_code=400, detail=f"'{action.field}' is not a relinkable field on {table}.")
    pk_col = PRIMARY_KEY_COLUMN.get(table)
    if not pk_col:
        raise HTTPException(status_code=400, detail="Unknown table.")
    reason = action.reason.strip() or None
    connection = open_db()
    clog = open_corrections()
    try:
        row = connection.execute(
            f"SELECT {pk_col} AS pk, {action.field} AS old FROM {table} WHERE {pk_col} = ?", (record_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Record not found.")
        old_id = row["old"]
        # reuse-or-create-verbatim (logs a `create` op if a new phrase is minted)
        new_id = _resolve_lookup_id(connection, kind, action.value, reviewer=action.reviewer, reason=reason or "relink")
        if normalize_value(new_id) == normalize_value(old_id):
            raise HTTPException(status_code=409, detail="That is already the recorded value.")
        with connection:
            connection.execute(f"UPDATE {table} SET {action.field} = ? WHERE {pk_col} = ?", (new_id, record_id))
        corrections_db.record_operation(
            clog, op="update", db_table=table, pk={pk_col: int(record_id)}, field=action.field,
            before_value=old_id, after_value=new_id, by=action.reviewer, reason=reason,
        )
    finally:
        clog.close()
        connection.close()
    # The chosen/typed phrase IS the new display text (verbatim); "" → none ("—").
    return {"ok": True, "value": action.value.strip()}


# ---------------------------------------------------------------------------
# Contract places — add / remove / restore / edit-address. The place itself is
# part of the composite PK, so "re-pointing" a place = remove + add (both audited
# + replay-safe). The address is a plain editable text field. No cascade.
# ---------------------------------------------------------------------------


class PlaceAdd(BaseModel):
    reviewer: str = Field(min_length=1)
    place: str = Field(min_length=1)   # the place phrase (reused on exact match, else created verbatim)
    address: str = ""
    reason: str = ""


class PlaceAddress(BaseModel):
    reviewer: str = Field(min_length=1)
    address: str = ""
    reason: str = ""


@app.post("/api/db/contract/{cid}/place/add")
def add_place(cid: str, payload: PlaceAdd) -> dict[str, Any]:
    connection = open_db()
    clog = open_corrections()
    try:
        if not connection.execute("SELECT 1 FROM contract WHERE contract_id = ?", (cid,)).fetchone():
            raise HTTPException(status_code=404, detail="Contract not found.")
        reason = payload.reason.strip() or "added a place"
        place_id = _resolve_lookup_id(connection, "place", payload.place, reviewer=payload.reviewer, reason=reason)
        if place_id is None:
            raise HTTPException(status_code=400, detail="A place is required.")
        addr = payload.address.strip() or None
        existing = connection.execute(
            "SELECT is_deleted FROM contract_place WHERE contract_id = ? AND place_id = ?", (cid, place_id)
        ).fetchone()
        if existing and not existing["is_deleted"]:
            raise HTTPException(status_code=409, detail="That place is already on this contract.")
        if existing:  # a soft-deleted link → restore it (+ refresh address) rather than duplicate the PK
            with connection:
                connection.execute(
                    "UPDATE contract_place SET is_deleted = 0, address = ? WHERE contract_id = ? AND place_id = ?",
                    (addr, cid, place_id),
                )
            corrections_db.record_operation(
                clog, op="restore", db_table="contract_place",
                pk={"place_id": int(place_id), "contract_id": int(cid)}, by=payload.reviewer, reason=reason,
            )
        else:
            _insert_and_log(
                connection, table="contract_place",
                pk={"place_id": int(place_id), "contract_id": int(cid)},
                data={"place_id": int(place_id), "contract_id": int(cid), "address": addr, "place_db": 0, "is_deleted": 0},
                reviewer=payload.reviewer, reason=reason,
            )
    finally:
        clog.close()
        connection.close()
    return {"ok": True}


def _set_place_removed(cid: str, place_id: str, *, removed: bool, action: RecordAction) -> dict[str, Any]:
    if removed and not action.reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to remove a place.")
    connection = open_db()
    clog = open_corrections()
    try:
        row = connection.execute(
            "SELECT is_deleted FROM contract_place WHERE contract_id = ? AND place_id = ?", (cid, place_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Place not found on this contract.")
        if bool(row["is_deleted"]) == removed:
            raise HTTPException(status_code=409, detail="Place is already in that state.")
        with connection:
            connection.execute(
                "UPDATE contract_place SET is_deleted = ? WHERE contract_id = ? AND place_id = ?",
                (1 if removed else 0, cid, place_id),
            )
        corrections_db.record_operation(
            clog, op="delete" if removed else "restore", db_table="contract_place",
            pk={"place_id": int(place_id), "contract_id": int(cid)},
            by=action.reviewer, reason=action.reason.strip() or None,
        )
    finally:
        clog.close()
        connection.close()
    return {"ok": True, "removed": removed}


@app.post("/api/db/contract/{cid}/place/{place_id}/remove")
def remove_place(cid: str, place_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_place_removed(cid, place_id, removed=True, action=action)


@app.post("/api/db/contract/{cid}/place/{place_id}/restore")
def restore_place(cid: str, place_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_place_removed(cid, place_id, removed=False, action=action)


@app.post("/api/db/contract/{cid}/place/{place_id}/address")
def edit_place_address(cid: str, place_id: str, payload: PlaceAddress) -> dict[str, Any]:
    connection = open_db()
    clog = open_corrections()
    try:
        row = connection.execute(
            "SELECT address FROM contract_place WHERE contract_id = ? AND place_id = ? AND is_deleted = 0", (cid, place_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Place not found on this contract.")
        old = row["address"]
        new = payload.address.strip() or None
        if normalize_value(old) == normalize_value(new):
            raise HTTPException(status_code=409, detail="The address is unchanged.")
        with connection:
            connection.execute(
                "UPDATE contract_place SET address = ? WHERE contract_id = ? AND place_id = ?", (new, cid, place_id)
            )
        corrections_db.record_operation(
            clog, op="update", db_table="contract_place",
            pk={"place_id": int(place_id), "contract_id": int(cid)}, field="address",
            before_value=old, after_value=new, by=payload.reviewer, reason=payload.reason.strip() or None,
        )
    finally:
        clog.close()
        connection.close()
    return {"ok": True, "value": payload.address.strip()}


# ---------------------------------------------------------------------------
# "Needs review" — DB-intrinsic data-quality flags (Tier 1). Computed live over
# main.db (no Word dependency, is_deleted-filtered); fixing a record removes its
# flag on the next load. Dismiss = "reviewed, not an error" (append-only log).
# ---------------------------------------------------------------------------


def dismissed_flag_keys() -> set[str]:
    return {row.get("key") for row in load_jsonl(FLAG_DISMISSALS_PATH) if row.get("key")}


WORD_DATE_GROUP_META = {
    "label": "Word source — date differs",
    "severity": "medium",
    "explanation": (
        "An independent transcription (the Word source) records a different registration date for "
        "this act. Open the record, compare against the narrative and the folio image, and correct "
        "the date only if the database is wrong — Word is evidence, not the truth."
    ),
}


def _word_date_flags(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Surfaced Word↔DB registration-date conflicts as 'Needs review' flags (Word-coupled,
    kept out of the Word-free data_quality module). Read-only; degrades to [] if the Word
    build is absent. Deep-links via kind 'word_date' → opens the evidence panel, not the
    editor (verify against the manuscript before correcting). Tracked-change cases first."""
    try:
        checks = word_cross_check.build_checks(connection)
    except Exception:
        return []
    firms = {r[0]: r[1] for r in connection.execute("SELECT contract_id, firm_name FROM contract")}
    subs = {r[0]: r[1] for r in connection.execute("SELECT contract_id, sub_firm_name FROM sub_contract")}
    out: list[dict[str, Any]] = []
    for rid, c in checks.items():
        if not c.get("surfaced"):
            continue
        table, _, pk = rid.partition(":")
        name = (firms.get(int(pk)) if table == "contract" else subs.get(int(pk))) or ""
        name = name.strip() or (f"Contract {pk}" if table == "contract" else f"Sub-contract {pk}")
        tracked = c["tier"] == "tracked_change"
        out.append({
            "key": f"{table}:{pk}:word_date_differs",
            "group": "word_date_differs",
            "table": table,
            "pk": str(pk),
            "title": f"{name} — DB {c['db_display']} vs Word {c['word_display']}" + (" · revision" if tracked else ""),
            "severity": "medium",
            "explanation": WORD_DATE_GROUP_META["explanation"],
            "fix": {"kind": "word_date", "field": None},
        })
    out.sort(key=lambda f: 0 if f["title"].endswith("· revision") else 1)  # tracked-change first
    return out


@app.get("/api/db/flags")
def db_flags() -> dict[str, Any]:
    connection = open_db()
    try:
        items = data_quality.flags(connection) + _word_date_flags(connection)
    finally:
        connection.close()
    dismissed = dismissed_flag_keys()
    items = [f for f in items if f["key"] not in dismissed]
    meta_lookup = {**data_quality.GROUP_META, "word_date_differs": WORD_DATE_GROUP_META}
    groups: dict[str, dict[str, Any]] = {}
    for f in items:
        meta = meta_lookup[f["group"]]
        g = groups.setdefault(
            f["group"],
            {"group": f["group"], "label": meta["label"], "severity": meta["severity"],
             "explanation": meta["explanation"], "items": []},
        )
        g["items"].append(f)
    # high severity first, then larger groups
    ordered = sorted(groups.values(), key=lambda g: (0 if g["severity"] == "high" else 1, -len(g["items"])))
    return {"total": len(items), "groups": ordered}


class FlagDismiss(BaseModel):
    reviewer: str = Field(min_length=1)
    reason: str = ""


@app.post("/api/db/flags/{key:path}/dismiss")
def dismiss_flag(key: str, action: FlagDismiss) -> dict[str, Any]:
    FLAG_DISMISSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FLAG_DISMISSALS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "key": key, "reviewer": action.reviewer, "reason": action.reason.strip(),
            "at": datetime.now(timezone.utc).isoformat(),
        }) + "\n")
    return {"ok": True, "dismissed": key}


class ContractCreate(BaseModel):
    reviewer: str = Field(min_length=1)
    # Provenance line — REQUIRED: a DB-native row has no Word summary to point at.
    source: str = Field(min_length=3)
    archive: str = "ASF"
    series: str = ""
    folder: str = Field(min_length=1)
    folio: str = Field(min_length=1)
    registration_date: str
    register_number: int | None = None
    firm_name: str = ""
    economic_activity: str = ""
    total: int | None = None
    document: str = Field(min_length=10)


class SubContractCreate(BaseModel):
    reviewer: str = Field(min_length=1)
    source: str = Field(min_length=3)
    main_contract_id: int
    sub_type: str
    archive: str = "ASF"
    series: str = ""
    folder: str = Field(min_length=1)
    folio: str = Field(min_length=1)
    registration_date: str
    end_date: str = ""
    renewal_months: int | None = None
    sub_firm_name: str = ""
    document: str = Field(min_length=10)


@app.post("/api/db/create/contract")
def create_contract(payload: ContractCreate) -> dict[str, Any]:
    if not DATE_OR_BLANK.match(payload.registration_date):
        raise HTTPException(status_code=400, detail="Registration date must be YYYY-MM-DD.")
    connection = open_db()
    try:
        if payload.register_number is not None:
            taken = connection.execute(
                "SELECT contract_id FROM contract WHERE contract_id = ?", (payload.register_number,)
            ).fetchone()
            if taken:
                raise HTTPException(
                    status_code=409,
                    detail=f"Contract {payload.register_number} already exists — if this is a later act on it, add it as a sub-contract instead.",
                )
            new_id = payload.register_number
        else:
            new_id = _next_id(connection, "contract", "contract_id")
        reason = f"source: {payload.source.strip()}"
        sector = _resolve_lookup_id(
            connection, "economic_activity", payload.economic_activity,
            reviewer=payload.reviewer.strip(), reason=f"created with contract {new_id}; {reason}",
        )
        data: dict[str, Any] = {
            "contract_id": new_id,
            "archive": payload.archive.strip() or "ASF",
            "series": payload.series.strip(),
            "folder": payload.folder.strip(),
            "folio": payload.folio.strip(),
            "registration_date": payload.registration_date,
            "firm_name": payload.firm_name.strip() or None,
            "economic_sector": sector,
            "total": payload.total,
            "document": payload.document.strip(),
            "temp": 1,
            "is_deleted": 0,
        }
        _insert_and_log(
            connection,
            table="contract",
            pk={"contract_id": new_id},
            data=data,
            reviewer=payload.reviewer.strip(),
            reason=reason,
        )
    finally:
        connection.close()
    return {"ok": True, "id": str(new_id), "row_id": f"contract:{new_id}"}


@app.post("/api/db/create/sub_contract")
def create_sub_contract(payload: SubContractCreate) -> dict[str, Any]:
    if payload.sub_type not in SUB_TYPES:
        raise HTTPException(status_code=400, detail=f"sub_type must be one of {SUB_TYPES}.")
    if not DATE_OR_BLANK.match(payload.registration_date):
        raise HTTPException(status_code=400, detail="Registration date must be YYYY-MM-DD.")
    if payload.end_date and not DATE_OR_BLANK.match(payload.end_date):
        raise HTTPException(status_code=400, detail="End date must be YYYY-MM-DD (or empty).")
    connection = open_db()
    try:
        parent = connection.execute(
            "SELECT contract_id FROM contract WHERE contract_id = ?", (payload.main_contract_id,)
        ).fetchone()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent contract not found.")
        new_id = _next_id(connection, "sub_contract", "contract_id")
        data: dict[str, Any] = {
            "contract_id": new_id,
            "main_contract_id": payload.main_contract_id,
            "sub_type": payload.sub_type,
            "archive": payload.archive.strip() or "ASF",
            "series": payload.series.strip(),
            "folder": payload.folder.strip(),
            "folio": payload.folio.strip(),
            "registration_date": payload.registration_date,
            "end_date": payload.end_date or None,
            "renewal_months": payload.renewal_months,
            "sub_firm_name": payload.sub_firm_name.strip() or None,
            "document": payload.document.strip(),
            "temp": 1,
            "is_deleted": 0,
        }
        _insert_and_log(
            connection,
            table="sub_contract",
            pk={"contract_id": new_id},
            data=data,
            reviewer=payload.reviewer.strip(),
            reason=f"source: {payload.source.strip()}",
        )
    finally:
        connection.close()
    return {"ok": True, "id": str(new_id), "row_id": f"sub_contract:{new_id}"}


# ---------------------------------------------------------------------------
# Adding investors (person + role + capital, atomically)
#
# The data's shape (audited 2026-06-12): every contract has ≥2 investors, both
# roles on 99.4%; each investor joins EXACTLY ONE investment; 11% of
# investments are joint (shared tranches, up to 7 people); GP cash is 0 by
# convention in 64% of cases (the accomandatario contributes industria);
# `is_joint` is DERIVED from the group structure, never asked (the legacy flag
# disagrees with reality in 146 rows). Person reuse is real (26% of appearing
# persons recur), so the flow is search-first with a same-surname wall before
# any new person is minted (1,307 identical-name pairs already exist).
# ---------------------------------------------------------------------------


@app.get("/api/db/person-search")
def person_search(q: str = "", limit: int = Query(default=12, ge=1, le=50)) -> dict[str, Any]:
    """Whole-database person search with the disambiguating context an encoder
    needs to follow the 2014 rule ("search for existing before creating"):
    patronymic, residence(s), and how many contracts the person appears on."""
    term = q.strip()
    if len(term) < 2:
        return {"results": []}
    # "baccio aldobrandini" spans two columns — every word must match SOME name
    # field (AND of per-word ORs), or the query is the exact person id.
    words = term.split()
    word_clause = (
        "(p.first_name LIKE ? OR p.last_name LIKE ? OR p.father_mother LIKE ? OR p.nickname LIKE ?)"
    )
    where = " AND ".join(word_clause for _ in words)
    params: list[Any] = []
    for word in words:
        params.extend([f"%{word}%"] * 4)
    connection = open_db()
    try:
        rows = connection.execute(
            f"""SELECT p.person_id, p.first_name, p.father_mother, p.grandfather, p.last_name,
                      p.nickname, p.is_woman,
                      count(DISTINCT i.contract_id) AS appearances,
                      group_concat(DISTINCT pl.place_name) AS residences
               FROM person p
               LEFT JOIN investor i ON i.person_id = p.person_id
               LEFT JOIN place pl ON pl.place_id = i.place_of_residence
               WHERE p.is_deleted = 0 AND (({where}) OR CAST(p.person_id AS TEXT) = ?)
               GROUP BY p.person_id
               ORDER BY CASE WHEN CAST(p.person_id AS TEXT) = ? THEN 0 ELSE 1 END,
                        appearances DESC, p.last_name, p.first_name
               LIMIT ?""",
            (*params, term, term, limit),
        ).fetchall()
    finally:
        connection.close()
    return {
        "results": [
            {
                "person_id": str(r["person_id"]),
                "display_name": person_display_name(r["first_name"], r["last_name"], r["nickname"]),
                "father_mother": (r["father_mother"] or "").strip(),
                "residences": (r["residences"] or "").replace(",", ", "),
                "appearances": r["appearances"],
                "is_woman": bool(r["is_woman"]),
            }
            for r in rows
        ]
    }


@app.get("/api/db/same-surname")
def same_surname(last_name: str = "") -> dict[str, Any]:
    """The near-match wall: existing persons whose surname matches (diacritic
    and case insensitive), shown before a new person may be created."""
    needle = _lookup_norm(last_name)
    if not needle:
        return {"results": []}
    connection = open_db()
    try:
        rows = connection.execute(
            """SELECT p.person_id, p.first_name, p.father_mother, p.last_name, p.nickname,
                      count(DISTINCT i.contract_id) AS appearances,
                      group_concat(DISTINCT pl.place_name) AS residences
               FROM person p
               LEFT JOIN investor i ON i.person_id = p.person_id
               LEFT JOIN place pl ON pl.place_id = i.place_of_residence
               WHERE p.is_deleted = 0 AND trim(coalesce(p.last_name, '')) <> ''
               GROUP BY p.person_id""",
        ).fetchall()
    finally:
        connection.close()
    hits = [
        {
            "person_id": str(r["person_id"]),
            "display_name": person_display_name(r["first_name"], r["last_name"], r["nickname"]),
            "father_mother": (r["father_mother"] or "").strip(),
            "residences": (r["residences"] or "").replace(",", ", "),
            "appearances": r["appearances"],
        }
        for r in rows
        if _lookup_norm(r["last_name"]) == needle
    ]
    hits.sort(key=lambda h: (-h["appearances"], h["display_name"]))
    return {"results": hits[:12]}


@app.get("/api/db/contract-investments/{contract_id}")
def contract_investments(contract_id: str) -> dict[str, Any]:
    """The contract's existing capital tranches, readable, for the
    "shares an existing tranche" path (11% of investments are joint)."""
    connection = open_db()
    try:
        rows = connection.execute(
            """SELECT v.investment_id, v.type, v.investment_cash, v.investment_non_cash,
                      v.partnership_name,
                      group_concat(p.first_name || ' ' || coalesce(p.last_name, ''), '; ') AS members
               FROM investment v
               LEFT JOIN investor_group g ON g.investment_id = v.investment_id AND g.is_deleted = 0
               LEFT JOIN investor i ON i.investor_id = g.investor_id AND i.is_deleted = 0
               LEFT JOIN person p ON p.person_id = i.person_id
               WHERE v.contract_id = ? AND v.is_deleted = 0
               GROUP BY v.investment_id
               ORDER BY v.investment_id""",
            (contract_id,),
        ).fetchall()
    finally:
        connection.close()
    return {
        "investments": [
            {
                "investment_id": str(r["investment_id"]),
                "type": r["type"],
                "cash": r["investment_cash"],
                "non_cash": (r["investment_non_cash"] or "").strip(),
                "partnership_name": (r["partnership_name"] or "").strip(),
                "members": (r["members"] or "").strip(),
            }
            for r in rows
        ]
    }


class NewPerson(BaseModel):
    first_name: str = ""
    father_mother: str = ""
    last_name: str = ""
    is_woman: bool = False


class InvestorCreate(BaseModel):
    reviewer: str = Field(min_length=1)
    contract_id: int
    person_id: int | None = None
    new_person: NewPerson | None = None
    # Own tranche: role + capital. Joining an existing tranche: role and capital
    # come from the investment itself.
    role: str = ""  # gp | lp
    join_investment_id: int | None = None
    investment_cash: int | None = None
    cash_unspecified: bool = False
    investment_non_cash: str = ""
    partnership_name: str = ""
    # Person-on-this-contract attributes (raw interpretive phrases for lookups).
    title: str = ""
    residence: str = ""
    origin: str = ""
    profession: str = ""
    via_proxy: bool = False
    citizen_florence: bool = False
    is_widow: bool = False
    is_guardian: bool = False
    is_jewish: bool = False
    is_convert: bool = False
    heirs: bool = False
    heirs_of: bool = False
    and_c: bool = False
    note: str = ""


@app.post("/api/db/create/investor")
def create_investor(payload: InvestorCreate) -> dict[str, Any]:
    """Add a person to a contract: person (reused or new) + investor row +
    capital tranche (own, or joining an existing one) + the group link — one
    audited operation group, replay-safe. `is_joint` is derived from the
    resulting group structure, including on existing siblings."""
    if payload.person_id is None and payload.new_person is None:
        raise HTTPException(status_code=400, detail="Pick an existing person or describe a new one.")
    if payload.join_investment_id is None and payload.role not in ("gp", "lp"):
        raise HTTPException(status_code=400, detail="Role must be gp or lp.")
    connection = open_db()
    try:
        contract = connection.execute(
            "SELECT contract_id FROM contract WHERE contract_id = ?",
            (payload.contract_id,),
        ).fetchone()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found.")
        reviewer = payload.reviewer.strip()
        reason = f"added investor on contract {payload.contract_id}"
        if payload.note.strip():
            reason += f"; {payload.note.strip()}"

        # 1. The person — reused, or created behind the same-surname wall.
        if payload.person_id is not None:
            person_row = connection.execute(
                "SELECT person_id FROM person WHERE person_id = ? AND is_deleted = 0",
                (payload.person_id,),
            ).fetchone()
            if not person_row:
                raise HTTPException(status_code=404, detail="Person not found.")
            person_id = int(payload.person_id)
            person_created = False
        else:
            np = payload.new_person
            if not (np.first_name.strip() or np.last_name.strip()):
                raise HTTPException(status_code=400, detail="A new person needs at least a name.")
            person_id = _next_id(connection, "person", "person_id")
            _insert_and_log(
                connection,
                table="person",
                pk={"person_id": person_id},
                data={
                    "person_id": person_id,
                    "first_name": np.first_name.strip() or None,
                    "father_mother": np.father_mother.strip() or None,
                    "last_name": np.last_name.strip() or None,
                    "is_woman": 1 if np.is_woman else 0,
                    "temp": 1,
                    "is_deleted": 0,
                },
                reviewer=reviewer,
                reason=reason,
            )
            person_created = True

        # 2. Lookups — exact raw-text reuse or verbatim creation, never normalized.
        title_id = _resolve_lookup_id(connection, "title", payload.title, reviewer=reviewer, reason=reason)
        residence_id = _resolve_lookup_id(connection, "place", payload.residence, reviewer=reviewer, reason=reason)
        origin_id = _resolve_lookup_id(connection, "place", payload.origin, reviewer=reviewer, reason=reason)

        # 3. The capital tranche — own, or an existing one being joined.
        if payload.join_investment_id is not None:
            investment = connection.execute(
                "SELECT investment_id, type FROM investment WHERE investment_id = ? AND contract_id = ? AND is_deleted = 0",
                (payload.join_investment_id, payload.contract_id),
            ).fetchone()
            if not investment:
                raise HTTPException(status_code=404, detail="That investment does not belong to this contract.")
            investment_id = int(investment["investment_id"])
            joining = True
        else:
            investment_id = _next_id(connection, "investment", "investment_id")
            _insert_and_log(
                connection,
                table="investment",
                pk={"investment_id": investment_id},
                data={
                    "investment_id": investment_id,
                    "contract_id": payload.contract_id,
                    "type": payload.role,
                    "investment_cash": None if payload.cash_unspecified else (payload.investment_cash or 0),
                    "investment_non_cash": payload.investment_non_cash.strip() or None,
                    "partnership_name": payload.partnership_name.strip() or None,
                    "temp": 1,
                    "is_deleted": 0,
                },
                reviewer=reviewer,
                reason=reason,
            )
            joining = False

        # 4. The investor row (the person's appearance on this contract).
        investor_id = _next_id(connection, "investor", "investor_id")
        _insert_and_log(
            connection,
            table="investor",
            pk={"investor_id": investor_id},
            data={
                "investor_id": investor_id,
                "person_id": person_id,
                "contract_id": payload.contract_id,
                "title": title_id,
                "place_of_residence": residence_id,
                "place_of_origin": origin_id,
                "profession": payload.profession.strip() or None,
                "citizen_florence": 1 if payload.citizen_florence else 0,
                "is_widow": 1 if payload.is_widow else 0,
                "is_guardian": 1 if payload.is_guardian else 0,
                "is_jewish": 1 if payload.is_jewish else 0,
                "jewish_db": 0,
                "is_convert": 1 if payload.is_convert else 0,
                "via_proxy": 1 if payload.via_proxy else 0,
                "is_joint": 1 if joining else 0,
                "heirs": 1 if payload.heirs else 0,
                "heirs_of": 1 if payload.heirs_of else 0,
                "and_c": 1 if payload.and_c else 0,
                "temp": 1,
                "is_deleted": 0,
            },
            reviewer=reviewer,
            reason=reason,
        )

        # 5. The group link.
        _insert_and_log(
            connection,
            table="investor_group",
            pk={"investor_id": investor_id, "investment_id": investment_id},
            data={"investor_id": investor_id, "investment_id": investment_id},
            reviewer=reviewer,
            reason=reason,
        )

        # 6. Derive is_joint on existing siblings of a joined tranche (the flag
        # is structural; the legacy data drifted precisely because it was manual).
        if joining:
            siblings = connection.execute(
                """SELECT i.investor_id FROM investor_group g JOIN investor i ON i.investor_id = g.investor_id
                   WHERE g.investment_id = ? AND i.investor_id <> ? AND i.is_joint = 0
                     AND g.is_deleted = 0 AND i.is_deleted = 0""",
                (investment_id, investor_id),
            ).fetchall()
            clog = open_corrections()
            try:
                for sib in siblings:
                    with connection:
                        connection.execute(
                            "UPDATE investor SET is_joint = 1 WHERE investor_id = ?",
                            (sib["investor_id"],),
                        )
                    corrections_db.record_operation(
                        clog,
                        op="update",
                        db_table="investor",
                        pk={"investor_id": int(sib["investor_id"])},
                        by=reviewer,
                        field="is_joint",
                        before_value="0",
                        after_value="1",
                        reason=f"joint tranche gained a member ({reason})",
                    )
            finally:
                clog.close()
    finally:
        connection.close()
    return {
        "ok": True,
        "investor_id": str(investor_id),
        "person_id": str(person_id),
        "person_created": person_created,
        "investment_id": str(investment_id),
        "joined_existing": payload.join_investment_id is not None,
    }


@app.get("/api/db/contract-persons/{contract_id}")
def contract_persons(contract_id: str) -> dict[str, Any]:
    """Investors on one contract, with disambiguating context — for the person picker.

    Read-only. Names in this corpus are highly ambiguous, so a free-text field is unsafe:
    the picker resolves a name mention to an existing ``person_id`` and, by default, only
    offers the people already recorded on *this* contract (the common case). Broader
    cross-database search is a separate, explicit step in the UI. No inserts here.
    """
    connection = open_db()
    try:
        contract = connection.execute(
            "SELECT contract_id, firm_name, registration_date FROM contract WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found.")
        rows = connection.execute(
            """
            SELECT i.person_id AS person_id, i.profession AS profession,
                   i.place_of_residence AS place_of_residence,
                   p.first_name AS first_name, p.last_name AS last_name, p.nickname AS nickname
            FROM investor i
            LEFT JOIN person p ON p.person_id = i.person_id
            WHERE i.contract_id = ? AND i.is_deleted = 0
            """,
            (contract_id,),
        ).fetchall()
        persons: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for r in rows:
            pid = r["person_id"]
            if pid in (None, "") or pid in seen:
                continue
            seen.add(pid)
            residence = lookup_value(connection, "place", "place_id", r["place_of_residence"], "place_name")
            detail = " · ".join(
                part for part in (r["profession"] or "", residence or "") if part
            )
            appears = connection.execute(
                "SELECT COUNT(DISTINCT contract_id) AS c FROM investor WHERE person_id = ? AND is_deleted = 0",
                (pid,),
            ).fetchone()["c"]
            persons.append({
                "person_id": str(pid),
                "row_id": f"person:{pid}",
                "display_name": person_display_name(r["first_name"], r["last_name"], r["nickname"]),
                "detail": detail or None,
                "appears_on_contracts": appears,
                "first_name": r["first_name"] or "",
                "last_name": r["last_name"] or "",
            })
        return {
            "contract_id": str(contract_id),
            "contract_title": contract["firm_name"] or f"Contract {contract_id}",
            "contract_date": display_text(contract["registration_date"]),
            "persons": persons,
        }
    finally:
        connection.close()


def group_word_entry_images(link_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse folio link rows that share one physical scan (opening spread).

    The image pipeline emits one row per folio side (left/right) with the same
    ``image_path``. Word entries spanning both pages of an opening therefore
    produce duplicate photos in the UI unless grouped here.
    """
    position_order = {"left": 0, "right": 1}
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for row in link_rows:
        path = row.get("image_path")
        if not path:
            continue
        if path not in grouped:
            grouped[path] = {
                "path": path,
                "file": row.get("image_file"),
                "role": row.get("image_role"),
                "needs_review": bool(row.get("needs_review")),
                "folios": [],
            }
            order.append(str(path))
        else:
            item = grouped[path]
            item["needs_review"] = item["needs_review"] or bool(row.get("needs_review"))
            if not item.get("file"):
                item["file"] = row.get("image_file")
            if not item.get("role"):
                item["role"] = row.get("image_role")

        folios: list[dict[str, Any]] = grouped[path]["folios"]
        candidate = {
            "folio": row.get("matched_folio"),
            "page_position": row.get("page_position"),
            "entry_folio_role": row.get("entry_folio_role"),
        }
        if not any(
            f.get("folio") == candidate["folio"] and f.get("page_position") == candidate["page_position"]
            for f in folios
        ):
            folios.append(candidate)

    def folio_sort_key(folio_row: dict[str, Any]) -> tuple[int, int, str]:
        pos = str(folio_row.get("page_position") or "").lower()
        role = str(folio_row.get("entry_folio_role") or "")
        role_order = 0 if role == "start" else 1 if role == "end" else 2
        return (position_order.get(pos, 2), role_order, str(folio_row.get("folio") or ""))

    result: list[dict[str, Any]] = []
    for path in order:
        item = grouped[path]
        item["folios"].sort(key=folio_sort_key)
        result.append(item)
    return result


def word_entry_rich_for_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Tracked-changes token stream + comment/footnote bodies for one entry.

    The Word summaries are frozen provenance; their tracked changes and
    comments are part of the evidence and must be visible wherever the summary
    is shown (decision 2026-06-11). Built from the extraction layer directly so
    it works for ANY source entry, not only QA-packet cases.
    """
    register_id = str(entry.get("register_id") or "")
    comments = []
    for comment_id in entry.get("comment_ids") or []:
        body = word_comments_by_key().get((register_id, str(comment_id)))
        if body:
            comments.append(
                {
                    "id": str(comment_id),
                    "author": body.get("author"),
                    "date": body.get("date"),
                    "initials": body.get("initials"),
                    "text": body.get("text"),
                }
            )
    notes = []
    for kind, id_field in (("footnote", "footnote_ids"), ("endnote", "endnote_ids")):
        for note_id in entry.get(id_field) or []:
            body = word_notes_by_key().get((register_id, str(note_id)))
            if body:
                notes.append({"id": str(note_id), "kind": kind, "text": body.get("text")})
    return build_word_entry_rich(
        {
            "word_entry_revision_text": entry.get("revision_aware_text"),
            "word_entry_has_revisions": entry.get("has_revisions"),
            "word_entry_comments": comments,
            "word_entry_notes": notes,
            "word_entry_text": entry.get("current_text"),
        }
    )


@app.get("/api/word-entry/{source_entry_id}")
def word_entry(source_entry_id: str) -> dict[str, Any]:
    """Reading text, tracked changes/comments, and manuscript image(s) for one
    Word source entry.

    Powers the slide-in panel opened from a database record's Word summary.
    Read-only; serves derived pipeline outputs.
    """
    entry = source_entries_by_id().get(source_entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Word source entry not found.")
    images = group_word_entry_images(image_candidates_by_entry().get(source_entry_id, []))
    return {
        "source_entry_id": entry.get("source_entry_id"),
        "source_entry_key": entry.get("source_entry_key"),
        "register_id": entry.get("register_id"),
        "label": entry.get("event_label_raw") or entry.get("event_label_guess"),
        "date": entry.get("registration_date_raw"),
        "folio": _entry_folio(entry),
        "has_revisions": bool(entry.get("has_revisions")),
        "text": entry.get("current_text") or "",
        "rich": word_entry_rich_for_entry(entry),
        "images": images,
    }


# ---------------------------------------------------------------------------
# Corrections: propose → approve → apply (the only path that writes to SQLite)
# ---------------------------------------------------------------------------


class CorrectionCreate(BaseModel):
    reviewer: str = Field(min_length=1)
    db_row_id: str
    field: str
    change_type: str = "correct"
    proposed_value: str = ""
    rationale: str = ""
    origin: str = "manual"
    source_entry_id: str = ""
    source_entry_key: str = ""
    source_quote: str = ""
    source_register_id: str = ""
    source_folio: str = ""
    link_review_id: str = ""


class CorrectionAction(BaseModel):
    reviewer: str = Field(min_length=1)
    note: str = ""


def _drift_check(proposal: dict[str, Any]) -> dict[str, Any]:
    """Re-read the live DB value and flag whether it has drifted from the pre-image."""
    table = proposal.get("db_table")
    field = proposal.get("field")
    pk_col = PRIMARY_KEY_COLUMN.get(table or "")
    if not pk_col:
        return {**proposal, "db_value_now": None, "is_stale": False}
    pk_value = (proposal.get("primary_key") or {}).get(pk_col)
    connection = open_db()
    try:
        if field not in table_columns(connection, table):
            return {**proposal, "db_value_now": None, "is_stale": True}
        current = read_db_value(connection, table, pk_value, field)
    except HTTPException:
        return {**proposal, "db_value_now": None, "is_stale": True}
    finally:
        connection.close()
    is_stale = normalize_value(current) != normalize_value(proposal.get("current_value"))
    return {**proposal, "db_value_now": normalize_value(current), "is_stale": is_stale}


@app.get("/api/corrections")
def list_corrections(
    status: str = "All", table: str = "All", origin: str = "All"
) -> dict[str, Any]:
    rows = load_proposals()
    if status != "All":
        rows = [r for r in rows if r.get("status") == status]
    if table != "All":
        rows = [r for r in rows if r.get("db_table") == table]
    if origin != "All":
        rows = [r for r in rows if r.get("origin") == origin]
    rank = {"proposed": 0, "approved": 1, "applied": 2, "reverted": 3, "rejected": 4}
    rows.sort(key=lambda r: (rank.get(r.get("status"), 9), r.get("created_at", "")), reverse=False)
    return {
        "total": len(rows),
        "statuses": sorted({r.get("status") for r in load_proposals() if r.get("status")}),
        "tables": sorted({r.get("db_table") for r in load_proposals() if r.get("db_table")}),
        "proposals": rows,
    }


@app.post("/api/corrections")
def create_correction(payload: CorrectionCreate) -> dict[str, Any]:
    parsed = primary_key_for(payload.db_row_id)
    if not parsed:
        raise HTTPException(status_code=400, detail="Unknown db_row_id.")
    table, raw_id = parsed
    value = validate_correction(table, payload.field, payload.change_type, payload.proposed_value)
    if payload.origin not in {"manual", "agent_suggested"}:
        raise HTTPException(status_code=400, detail="Unknown origin.")
    if not value and not payload.rationale.strip() and not payload.source_quote.strip():
        raise HTTPException(
            status_code=400,
            detail="Provide a proposed value, a source quote, or a rationale.",
        )
    connection = open_db()
    try:
        if payload.field not in table_columns(connection, table):
            raise HTTPException(status_code=400, detail="Unknown field.")
        current = read_db_value(connection, table, raw_id, payload.field)
    finally:
        connection.close()
    now = datetime.now(timezone.utc).isoformat()
    pk_col = PRIMARY_KEY_COLUMN[table]
    proposal = {
        "proposal_id": uuid.uuid4().hex[:12],
        "created_at": now,
        "created_by": payload.reviewer,
        "origin": payload.origin,
        "db_table": table,
        "db_row_id": payload.db_row_id,
        "primary_key": {pk_col: raw_id},
        "field": payload.field,
        "field_label": CORRECTABLE_FIELDS[table][payload.field]["label"],
        "change_type": payload.change_type,
        "current_value": normalize_value(current),
        "proposed_value": value,
        "rationale": payload.rationale.strip(),
        "source": {
            "source_entry_id": payload.source_entry_id or None,
            "source_entry_key": payload.source_entry_key or None,
            "source_quote": payload.source_quote.strip() or None,
            "register_id": payload.source_register_id or None,
            "folio": payload.source_folio or None,
            "link_review_id": payload.link_review_id or None,
        },
        "evidence_fingerprint": evidence_fingerprint(current, payload.source_quote),
        "status": "proposed",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
        "applied_at": None,
        "applied_by": None,
        "applied_run_id": None,
    }
    save_proposal(proposal)
    append_correction_event(
        {
            "event": "created",
            "proposal_id": proposal["proposal_id"],
            "at": now,
            "by": payload.reviewer,
            "field": f"{table}.{payload.field}",
            "pre_image": proposal["current_value"],
            "post_image": value,
        }
    )
    return {"ok": True, "proposal": proposal}


@app.get("/api/corrections/{proposal_id}")
def get_correction(proposal_id: str) -> dict[str, Any]:
    proposal = proposal_by_id(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    return _drift_check(proposal)


def _transition(proposal_id: str, action: CorrectionAction, *, to_status: str, from_status: str) -> dict[str, Any]:
    proposal = proposal_by_id(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    if proposal.get("status") != from_status:
        raise HTTPException(
            status_code=409,
            detail=f"Only '{from_status}' proposals can move to '{to_status}'.",
        )
    now = datetime.now(timezone.utc).isoformat()
    proposal["status"] = to_status
    proposal["reviewed_by"] = action.reviewer
    proposal["reviewed_at"] = now
    proposal["review_note"] = action.note.strip() or None
    save_proposal(proposal)
    append_correction_event(
        {
            "event": to_status,
            "proposal_id": proposal_id,
            "at": now,
            "by": action.reviewer,
            "note": action.note.strip() or None,
        }
    )
    return {"ok": True, "proposal": proposal}


@app.post("/api/corrections/{proposal_id}/approve")
def approve_correction(proposal_id: str, action: CorrectionAction) -> dict[str, Any]:
    return _transition(proposal_id, action, to_status="approved", from_status="proposed")


@app.post("/api/corrections/{proposal_id}/reject")
def reject_correction(proposal_id: str, action: CorrectionAction) -> dict[str, Any]:
    return _transition(proposal_id, action, to_status="rejected", from_status="proposed")


def _log_update_op(
    *,
    table: str,
    pk_col: str,
    pk_value: Any,
    field: str,
    before_value: Any,
    after_value: Any,
    reviewer: str,
    reason: str | None,
    run_id: str | None,
    note: str | None,
) -> None:
    """Mirror an applied inline field edit into the authoritative op-log
    (corrections.db) as an `update`. The JSONL proposal store drives the review
    UI; this is what `db_import.replay_corrections` actually replays onto a fresh
    seed — so without this, a `main.db` rebuild would silently drop the edit.
    Raw (un-normalized) values are passed so a NULL pre-image stays NULL on
    replay. Best-effort: a failure here must not undo the committed main.db write."""
    try:
        pk_int: Any = int(pk_value)
    except (TypeError, ValueError):
        pk_int = pk_value
    clog = open_corrections()
    try:
        corrections_db.record_operation(
            clog,
            op="update",
            db_table=table,
            pk={pk_col: pk_int},
            field=field,
            before_value=before_value,
            after_value=after_value,
            by=reviewer,
            reason=reason,
            run_id=run_id,
            note=note,
        )
    finally:
        clog.close()


@app.post("/api/corrections/{proposal_id}/apply")
def apply_correction(proposal_id: str, action: CorrectionAction) -> dict[str, Any]:
    proposal = proposal_by_id(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    if proposal.get("status") != "approved":
        raise HTTPException(status_code=409, detail="Only approved proposals can be applied.")
    if proposal.get("change_type") == "flag_uncertain":
        raise HTTPException(status_code=400, detail="A flag-uncertain note has no value to write.")
    table = proposal["db_table"]
    field = proposal["field"]
    pk_col = PRIMARY_KEY_COLUMN.get(table)
    if not pk_col:
        raise HTTPException(status_code=400, detail="Unknown table.")
    pk_value = (proposal.get("primary_key") or {}).get(pk_col)
    meta = CORRECTABLE_FIELDS.get(table, {}).get(field)
    if not meta:
        raise HTTPException(status_code=400, detail="Field is no longer correctable.")

    path = db_path()
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        if field not in table_columns(connection, table):
            raise HTTPException(status_code=400, detail=f"'{field}' is not a column of {table}.")
        current = read_db_value(connection, table, pk_value, field)
        if normalize_value(current) != normalize_value(proposal.get("current_value")):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Database value changed since the proposal (now "
                    f"'{normalize_value(current)}'). Re-confirm before applying."
                ),
            )
        new_value: Any = proposal["proposed_value"]
        if meta["input_type"] in ("number", "bool"):
            new_value = int(proposal["proposed_value"])
        with connection:  # transaction: commit on success, rollback on error
            connection.execute(
                f"UPDATE {table} SET {field} = ? WHERE {pk_col} = ?", (new_value, pk_value)
            )
    finally:
        connection.close()

    now = datetime.now(timezone.utc).isoformat()
    run_id = f"apply-{now[:10]}"
    # §5: persist the edit to the authoritative op-log so it survives a reseed.
    _log_update_op(
        table=table,
        pk_col=pk_col,
        pk_value=pk_value,
        field=field,
        before_value=current,
        after_value=new_value,
        reviewer=action.reviewer,
        reason=proposal.get("rationale") or None,
        run_id=run_id,
        note=action.note.strip() or None,
    )
    proposal["status"] = "applied"
    proposal["applied_at"] = now
    proposal["applied_by"] = action.reviewer
    proposal["applied_run_id"] = run_id
    save_proposal(proposal)
    append_correction_event(
        {
            "event": "applied",
            "proposal_id": proposal_id,
            "at": now,
            "by": action.reviewer,
            "run_id": run_id,
            "field": f"{table}.{field}",
            "pre_image": proposal["current_value"],
            "post_image": proposal["proposed_value"],
            "note": action.note.strip() or None,
        }
    )
    return {"ok": True, "proposal": proposal}


@app.post("/api/corrections/{proposal_id}/revert")
def revert_correction(proposal_id: str, action: CorrectionAction) -> dict[str, Any]:
    proposal = proposal_by_id(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    if proposal.get("status") != "applied":
        raise HTTPException(status_code=409, detail="Only applied proposals can be reverted.")
    table = proposal["db_table"]
    field = proposal["field"]
    pk_col = PRIMARY_KEY_COLUMN.get(table)
    pk_value = (proposal.get("primary_key") or {}).get(pk_col)
    meta = CORRECTABLE_FIELDS.get(table, {}).get(field)
    if not pk_col or not meta:
        raise HTTPException(status_code=400, detail="Unknown table/field.")

    path = db_path()
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        if field not in table_columns(connection, table):
            raise HTTPException(status_code=400, detail=f"'{field}' is not a column of {table}.")
        current = read_db_value(connection, table, pk_value, field)
        restore: Any = proposal["current_value"]
        if meta["input_type"] in ("number", "bool"):
            restore = int(proposal["current_value"]) if proposal["current_value"] != "" else None
        with connection:
            connection.execute(
                f"UPDATE {table} SET {field} = ? WHERE {pk_col} = ?", (restore, pk_value)
            )
    finally:
        connection.close()

    now = datetime.now(timezone.utc).isoformat()
    # §5: a revert is itself a change — log a compensating `update` (applied value
    # → original) so replay nets out to the original on a reseed.
    _log_update_op(
        table=table,
        pk_col=pk_col,
        pk_value=pk_value,
        field=field,
        before_value=current,
        after_value=restore,
        reviewer=action.reviewer,
        reason="revert",
        run_id=f"revert-{now[:10]}",
        note=action.note.strip() or None,
    )
    proposal["status"] = "reverted"
    save_proposal(proposal)
    append_correction_event(
        {
            "event": "reverted",
            "proposal_id": proposal_id,
            "at": now,
            "by": action.reviewer,
            "field": f"{table}.{field}",
            "pre_image": normalize_value(current),
            "post_image": proposal["current_value"],
            "note": action.note.strip() or None,
        }
    )
    return {"ok": True, "proposal": proposal}


# ---------------------------------------------------------------------------
# Correction candidates: read the derived "possibly needs correction" queue.
# These are hypotheses, not proposals. The only write here is the dismissal log;
# drafting a fix goes through POST /api/corrections with an empty value.
# ---------------------------------------------------------------------------


class CandidateDismiss(BaseModel):
    reviewer: str = Field(min_length=1)
    reason: str = ""


def load_candidates() -> list[dict[str, Any]]:
    return load_jsonl(CANDIDATES_PATH)


def load_candidate_dismissals() -> dict[str, dict[str, Any]]:
    """Latest dismissal per candidate_key (append-only log, last wins)."""
    out: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(CANDIDATE_DISMISSALS_PATH):
        key = row.get("candidate_key")
        if key:
            out[str(key)] = row
    return out


def open_proposals_by_field() -> dict[tuple[str, str], dict[str, Any]]:
    """Latest non-rejected proposal keyed by (db_row_id, field) — for "already handled" annotation."""
    by_field: dict[tuple[str, str], dict[str, Any]] = {}
    for proposal in load_proposals():
        if proposal.get("status") == "rejected":
            continue
        key = (str(proposal.get("db_row_id")), str(proposal.get("field")))
        existing = by_field.get(key)
        if not existing or proposal.get("created_at", "") >= existing.get("created_at", ""):
            by_field[key] = proposal
    return by_field


@app.get("/api/correction-candidates")
def list_correction_candidates(
    family: str = "All",
    reason: str = "All",
    table: str = "All",
    register: str = "All",
    strength: str = "All",
    include_dismissed: bool = False,
    include_handled: bool = True,
) -> dict[str, Any]:
    candidates = load_candidates()
    dismissals = load_candidate_dismissals()
    confirmed_map, _ = decision_link_maps()
    proposals = open_proposals_by_field()

    annotated: list[dict[str, Any]] = []
    for cand in candidates:
        key = str(cand.get("candidate_key"))
        db_row_id = str(cand.get("db_row_id"))
        entry_key = str(cand.get("source_entry_key") or "")
        link_confirmed = bool(entry_key and entry_key in confirmed_map.get(db_row_id, set()))
        dismissal = dismissals.get(key)
        proposal = proposals.get((db_row_id, str(cand.get("field"))))
        score = float(cand.get("priority_score") or 0)
        # A conflict on a human-confirmed link is the strongest cue: identity is
        # settled, so the field really should agree.
        if link_confirmed and cand.get("family") == "word_db_conflict":
            score += 50
        annotated.append(
            {
                **cand,
                "link_confirmed": link_confirmed,
                "dismissed": dismissal is not None,
                "dismissed_reason": (dismissal or {}).get("reason"),
                "existing_proposal": (
                    {
                        "proposal_id": proposal.get("proposal_id"),
                        "status": proposal.get("status"),
                        "proposed_value": proposal.get("proposed_value"),
                    }
                    if proposal
                    else None
                ),
                "rank_score": score,
            }
        )

    def keep(cand: dict[str, Any]) -> bool:
        if not include_dismissed and cand["dismissed"]:
            return False
        if not include_handled and cand["existing_proposal"]:
            return False
        if family != "All" and cand.get("family") != family:
            return False
        if reason != "All" and cand.get("reason_code") != reason:
            return False
        if table != "All" and cand.get("db_table") != table:
            return False
        if register != "All" and cand.get("register_id") != register:
            return False
        if strength != "All" and cand.get("strength") != strength:
            return False
        return True

    visible = [c for c in annotated if keep(c)]
    handled_rank = {"applied": 2, "approved": 1}
    visible.sort(
        key=lambda c: (
            1 if c["existing_proposal"] else 0,  # un-handled first
            -c["rank_score"],
            c.get("db_table", ""),
            c.get("reason_code", ""),
        )
    )

    return {
        "total": len(visible),
        "total_all": len(annotated),
        "dismissed_count": sum(1 for c in annotated if c["dismissed"]),
        "handled_count": sum(1 for c in annotated if c["existing_proposal"]),
        "families": sorted({c.get("family") for c in candidates if c.get("family")}),
        "reasons": sorted({c.get("reason_code") for c in candidates if c.get("reason_code")}),
        "tables": sorted({c.get("db_table") for c in candidates if c.get("db_table")}),
        "registers": sorted({c.get("register_id") for c in candidates if c.get("register_id")}),
        "strengths": ["high", "medium", "low"],
        "candidates": visible,
        "generated_at": (candidates[0].get("generated_at") if candidates else None),
    }


@app.post("/api/correction-candidates/{candidate_key}/dismiss")
def dismiss_correction_candidate(candidate_key: str, payload: CandidateDismiss) -> dict[str, Any]:
    match = next((c for c in load_candidates() if str(c.get("candidate_key")) == candidate_key), None)
    if not match:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "candidate_key": candidate_key,
        "db_row_id": match.get("db_row_id"),
        "field": match.get("field"),
        "reason_code": match.get("reason_code"),
        "reason": payload.reason.strip(),
        "dismissed_at": now,
        "dismissed_by": payload.reviewer,
    }
    CORRECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with CANDIDATE_DISMISSALS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "dismissal": record}


# ---------------------------------------------------------------------------
# Single-origin static serving (production)
#
# In dev the React app runs under Vite (port 5173) and proxies /api to here.
# In deployment we serve the built app from THIS process so the browser makes
# same-origin calls (no CORS, no second host). This block is intentionally LAST
# in the module so every /api route is registered before the SPA catch-all.
# Enabled by FLORACCO_SERVE_STATIC; off by default so local dev is unaffected.
# ---------------------------------------------------------------------------

if os.getenv("FLORACCO_SERVE_STATIC", "").strip().lower() in {"1", "true", "yes", "on"}:
    DIST_DIR = (PROJECT_ROOT / "apps/review/dist").resolve()
    if (DIST_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str) -> FileResponse:
        # /api/* is handled by the routes above; an unknown /api path is a real
        # 404, not the SPA shell.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Unknown API route.")
        # Serve a real static file (favicon, etc.) when it exists and is safely
        # inside dist; otherwise return index.html so client-side routing works
        # (e.g. /database/contract/195 on a hard refresh).
        candidate = (DIST_DIR / full_path).resolve()
        if full_path and candidate.is_file() and DIST_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(DIST_DIR / "index.html")
