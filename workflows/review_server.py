"""Local API server for the FlorAcco review platform.

Run:
    uv run uvicorn workflows.review_server:app --reload

Write scope: review decisions (CSV), correction proposals (JSONL), and —
exclusively through the audited corrections.db op-log — governed updates,
hide/restore, creation of DB-native rows in main.db, and reversible stand-off
identity links. Word files, images, and pipeline outputs are never written.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, NamedTuple

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from workflows import (
    corrections_db,
    data_quality,
    jewish_review,
    locks,
    person_cache,
    place_cache,
    reference_match,
    search_index,
    word_cross_check,
)
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
# Saved Find questions, per signed-in person: append-only (saved / renamed / removed).
FIND_QUESTIONS_PATH = CORRECTIONS_DIR / "find_questions.jsonl"
DEFAULT_DB_PATH = DATA_ROOT / "sqlite/main.db"
PERSON_CACHE_PATH = Path(
    os.getenv("FLORACCO_PERSON_CACHE_PATH") or (DATA_ROOT / "sqlite/person_cache.db")
).expanduser().resolve()
PERSON_MODEL_PATH = Path(
    os.getenv("FLORACCO_PERSON_MODEL_PATH")
    or (PROJECT_ROOT / "docs/person_linkage/person_model.json")
).expanduser().resolve()
PERSON_LABELING_PACKET_PATH = Path(
    os.getenv("FLORACCO_PERSON_LABELING_PACKET_PATH")
    or (DATA_ROOT / "derived/person-linkage/labeling_packet_v3.jsonl")
).expanduser().resolve()


def person_linkage_enabled() -> bool:
    """Explicit in single-origin deployments; on by default only in local dev."""
    raw = os.getenv("FLORACCO_ENABLE_PERSON_LINKAGE")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    serve_static = os.getenv("FLORACCO_SERVE_STATIC", "").strip().lower()
    return serve_static not in {"1", "true", "yes", "on"}

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
        "is_jewish": {"label": "Jewish — stated in the act", "input_type": "bool"},
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
CHANGE_TYPES = {"correct", "fill_missing", "flag_uncertain", "clear"}
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


def serialized_write(func):
    """Run a mutating endpoint's ENTIRE body under the process-wide write lock
    (docs/multi_user_safety.md §0, Phase A): with every write serialized, each
    check→write→log sequence is atomic — no drift-check TOCTOU, no max()+1 id
    collisions, no duplicate verbatim-lookup mints, no torn store rewrites. An
    external maintenance holder of the cross-process flock yields a clean 503."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            # One request = one user action: every operation it logs shares a run_id.
            with locks.write_lock(), corrections_db.action_scope():
                return func(*args, **kwargs)
        except locks.WriteLockBusy:
            raise HTTPException(
                status_code=503,
                detail="Maintenance in progress — nothing was saved; try again shortly.",
            )
    return wrapper


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
@serialized_write
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
    # Write-temp-then-replace: the decisions ledger never passes through a
    # truncated state a concurrent reader or backup could observe (§0 Phase A2).
    tmp = DECISIONS_PATH.with_name(DECISIONS_PATH.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DECISION_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept_rows)
    os.replace(tmp, DECISIONS_PATH)
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
        raise HTTPException(status_code=503, detail="The database is not available on the server right now.")
    # 15 s busy wait: a write queued behind a long Analysis read (≤6 s cap) waits
    # and then succeeds instead of erroring (docs/multi_user_safety.md §0 A3).
    connection = sqlite3.connect(path, timeout=15.0)
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


PLACEHOLDER_DATE = "0000-00-00"


def display_date(value: Any) -> str:
    """A stored date for reading. The archive export wrote 0000-00-00 where no
    date was recorded; it reads "not recorded". The stored value is unchanged."""
    if str(value or "").strip() == PLACEHOLDER_DATE:
        return "not recorded"
    return display_text(value)


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
    targets: list[tuple[str, str]],
    cap: int | None = 24,
    include_unproposed_confirmed: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Aggregate linked Word sources for a set of (db_row_id, via_label) targets.

    A person has no direct Word link — the matcher links narratives to
    contract / sub_contract rows, not to person identities. So a person's
    manuscript context is surfaced *indirectly*, from the contracts where the
    person is recorded as an investor. Each returned source carries a ``via``
    label naming that contract, so the provenance stays explicit. Returns
    (sources_up_to_cap, total_before_cap); one pass over the link file.
    ``cap=None`` keeps every source; ``include_unproposed_confirmed`` adds the
    links a reviewer confirmed that the matcher never proposed, as
    ``linked_word_sources`` does for one row.
    """
    target_label = {db_row_id: label for db_row_id, label in targets}
    if not target_label:
        return [], 0
    confirmed_map, rejected_map = decision_link_maps()
    seen: set[tuple[str, str]] = set()
    covered_keys: dict[str, set[str]] = {}
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
        covered_keys.setdefault(str(db_row_id), set()).add(key)
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
    if include_unproposed_confirmed:
        entries = source_entries_by_key()
        for db_row_id, label in target_label.items():
            for key in sorted(
                confirmed_map.get(db_row_id, set()) - covered_keys.get(db_row_id, set())
            ):
                entry = entries.get(key)
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
                        "via": label,
                        "via_row_id": db_row_id,
                        "comment_count": len(entry.get("comment_ids") or []),
                    }
                )
    status_rank = {"confirmed": 0, "proposed": 1, "rejected": 2}
    sources.sort(
        key=lambda item: (status_rank.get(item["status"], 3), -(item["strength"] or 0.0))
    )
    return (sources if cap is None else sources[:cap]), len(sources)


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
    if change_type == "clear":
        # Empty out the field (set NULL) — most fields are legitimately blank for
        # many contracts. Booleans are exempt: the corpus has no NULL booleans.
        if meta["input_type"] == "bool":
            raise HTTPException(status_code=400, detail="A yes/no field can't be cleared.")
        return ""
    if not value:
        raise HTTPException(status_code=400, detail="A proposed value is required.")
    input_type = meta["input_type"]
    if input_type in ("text", "textarea") and data_quality.is_placeholder_text(value):
        raise HTTPException(
            status_code=400,
            detail=f"“{value}” is placeholder text. To blank the field, delete it and click Clear field.",
        )
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


def reject_placeholder_text(**fields: str) -> None:
    """Refuse create-form text that stands in for nothing ("NULL", "none", a dash).

    Keyword names are the reviewer-facing labels used in the error. The inline
    editor gets the same guard in validate_correction; together with the importer's
    seed hygiene this keeps placeholder strings out of the corpus for good."""
    for label, value in fields.items():
        if data_quality.is_placeholder_text(value):
            raise HTTPException(
                status_code=400,
                detail=f"{label}: “{value.strip()}” is placeholder text — leave the field empty instead.",
            )


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
    # Write-temp-then-replace: readers always see a complete old-or-new ledger,
    # and a crash mid-write can no longer truncate it (§0 Phase A2).
    tmp = PROPOSALS_PATH.with_name(PROPOSALS_PATH.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, PROPOSALS_PATH)


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
               i.is_jewish AS is_jewish, i.jewish_db AS jewish_db, i.is_convert AS is_convert,
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
                boolf("Jewish — stated in the act", "is_jewish"),
                # The legacy editorial layer (2010s data entry) is display-only:
                # the platform never sets it, and the only sanctioned write is the
                # audited refute action (jewish_db 1→0) offered on this field.
                {
                    "label": "Jewish — editorial attribution",
                    "value": yes_no(inv["jewish_db"]),
                    "note": ("Marked as Jewish by the project's data-entry team during the "
                             "original database entry (2010s), on the basis of name and context."),
                    "refute": ({"investor_id": str(iid)}
                               if inv["jewish_db"] in (1, "1", True) else None),
                },
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
                          "is_jewish", "jewish_db", "is_convert", "heirs", "heirs_of", "and_c")
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
            # The two Jewish layers, kept apart (read-only views such as Find's drawer).
            "jewish": None
            if removed
            else {"stated": inv["is_jewish"] in (1, "1", True), "attributed": inv["jewish_db"] in (1, "1", True)},
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
    # A stake with nobody attached: `unattached` marks it for the UI (which says
    # so in words and offers Attach / Remove); Status keeps its meaning of the
    # partner's personal standing (widow, guardian) and stays blank here.
    def make_stake_row(iv: sqlite3.Row, *, removed: bool = False) -> dict[str, Any]:
        inv_id = iv["investment_id"]
        return {
            "key": f"investment:{inv_id}",
            "investment_id": str(inv_id),
            "person": None,
            "role": editable_cell("investment", inv_id, "type", iv["inv_type"], display_text(iv["inv_type"]), proposals),
            "cash": cash_cell(inv_id, iv["investment_cash"], iv["investment_non_cash"]),
            "profession": None,
            "residence": "—",
            "status": "removed" if removed else "—",
            "unattached": True,
            "removed": removed,
            "attributes": None,
        }

    rows.extend(make_stake_row(iv) for iv in orphan_investments)

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
        # Stakes removed through the stake endpoint (soft-deleted investment rows)
        # come back greyed with a Restore, like removed partners.
        removed_stakes = connection.execute(
            """
            SELECT investment_id, type AS inv_type, investment_cash, investment_non_cash
            FROM investment WHERE contract_id = ? AND is_deleted = 1 ORDER BY investment_id
            """,
            (raw_id,),
        ).fetchall()
        rows.extend(make_stake_row(iv, removed=True) for iv in removed_stakes)

    return {"count": live_count, "rows": rows, "removed_count": len(rows) - live_count}


def build_places(connection: sqlite3.Connection, raw_id: str, include_hidden: bool = False) -> dict[str, Any]:
    """The contract's place(s) — where the firm operated — as an editable block.
    `address` is free text (editable); the place itself is re-pointed by remove +
    add (its place_id is part of the composite PK). Live rows only by default.
    `inferred` is the data-entry team's mark (place_db), as Find's List writes it."""
    def make(pl: sqlite3.Row, *, removed: bool = False) -> dict[str, Any]:
        return {
            "key": f"place:{pl['place_id']}",
            "place_id": str(pl["place_id"]),
            "place": display_text(pl["place_name"]),
            "address": pl["address"] or "",
            "inferred": pl["place_db"] in (1, "1", True),
            "removed": removed,
        }

    sql = (
        "SELECT cp.place_id AS place_id, cp.address AS address, p.place_name AS place_name, cp.place_db AS place_db "
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
    # currency_id 0 is the "no currency recorded" sentinel: lookup_value returns
    # None for it, which must never be rendered literally ("4500 None").
    if total in (None, ""):
        total_text = None
    elif currency:
        total_text = f"{total} {currency}"
    else:
        total_text = f"{total} · currency not recorded"
    corrections = proposals_for_row(f"contract:{raw_id}")
    fields = [
        record_field("Firm name", "contract", "firm_name", data.get("firm_name"), display_text(data.get("firm_name")), corrections),
        record_field("Registration date", "contract", "registration_date", data.get("registration_date"), display_date(data.get("registration_date")), corrections),
        record_field("Start date", "contract", "start_date", data.get("start_date"), display_date(data.get("start_date")), corrections),
        # Capital amount + its currency unit are one concept ("1000" + "fiorini
        # d'oro"); kept consecutive (and a row-start pair at the usual width) so
        # they read and edit together, not 10 fields apart.
        record_field("Total capital", "contract", "total", data.get("total"), display_text(total_text), corrections),
        relink_field(connection, "Currency", "contract", raw_id, "currency_id", "currency", data.get("currency_id")),
        record_field("Folio", "contract", "folio", data.get("folio"), display_text(data.get("folio")), corrections),
        record_field("Archive / series / folder", "contract", None, None, display_text(archive_ref), corrections),
        record_field("Duration (months)", "contract", "duration_months", data.get("duration_months"), display_text(data.get("duration_months")), corrections),
        record_field("Automatic renewal", "contract", "automatic_renewal", data.get("automatic_renewal"), yes_no(data.get("automatic_renewal")), corrections),
        record_field("Renewal period (months)", "contract", "automatic_renewal_months", data.get("automatic_renewal_months"), display_text(data.get("automatic_renewal_months")), corrections),
        record_field("Special clauses", "contract", "clauses", data.get("clauses"), yes_no(data.get("clauses")), corrections),
        record_field("Place at discretion", "contract", "pl_discretion", data.get("pl_discretion"), yes_no(data.get("pl_discretion")), corrections),
        record_field("Economic activity at discretion", "contract", "ec_discretion", data.get("ec_discretion"), yes_no(data.get("ec_discretion")), corrections),
        record_field("Administrators named", "contract", "administrators", data.get("administrators"), yes_no(data.get("administrators")), corrections),
        record_field("Additional documents", "contract", "additional_docs", data.get("additional_docs"), yes_no(data.get("additional_docs")), corrections),
        relink_field(connection, "Economic activity", "contract", raw_id, "economic_sector", "economic_activity", data.get("economic_sector")),
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
                            display_date(s["registration_date"]),
                            display_text(s["folio"]),
                            display_text(s["sub_firm_name"]),
                        ],
                    }
                    for s in subs
                ],
            }
        )
    title, subtitle = record_heading("Contract", raw_id, data.get("firm_name"), record_locator(data))
    register = register_label(data.get("series"), data.get("folder"))
    return {
        "table": "contract",
        "id": str(raw_id),
        "row_id": f"contract:{raw_id}",
        "title": title,
        "subtitle": subtitle,
        "fields": fields,
        "partners": partners,
        "places": places,
        "sections": sections,
        "document": clean_document(data.get("document")),
        "document_correction": corrections.get("document"),
        "manuscript_images": manuscript_images_for(data.get("folder"), data.get("folio")),
        "word_sources": linked_word_sources(f"contract:{raw_id}"),
        "word_sources_note": None,
        # For read-only views (Find's drawer): the register as the filters name it,
        # "" when none is recorded, and the data-entry team's mark on the activity.
        "register": "" if register == NO_REGISTER_LABEL else register,
        "activity_inferred": data.get("economic_sector_db") in (1, "1", True),
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
        record_field("Registration date", "sub_contract", "registration_date", data.get("registration_date"), display_date(data.get("registration_date")), corrections),
        record_field("End date", "sub_contract", "end_date", data.get("end_date"), display_date(data.get("end_date")), corrections),
        record_field("Renewal (months)", "sub_contract", "renewal_months", data.get("renewal_months"), display_text(data.get("renewal_months")), corrections),
        record_field("Folio", "sub_contract", "folio", data.get("folio"), display_text(data.get("folio")), corrections),
        record_field("Archive / series / folder", "sub_contract", None, None, display_text(archive_ref), corrections),
    ]
    _attach_word_check(connection, fields, f"sub_contract:{raw_id}")

    sections: list[dict[str, Any]] = []
    main_id = data.get("main_contract_id")
    if main_id not in (None, ""):
        main = connection.execute(
            "SELECT contract_id, firm_name, registration_date, folio FROM contract WHERE contract_id = ? AND is_deleted = 0",
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
                                display_date(main["registration_date"]),
                                display_text(main["folio"]),
                            ],
                        }
                    ],
                }
            )

    title, subtitle = record_heading(
        "Sub-contract",
        raw_id,
        data.get("sub_firm_name"),
        str(data.get("sub_type") or "").strip(),
        record_locator(data),
        f"on contract {main_id}" if main_id else "",
    )
    return {
        "table": "sub_contract",
        "id": str(raw_id),
        "row_id": f"sub_contract:{raw_id}",
        "title": title,
        "subtitle": subtitle,
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

    # One line per appearance (investor row), not per contract: an entry that
    # is on one act twice — Gherardi 7849 as the senator and as his bank's
    # stake — gets two lines that the role, title and heirs flag tell apart.
    contracts = connection.execute(
        """
        SELECT i.investor_id AS investor_id, i.contract_id AS contract_id,
               i.profession AS profession, i.heirs_of AS heirs_of,
               t.title_name AS title,
               GROUP_CONCAT(DISTINCT inv.type) AS roles,
               c.firm_name AS firm_name, c.registration_date AS registration_date
        FROM investor i
        LEFT JOIN contract c ON c.contract_id = i.contract_id
        LEFT JOIN title t ON t.title_id = i.title
        LEFT JOIN investor_group ig ON ig.investor_id = i.investor_id AND ig.is_deleted = 0
        LEFT JOIN investment inv ON inv.investment_id = ig.investment_id AND inv.is_deleted = 0
        WHERE i.person_id = ? AND i.is_deleted = 0
          AND (c.contract_id IS NULL OR c.is_deleted = 0)
        GROUP BY i.investor_id
        ORDER BY c.registration_date, i.contract_id, i.investor_id
        """,
        (raw_id,),
    ).fetchall()

    def recorded_as(c: sqlite3.Row) -> str:
        roles = sorted({part.strip().lower() for part in str(c["roles"] or "").split(",") if part.strip()})
        words = [
            {"gp": "general partner", "lp": "external investor"}.get(role, role) for role in roles
        ]
        if c["heirs_of"]:
            words.append("through heirs")
        role_text = ", ".join(words)
        title = str(c["title"] or "").strip()
        return " · ".join(part for part in (title, role_text) if part) or "—"

    # A reader's marking of which acts are whose, as a quiet last column:
    # "someone else" / "unsure", blank for this entry; absent when no marks
    # stand. The record itself is unchanged by it.
    marks = _person_card_marks(int(raw_id)) if contracts else None
    mark_word = {"other": "someone else", "unsure": "unsure", "this": ""}
    sections: list[dict[str, Any]] = []
    if contracts:
        sections.append(
            {
                "title": f"Appears in contracts ({len(contracts)})",
                "columns": ["Firm", "Date", "Recorded as", "Profession"]
                + (["Marked as"] if marks is not None else []),
                "link_table": "contract",
                "rows": [
                    {
                        # `id` opens the contract; `key` tells two appearances
                        # on one contract apart.
                        "id": str(c["contract_id"]),
                        "key": f"{c['contract_id']}:{c['investor_id']}",
                        "investor_id": int(c["investor_id"]),
                        "cells": [
                            display_text(c["firm_name"]),
                            display_date(c["registration_date"]),
                            recorded_as(c),
                            display_text(c["profession"]),
                        ]
                        + (
                            [mark_word.get(marks.get(int(c["investor_id"]), "this"), "not marked")]
                            if marks is not None
                            else []
                        ),
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
            f"{len(targets)} contract(s) where this person is recorded as a "
            "partner."
        )
        if total > cap:
            word_sources_note += f" Showing the first {cap} of {total}."

    # "Person #134 · 2 contracts, 1445–1452": the same shape as the browse row.
    years = sorted(
        {
            str(c["registration_date"])[:4]
            for c in contracts
            if str(c["registration_date"] or "")[:4].isdigit() and str(c["registration_date"]) != "0000-00-00"
        }
    )
    span = f", {years[0]}" if len(years) == 1 else f", {years[0]}–{years[-1]}" if years else ""
    appearances = (
        "no contracts" if not contracts else f"{len(contracts)} contract{'' if len(contracts) == 1 else 's'}{span}"
    )
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
        "subtitle": f"Person entry #{raw_id} · {appearances}",
        "fields": fields,
        "sections": sections,
        "document": None,
        "word_sources": word_sources,
        "word_sources_note": word_sources_note,
        "identity_status": person_identity_status(int(raw_id)),
    }


def search_meta(date: Any, folio: Any, folder: Any, sub_type: Any = None, series: Any = None) -> str:
    """Readable one-line meta for a search result.

    Placeholder dates (``0000-00-00``) are suppressed — they are a known
    data-quality issue, not information — and the register folder is shown so
    a result can be located archivally even when the firm name is blank. Pass
    ``series`` too where it is at hand so the rows with swapped series/folder
    columns still read "reg. 10842" rather than "reg. Mercanzia".
    """
    date_text = str(date or "").strip()
    if date_text == "0000-00-00":
        date_text = "no date"
    number = register_number(series, folder) if series is not None else str(folder or "").strip()
    parts = [
        str(sub_type or "").strip(),
        date_text,
        f"c. {str(folio).strip()}" if str(folio or "").strip() else "",
        f"reg. {number}" if number else "",
    ]
    return " · ".join(part for part in parts if part)


def _person_search_meta(person_id: Any, contract_count: Any) -> str:
    """Meta line for a person browse row: '#12 · 2 contracts'.

    Formatted server-side so every client renders the same plural-safe copy;
    "no contracts" marks the stranded entries (name-forms attached to nothing)
    that person-linkage review keeps surfacing.
    """
    count = int(contract_count or 0)
    if count == 0:
        suffix = "no contracts"
    elif count == 1:
        suffix = "1 contract"
    else:
        suffix = f"{count} contracts"
    return f"Entry #{person_id} · {suffix}"


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
            # or what trade it ran — not only the firm_name string. The two lookups
            # are plain IN-subqueries, evaluated once per search: as correlated
            # EXISTS clauses they re-scanned the unindexed investor table for each
            # of the 4,866 rows and every keystroke cost six seconds (UI review
            # 2026-09-10), which read as "the search does nothing".
            conditions.append(
                "(firm_name LIKE ? OR folio LIKE ? OR CAST(contract_id AS TEXT) LIKE ? "
                "OR contract_id IN (SELECT iv.contract_id FROM investor iv "
                "  JOIN person p ON p.person_id = iv.person_id "
                "  WHERE iv.is_deleted = 0 AND p.is_deleted = 0 "
                "  AND (p.first_name LIKE ? OR p.last_name LIKE ? OR p.nickname LIKE ?)) "
                "OR CAST(economic_sector AS TEXT) IN (SELECT CAST(ea.ec_activity_id AS TEXT) "
                "  FROM economic_activity ea WHERE ea.activity LIKE ?))"
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
        if register == NO_REGISTER:
            conditions.append("TRIM(COALESCE(folder, '')) = ''")
        elif register:
            # A register number lives in `folder`, except on the rows where the two
            # columns were swapped at entry ("Mercanzia" / "10842"): match those too.
            conditions.append(
                "(TRIM(folder) = ? OR (TRIM(COALESCE(series, '')) = ? "
                "AND TRIM(COALESCE(folder, '')) NOT GLOB '[0-9]*'))"
            )
            params += [register, register]
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
                f"SELECT contract_id, registration_date, folio, firm_name, folder, series FROM contract {where} "
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
                        r["registration_date"], r["folio"], r["folder"], series=r["series"]
                    ),
                }
                for r in rows
            ]
        elif table == "sub_contract":
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM sub_contract {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"SELECT contract_id, registration_date, folio, sub_firm_name, sub_type, folder, series "
                f"FROM sub_contract {where} "
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
                        r["registration_date"], r["folio"], r["folder"], r["sub_type"], series=r["series"]
                    ),
                }
                for r in rows
            ]
        else:  # person
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM person {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                "SELECT person_id, first_name, last_name, nickname, "
                # Live-contract count rides along so the browse rail can tell a
                # working row from a stranded entry (a name-form that never
                # appears in any contract) without opening each record.
                "(SELECT COUNT(DISTINCT iv.contract_id) FROM investor iv "
                " WHERE iv.person_id = person.person_id AND iv.is_deleted = 0"
                ") AS live_contract_count "
                f"FROM person {where} "
                # Mononyms (blank surname) sort last, not first (the default name sort).
                f"ORDER BY CASE WHEN CAST(person_id AS TEXT) = ? THEN 0 ELSE 1 END, {order_body} "
                "LIMIT ? OFFSET ?",
                (*params, term, limit, offset),
            ).fetchall()
            identity = person_identity_statuses([int(r["person_id"]) for r in rows])
            results = [
                {
                    "id": str(r["person_id"]),
                    "row_id": f"person:{r['person_id']}",
                    "title": person_display_name(
                        r["first_name"], r["last_name"], r["nickname"]
                    ),
                    "meta": _person_search_meta(
                        r["person_id"], r["live_contract_count"]
                    ),
                    "identity_hint": _person_identity_hint(identity[int(r["person_id"])]),
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


# Filter sentinel for rows whose register folder is blank. The browse filter's
# "All registers" option is the empty string, so "(no register)" needs its own
# non-empty value or the two are indistinguishable on the wire.
NO_REGISTER = "__none__"
NO_REGISTER_LABEL = "(no register)"


def register_parts(series: str | None, folder: str | None) -> tuple[str, str]:
    """(series kind, register number) for one row, read-time only.

    Tolerates what the corpus actually holds: the series misspelled ("Mercancia",
    "Mercazia"), spelled with varying case, left blank on a stray row, and — for
    two rows — swapped with the folder ("Mercanzia" in `folder`, "10842" in
    `series`). Kind is "mercanzia", "commercio" or "" (unknown). Stored values are
    never rewritten."""
    series_text = (series or "").strip()
    number = (folder or "").strip()
    if number and not number[:1].isdigit() and series_text[:1].isdigit():
        series_text, number = number, series_text  # swapped columns
    series_lc = series_text.lower()
    if series_lc.startswith("merca"):
        kind = "mercanzia"
    elif "commercio" in series_lc or "camera" in series_lc:
        kind = "commercio"
    else:
        kind = ""
    return kind, number


def register_label_for(kind: str, number: str) -> str:
    if not number:
        return NO_REGISTER_LABEL
    if kind == "mercanzia":
        return f"Mercanzia {number}"
    if kind == "commercio":
        return f"Camera di Commercio {number}"
    return number


def register_label(series: str | None, folder: str | None) -> str:
    """Human register label from the (case-varying) series + (trimmed) folder,
    collapsed to one clean label at read time (no data writes)."""
    return register_label_for(*register_parts(series, folder))


def register_number(series: str | None, folder: str | None) -> str:
    """The register number a row belongs to, swap-tolerant (see register_parts)."""
    return register_parts(series, folder)[1]


def record_locator(data: dict[str, Any]) -> str:
    """'Mercanzia 10831 · c. 7r · 1445-04-19': the archival address of a contract
    or sub-contract row, as far as the row records it."""
    register = register_label(data.get("series"), data.get("folder"))
    if register == NO_REGISTER_LABEL:
        register = "no register recorded"
    folio = str(data.get("folio") or "").strip()
    date = str(data.get("registration_date") or "").strip()
    parts = [register, f"c. {folio}" if folio else "", date if date and date != "0000-00-00" else ""]
    return " · ".join(part for part in parts if part)


def record_heading(kind: str, raw_id: Any, name: Any, *details: str) -> tuple[str, str]:
    """(title, subtitle) for a record page. The title is the firm's name when the
    row has one, else 'Contract 63'. The subtitle carries the number only when the
    title does not, then the locator and any further detail, and says so when no
    name is recorded — so the header's best line is never 'Main contract · 63'
    above 'Contract 63'."""
    name_text = str(name or "").strip()
    title = name_text or f"{kind} {raw_id}"
    parts = [f"{kind} {raw_id}" if name_text else "", *details, "" if name_text else "no firm name recorded"]
    return title, " · ".join(part for part in parts if part)


def _register_sort_key(kind: str, number: str) -> tuple[int, int, str]:
    """Chronological order for the filter: Mercanzia (1445–1770), then Camera di
    Commercio (1786–1808), then unknown series, then the blank bucket last; within
    a series by number ("1263" before "1263bis")."""
    rank = {"mercanzia": 0, "commercio": 1}.get(kind, 2) if number else 3
    digits = re.match(r"\d+", number)
    return rank, int(digits.group(0)) if digits else 0, number


def register_facets(rows: list[Any]) -> list[dict[str, Any]]:
    """Collapse per-(series, folder) counts into one filter option per register
    number. The series label is a majority vote across that register's rows, so one
    misspelled or blank row no longer strips the prefix from the other 170."""
    votes: dict[str, dict[str, int]] = {}
    for row in rows:
        kind, number = register_parts(row["series"], row["folder"])
        bucket = votes.setdefault(number, {})
        bucket[kind] = bucket.get(kind, 0) + int(row["c"])
    options = []
    for number, kinds in votes.items():
        known = {k: n for k, n in kinds.items() if k}
        kind = max(known, key=known.__getitem__) if known else ""
        options.append(
            {
                "folder": number or NO_REGISTER,
                "label": register_label_for(kind, number),
                "count": sum(kinds.values()),
                "sort": _register_sort_key(kind, number),
            }
        )
    options.sort(key=lambda o: o["sort"])
    for option in options:
        del option["sort"]
    return options


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
        registers = register_facets(
            connection.execute(
                f"SELECT TRIM(COALESCE(series, '')) AS series, TRIM(COALESCE(folder, '')) AS folder, "
                f"COUNT(*) AS c FROM {table} WHERE is_deleted = 0 GROUP BY 1, 2"
            ).fetchall()
        )
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


# ---- Reference: the controlled vocabularies (read-only browse) ----
# The verbatim term is the source. We compute ONLY facts here: usage counts and
# text matches. No categories, normalized forms, or groupings are stored or
# returned — only the term as transcribed and how many records reference it.
REFERENCE_KINDS: dict[str, tuple[str, str, str]] = {
    "place": ("place", "place_id", "place_name"),
    "title": ("title", "title_id", "title_name"),
    "currency": ("currency", "currency_id", "currency"),
    "activity": ("economic_activity", "ec_activity_id", "activity"),
}


# Each vocabulary's kinds of use, as (term, contract) pairs on LIVE contracts only
# (joined to contract), so links on hidden contracts, or on contract numbers that
# do not exist, never count: the same population the term page and Analysis use.
# Every number counts contracts (WH, 2026-10-26): a contract that records a term
# several times, or in several ways, counts once.
_LIVE_INVESTOR = "FROM investor i JOIN contract c ON c.contract_id = i.contract_id AND c.is_deleted = 0 "
_REFERENCE_PARTS: dict[str, list[tuple[str, str]]] = {
    "place": [
        (
            "activity",
            "SELECT DISTINCT cp.place_id k, cp.contract_id cid FROM contract_place cp "
            "JOIN contract c ON c.contract_id = cp.contract_id AND c.is_deleted = 0 "
            "WHERE cp.is_deleted = 0",
        ),
        ("residence", f"SELECT DISTINCT i.place_of_residence k, i.contract_id cid {_LIVE_INVESTOR}WHERE i.is_deleted = 0"),
        ("origin", f"SELECT DISTINCT i.place_of_origin k, i.contract_id cid {_LIVE_INVESTOR}WHERE i.is_deleted = 0"),
    ],
    "title": [
        (col, f"SELECT DISTINCT i.{col} k, i.contract_id cid {_LIVE_INVESTOR}WHERE i.is_deleted = 0")
        for col in ("title", "title_husband", "title_father_mother", "title_grandfather")
    ],
    "currency": [("contract", "SELECT currency_id k, contract_id cid FROM contract WHERE is_deleted = 0")],
    "activity": [("contract", "SELECT economic_sector k, contract_id cid FROM contract WHERE is_deleted = 0")],
}


def _reference_contracts(connection: sqlite3.Connection, kind: str) -> dict[int, dict[str, set[int]]]:
    """Per term, the live contracts behind each kind of use — pure counting, no judgement."""
    links: dict[int, dict[str, set[int]]] = {}
    for part, sql in _REFERENCE_PARTS.get(kind, []):
        for row in connection.execute(sql):
            key = row["k"]
            if key not in (None, 0, ""):
                links.setdefault(int(key), {}).setdefault(part, set()).add(int(row["cid"]))
    return links


def _reference_counts(connection: sqlite3.Connection, kind: str) -> dict[int, int]:
    """How many live contracts record each term, in any of its kinds of use."""
    return {k: len(set().union(*v.values())) for k, v in _reference_contracts(connection, kind).items()}


@app.get("/api/db/reference/{kind}")
def db_reference(
    kind: str,
    q: str = "",
    sort: str = "usage",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=300),
    orphans_only: bool = False,
) -> dict[str, Any]:
    if kind not in REFERENCE_KINDS:
        raise HTTPException(status_code=400, detail="Unknown vocabulary.")
    table, id_col, name_col = REFERENCE_KINDS[kind]
    connection = open_db()
    try:
        counts = _reference_counts(connection, kind)
        items = [
            {"id": int(r["id"]), "value": r["value"], "count": counts.get(int(r["id"]), 0)}
            for r in connection.execute(f"SELECT {id_col} AS id, {name_col} AS value FROM {table}")
            if (r["value"] or "").strip()
        ]
        # Top-by-usage for the overview chart — global, before any filtering. Facts only.
        top = [
            {"value": it["value"], "count": it["count"]}
            for it in sorted(items, key=lambda x: -x["count"])[:12]
            if it["count"] > 0
        ]
        term = q.strip()
        if term:
            needle = _lookup_norm(term)
            items = [it for it in items if needle in _lookup_norm(it["value"])]
        # Default view = the vocabulary as actually used (≥1 structured reference).
        # The unused terms are old-site lookup rows never linked to a record; they
        # stay reachable as a curation worklist via orphans_only, never deleted.
        items = [it for it in items if (it["count"] == 0) == orphans_only]
        if sort == "name":
            items.sort(key=lambda x: _lookup_norm(x["value"]))
        else:
            items.sort(key=lambda x: (-x["count"], _lookup_norm(x["value"])))
        total = len(items)
        page = items[offset : offset + limit]
        return {"kind": kind, "total": total, "shown": len(page), "offset": offset, "terms": page, "top": top}
    finally:
        connection.close()


@app.get("/api/db/reference/{kind}/{term_id}/records")
def db_reference_records(
    kind: str,
    term_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=120, ge=1, le=500),
) -> dict[str, Any]:
    """The contracts that reference a term — the live index + a by-decade histogram
    (when the term is attested). All derived by following foreign keys; no inference.
    Paged by offset/limit over the full date-ordered list; record_total, by_decade
    and narrative_mentions always describe the whole set."""
    if kind not in REFERENCE_KINDS:
        raise HTTPException(status_code=400, detail="Unknown vocabulary.")
    table, id_col, name_col = REFERENCE_KINDS[kind]
    connection = open_db()
    try:
        row = connection.execute(f"SELECT {name_col} AS v FROM {table} WHERE {id_col} = ?", (term_id,)).fetchone()
        value = row["v"] if row else ""
        if not (value or "").strip():
            # A link to a term that is gone (or a blank row): the page says so.
            raise HTTPException(status_code=404, detail="This term is not in the list.")
        cids: set[int] = set()
        if kind == "place":
            for r in connection.execute(
                "SELECT DISTINCT contract_id c FROM contract_place WHERE is_deleted = 0 AND place_id = ?", (term_id,)
            ):
                cids.add(r["c"])
            for r in connection.execute(
                "SELECT DISTINCT contract_id c FROM investor WHERE is_deleted = 0 "
                "AND (place_of_residence = ? OR place_of_origin = ?)",
                (term_id, term_id),
            ):
                cids.add(r["c"])
        elif kind == "title":
            for r in connection.execute(
                "SELECT DISTINCT contract_id c FROM investor WHERE is_deleted = 0 "
                "AND (title = ? OR title_husband = ? OR title_grandfather = ? OR title_father_mother = ?)",
                (term_id, term_id, term_id, term_id),
            ):
                cids.add(r["c"])
        elif kind == "currency":
            for r in connection.execute(
                "SELECT contract_id c FROM contract WHERE is_deleted = 0 AND currency_id = ?", (term_id,)
            ):
                cids.add(r["c"])
        elif kind == "activity":
            for r in connection.execute(
                "SELECT contract_id c FROM contract WHERE is_deleted = 0 AND economic_sector = ?", (term_id,)
            ):
                cids.add(r["c"])
        cids.discard(None)
        records: list[dict[str, Any]] = []
        decade: dict[int, int] = {}
        if cids:
            marks = ",".join("?" * len(cids))
            for r in connection.execute(
                f"SELECT contract_id, firm_name, registration_date, folio, folder FROM contract "
                f"WHERE contract_id IN ({marks}) AND is_deleted = 0 "
                "ORDER BY CASE WHEN registration_date IS NULL OR registration_date IN ('', '0000-00-00') "
                "THEN 1 ELSE 0 END, registration_date, contract_id",
                tuple(cids),
            ):
                records.append(
                    {
                        "id": str(r["contract_id"]),
                        "row_id": f"contract:{r['contract_id']}",
                        "title": (r["firm_name"] or "").strip() or f"Contract {r['contract_id']}",
                        "meta": search_meta(r["registration_date"], r["folio"], r["folder"]),
                    }
                )
                d = str(r["registration_date"] or "")
                if len(d) >= 4 and d[:4].isdigit() and d not in ("0000-00-00",):
                    dec = int(d[:3] + "0")
                    decade[dec] = decade.get(dec, 0) + 1
        # How often the term's text appears in the free narrative — a different,
        # honest signal from the structured links above. For an unused term this
        # shows whether it is a genuine missing-link candidate (in the text) or a
        # stray spelling (absent). Case-insensitive substring; a rough but useful count,
        # kept apart for contracts and sub-contracts so the page can say which.
        narrative_contracts = narrative_sub_contracts = 0
        if (value or "").strip():
            like = f"%{value}%"
            narrative_contracts = connection.execute(
                "SELECT COUNT(*) FROM contract WHERE is_deleted = 0 AND document LIKE ? COLLATE NOCASE", (like,)
            ).fetchone()[0]
            narrative_sub_contracts = connection.execute(
                "SELECT COUNT(*) FROM sub_contract WHERE is_deleted = 0 AND document LIKE ? COLLATE NOCASE", (like,)
            ).fetchone()[0]
        # The rail's number for this term (its contracts), split by kind of use;
        # same SQL as the rail. A contract can count under more than one part.
        links = _reference_contracts(connection, kind).get(term_id, {})
        parts = {part: len(c) for part, c in links.items()}
        # Machine resolution + coordinate (place only, if the batch has run).
        resolution = None
        if kind == "place" and place_cache.exists():
            pcache = place_cache.connect()
            try:
                resolution = place_cache.resolution_for(pcache, term_id)
            finally:
                pcache.close()
        return {
            "kind": kind,
            "value": value,
            # count the LIVE records (the same population the list + histogram
            # show), not the raw id set — which can include hidden contracts and
            # the phantom-contract links catalogued in the integrity report.
            "record_total": len(records),
            "usage_parts": parts,
            "usage_total": len(set().union(*links.values())),
            "narrative_mentions": narrative_contracts + narrative_sub_contracts,
            "narrative_contracts": narrative_contracts,
            "narrative_sub_contracts": narrative_sub_contracts,
            "offset": offset,
            "records": records[offset : offset + limit],
            "by_decade": [{"decade": k, "count": v} for k, v in sorted(decade.items())],
            "resolution": resolution,
        }
    finally:
        connection.close()


# ---- Reference: duplicate finder + interpretive links (human curation) ----
# A matcher proposes same-as candidates; it never acts. A human links (additive,
# attributed, reversible) or marks distinct. Links live in corrections.db and
# never alter the verbatim term in main.db.

def _link_lookup(connection: sqlite3.Connection, kind: str) -> tuple[str, str, str]:
    table, id_col, name_col = REFERENCE_KINDS[kind]
    return table, id_col, name_col


# Confidence at/above which a machine suggestion joins the main "likely the same"
# lane; below it goes to the separate "needs your eye" lane.
LLM_CONFIDENT_THRESHOLD = 0.8


def _llm_suggestion_families(
    kind: str, name_by_id: dict[int, str], counts: dict[int, int], undecided
) -> list[dict[str, Any]]:
    """Cached machine (LLM) same-as suggestions as worklist families. Place only,
    and only if the resolution batch has been run (place_cache.db present).
    Returns ALL suggestion families (the caller splits by confidence); each is
    flagged source="llm" with the model's rationale, and passes the same
    undecided() filter so acted-on groups disappear."""
    if kind != "place" or not place_cache.exists():
        return []
    cache = place_cache.connect()
    try:
        out: list[dict[str, Any]] = []
        for grp in place_cache.suggestion_families(cache):
            terms = [
                {"id": pid, "value": name_by_id[pid], "count": counts.get(pid, 0)}
                for pid in grp["place_ids"] if pid in name_by_id
            ]
            if len(terms) < 2 or not undecided(terms):
                continue
            terms.sort(key=lambda t: -t["count"])
            out.append({
                "signature": grp["group_key"],
                "source": "llm",
                "rationale": grp["rationale"],
                "confidence": grp["confidence"],
                "terms": terms,
            })
        out.sort(key=lambda f: (-f["confidence"], -sum(t["count"] for t in f["terms"])))
        return out
    finally:
        cache.close()


@app.get("/api/db/reference/{kind}/duplicates")
def db_reference_duplicates(kind: str) -> dict[str, Any]:
    """Same-as candidates for a vocabulary, with already-decided pairs removed.

    Returns ``{available, families, cautions, links}``. ``available`` is False
    for vocabularies whose matcher isn't built yet (place/title/activity)."""
    if kind not in REFERENCE_KINDS:
        raise HTTPException(status_code=400, detail="Unknown vocabulary.")
    matcher = reference_match.MATCHERS.get(kind)
    if matcher is None:
        return {"kind": kind, "available": False, "families": [], "uncertain": [],
                "cautions": [], "links": []}
    table, id_col, name_col = _link_lookup(connection := open_db(), kind)
    clog = open_corrections()
    try:
        counts = _reference_counts(connection, kind)
        terms = [
            {"id": int(r["id"]), "value": r["value"], "count": counts.get(int(r["id"]), 0)}
            for r in connection.execute(f"SELECT {id_col} AS id, {name_col} AS value FROM {table}")
            if (r["value"] or "").strip() and counts.get(int(r["id"]), 0) > 0
        ]
        result = matcher(terms)
        links = corrections_db.active_reference_links(clog, kind)
        # A family is resolved once its members are all transitively connected by
        # same-as links, or each remaining pair is marked distinct. (Linking three
        # variants to one canonical makes the two variants same *transitively* — we
        # must not keep re-suggesting them.)
        same_pairs = [
            (int(ln["from_id"]), int(ln["to_id"]))
            for ln in links if ln["rel"] in ("same_as", "variant_of")
        ]
        distinct_set = {
            frozenset((int(ln["from_id"]), int(ln["to_id"])))
            for ln in links if ln["rel"] == "distinct"
        }

        def undecided(group: list[dict]) -> bool:
            ids = [t["id"] for t in group]
            id_set = set(ids)
            parent = {i: i for i in ids}

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            for a, b in same_pairs:
                if a in id_set and b in id_set:
                    parent[find(a)] = find(b)
            return any(
                find(ids[i]) != find(ids[j]) and frozenset((ids[i], ids[j])) not in distinct_set
                for i in range(len(ids))
                for j in range(i + 1, len(ids))
            )

        families = [f for f in result["families"] if undecided(f["terms"])]
        cautions = result.get("cautions", [])
        # decorate links with the current verbatim values for display
        name_by_id = {int(r["id"]): r["value"] for r in connection.execute(
            f"SELECT {id_col} AS id, {name_col} AS value FROM {table}")}
        for ln in links:
            ln["from_value"] = name_by_id.get(int(ln["from_id"]), "")
            ln["to_value"] = name_by_id.get(int(ln["to_id"]), "")

        # Machine (LLM) suggestions — currently place only. Confident ones join the
        # main lane (flagged source="llm"); low-confidence ones go to a separate
        # "needs your eye" lane. Same undecided() filter so acted-on pairs vanish.
        uncertain = _llm_suggestion_families(kind, name_by_id, counts, undecided)
        confident = [f for f in uncertain if f["confidence"] >= LLM_CONFIDENT_THRESHOLD]
        families = families + confident
        uncertain = [f for f in uncertain if f["confidence"] < LLM_CONFIDENT_THRESHOLD]
        return {
            "kind": kind,
            "available": True,
            "families": families,
            "uncertain": uncertain,
            "cautions": cautions,
            "links": links,
            "note": result.get("note", ""),
        }
    finally:
        clog.close()
        connection.close()


class ReferenceLink(BaseModel):
    reviewer: str = Field(min_length=1)
    rel: str = "same_as"        # same_as | variant_of | distinct
    from_id: int
    to_id: int
    reason: str = ""


@app.post("/api/db/reference/{kind}/link")
@serialized_write
def db_reference_link(kind: str, action: ReferenceLink) -> dict[str, Any]:
    """Record a reviewed link between two terms — additive, attributed,
    reversible. Never mutates either verbatim term."""
    if kind not in REFERENCE_KINDS:
        raise HTTPException(status_code=400, detail="Unknown vocabulary.")
    if action.rel not in ("same_as", "variant_of", "distinct"):
        raise HTTPException(status_code=400, detail="Unknown relation.")
    if action.from_id == action.to_id:
        raise HTTPException(status_code=400, detail="Cannot link a term to itself.")
    clog = open_corrections()
    try:
        # guard against duplicating an existing active link on the same pair
        for ln in corrections_db.active_reference_links(clog, kind):
            if frozenset((int(ln["from_id"]), int(ln["to_id"]))) == frozenset((action.from_id, action.to_id)):
                raise HTTPException(status_code=409, detail="These two terms are already linked.")
        link_id = corrections_db.add_reference_link(
            clog, kind=kind, rel=action.rel, from_id=action.from_id, to_id=action.to_id,
            by=action.reviewer, reason=action.reason.strip() or None,
        )
        return {"ok": True, "link_id": link_id}
    finally:
        clog.close()


class ReviewerOnly(BaseModel):
    reviewer: str = Field(min_length=1)
    reason: str = ""


@app.post("/api/db/reference/link/{link_id}/revoke")
@serialized_write
def db_reference_link_revoke(link_id: str, action: ReviewerOnly) -> dict[str, Any]:
    clog = open_corrections()
    try:
        link = corrections_db.reference_link_by_id(clog, link_id)
        if link and link.get("kind") == "person":
            raise HTTPException(
                status_code=409,
                detail="Person identity links must be undone from the People review tool.",
            )
        ok = corrections_db.revoke_reference_link(clog, link_id, by=action.reviewer)
        if not ok:
            raise HTTPException(status_code=404, detail="This link no longer exists or was already undone.")
        return {"ok": True}
    finally:
        clog.close()


@app.get("/api/db/reference/place/map")
def db_reference_place_map() -> dict[str, Any]:
    """Geocoded places for the map: verbatim · usage · lat/lon · type · approx.
    Empty until the resolution batch (place_resolve.py) has run."""
    if not place_cache.exists():
        return {"available": False, "points": []}
    connection = open_db()
    cache = place_cache.connect()
    try:
        counts = _reference_counts(connection, "place")
        names = {int(r["place_id"]): r["place_name"] for r in connection.execute(
            "SELECT place_id, place_name FROM place")}
        points = []
        for g in place_cache.all_geo(cache):
            pid = g["place_id"]
            if pid not in names:
                continue
            points.append({
                "id": pid,
                "value": names[pid],
                "count": counts.get(pid, 0),
                "lat": g["lat"], "lon": g["lon"],
                "type": g["type"], "approx": g["approx"],
                "modern_name": g["modern_name"],
            })
        return {"available": True, "points": points}
    finally:
        cache.close()
        connection.close()


# ---- People: reviewed identity links over preserved person rows -------------

_PERSON_JSON_COLUMNS = {
    "blocking_lanes_json",
    "reasons_json",
    "concordance_reasons_json",
    "network_diagnostics_json",
    "firm_token_diagnostics_json",
    "evidence_json",
    "shared_contract_ids_json",
    "shared_firms_json",
    "shared_firm_words_json",
    "shared_partner_ids_json",
    "roles_json",
    "career_span_json",
    "contract_pointers_json",
    "source_pointers_json",
    "waterfall_contributions_json",
}
# The rail's lane order; the all-lanes search sorts its results by it.
_PERSON_LANE_ORDER = [
    "labeling_round",
    "likely_duplicates",
    "high_concordance",
    "read_source",
    "other_matches",
    "possible_splits",
    "decided",
    "rule_exclusions",
]
_PERSON_CLEAR_TIERS = {"batch_ghost", "same_as_strong"}
_PERSON_CAUTION_TIERS = {
    "caution_coappearance",
    "caution_posthumous_conflict",
    "caution_gf_conflict",
}


def _person_labeling_packet_rows() -> list[dict[str, Any]]:
    """Frozen packet order/provenance for the blind labeling round.

    Parsed bytes are memoised on (path, mtime, size) so Database People browse
    does not re-read the JSONL on every keystroke. A replaced packet (new mtime
    or size) misses the cache; a missing file is not cached as empty.
    """
    path = PERSON_LABELING_PACKET_PATH
    if not path.is_file():
        return []
    stat = path.stat()
    return list(
        _person_labeling_packet_rows_cached(str(path.resolve()), stat.st_mtime, stat.st_size)
    )


@lru_cache(maxsize=1)
def _person_labeling_packet_rows_cached(
    path: str, mtime: float, size: int
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{path}:{line_number}: invalid JSON") from exc
            key = str(row.get("pair_key") or "")
            if key and key not in seen:
                seen.add(key)
                rows.append(row)
    return tuple(rows)


def _person_labeling_metadata(case_id: str) -> dict[str, Any] | None:
    if not case_id.startswith("pair:"):
        return None
    pair_key_value = case_id.removeprefix("pair:")
    row = next(
        (
            item
            for item in _person_labeling_packet_rows()
            if str(item.get("pair_key")) == pair_key_value
        ),
        None,
    )
    if not row:
        return None
    manifest_path = PERSON_LABELING_PACKET_PATH.with_name(
        f"{PERSON_LABELING_PACKET_PATH.stem}.manifest.json"
    )
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    return {
        "packet_id": f"{manifest.get('algorithm_version', 'unknown')}:{str(manifest.get('packet_sha256') or '')[:12]}",
        "packet_sha256": manifest.get("packet_sha256"),
        "algorithm_version": manifest.get("algorithm_version"),
        "labeling_stratum": row.get("labeling_stratum"),
        "stratum_population": row.get("stratum_population"),
        "stratum_sample_size": row.get("stratum_sample_size"),
        "inclusion_probability": row.get("inclusion_probability"),
        "sampling_weight": row.get("sampling_weight"),
    }


def _person_labeling_packet_matches_run(
    rows: list[dict[str, Any]], run: dict[str, Any]
) -> bool:
    """Every packet row must come from the current cache run to run the round."""
    return bool(rows) and all(
        str(row.get("run_id")) == str(run.get("run_id")) for row in rows
    )


def _person_labeling_open_case_ids(
    run: dict[str, Any], latest: dict[str, dict[str, Any]]
) -> set[str]:
    """Case ids still frozen in the blind labeling round for this cache run.

    One membership test gates the blind detail payload, the standard-decision
    409, the lane exclusions and the summary count, so they can never drift
    apart. A missing or stale packet (any row cut from another cache run)
    yields no members: half-blinding a mismatched round would stamp labels
    with the wrong sampling provenance, so the round simply suspends and
    normal review behaviour returns. A case leaves the round once its latest
    event is a decision, and re-enters it when that decision is undone or
    the case is reopened.
    """
    rows = _person_labeling_packet_rows()
    if not _person_labeling_packet_matches_run(rows, run):
        return set()
    open_ids: set[str] = set()
    for row in rows:
        case_id = f"pair:{row['pair_key']}"
        event = latest.get(case_id)
        if event is None or event["action"] in {"reopen", "revoke"}:
            open_ids.add(case_id)
    return open_ids


def _person_labeling_frozen_case_ids(
    run: dict[str, Any], latest: dict[str, dict[str, Any]]
) -> set[str]:
    """Open packet cases PLUS their co-minted sibling pairs.

    Four rows minted in one entry batch (the Corradini shape) produce star
    pairs against the row with the appearance — sampled into the packet —
    and ghost-to-ghost sibling pairs that are not. The siblings carry the
    same family, the same evidence, and a visible score, so leaving them
    browsable mid-round is a side door into the blind judgment. A pair is
    frozen when BOTH its persons belong to open packet cases; a pair with
    any outside person is other work and stays visible. Everything thaws
    per-person as that person's packet cases are decided.
    """
    open_ids = _person_labeling_open_case_ids(run, latest)
    if not open_ids:
        return open_ids
    members: set[int] = set()
    for case_id in open_ids:
        left, _, right = case_id.removeprefix("pair:").partition(":")
        members.add(int(left))
        members.add(int(right))
    cache = open_person_cache()
    try:
        marks = ",".join("?" for _ in members)
        ordered = sorted(members)
        rows = cache.execute(
            f"SELECT pair_key FROM person_pair_suggestion "
            f"WHERE person_id_l IN ({marks}) AND person_id_r IN ({marks})",
            (*ordered, *ordered),
        ).fetchall()
    finally:
        cache.close()
    return open_ids | {f"pair:{row['pair_key']}" for row in rows}


# Record-derived evidence a blind labeler may see. Everything else inside
# evidence_json (network_diagnostics, firm_token_diagnostics) is computed by
# the pipeline and would leak the model's routing into the label.
_PERSON_BLIND_EVIDENCE_KEYS = {
    "names",
    "name_review",
    "career",
    "roles",
    "appearance_counts",
    "shared",
    "context_for_review",
}


def open_person_cache() -> sqlite3.Connection:
    if not PERSON_CACHE_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Person-linkage suggestions have not been built yet.",
        )
    connection = sqlite3.connect(f"file:{PERSON_CACHE_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _decode_person_pair(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for column in _PERSON_JSON_COLUMNS:
        value = item.get(column)
        if isinstance(value, str):
            item[column] = json.loads(value)
    item["case_id"] = f"pair:{item['pair_key']}"
    return item


def _decode_person_group(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for column in (
        "person_ids_json",
        "edge_pair_keys_json",
        "pair_matrix_json",
        "reasons_json",
    ):
        if isinstance(item.get(column), str):
            item[column] = json.loads(item[column])
    item["case_id"] = f"group:{item['group_key']}"
    return item


def _person_name_from_evidence(evidence: dict[str, Any], side: str) -> str:
    names = ((evidence.get("names") or {}).get(side) or {})
    return person_display_name(
        names.get("first_name"),
        names.get("last_name"),
        names.get("nickname"),
        names.get("father_mother"),
        names.get("grandfather"),
    )


def _person_pair_lane(row: dict[str, Any]) -> str:
    tier = row.get("deterministic_tier")
    if tier in _PERSON_CLEAR_TIERS:
        return "likely_duplicates"
    if tier in _PERSON_CAUTION_TIERS:
        return "read_source"
    if tier == "distinct_strong":
        return "rule_exclusions"
    if row.get("high_concordance"):
        return "high_concordance"
    return "other_matches"


def _person_pair_preview(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence_json") or {}
    career = row.get("career_span_json") or {}
    return {
        "case_id": row["case_id"],
        "kind": "pair",
        "person_ids": [int(row["person_id_l"]), int(row["person_id_r"])],
        "names": [
            _person_name_from_evidence(evidence, "left"),
            _person_name_from_evidence(evidence, "right"),
        ],
        "lane": _person_pair_lane(row),
        "tier": row.get("deterministic_tier"),
        "source": row.get("source"),
        "score_band": row.get("score_band"),
        "priority_band": row.get("priority_band"),
        "review_rank": row.get("review_rank"),
        "review_percentile": row.get("review_percentile"),
        "review_score": row.get("review_score"),
        "match_probability": row.get("match_probability"),
        "career": career,
        "reasons": row.get("concordance_reasons_json") or row.get("reasons_json") or [],
    }


def _person_group_preview(
    group: dict[str, Any], pair_by_key: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    edges = [
        pair_by_key[key]
        for key in group.get("edge_pair_keys_json") or []
        if key in pair_by_key
    ]
    names_by_id: dict[int, str] = {}
    probabilities: list[float] = []
    review_scores: list[float] = []
    for edge in edges:
        evidence = edge.get("evidence_json") or {}
        names_by_id[int(edge["person_id_l"])] = _person_name_from_evidence(evidence, "left")
        names_by_id[int(edge["person_id_r"])] = _person_name_from_evidence(evidence, "right")
        if edge.get("match_probability") is not None:
            probabilities.append(float(edge["match_probability"]))
        if edge.get("review_score") is not None:
            review_scores.append(float(edge["review_score"]))
    ids = [int(value) for value in group.get("person_ids_json") or []]
    all_rule_clear = bool(edges) and all(
        edge.get("deterministic_tier") in _PERSON_CLEAR_TIERS for edge in edges
    )
    guarded = group.get("guard_status") not in (None, "", "clear")
    probability = min(probabilities) if probabilities else None
    review_score = min(review_scores) if review_scores else None
    return {
        "case_id": group["case_id"],
        "kind": "group",
        "person_ids": ids,
        "names": [names_by_id.get(value, f"Entry #{value}") for value in ids],
        "lane": (
            "possible_splits"
            if guarded
            else "likely_duplicates"
            if all_rule_clear
            else "other_matches"
        ),
        "tier": "group",
        "source": "rule" if all_rule_clear else "splink",
        "score_band": (
            "very_high"
            if probability is not None and probability >= 0.90
            else "high"
            if probability is not None and probability >= 0.70
            else "possible"
            if probability is not None and probability >= 0.50
            else None
        ),
        "match_probability": probability,
        "review_score": review_score,
        "review_rank": min(
            (int(edge["review_rank"]) for edge in edges if edge.get("review_rank") is not None),
            default=None,
        ),
        "review_percentile": min(
            (
                float(edge["review_percentile"])
                for edge in edges
                if edge.get("review_percentile") is not None
            ),
            default=None,
        ),
        "priority_band": next(
            (edge.get("priority_band") for edge in edges if edge.get("priority_band")),
            None,
        ),
        "career": {
            "first_year": group.get("first_year"),
            "last_year": group.get("last_year"),
            "combined_span_years": group.get("implied_career_years"),
        },
        "reasons": group.get("reasons_json") or [],
        "guard_status": group.get("guard_status"),
    }


def _active_person_link_state(
    clog: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[int, int], set[frozenset[int]]]:
    links = corrections_db.active_reference_links(clog, "person")
    parent: dict[int, int] = {}

    def find(value: int) -> int:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    for link in links:
        if link["rel"] == "same_as":
            left, right = find(int(link["from_id"])), find(int(link["to_id"]))
            if left != right:
                low, high = sorted((left, right))
                parent[high] = low
    for value in list(parent):
        parent[value] = find(value)
    distinct = {
        frozenset(
            (
                parent.get(int(link["from_id"]), int(link["from_id"])),
                parent.get(int(link["to_id"]), int(link["to_id"])),
            )
        )
        for link in links
        if link["rel"] == "distinct"
    }
    return links, parent, distinct


def _latest_person_events(clog: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in corrections_db.person_review_events(clog):
        case_id = event.get("case_id")
        if case_id and case_id not in latest:
            latest[str(case_id)] = event
    return latest


# A decided case that the open round still holds (its pair, or a pair of its
# group, waits under Judge blind) is listed with names and numbers only, like
# the round's own rows: no score, rank or routing reaches the list.
_ROUND_SAFE_PREVIEW_KEYS = ("case_id", "kind", "lane", "person_ids", "names", "career", "status")


def _round_safe_preview(preview: dict[str, Any]) -> dict[str, Any]:
    return {key: preview[key] for key in _ROUND_SAFE_PREVIEW_KEYS if key in preview}


def _person_case_status(
    case: dict[str, Any],
    parent: dict[int, int],
    distinct: set[frozenset[int]],
    latest_events: dict[str, dict[str, Any]],
) -> str:
    event = latest_events.get(case["case_id"])
    if event and event["action"] in {"defer", "flag_split"}:
        return str(event["action"])
    # An undone decision ("revoke") is not a status of its own: the revoke
    # cleared the links, so the fall-through below reads the case as open
    # again and it returns to its lane (2026-10-26; before, it parked under
    # Decided as "revoked" until someone pressed reopen).
    ids = [int(value) for value in case.get("person_ids") or []]
    if len(ids) >= 2:
        roots = {parent.get(value, value) for value in ids}
        if len(roots) == 1:
            return "same_as"
        pairs = [
            frozenset(
                (
                    parent.get(left, left),
                    parent.get(right, right),
                )
            )
            for left, right in itertools.combinations(ids, 2)
        ]
        if pairs and all(pair in distinct for pair in pairs):
            return "distinct"
    return "open"


def _person_identity_case_is_open(
    case_id: str,
    person_ids: list[int],
    parent: dict[int, int],
    distinct: set[frozenset[int]],
    latest: dict[str, dict[str, Any]],
) -> bool:
    """Whether Database should treat this case as still waiting.

    Same rule as the People lanes: a reopen returns the case to open. Presence
    in ``latest`` is not a close — defer/flag_split/links are.
    """
    return (
        _person_case_status(
            {"case_id": case_id, "person_ids": person_ids},
            parent,
            distinct,
            latest,
        )
        == "open"
    )


def _person_cache_groups(
    cache: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load the small group projection plus only the pair edges it references."""
    groups = [
        _decode_person_group(row)
        for row in cache.execute("SELECT * FROM person_group_projection")
    ]
    edge_keys = sorted(
        {
            key
            for group in groups
            for key in group.get("edge_pair_keys_json") or []
        }
    )
    if not edge_keys:
        return groups, {}
    marks = ",".join("?" for _ in edge_keys)
    pairs = [
        _decode_person_pair(row)
        for row in cache.execute(
            f"SELECT * FROM person_pair_suggestion WHERE pair_key IN ({marks})",
            edge_keys,
        )
    ]
    return groups, {str(row["pair_key"]): row for row in pairs}


def _person_cache_stale(run: dict[str, Any]) -> dict[str, Any]:
    current_hash = (
        person_cache.sha256_path(PERSON_MODEL_PATH) if PERSON_MODEL_PATH.exists() else None
    )
    model_changed = current_hash != run.get("model_sha256")
    comparison_changed = (
        run.get("comparison_rule_version") != person_cache.COMPARISON_RULE_VERSION
    )
    deterministic_changed = (
        run.get("deterministic_rule_version")
        != person_cache.DETERMINISTIC_RULE_VERSION
    )
    diagnostics_changed = (
        run.get("diagnostic_rule_version")
        != person_cache.DIAGNOSTIC_RULE_VERSION
    )
    source_path = (
        DERIVED_ROOT
        / "05_db_candidate_matches/source_entry_db_link_candidates.jsonl"
    )
    current_source_hash = (
        f"sha256:{person_cache.sha256_path(source_path)}" if source_path.exists() else ""
    )
    source_pointers_changed = (
        str(run.get("source_pointer_fingerprint") or "") != current_source_hash
    )
    try:
        db_newer = db_path().stat().st_mtime > datetime.fromisoformat(
            str(run["run_timestamp"]).replace("Z", "+00:00")
        ).timestamp()
    except (OSError, ValueError, KeyError):
        db_newer = True
    return {
        "stale": bool(
            model_changed
            or comparison_changed
            or deterministic_changed
            or diagnostics_changed
            or source_pointers_changed
            or db_newer
        ),
        "model_changed": model_changed,
        "comparison_rules_changed": comparison_changed,
        "deterministic_rules_changed": deterministic_changed,
        "diagnostic_rules_changed": diagnostics_changed,
        "source_pointers_changed": source_pointers_changed,
        "database_newer_than_cache": db_newer,
    }


def _person_split_flag(
    latest: dict[str, dict[str, Any]], person_id: int
) -> dict[str, Any] | None:
    """Active flag_split for one entered row, regardless of which case stored it.

    The People UI remaps a one-person flag to ``split:<id>``, but the API
    records under whatever case_id was posted. Summary already counts any
    latest ``flag_split``; Database and the picker must use the same scan.
    A reopen (or any later non-flag action) on that case clears the flag.
    """
    wanted = int(person_id)
    matches = [
        event
        for event in latest.values()
        if event.get("action") == "flag_split"
        and wanted in {int(value) for value in (event.get("person_ids") or [])}
    ]
    if not matches:
        return None
    event = max(
        matches,
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("event_id") or "")),
    )
    # A change of marks is a later flag_split event carrying the first
    # flag's authorship as `seam.root`; the flag keeps the original flagger
    # and note, and says who changed the marks last. `event_id` stays the
    # latest event: it is what a lift (reopen) must name.
    seam = _event_seam(event)
    root = (seam or {}).get("root") or None
    return {
        "person_id": wanted,
        "case_id": str(event.get("case_id") or f"split:{wanted}"),
        "event_id": str(event.get("event_id") or ""),
        "created_by": (root or event).get("created_by") or "",
        "created_at": (root or event).get("created_at") or "",
        "rationale": (root or event).get("rationale") or "",
        "root_event_id": str((root or event).get("event_id") or ""),
        "changed_by": (event.get("created_by") or "") if root else None,
        "changed_at": (event.get("created_at") or "") if root else None,
        "change_note": (event.get("rationale") or "") if root else None,
        "marks": _seam_counts(seam) if seam else None,
    }


def _event_seam(event: dict[str, Any] | None) -> dict[str, Any] | None:
    """The marks stored with a flag_split event, if any."""
    if not event:
        return None
    seam = (event.get("evidence_snapshot") or {}).get("seam")
    return seam if isinstance(seam, dict) else None


def _seam_counts(seam: dict[str, Any]) -> dict[str, Any]:
    """Side counts from the stored marks alone; no database needed."""
    counts: dict[str, Any] = {side: 0 for side in SEAM_SIDES}
    for mark in seam.get("assignments") or []:
        side = str(mark.get("side") or "")
        if side in counts:
            counts[side] += 1
    counts["total"] = sum(counts[side] for side in SEAM_SIDES)
    counts["other_entry_id"] = seam.get("other_entry_id")
    return counts


def _appearance_year(appearance: dict[str, Any]) -> int | None:
    date = str(appearance.get("registration_date") or "")
    if appearance.get("heirs_of") or not date or date == PLACEHOLDER_DATE:
        return None
    try:
        return int(date[:4])
    except ValueError:
        return None


def _person_seam_view(
    person: dict[str, Any], event: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The standing marks of one entry, read against its live appearances.

    Every live appearance is listed with its side; counts, the band between
    the two men (when their dated living acts separate in time) and drift —
    appearances added since the marking (`unmarked`) or hidden since
    (`hidden`) — are derived here, never stored.
    """
    if not event or event.get("action") != "flag_split":
        return None
    seam = _event_seam(event)
    if not seam:
        return None
    marks = {
        int(mark["investor_id"]): str(mark.get("side") or "unsure")
        for mark in seam.get("assignments") or []
        if mark.get("investor_id") is not None
    }
    live = {
        int(appearance["investor_id"]): appearance
        for appearance in person.get("appearances") or []
    }
    assignments: list[dict[str, Any]] = []
    years: dict[str, list[int]] = {"this": [], "other": []}
    counts: dict[str, int] = {side: 0 for side in SEAM_SIDES}
    for investor_id, appearance in live.items():
        side = marks.get(investor_id, "unmarked")
        if side in counts:
            counts[side] += 1
        year = _appearance_year(appearance)
        if side in years and year is not None:
            years[side].append(year)
        assignments.append(
            {
                "investor_id": investor_id,
                "contract_id": appearance.get("contract_id"),
                "registration_date": appearance.get("registration_date"),
                "side": side,
            }
        )
    assignments.sort(
        key=lambda item: (
            str(item["registration_date"] or "9999"),
            int(item["contract_id"] or 0),
            item["investor_id"],
        )
    )
    hidden = sorted(set(marks) - set(live))
    unmarked = sorted(set(live) - set(marks))
    band: list[int] | None = None
    if years["this"] and years["other"]:
        if max(years["this"]) < min(years["other"]):
            band = [max(years["this"]), min(years["other"])]
        elif max(years["other"]) < min(years["this"]):
            band = [max(years["other"]), min(years["this"])]
    root = seam.get("root") or {}
    return {
        "version": seam.get("version", 1),
        "event_id": str(event.get("event_id") or ""),
        "root_event_id": str(root.get("event_id") or event.get("event_id") or ""),
        "assignments": assignments,
        "counts": {**counts, "unmarked": len(unmarked), "hidden": len(hidden)},
        "unmarked": unmarked,
        "hidden": hidden,
        "band": band,
        "other_entry_id": seam.get("other_entry_id"),
    }


def _person_card_marks(person_id: int) -> dict[int, str] | None:
    """The standing marks of one entry by appearance, for its Database card.

    Read from the flag event's snapshot alone (no cache, no model): the card
    shows a reader's marking as a note, never as a change to the record.
    Shown on WH's interim decision of 2026-10-27 (register §5 (e)).
    """
    if not person_linkage_enabled():
        return None
    clog = open_corrections()
    try:
        latest = _latest_person_events(clog)
    finally:
        clog.close()
    flag = _person_split_flag(latest, person_id)
    if not flag or not flag.get("marks"):
        return None
    seam = _event_seam(latest.get(flag["case_id"])) or {}
    return {
        int(mark["investor_id"]): str(mark.get("side") or "unsure")
        for mark in seam.get("assignments") or []
        if mark.get("investor_id") is not None
    }


def _person_ids_in_open_round(
    run: dict[str, Any], latest: dict[str, dict[str, Any]]
) -> set[int]:
    """Entries that an open blind-round case still holds."""
    ids: set[int] = set()
    for case_id in _person_labeling_open_case_ids(run, latest):
        for part in case_id.removeprefix("pair:").split(":"):
            if part.isdigit():
                ids.add(int(part))
    return ids


def _validate_seam(seam: PersonSeam, person_id: int) -> None:
    """Marks must cover exactly the entry's live appearances, once each."""
    for mark in seam.assignments:
        if mark.side not in SEAM_SIDES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown mark '{mark.side}' on appearance #{mark.investor_id}.",
            )
    ids = [int(mark.investor_id) for mark in seam.assignments]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=400, detail="An appearance is marked twice.")
    connection = open_db()
    try:
        live = {
            int(row["investor_id"])
            for row in connection.execute(
                "SELECT investor_id FROM investor WHERE person_id=? AND is_deleted=0",
                (person_id,),
            )
        }
        unknown = sorted(set(ids) - live)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Appearance #{unknown[0]} is not a live appearance of entry "
                    f"#{person_id}."
                ),
            )
        missing = sorted(live - set(ids))
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Every appearance needs a mark; missing: "
                    + ", ".join(f"#{value}" for value in missing)
                    + "."
                ),
            )
        if seam.other_entry_id is not None:
            if int(seam.other_entry_id) == int(person_id):
                raise HTTPException(
                    status_code=400, detail="The other entry cannot be the entry itself."
                )
            row = connection.execute(
                "SELECT is_deleted FROM person WHERE person_id=?", (seam.other_entry_id,)
            ).fetchone()
            if not row or row["is_deleted"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Entry #{seam.other_entry_id} is not in the database.",
                )
    finally:
        connection.close()


def _person_standing_split_flags(latest: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every entered row with a standing flag, newest flag first.

    The same scan `_person_split_flag` makes for one row, run once over the
    latest events for all of them. A flag stands while its event is still the
    latest on its case; a reopen (lifting the flag) clears it. This is what
    the Flagged facet, the Decided lane and the Database worklist list — the
    review of 2026-10-25 found a flag disappeared from every list the moment
    it was written.
    """
    flags: dict[int, dict[str, Any]] = {}
    for event in latest.values():
        if event.get("action") != "flag_split":
            continue
        for value in event.get("person_ids") or []:
            flag = _person_split_flag(latest, int(value))
            if flag:
                flags[int(value)] = flag
    return sorted(
        flags.values(),
        key=lambda item: (str(item["created_at"]), str(item["event_id"])),
        reverse=True,
    )


def _plain_date(iso: str) -> str:
    """'2026-09-03T16:05:39+00:00' → '3 Sep 2026', for a sentence a reviewer reads."""
    try:
        parsed = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)[:10]
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


def _split_case_ids(latest: dict[str, dict[str, Any]]) -> list[int]:
    """Entered rows that have a split case of their own in the event history."""
    ids: list[int] = []
    for case_id in latest:
        if case_id.startswith("split:") and case_id.removeprefix("split:").isdigit():
            ids.append(int(case_id.removeprefix("split:")))
    return ids


def _with_decision_meta(
    preview: dict[str, Any], event: dict[str, Any] | None
) -> dict[str, Any]:
    """Who decided, when, and the note — on Decided rows and flagged entries.

    A reopen or an undo is not a decision: a case whose latest event is one
    of those is open again and shows no decision line.
    """
    if event and event.get("action") not in {"reopen", "revoke"}:
        preview["decided_by"] = event.get("created_by") or ""
        preview["decided_at"] = event.get("created_at") or ""
        preview["note"] = event.get("rationale") or ""
    return preview


def _person_identity_suppressed_case_ids(
    run: dict[str, Any],
    latest: dict[str, dict[str, Any]],
    groups: list[tuple[str, list[int], list[str]]],
) -> set[str]:
    """Labeling-round cases must not appear as Database identity hints.

    One membership test already gates blind detail, the standard-decision 409,
    lane counts and summary. Identity open_cases uses the same set, plus any
    group whose edges include an open packet pair — opening that group from
    Database would unblind the packet edge. Unrelated groups on the same
    person stay visible: that is other work, not the round.
    """
    frozen_pairs = _person_labeling_frozen_case_ids(run, latest)
    suppressed = set(frozen_pairs)
    packet_keys = {case_id.removeprefix("pair:") for case_id in frozen_pairs}
    if not packet_keys:
        return suppressed
    for case_id, _members, edge_keys in groups:
        if packet_keys.intersection(edge_keys):
            suppressed.add(case_id)
    return suppressed


def _person_identity_hint(status: dict[str, Any]) -> dict[str, Any]:
    """Browse/picker summary of identity_status — no scores, no tiers."""
    flags = status.get("split_flags") or []
    return {
        "open_count": status["open_count"],
        "linked_count": len(status["person_ids"]),
        "case_id": (
            status["open_cases"][0]
            if status["open_cases"]
            else (flags[0]["case_id"] if flags else None)
        ),
        "split_flagged": bool(flags),
    }


def _person_hits_with_identity(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach identity_hint to person-search / same-surname hits."""
    if not hits:
        return hits
    identity = person_identity_statuses([int(hit["person_id"]) for hit in hits])
    for hit in hits:
        hit["identity_hint"] = _person_identity_hint(identity[int(hit["person_id"])])
    return hits


def _person_identity_payload(
    *,
    available: bool,
    person_id: int,
    family: list[int],
    links: list[dict[str, Any]],
    latest: dict[str, dict[str, Any]],
    open_cases: list[str],
) -> dict[str, Any]:
    flag = _person_split_flag(latest, person_id)
    return {
        "available": available,
        "person_ids": family,
        "active_links": [
            link
            for link in links
            if int(link["from_id"]) in family or int(link["to_id"]) in family
        ],
        "open_cases": open_cases[:12],
        "open_count": len(open_cases),
        "split_flags": [flag] if flag else [],
    }


def _person_stranded_exact_hint(row: sqlite3.Row | dict[str, Any]) -> bool:
    """Review-tier stranded pair whose given name, father, and surname all match.

    The model scores these pairs low (a zero-appearance side has no business
    context to agree on), so the p>=0.50 arm never surfaces them — yet a full
    exact name chain against a record that never reaches a contract is exactly
    the superseded-entry pattern reviewers want flagged on the person record.
    Exact flags carry person_linkage's both-recorded semantics: a missing name
    part is never "exact", so sludge pairs (first+father only) stay out.
    """
    if row["deterministic_tier"] != "review":
        return False
    left, right = row["appearances_l"], row["appearances_r"]
    # Both counts must be recorded, and exactly one of them zero (XOR): a
    # both-zero pair strands two entries and belongs to bulk review, not hints.
    if left is None or right is None or ((left == 0) == (right == 0)):
        return False
    return bool(row["given_exact"]) and bool(row["father_exact"]) and bool(row["surname_exact"])


def person_identity_statuses(person_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Reviewed identity families, split flags, and open cases for entered rows."""
    wanted = sorted({int(value) for value in person_ids})
    if not wanted:
        return {}
    if not person_linkage_enabled():
        return {
            person_id: _person_identity_payload(
                available=False,
                person_id=person_id,
                family=[person_id],
                links=[],
                latest={},
                open_cases=[],
            )
            for person_id in wanted
        }
    clog = open_corrections()
    try:
        links, parent, distinct = _active_person_link_state(clog)
        latest = _latest_person_events(clog)
    finally:
        clog.close()
    families: dict[int, list[int]] = {}
    for person_id in wanted:
        root = parent.get(person_id, person_id)
        families[person_id] = sorted(
            value for value in set(parent) | {person_id} if parent.get(value, value) == root
        )
    if not PERSON_CACHE_PATH.exists():
        return {
            person_id: _person_identity_payload(
                available=False,
                person_id=person_id,
                family=families[person_id],
                links=links,
                latest=latest,
                open_cases=[],
            )
            for person_id in wanted
        }
    cache = open_person_cache()
    try:
        run_row = cache.execute("SELECT * FROM cache_run LIMIT 1").fetchone()
        run = dict(run_row) if run_row else {}
        marks = ",".join("?" for _ in wanted)
        pair_rows = cache.execute(
            # json_extract in the SELECT (rather than decoding whole rows in
            # Python) keeps this hot path — it runs per browse page — reading
            # five scalars instead of parsing every evidence blob.
            f"""
            SELECT pair_key,person_id_l,person_id_r,deterministic_tier,match_probability,
              json_extract(evidence_json,'$.appearance_counts.left') AS appearances_l,
              json_extract(evidence_json,'$.appearance_counts.right') AS appearances_r,
              json_extract(evidence_json,'$.name_review.given_name.exact') AS given_exact,
              json_extract(evidence_json,'$.name_review.father.exact') AS father_exact,
              json_extract(evidence_json,'$.name_review.surname.exact') AS surname_exact
            FROM person_pair_suggestion
            WHERE person_id_l IN ({marks}) OR person_id_r IN ({marks})
            ORDER BY match_probability DESC
            """,
            (*wanted, *wanted),
        ).fetchall()
        group_rows = cache.execute(
            "SELECT group_key, person_ids_json, edge_pair_keys_json FROM person_group_projection"
        ).fetchall()
    finally:
        cache.close()
    groups = [
        (
            f"group:{row['group_key']}",
            [int(value) for value in json.loads(row["person_ids_json"])],
            [str(value) for value in json.loads(row["edge_pair_keys_json"])],
        )
        for row in group_rows
    ]
    projected_pair_keys = {key for _, _, keys in groups for key in keys}
    suppressed = _person_identity_suppressed_case_ids(run, latest, groups)
    result: dict[int, dict[str, Any]] = {}
    for person_id in wanted:
        open_cases = [
            case_id
            for case_id, members, _ in groups
            if person_id in members
            and _person_identity_case_is_open(case_id, members, parent, distinct, latest)
            and case_id not in suppressed
        ]
        open_cases.extend(
            f"pair:{row['pair_key']}"
            for row in pair_rows
            if person_id in (int(row["person_id_l"]), int(row["person_id_r"]))
            and _person_identity_case_is_open(
                f"pair:{row['pair_key']}",
                [int(row["person_id_l"]), int(row["person_id_r"])],
                parent,
                distinct,
                latest,
            )
            and f"pair:{row['pair_key']}" not in suppressed
            and str(row["pair_key"]) not in projected_pair_keys
            and row["deterministic_tier"] not in _PERSON_CLEAR_TIERS
            and (
                row["deterministic_tier"] in _PERSON_CAUTION_TIERS
                or (
                    row["deterministic_tier"] != "distinct_strong"
                    and row["match_probability"] is not None
                    and float(row["match_probability"]) >= 0.50
                )
                or _person_stranded_exact_hint(row)
            )
        )
        open_cases = list(dict.fromkeys(open_cases))
        result[person_id] = _person_identity_payload(
            available=True,
            person_id=person_id,
            family=families[person_id],
            links=links,
            latest=latest,
            open_cases=open_cases,
        )
    return result


def person_identity_status(person_id: int) -> dict[str, Any]:
    """Reviewed identity family, split flag, and open cases for one entered row."""
    return person_identity_statuses([person_id])[person_id]


@app.get("/api/person-linkage/summary")
def person_linkage_summary() -> dict[str, Any]:
    if not person_linkage_enabled():
        return {
            "available": False,
            "disabled": True,
            "lanes": {},
            "decisions": {},
            "stale": {"stale": False},
        }
    if not PERSON_CACHE_PATH.exists():
        return {"available": False, "lanes": {}, "decisions": {}, "stale": {"stale": True}}
    cache = open_person_cache()
    try:
        run = dict(cache.execute("SELECT * FROM cache_run LIMIT 1").fetchone())
        groups, pair_by_key = _person_cache_groups(cache)
        tier_counts = {
            row["tier"]: int(row["n"])
            for row in cache.execute(
                """
                SELECT COALESCE(deterministic_tier,'model_only') AS tier, COUNT(*) AS n
                FROM person_pair_suggestion GROUP BY deterministic_tier
                """
            )
        }
    finally:
        cache.close()
    clear_groups = [
        group
        for group in groups
        if group.get("guard_status") == "clear"
        and group.get("edge_pair_keys_json")
        and all(
            pair_by_key.get(key, {}).get("deterministic_tier") in _PERSON_CLEAR_TIERS
            for key in group["edge_pair_keys_json"]
        )
    ]
    projected_edges = {
        key for group in groups for key in group.get("edge_pair_keys_json") or []
    }
    projected_clear_edges = sum(
        pair_by_key.get(key, {}).get("deterministic_tier") in _PERSON_CLEAR_TIERS
        for key in projected_edges
    )
    projected_other_edges = len(projected_edges) - projected_clear_edges
    cache = open_person_cache()
    try:
        all_high_concordance = int(
            cache.execute(
                "SELECT COUNT(*) FROM person_pair_suggestion WHERE high_concordance=1"
            ).fetchone()[0]
        )
    finally:
        cache.close()
    high_concordance_pairs = all_high_concordance - sum(
        bool(pair_by_key.get(key, {}).get("high_concordance"))
        for key in projected_edges
    )
    raw_other = tier_counts.get("review", 0) + tier_counts.get("model_only", 0)
    lane_counts: Counter[str] = Counter(
        {
            # Recounted below once decision events are known: only undecided
            # (or reopened) packet cases from the current run stay in the round.
            "labeling_round": 0,
            "likely_duplicates": len(clear_groups),
            "read_source": tier_counts.get("caution_coappearance", 0)
            + tier_counts.get("caution_posthumous_conflict", 0)
            + tier_counts.get("caution_gf_conflict", 0),
            "high_concordance": high_concordance_pairs,
            "rule_exclusions": tier_counts.get("distinct_strong", 0),
            "other_matches": raw_other - projected_other_edges
            - high_concordance_pairs + (len(groups) - len(clear_groups)),
            "possible_splits": len(_person_split_previews()),
        }
    )
    clog = open_corrections()
    try:
        links, parent, distinct = _active_person_link_state(clog)
        events = corrections_db.person_review_events(clog)
        latest = _latest_person_events(clog)
    finally:
        clog.close()
    packet_rows = _person_labeling_packet_rows()
    lane_counts["labeling_round"] = len(_person_labeling_open_case_ids(run, latest))
    # Human decisions are few and carry their case id; subtract them from the
    # corresponding source lane without materialising all 15,637 suggestions.
    decided = 0
    group_map = {group["case_id"]: group for group in groups}
    split_map = {case["case_id"]: case for case in _person_split_previews()}
    cache = open_person_cache()
    try:
        for case_id, event in latest.items():
            if event["action"] in {"reopen", "revoke", "bulk_rule_ack"}:
                continue
            lane = None
            if case_id in group_map:
                lane = _person_group_preview(group_map[case_id], pair_by_key)["lane"]
            elif case_id in split_map:
                lane = "possible_splits"
            elif case_id.startswith("split:"):
                # A flagged entry outside the rule's candidates: decided, but
                # never part of the To-check count, so nothing to subtract.
                decided += 1
                continue
            elif case_id.startswith("pair:"):
                row = cache.execute(
                    "SELECT * FROM person_pair_suggestion WHERE pair_key=?",
                    (case_id.removeprefix("pair:"),),
                ).fetchone()
                if row:
                    lane = _person_pair_lane(_decode_person_pair(row))
            if lane:
                lane_counts[lane] = max(0, lane_counts[lane] - 1)
                decided += 1
    finally:
        cache.close()
    lane_counts["decided"] = decided
    return {
        "available": True,
        "run": run,
        "stale": _person_cache_stale(run),
        "labeling_packet_available": PERSON_LABELING_PACKET_PATH.is_file(),
        "labeling_packet_stale": bool(packet_rows)
        and not _person_labeling_packet_matches_run(packet_rows, run),
        "lanes": dict(lane_counts),
        # Standing flags of any span: the count behind the Flagged chip.
        "flagged_entries": len(_person_standing_split_flags(latest)),
        "decisions": {
            "active_links": len(links),
            "same_as": sum(link["rel"] == "same_as" for link in links),
            "distinct": sum(link["rel"] == "distinct" for link in links),
            "events": len(events),
            "deferred": sum(event["action"] == "defer" for event in latest.values()),
            "split_flags": sum(event["action"] == "flag_split" for event in latest.values()),
        },
    }


def _person_split_previews() -> list[dict[str, Any]]:
    from workflows.person_features import classify_entity_kind

    connection = open_db()
    try:
        rows = connection.execute(
            """
            SELECT p.person_id, p.first_name, p.father_mother, p.grandfather,
                   p.last_name, p.nickname,
                   MIN(CAST(substr(c.registration_date,1,4) AS INTEGER)) AS first_year,
                   MAX(CAST(substr(c.registration_date,1,4) AS INTEGER)) AS last_year,
                   COUNT(*) AS appearances
            FROM person p
            JOIN investor i ON i.person_id=p.person_id AND i.is_deleted=0
            JOIN contract c ON c.contract_id=i.contract_id AND c.is_deleted=0
            WHERE p.is_deleted=0 AND COALESCE(i.heirs_of,0)=0
              AND c.registration_date NOT IN ('', '0000-00-00')
            GROUP BY p.person_id
            HAVING last_year-first_year > 60
            ORDER BY last_year-first_year DESC
            """
        ).fetchall()
        return [
            {
                "case_id": f"split:{row['person_id']}",
                "kind": "split",
                "person_ids": [int(row["person_id"])],
                "names": [
                    person_display_name(
                        row["first_name"],
                        row["last_name"],
                        row["nickname"],
                        row["father_mother"],
                        row["grandfather"],
                    )
                ],
                "lane": "possible_splits",
                "tier": "career_span",
                "source": "rule",
                "score_band": None,
                "match_probability": None,
                "career": {
                    "first_year": row["first_year"],
                    "last_year": row["last_year"],
                    "combined_span_years": row["last_year"] - row["first_year"],
                },
                "reasons": [
                    f"the entry spans {row['last_year']-row['first_year']} years"
                ],
            }
            for row in rows
            if classify_entity_kind(
                row["first_name"],
                row["father_mother"],
                row["grandfather"],
                row["last_name"],
            )
            == "person"
        ]
    finally:
        connection.close()


def _person_split_previews_for(person_ids: list[int]) -> dict[str, dict[str, Any]]:
    """Split-case previews for given entered rows, whatever their span.

    `_person_split_previews` lists the rule's candidates (a living span over
    sixty years). A flagged entry can be any row, including one that appears
    in no contract, so this builder takes ids and LEFT JOINs the appearances.
    """
    wanted = sorted({int(value) for value in person_ids})
    if not wanted:
        return {}
    connection = open_db()
    try:
        marks = ",".join("?" for _ in wanted)
        rows = connection.execute(
            f"""
            SELECT p.person_id, p.first_name, p.father_mother, p.grandfather,
                   p.last_name, p.nickname,
                   MIN(CASE WHEN COALESCE(i.heirs_of,0)=0
                             AND c.registration_date NOT IN ('', '0000-00-00')
                            THEN CAST(substr(c.registration_date,1,4) AS INTEGER) END) AS first_year,
                   MAX(CASE WHEN COALESCE(i.heirs_of,0)=0
                             AND c.registration_date NOT IN ('', '0000-00-00')
                            THEN CAST(substr(c.registration_date,1,4) AS INTEGER) END) AS last_year,
                   COUNT(c.contract_id) AS appearances
            FROM person p
            LEFT JOIN investor i ON i.person_id=p.person_id AND i.is_deleted=0
            LEFT JOIN contract c ON c.contract_id=i.contract_id AND c.is_deleted=0
            WHERE p.person_id IN ({marks}) AND p.is_deleted=0
            GROUP BY p.person_id
            """,
            wanted,
        ).fetchall()
    finally:
        connection.close()
    previews: dict[str, dict[str, Any]] = {}
    for row in rows:
        first_year, last_year = row["first_year"], row["last_year"]
        span = (
            int(last_year) - int(first_year)
            if first_year is not None and last_year is not None
            else None
        )
        previews[f"split:{row['person_id']}"] = {
            "case_id": f"split:{row['person_id']}",
            "kind": "split",
            "person_ids": [int(row["person_id"])],
            "names": [
                person_display_name(
                    row["first_name"],
                    row["last_name"],
                    row["nickname"],
                    row["father_mother"],
                    row["grandfather"],
                )
            ],
            "lane": "possible_splits",
            "tier": "career_span",
            "source": "rule",
            "score_band": None,
            "match_probability": None,
            "career": {
                "first_year": first_year,
                "last_year": last_year,
                "combined_span_years": span,
            },
            "reasons": [],
        }
    return previews


@app.get("/api/person-linkage/cases")
def person_linkage_cases(
    lane: str = "likely_duplicates",
    status: str = "open",
    priority_band: str = "All",
    q: str = "",
    stranded: int = 0,
    stranded_scope: str = "",
    facet: str = "",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    if not person_linkage_enabled():
        raise HTTPException(status_code=404, detail="Person identity review is not enabled.")
    # The stranded facet is a pair-population filter, so it only means anything
    # in the one lane whose pairs it partitions; elsewhere it is ignored rather
    # than rejected, so a stale bookmarked URL still renders its lane.
    stranded_active = bool(stranded) and lane == "other_matches"
    cache = open_person_cache()
    run = dict(cache.execute("SELECT * FROM cache_run LIMIT 1").fetchone())
    groups, pair_by_key = _person_cache_groups(cache)
    clear_groups = [
        group
        for group in groups
        if group.get("edge_pair_keys_json")
        and group.get("guard_status") == "clear"
        and all(
            pair_by_key.get(key, {}).get("deterministic_tier") in _PERSON_CLEAR_TIERS
            for key in group["edge_pair_keys_json"]
        )
    ]
    projected_edge_keys = {
        key for group in groups for key in group.get("edge_pair_keys_json") or []
    }
    model_groups = [group for group in groups if group not in clear_groups]
    clog = open_corrections()
    try:
        _, parent, distinct = _active_person_link_state(clog)
        latest = _latest_person_events(clog)
    finally:
        clog.close()
    # Pairs frozen in the open blind round must vanish from their natural
    # lanes: seeing one both blinded and scored would defeat the round. The
    # frozen set is the packet's open cases plus their co-minted sibling
    # pairs (both persons in open packet cases) — the Corradini ghost-to-
    # ghost pairs that would otherwise show the family's score mid-round.
    frozen_pair_keys = {
        case_id.removeprefix("pair:")
        for case_id in _person_labeling_frozen_case_ids(run, latest)
    }

    def group_in_open_round(group: dict[str, Any]) -> bool:
        return any(
            key in frozen_pair_keys
            for key in group.get("edge_pair_keys_json") or []
        )

    def held_by_round(case_id: str, group_map: dict[str, dict[str, Any]]) -> bool:
        """A decided group with a frozen pair, or a decided frozen pair."""
        if case_id in group_map:
            return group_in_open_round(group_map[case_id])
        return case_id.startswith("pair:") and case_id.removeprefix("pair:") in frozen_pair_keys

    def reserved_for_labeling_count() -> int:
        """Open-round cases whose NATURAL lane is the requested one.

        The lane badge counts what the lane holds; the listing hides what the
        blind round has frozen. Without this number the two disagree with no
        explanation — a badge of 15 over "No open cases". It is deliberately
        independent of the status/band/search filters: the promise is "N cases
        of this lane's kind are in the round", not "N would have matched your
        current filters".
        """
        if lane in {"labeling_round", "decided", "all"} or not frozen_pair_keys:
            return 0
        reserved = 0
        if lane == "likely_duplicates":
            return sum(1 for group in clear_groups if group_in_open_round(group))
        if lane in {"possible_splits", "other_matches"}:
            reserved += sum(
                1
                for group in model_groups
                if group_in_open_round(group)
                and _person_group_preview(group, pair_by_key)["lane"] == lane
            )
        if lane == "possible_splits":
            return reserved
        lane_condition = {
            "read_source": (
                "deterministic_tier IN "
                "('caution_coappearance','caution_posthumous_conflict','caution_gf_conflict')"
            ),
            "high_concordance": "high_concordance=1",
            "rule_exclusions": "deterministic_tier='distinct_strong'",
            "other_matches": (
                "(deterministic_tier='review' OR deterministic_tier IS NULL) "
                "AND high_concordance=0"
            ),
        }[lane]
        # A packet pair that is also a projected group edge belongs to its
        # group's lane, so it must not count here as well.
        keys = sorted(frozen_pair_keys - projected_edge_keys)
        if not keys:
            return reserved
        marks = ",".join("?" for _ in keys)
        reserved += int(
            cache.execute(
                f"SELECT COUNT(*) FROM person_pair_suggestion "
                f"WHERE ({lane_condition}) AND pair_key IN ({marks})",
                keys,
            ).fetchone()[0]
        )
        return reserved

    reserved_for_labeling = reserved_for_labeling_count()

    query = q.strip().lower()

    def matches_query(case: dict[str, Any]) -> bool:
        return not query or (
            query in " ".join(case.get("names") or []).lower()
            or query in " ".join(str(value) for value in case.get("person_ids") or [])
        )

    def keep_preview(case: dict[str, Any]) -> bool:
        case["status"] = _person_case_status(case, parent, distinct, latest)
        if lane == "decided":
            if case["status"] == "open":
                return False
        elif status != "All" and case["status"] != status:
            return False
        if priority_band != "All" and case.get("priority_band") != priority_band:
            return False
        return matches_query(case)

    if lane == "possible_splits" and facet == "flagged":
        # The reviewer's own flags, newest first, whatever the entry's span.
        # Status is deliberately not filtered: a flag is the row's reason to
        # be here, and the row shows who wrote it, when, and the note.
        flags = _person_standing_split_flags(latest)
        previews = _person_split_previews_for([int(flag["person_id"]) for flag in flags])
        flagged: list[dict[str, Any]] = []
        for flag in flags:
            preview = previews.get(f"split:{flag['person_id']}")
            if not preview:
                continue
            preview["status"] = _person_case_status(preview, parent, distinct, latest)
            _with_decision_meta(preview, flag)
            preview["flag"] = flag
            if matches_query(preview):
                flagged.append(preview)
        cache.close()
        return {
            "available": True,
            "run": run,
            "stale": _person_cache_stale(run),
            "total": len(flagged),
            "offset": offset,
            "cases": flagged[offset : offset + limit],
            "reserved_for_labeling": 0,
            "facet": "flagged",
        }

    if lane == "all":
        # One search across every lane: the box finds a person or a number
        # wherever its cases sit, and the client groups the results under lane
        # headings. Each result carries the lane it belongs to, so opening it
        # can select that lane. Without a query there is nothing to search: the
        # lanes are the browse view. Band and stranded filters do not apply.
        if not query:
            cache.close()
            return {
                "available": True,
                "run": run,
                "stale": _person_cache_stale(run),
                "total": 0,
                "offset": offset,
                "cases": [],
                "reserved_for_labeling": 0,
            }
        found: list[dict[str, Any]] = []

        def is_open(case: dict[str, Any]) -> bool:
            case["status"] = _person_case_status(case, parent, distinct, latest)
            return case["status"] == "open"

        # 1. Open cases of the blind round, with the blind preview (names and
        #    numbers only), under Judge blind. A packet built for another cache
        #    is skipped here rather than failing the whole search.
        packet_rows = _person_labeling_packet_rows()
        packet_run_ids = {str(row.get("run_id")) for row in packet_rows if row.get("run_id")}
        if not packet_run_ids or packet_run_ids == {str(run["run_id"])}:
            for packet_row in packet_rows:
                pair_key_value = str(packet_row["pair_key"])
                if pair_key_value not in frozen_pair_keys:
                    continue  # a decided packet case surfaces under Decided
                row = cache.execute(
                    "SELECT * FROM person_pair_suggestion WHERE pair_key=?", (pair_key_value,)
                ).fetchone()
                if not row:
                    continue
                pair = _decode_person_pair(row)
                evidence = pair.get("evidence_json") or {}
                preview = {
                    "case_id": pair["case_id"],
                    "lane": "labeling_round",
                    "person_ids": [int(pair["person_id_l"]), int(pair["person_id_r"])],
                    "names": [
                        _person_name_from_evidence(evidence, "left"),
                        _person_name_from_evidence(evidence, "right"),
                    ],
                    "career": pair.get("career_span_json") or {},
                }
                if matches_query(preview) and is_open(preview):
                    preview["is_reviewed"] = False
                    found.append(preview)
        # 2. Open group cases (typing duplicates, split candidates, grouped
        #    other matches), each under the lane its preview names.
        for group in clear_groups + model_groups:
            if group_in_open_round(group):
                continue
            preview = _person_group_preview(group, pair_by_key)
            if group in clear_groups:
                preview["lane"] = "likely_duplicates"
            if matches_query(preview) and is_open(preview):
                found.append(preview)
        rule_splits = _person_split_previews()
        for split_case in rule_splits:
            if matches_query(split_case) and is_open(split_case):
                found.append(split_case)
        # An entry with a split history of its own but no span to list it
        # under the rule (a lifted flag) stays findable, so its note is not
        # lost: it opens under "One entry, two people?" with its history.
        listed_splits = {case["case_id"] for case in rule_splits}
        for case_id, split_case in _person_split_previews_for(_split_case_ids(latest)).items():
            if case_id in listed_splits:
                continue
            if matches_query(split_case) and is_open(split_case):
                found.append(split_case)
        # 3. Open pairs in the pair lanes, one query over the whole table; the
        #    preview already names the pair's natural lane.
        pair_where = [
            "(lower(evidence_json) LIKE ? OR CAST(person_id_l AS TEXT)=? OR CAST(person_id_r AS TEXT)=?)"
        ]
        pair_params: list[Any] = [f"%{query}%", query, query]
        for excluded in (projected_edge_keys, frozen_pair_keys):
            if excluded:
                marks = ",".join("?" for _ in excluded)
                pair_where.append(f"pair_key NOT IN ({marks})")
                pair_params.extend(sorted(excluded))
        for row in cache.execute(
            f"SELECT * FROM person_pair_suggestion WHERE {' AND '.join(pair_where)} "
            "ORDER BY review_score DESC, person_id_l, person_id_r",
            pair_params,
        ).fetchall():
            preview = _person_pair_preview(_decode_person_pair(row))
            if preview["lane"] in _PERSON_LANE_ORDER and matches_query(preview) and is_open(preview):
                found.append(preview)
        # 4. Decided cases of every kind, under Decided, so a search for a
        #    person also shows what has already been settled about them.
        group_map = {group["case_id"]: group for group in groups}
        split_map = _person_split_previews_for(_split_case_ids(latest))
        for case_id in latest:
            if case_id in group_map:
                preview = _person_group_preview(group_map[case_id], pair_by_key)
            elif case_id in split_map:
                preview = split_map[case_id]
            elif case_id.startswith("pair:"):
                row = cache.execute(
                    "SELECT * FROM person_pair_suggestion WHERE pair_key=?",
                    (case_id.removeprefix("pair:"),),
                ).fetchone()
                if not row:
                    continue
                preview = _person_pair_preview(_decode_person_pair(row))
            else:
                continue
            if matches_query(preview) and not is_open(preview):
                preview["lane"] = "decided"
                if held_by_round(case_id, group_map):
                    preview = _round_safe_preview(preview)
                found.append(_with_decision_meta(preview, latest.get(case_id)))
        cache.close()
        rank = {name: index for index, name in enumerate(_PERSON_LANE_ORDER)}
        found.sort(
            key=lambda case: (
                rank.get(case.get("lane"), len(rank)),
                -(float(case.get("review_score") or -1)),
                tuple(case.get("person_ids") or []),
            )
        )
        lane_counts: dict[str, int] = {}
        for case in found:
            lane_counts[case["lane"]] = lane_counts.get(case["lane"], 0) + 1
        return {
            "available": True,
            "run": run,
            "stale": _person_cache_stale(run),
            "total": len(found),
            "offset": offset,
            "cases": found[offset : offset + limit],
            "reserved_for_labeling": 0,
            "lane_counts": lane_counts,
        }

    if lane == "labeling_round":
        cases: list[dict[str, Any]] = []
        packet_rows = _person_labeling_packet_rows()
        packet_run_ids = {
            str(row.get("run_id")) for row in packet_rows if row.get("run_id")
        }
        if packet_run_ids and packet_run_ids != {str(run["run_id"])}:
            cache.close()
            raise HTTPException(
                status_code=409,
                detail=(
                    "Judge blind is paused: its cases were drawn from an earlier set of suggestions, "
                    "and a new set has to be drawn before judging can resume."
                ),
            )
        for packet_row in packet_rows:
            pair_key_value = str(packet_row["pair_key"])
            row = cache.execute(
                "SELECT * FROM person_pair_suggestion WHERE pair_key=?",
                (pair_key_value,),
            ).fetchone()
            if not row:
                continue
            pair = _decode_person_pair(row)
            evidence = pair.get("evidence_json") or {}
            # Built from scratch rather than by stripping the standard preview:
            # a blacklist fails open the moment the preview grows a new scoring
            # field, while a whitelist keeps it out until deliberately admitted.
            preview = {
                "case_id": pair["case_id"],
                "lane": "labeling_round",
                "person_ids": [int(pair["person_id_l"]), int(pair["person_id_r"])],
                "names": [
                    _person_name_from_evidence(evidence, "left"),
                    _person_name_from_evidence(evidence, "right"),
                ],
                "career": pair.get("career_span_json") or {},
            }
            if keep_preview(preview):
                preview["is_reviewed"] = preview["status"] != "open"
                cases.append(preview)
        cache.close()
        return {
            "available": True,
            "run": run,
            "stale": _person_cache_stale(run),
            "total": len(cases),
            "offset": offset,
            "cases": cases[offset : offset + limit],
            "blind_labeling": True,
            "reserved_for_labeling": reserved_for_labeling,
        }

    if lane == "decided":
        group_map = {group["case_id"]: group for group in groups}
        # Every decided split case, flagged entries of any span included —
        # not only the rule's over-sixty-year candidates.
        split_map = _person_split_previews_for(_split_case_ids(latest))
        cases: list[dict[str, Any]] = []
        for case_id in latest:
            if case_id in group_map:
                preview = _person_group_preview(group_map[case_id], pair_by_key)
            elif case_id in split_map:
                preview = split_map[case_id]
            elif case_id.startswith("pair:"):
                row = cache.execute(
                    "SELECT * FROM person_pair_suggestion WHERE pair_key=?",
                    (case_id.removeprefix("pair:"),),
                ).fetchone()
                if not row:
                    continue
                preview = _person_pair_preview(_decode_person_pair(row))
            else:
                continue
            if keep_preview(preview):
                if held_by_round(case_id, group_map):
                    preview = _round_safe_preview(preview)
                cases.append(_with_decision_meta(preview, latest.get(case_id)))
        cache.close()
        # Newest decision first: "what did I just do" is the question here.
        cases.sort(key=lambda case: str(case.get("decided_at") or ""), reverse=True)
        return {
            "available": True,
            "run": run,
            "stale": _person_cache_stale(run),
            "total": len(cases),
            "offset": offset,
            "cases": cases[offset : offset + limit],
            "reserved_for_labeling": reserved_for_labeling,
        }

    group_cases: list[dict[str, Any]] = []
    if lane == "likely_duplicates":
        group_cases = [
            preview
            for preview in (
                _person_group_preview(group, pair_by_key)
                for group in clear_groups
                if not group_in_open_round(group)
            )
            if keep_preview(preview)
        ]
    elif lane == "possible_splits":
        group_cases = [case for case in _person_split_previews() if keep_preview(case)]
        group_cases.extend(
            preview
            for preview in (
                _person_group_preview(group, pair_by_key)
                for group in model_groups
                if not group_in_open_round(group)
            )
            if preview["lane"] == "possible_splits" and keep_preview(preview)
        )
    elif lane == "other_matches" and not stranded_active:
        # While the stranded facet is active the group previews are skipped:
        # measured on the current cache, zero stranded pairs are other_matches
        # group edges (batch_ghost XOR pairs live in Likely duplicates by
        # design), so interleaving groups would only dilute the facet.
        group_cases = [
            preview
            for preview in (
                _person_group_preview(group, pair_by_key)
                for group in model_groups
                if not group_in_open_round(group)
            )
            if preview["lane"] == "other_matches" and keep_preview(preview)
        ]
    group_cases.sort(
        key=lambda case: (
            -(float(case.get("review_score") or -1)),
            tuple(case.get("person_ids") or []),
        )
    )

    if lane in {"likely_duplicates", "possible_splits"}:
        cache.close()
        return {
            "available": True,
            "run": run,
            "stale": _person_cache_stale(run),
            "total": len(group_cases),
            "offset": offset,
            "cases": group_cases[offset : offset + limit],
            "reserved_for_labeling": reserved_for_labeling,
        }

    where: list[str] = []
    params: list[Any] = []
    if lane == "read_source":
        where.append(
            "deterministic_tier IN "
            "('caution_coappearance','caution_posthumous_conflict','caution_gf_conflict')"
        )
    elif lane == "high_concordance":
        where.append("high_concordance=1")
    elif lane == "rule_exclusions":
        where.append("deterministic_tier='distinct_strong'")
    else:
        where.append(
            "(deterministic_tier='review' OR deterministic_tier IS NULL) "
            "AND high_concordance=0"
        )
    if projected_edge_keys:
        marks = ",".join("?" for _ in projected_edge_keys)
        where.append(f"pair_key NOT IN ({marks})")
        params.extend(sorted(projected_edge_keys))
    if frozen_pair_keys:
        marks = ",".join("?" for _ in frozen_pair_keys)
        where.append(f"pair_key NOT IN ({marks})")
        params.extend(sorted(frozen_pair_keys))
    if priority_band != "All":
        where.append("priority_band=?")
        params.append(priority_band)
    if stranded_active:
        zero_l = "json_extract(evidence_json,'$.appearance_counts.left')=0"
        zero_r = "json_extract(evidence_json,'$.appearance_counts.right')=0"
        if stranded_scope == "all":
            # Full stranded population: any zero side (XOR plus both-zero),
            # whatever the tier — the lane condition still applies.
            where.append(f"({zero_l}) OR ({zero_r})")
        else:
            # Default: the review-tier XOR population — exactly one side has
            # never appeared in a contract, and the rules asked for review.
            where.append(f"(({zero_l}) <> ({zero_r})) AND deterministic_tier='review'")
    if query:
        where.append(
            "(lower(evidence_json) LIKE ? OR CAST(person_id_l AS TEXT)=? OR CAST(person_id_r AS TEXT)=?)"
        )
        params.extend([f"%{query}%", query, query])
    # A pair whose latest note is a reopen or an undo is open again and stays
    # in its lane; any other latest note is a decision that moves it out.
    decided_pair_keys = [
        case_id.removeprefix("pair:")
        for case_id, event in latest.items()
        if case_id.startswith("pair:") and event.get("action") not in {"reopen", "revoke"}
    ]
    if status == "open" and decided_pair_keys:
        marks = ",".join("?" for _ in decided_pair_keys)
        where.append(f"pair_key NOT IN ({marks})")
        params.extend(decided_pair_keys)
    where_sql = " AND ".join(f"({fragment})" for fragment in where)
    pair_total = int(
        cache.execute(
            f"SELECT COUNT(*) FROM person_pair_suggestion WHERE {where_sql}", params
        ).fetchone()[0]
    )
    group_offset = min(offset, len(group_cases))
    group_page = group_cases[group_offset : group_offset + limit]
    remaining = limit - len(group_page)
    pair_offset = max(0, offset - len(group_cases))
    pair_cases: list[dict[str, Any]] = []
    if stranded_active:
        # Stranded ordering leads with the evidence class, not the score: the
        # model scores every stranded pair low (a zero-appearance side has no
        # business context), so the score would bury the strongest name
        # evidence. Ranks: 0 = given+father+surname exact, 1 = given+surname
        # exact, 2 = father+grandfather exact (name-form drifted), 3 = rest.
        given = "json_extract(evidence_json,'$.name_review.given_name.exact')=1"
        father = "json_extract(evidence_json,'$.name_review.father.exact')=1"
        surname = "json_extract(evidence_json,'$.name_review.surname.exact')=1"
        grandfather = "json_extract(evidence_json,'$.name_review.grandfather.exact')=1"
        order_sql = (
            f"CASE WHEN {given} AND {father} AND {surname} THEN 0 "
            f"WHEN {given} AND {surname} THEN 1 "
            f"WHEN {father} AND {grandfather} THEN 2 "
            "ELSE 3 END ASC, review_score DESC, person_id_l, person_id_r"
        )
    else:
        order_sql = "review_score DESC, person_id_l, person_id_r"
    if remaining:
        rows = cache.execute(
            f"""
            SELECT * FROM person_pair_suggestion
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            (*params, remaining, pair_offset),
        ).fetchall()
        pair_cases = [_person_pair_preview(_decode_person_pair(row)) for row in rows]
        pair_cases = [case for case in pair_cases if keep_preview(case)]
    cache.close()
    return {
        "available": True,
        "run": run,
        "stale": _person_cache_stale(run),
        "total": len(group_cases) + pair_total,
        "offset": offset,
        "cases": group_page + pair_cases,
        "reserved_for_labeling": reserved_for_labeling,
    }


def _person_card(connection: sqlite3.Connection, person_id: int) -> dict[str, Any]:
    person = connection.execute(
        "SELECT * FROM person WHERE person_id=?", (person_id,)
    ).fetchone()
    if not person:
        return {
            "person_id": person_id,
            "display_name": f"Entry #{person_id} (no longer present)",
            "fields": {
                "first_name": None,
                "father_mother": None,
                "grandfather": None,
                "last_name": None,
                "nickname": None,
                "is_woman": None,
            },
            "first_year": None,
            "last_year": None,
            "n_appearances": 0,
            "appearances": [],
            "role_profile": {"gp": 0, "lp": 0},
            "context_profile": {
                "professions": [],
                "residences": [],
                "origins": [],
                "titles": [],
                "father_mother_titles": [],
                "grandfather_titles": [],
                "husband_titles": [],
                "economic_activities": [],
            },
            "unavailable": True,
        }
    data = dict(person)
    # Two facts the marking screen shows per act, absent from the reduced
    # schemas some tests build: whether the act was made through a proxy, and
    # the firm's name a stake is entered under (Gherardi's bank).
    proxy_column = "i.via_proxy" if "via_proxy" in table_columns(connection, "investor") else "0"
    stake_column = (
        "GROUP_CONCAT(DISTINCT inv.partnership_name)"
        if "partnership_name" in table_columns(connection, "investment")
        else "NULL"
    )
    appearances = connection.execute(
        f"""
        SELECT i.investor_id, i.contract_id, i.profession, i.heirs_of,
               c.registration_date, c.firm_name, c.folder,
               residence.place_name AS residence,
               origin.place_name AS origin,
               t.title_name AS title,
               parent_title.title_name AS father_mother_title,
               grandfather_title.title_name AS grandfather_title,
               husband_title.title_name AS husband_title,
               activity.activity AS economic_activity,
               GROUP_CONCAT(DISTINCT inv.type) AS roles,
               {proxy_column} AS via_proxy,
               {stake_column} AS stake_names
        FROM investor i
        LEFT JOIN contract c ON c.contract_id=i.contract_id AND c.is_deleted=0
        LEFT JOIN place residence ON residence.place_id=i.place_of_residence
        LEFT JOIN place origin ON origin.place_id=i.place_of_origin
        LEFT JOIN title t ON t.title_id=i.title
        LEFT JOIN title parent_title ON parent_title.title_id=i.title_father_mother
        LEFT JOIN title grandfather_title ON grandfather_title.title_id=i.title_grandfather
        LEFT JOIN title husband_title ON husband_title.title_id=i.title_husband
        LEFT JOIN economic_activity activity ON activity.ec_activity_id=c.economic_sector
        LEFT JOIN investor_group ig ON ig.investor_id=i.investor_id AND ig.is_deleted=0
        LEFT JOIN investment inv ON inv.investment_id=ig.investment_id AND inv.is_deleted=0
        WHERE i.person_id=? AND i.is_deleted=0
        GROUP BY i.investor_id
        ORDER BY c.registration_date, i.contract_id
        """,
        (person_id,),
    ).fetchall()
    dated_living = [
        int(str(row["registration_date"])[:4])
        for row in appearances
        if not row["heirs_of"]
        and row["registration_date"] not in (None, "", "0000-00-00")
    ]
    role_profile = {"gp": 0, "lp": 0}
    for row in appearances:
        for role in str(row["roles"] or "").split(","):
            if role.strip().lower() in role_profile:
                role_profile[role.strip().lower()] += 1

    def profile_values(column: str) -> list[str]:
        return sorted(
            {
                str(row[column]).strip()
                for row in appearances
                if row[column] is not None and str(row[column]).strip()
            },
            key=str.casefold,
        )

    return {
        "person_id": person_id,
        "display_name": person_display_name(
            data.get("first_name"),
            data.get("last_name"),
            data.get("nickname"),
            data.get("father_mother"),
            data.get("grandfather"),
        ),
        "fields": {
            key: data.get(key)
            for key in (
                "first_name",
                "father_mother",
                "grandfather",
                "last_name",
                "nickname",
                "is_woman",
            )
        },
        "first_year": min(dated_living) if dated_living else None,
        "last_year": max(dated_living) if dated_living else None,
        "n_appearances": len(appearances),
        "appearances": [dict(row) for row in appearances],
        "role_profile": role_profile,
        "context_profile": {
            "professions": profile_values("profession"),
            "residences": profile_values("residence"),
            "origins": profile_values("origin"),
            "titles": profile_values("title"),
            "father_mother_titles": profile_values("father_mother_title"),
            "grandfather_titles": profile_values("grandfather_title"),
            "husband_titles": profile_values("husband_title"),
            "economic_activities": profile_values("economic_activity"),
        },
        "unavailable": bool(data.get("is_deleted")),
    }


def _person_pair_by_key(cache: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = cache.execute(
        "SELECT * FROM person_pair_suggestion WHERE pair_key=?", (key,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="This case is no longer in the list of suggestions.")
    return _decode_person_pair(row)


def _person_evidence_basis(
    persons: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    group: dict[str, Any] | None,
    contracts: list[dict[str, Any]],
    run: dict[str, Any],
    business_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "persons": [
            {
                "person_id": person["person_id"],
                "fields": person["fields"],
                "first_year": person["first_year"],
                "last_year": person["last_year"],
                "n_appearances": person["n_appearances"],
                "appearances": person["appearances"],
                "role_profile": person.get("role_profile"),
                "context_profile": person.get("context_profile"),
                "unavailable": person.get("unavailable", False),
            }
            for person in persons
        ],
        "group": group,
        "pairs": [
            {
                "pair_key": pair.get("pair_key"),
                "deterministic_tier": pair.get("deterministic_tier"),
                "precedence_verdict": pair.get("precedence_verdict"),
                "match_probability": pair.get("match_probability"),
                "match_weight": pair.get("match_weight"),
                "reasons": pair.get("reasons_json"),
                "evidence": pair.get("evidence_json"),
                "source_pointers": pair.get("source_pointers_json"),
                "waterfall": pair.get("waterfall_contributions_json"),
            }
            for pair in pairs
        ],
        "provenance": run,
        "business_context": business_context or {},
        "shared_contracts": [
            {
                "contract_id": contract["contract_id"],
                "document": contract.get("document"),
                "document_sha256": hashlib.sha256(
                    str(contract.get("document") or "").encode("utf-8")
                ).hexdigest(),
                "word_source_ids": [
                    source.get("source_entry_id")
                    for source in contract.get("word_sources") or []
                ],
            }
            for contract in contracts
        ],
    }


def _person_business_context(
    connection: sqlite3.Connection,
    person_ids: list[int],
    persons: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve cached network ids/strings into reviewer-readable evidence."""
    from workflows.person_features import normalize_name

    shared_partner_ids = sorted(
        {
            int(partner_id)
            for pair in pairs
            for partner_id in pair.get("shared_partner_ids_json") or []
        }
    )
    shared_words = sorted(
        {
            str(word)
            for pair in pairs
            for word in pair.get("shared_firm_words_json") or []
        }
    )
    exact_firm_keys = sorted(
        {
            str(firm)
            for pair in pairs
            for firm in pair.get("shared_firms_json") or []
        }
    )

    partners: list[dict[str, Any]] = []
    for partner_id in shared_partner_ids:
        partner = connection.execute(
            """
            SELECT person_id, first_name, father_mother, grandfather, last_name, nickname
            FROM person WHERE person_id=?
            """,
            (partner_id,),
        ).fetchone()
        connections: dict[str, list[dict[str, Any]]] = {}
        for subject_id in person_ids:
            rows = connection.execute(
                """
                SELECT DISTINCT c.contract_id, c.registration_date, c.firm_name
                FROM investor subject
                JOIN investor partner
                  ON partner.contract_id=subject.contract_id
                 AND partner.person_id=?
                 AND partner.is_deleted=0
                JOIN contract c
                  ON c.contract_id=subject.contract_id AND c.is_deleted=0
                WHERE subject.person_id=? AND subject.is_deleted=0
                ORDER BY c.registration_date, c.contract_id
                """,
                (partner_id, subject_id),
            ).fetchall()
            connections[str(subject_id)] = [dict(row) for row in rows]
        partners.append(
            {
                "person_id": partner_id,
                "display_name": (
                    person_display_name(
                        partner["first_name"],
                        partner["last_name"],
                        partner["nickname"],
                        partner["father_mother"],
                        partner["grandfather"],
                    )
                    if partner
                    else f"Entry #{partner_id}"
                ),
                "connections_by_person": connections,
            }
        )

    exact_firms: list[dict[str, Any]] = []
    for firm_key in exact_firm_keys:
        by_person: dict[str, list[dict[str, Any]]] = {}
        raw_names: list[str] = []
        for person in persons:
            matches = [
                {
                    "contract_id": appearance["contract_id"],
                    "registration_date": appearance.get("registration_date"),
                    "firm_name": appearance.get("firm_name"),
                }
                for appearance in person["appearances"]
                if normalize_name(appearance.get("firm_name")) == firm_key
            ]
            by_person[str(person["person_id"])] = matches
            raw_names.extend(
                str(item["firm_name"]) for item in matches if item.get("firm_name")
            )
        exact_firms.append(
            {
                "normalized_name": firm_key,
                "display_name": raw_names[0] if raw_names else firm_key,
                "appearances_by_person": by_person,
            }
        )

    firm_word_evidence = []
    if shared_words and not exact_firms:
        for person in persons:
            matches = []
            seen: set[tuple[int, str]] = set()
            for appearance in person["appearances"]:
                raw_name = str(appearance.get("firm_name") or "").strip()
                normalized_tokens = set(normalize_name(raw_name).split())
                matched = sorted(normalized_tokens & set(shared_words))
                key = (int(appearance["contract_id"]), raw_name)
                if raw_name and matched and key not in seen:
                    seen.add(key)
                    matches.append(
                        {
                            "contract_id": appearance["contract_id"],
                            "registration_date": appearance.get("registration_date"),
                            "firm_name": raw_name,
                            "matched_words": matched,
                        }
                    )
            firm_word_evidence.append(
                {"person_id": person["person_id"], "firms": matches}
            )

    return {
        "exact_firms": exact_firms,
        "shared_partners": partners,
        "shared_firm_words": shared_words,
        "firm_word_evidence": firm_word_evidence,
    }


def _entry_word_sources(
    connection: sqlite3.Connection, person: dict[str, Any]
) -> list[dict[str, Any]]:
    """The Word summaries of the acts one entry appears in, per act, in date order.

    A one-entry ("split") case has no pair and so no shared contracts, which is
    where the Sources drawer used to look; a reader deciding which acts are
    whose needs the summary of each act (review W6; scope v2, prerequisite A).
    One pass over the link file for all the entry's acts, every candidate
    kept, reviewer-confirmed links included even when the matcher never
    proposed them. Returned beside — never inside — `evidence_basis`, so a
    standing flag's "evidence changed" check does not move when a link is
    proposed or judged.
    """
    acts: dict[int, dict[str, Any]] = {}
    for appearance in person.get("appearances") or []:
        raw = appearance.get("contract_id")
        if raw in (None, ""):
            continue
        contract_id = int(raw)
        acts.setdefault(
            contract_id,
            {
                "contract_id": contract_id,
                "registration_date": appearance.get("registration_date"),
                "firm_name": appearance.get("firm_name"),
                "folio": None,
                "document": None,
                "word_sources": [],
            },
        )
    if not acts:
        return []
    targets = [
        (f"contract:{contract_id}", act["firm_name"] or f"Contract {contract_id}")
        for contract_id, act in acts.items()
    ]
    sources, _total = word_sources_via_contracts(
        targets, cap=None, include_unproposed_confirmed=True
    )
    for source in sources:
        via = str(source.get("via_row_id") or "").removeprefix("contract:")
        if via.isdigit() and int(via) in acts:
            acts[int(via)]["word_sources"].append(source)
    marks = ",".join("?" for _ in acts)
    for row in connection.execute(
        f"SELECT contract_id, folio, document FROM contract "
        f"WHERE contract_id IN ({marks}) AND is_deleted=0",
        list(acts),
    ):
        act = acts[int(row["contract_id"])]
        act["folio"] = row["folio"]
        act["document"] = clean_document(row["document"])

    def date_key(act: dict[str, Any]) -> tuple[str, int]:
        date = str(act.get("registration_date") or "")
        return (date if date and date != PLACEHOLDER_DATE else "9999", act["contract_id"])

    return sorted(acts.values(), key=date_key)


def _person_case_payload(case_id: str) -> dict[str, Any]:
    if not person_linkage_enabled():
        raise HTTPException(status_code=404, detail="Person identity review is not enabled.")
    cache = open_person_cache()
    try:
        if case_id.startswith("pair:"):
            pair = _person_pair_by_key(cache, case_id.removeprefix("pair:"))
            ids = [int(pair["person_id_l"]), int(pair["person_id_r"])]
            pair_rows = [pair]
            group = None
        elif case_id.startswith("group:"):
            group_key = case_id.removeprefix("group:")
            row = cache.execute(
                "SELECT * FROM person_group_projection WHERE group_key=?", (group_key,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Person-linkage group not found.")
            group = _decode_person_group(row)
            ids = [int(value) for value in group["person_ids_json"]]
            pair_rows = [
                _person_pair_by_key(cache, key)
                for key in group.get("edge_pair_keys_json") or []
            ]
        elif case_id.startswith("split:"):
            ids = [int(case_id.removeprefix("split:"))]
            pair_rows = []
            group = None
        else:
            raise HTTPException(status_code=404, detail="Unknown person-linkage case.")
        run = dict(cache.execute("SELECT * FROM cache_run LIMIT 1").fetchone())
    finally:
        cache.close()

    connection = open_db()
    try:
        persons = [_person_card(connection, person_id) for person_id in ids]
        business_context = _person_business_context(
            connection, ids, persons, pair_rows
        )
        shared_contract_ids = sorted(
            {
                int(contract_id)
                for pair in pair_rows
                for contract_id in pair.get("shared_contract_ids_json") or []
            }
        )
        contracts = []
        for contract_id in shared_contract_ids:
            contract = connection.execute(
                """
                SELECT contract_id, registration_date, firm_name, folio, document
                FROM contract WHERE contract_id=? AND is_deleted=0
                """,
                (contract_id,),
            ).fetchone()
            if contract:
                item = dict(contract)
                item["document"] = clean_document(item.get("document"))
                item["word_sources"] = linked_word_sources(f"contract:{contract_id}")
                contracts.append(item)
        # A one-entry case: the summaries of its own acts, per act, dated.
        entry_sources = _entry_word_sources(connection, persons[0]) if len(ids) == 1 else None
    finally:
        connection.close()

    clog = open_corrections()
    try:
        all_links, parent, distinct = _active_person_link_state(clog)
        latest = _latest_person_events(clog)
        links = [
            link
            for link in all_links
            if int(link["from_id"]) in ids or int(link["to_id"]) in ids
        ]
        history = corrections_db.person_review_events(clog, case_id=case_id)
        split_flags = [
            flag
            for flag in (_person_split_flag(latest, person_id) for person_id in ids)
            if flag
        ]
        split_flagged_person_ids = [int(flag["person_id"]) for flag in split_flags]
        # One-entry case: the standing marks read against the live appearances,
        # and whether the open blind round still holds the entry (then the
        # screen offers no marking and the server refuses one).
        seam_view = _person_seam_view(persons[0], latest.get(case_id)) if len(ids) == 1 else None
        marks_held_by_round = (
            ids[0] in _person_ids_in_open_round(run, latest) if len(ids) == 1 else False
        )
    finally:
        clog.close()
    case_status = _person_case_status(
        {"case_id": case_id, "person_ids": ids}, parent, distinct, latest
    )
    probability_values = [
        float(pair["match_probability"])
        for pair in pair_rows
        if pair.get("match_probability") is not None
    ]
    tier = (
        pair_rows[0].get("deterministic_tier")
        if len(pair_rows) == 1
        else ("group" if pair_rows else "career_span")
    )
    latest_event = history[0] if history else None
    evidence_basis = _person_evidence_basis(
        persons, pair_rows, group, contracts, run, business_context
    )
    needs_recheck = bool(
        latest_event
        and person_cache.canonical_json(
            {
                key: (latest_event.get("evidence_snapshot") or {}).get(key)
                for key in evidence_basis
            }
        )
        != person_cache.canonical_json(evidence_basis)
    )
    if len(ids) == 1:
        lane = "possible_splits"
    elif group:
        lane = _person_group_preview(
            group, {str(pair["pair_key"]): pair for pair in pair_rows}
        )["lane"]
    else:
        lane = _person_pair_lane(pair_rows[0])
    return {
        "case_id": case_id,
        "kind": "group" if group else ("split" if len(ids) == 1 else "pair"),
        "person_ids": ids,
        "persons": persons,
        "pairs": pair_rows,
        "group": group,
        "shared_contracts": contracts,
        "business_context": business_context,
        "split_flagged_person_ids": split_flagged_person_ids,
        # Who flagged, when, why, and the event to lift it with (a reopen).
        "split_flags": split_flags,
        "active_links": links,
        "history": history,
        "status": case_status,
        "latest_event_id": history[0]["event_id"] if history else None,
        "tier": tier,
        "lane": lane,
        "match_probability": min(probability_values) if probability_values else None,
        "run": run,
        "stale": _person_cache_stale(run),
        "needs_recheck": needs_recheck,
        "evidence_basis": evidence_basis,
        # Split cases only: the Word summaries of the entry's own acts, in
        # date order. Not part of `evidence_basis` (see _entry_word_sources).
        **({"entry_sources": entry_sources} if entry_sources is not None else {}),
        # Split cases only: the standing marks, or null (see _person_seam_view),
        # and whether marking is blocked by the open blind round.
        **({"seam": seam_view, "marks_held_by_round": marks_held_by_round} if len(ids) == 1 else {}),
    }


def _person_blind_case_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Whitelist projection of a full case payload for the blind round.

    Constructed from scratch rather than by deleting fields: a blacklist fails
    open the moment the cache grows a new scoring column, while a whitelist
    keeps any future addition invisible until it is deliberately admitted.
    Only record-derived evidence survives — the labeler must judge from the
    sources, never from the model's own routing.
    """
    persons = []
    for person in payload["persons"]:
        card = dict(person)
        # The identity dashboard would reveal how many open cases the model
        # routed to these rows, which is exactly the signal being labeled.
        card.pop("identity_status", None)
        persons.append(card)
    pairs = [
        {
            "pair_key": pair.get("pair_key"),
            "person_id_l": pair.get("person_id_l"),
            "person_id_r": pair.get("person_id_r"),
            "career_span_json": pair.get("career_span_json"),
            "roles_json": pair.get("roles_json"),
            "contract_pointers_json": pair.get("contract_pointers_json"),
            "source_pointers_json": pair.get("source_pointers_json"),
            "shared_contract_ids_json": pair.get("shared_contract_ids_json"),
            "shared_firms_json": pair.get("shared_firms_json"),
            "shared_firm_words_json": pair.get("shared_firm_words_json"),
            "shared_partner_ids_json": pair.get("shared_partner_ids_json"),
            "evidence_json": {
                key: value
                for key, value in (pair.get("evidence_json") or {}).items()
                if key in _PERSON_BLIND_EVIDENCE_KEYS
            },
        }
        for pair in payload["pairs"]
    ]
    # History events carry score-at-decision provenance (match_probability,
    # model/cache hashes) and — for blind decisions — the labeling metadata
    # whose stratum name encodes the sampling cell. A reopened case re-enters
    # the round with its own prior decision in history, so events get the same
    # whitelist treatment as pairs; only reviewer-authored content survives.
    history = [
        {
            "event_id": event.get("event_id"),
            "action": event.get("action"),
            "person_ids": event.get("person_ids"),
            "link_ids": event.get("link_ids"),
            "rationale": event.get("rationale"),
            "created_by": event.get("created_by"),
            "created_at": event.get("created_at"),
            "evidence_snapshot": {
                key: value
                for key, value in (event.get("evidence_snapshot") or {}).items()
                if key == "citations"
            },
        }
        for event in payload["history"]
    ]
    return {
        "case_id": payload["case_id"],
        # Case identity, not evidence: the decision dock reads person_ids and
        # kind unconditionally (packet cases are always pairs), and the ids
        # already appear on every person card and pair row.
        "kind": payload["kind"],
        "person_ids": payload["person_ids"],
        "lane": "labeling_round",
        "status": payload["status"],
        # The bare event id carries no evidence; without it the client cannot
        # send expected_event_id, and a reopened case could never be re-decided.
        "latest_event_id": payload["latest_event_id"],
        "persons": persons,
        "pairs": pairs,
        "shared_contracts": payload["shared_contracts"],
        # Resolved partner names and dated firm co-appearances are main.db
        # record evidence (built by _person_business_context from investor and
        # contract rows) — the labeler is meant to weigh them; only the model's
        # own routing stays hidden.
        "business_context": payload["business_context"],
        "history": history,
    }


_PERSON_GROUP_IN_ROUND_DETAIL = (
    "Some of this group's pairs are waiting under Judge blind. Decide them there, "
    "or finish the round, to review the group here."
)


def _refuse_group_in_open_round(case: dict[str, Any], frozen: set[str]) -> None:
    """A group with a frozen pair is closed, by its link and by decision, while the
    round is open (review_2026-10-23 M2). The lanes (group_in_open_round) and the
    Database hints already hide such a group; this is the same pair test for the
    case URL, the group URL and the decision. Refused rather than blinded: the
    group is itself the model's clustering, a two-entry group IS its packet pair,
    and Judge blind labels pairs only, so a blinded group could be read but never
    decided."""
    if case.get("kind") != "group" or not frozen:
        return
    # A group already linked or kept apart (before the round began) stays open,
    # so its decision can be read and undone: none of its pairs can take a blind
    # label any more. After an undo it reads open again and is refused.
    if case.get("status") in {"same_as", "distinct"}:
        return
    edge_keys = (case.get("group") or {}).get("edge_pair_keys_json") or []
    if any(f"pair:{key}" in frozen for key in edge_keys):
        raise HTTPException(status_code=409, detail=_PERSON_GROUP_IN_ROUND_DETAIL)


@app.get("/api/person-linkage/cases/{case_id}")
def person_linkage_case(case_id: str) -> dict[str, Any]:
    payload = _person_case_payload(case_id)
    clog = open_corrections()
    try:
        latest = _latest_person_events(clog)
    finally:
        clog.close()
    frozen = _person_labeling_frozen_case_ids(payload["run"], latest)
    _refuse_group_in_open_round(payload, frozen)
    if payload["case_id"] in frozen:
        return _person_blind_case_payload(payload)
    return payload


# Marking which acts are whose, on a flagged entry: one mark per live
# appearance (investor row), stored inside the flag_split event's snapshot.
# Scope: docs/person_linkage/seam_scope_v2_2026-10-26.md §5.
SEAM_SIDES = ("this", "other", "unsure")
SEAM_REVISION_REASON = "seam_revision"


class PersonSeamMark(BaseModel):
    investor_id: int
    side: str


class PersonSeam(BaseModel):
    version: int = 1
    assignments: list[PersonSeamMark]
    other_entry_id: int | None = None
    revises_event_id: str | None = None


class PersonLinkageDecision(BaseModel):
    reviewer: str = Field(min_length=1)
    action: str
    person_ids: list[int]
    reason_code: str = ""
    rationale: str = ""
    citations: list[dict[str, Any]] = Field(default_factory=list)
    expected_status: str
    expected_event_id: str | None
    review_mode: str = "standard"
    seam: PersonSeam | None = None


def _validate_person_decision_scope(case: dict[str, Any], ids: list[int], action: str) -> None:
    expected = {int(value) for value in case["person_ids"]}
    submitted = {int(value) for value in ids}
    if not submitted or not submitted <= expected:
        raise HTTPException(status_code=400, detail="Decision ids are outside this review case.")
    if action in {"same_as", "distinct"} and len(submitted) < 2:
        raise HTTPException(status_code=400, detail="This decision needs at least two people.")


@app.post("/api/person-linkage/cases/{case_id}/decision")
@serialized_write
def decide_person_linkage(case_id: str, action: PersonLinkageDecision) -> dict[str, Any]:
    if not person_linkage_enabled():
        raise HTTPException(status_code=403, detail="Person identity review is not enabled.")
    if action.action not in {"same_as", "distinct", "defer", "reopen", "flag_split", "bulk_rule_ack"}:
        raise HTTPException(status_code=400, detail="Unknown person identity decision.")
    reviewer = action.reviewer.strip()
    if not reviewer:
        raise HTTPException(status_code=400, detail="Enter a reviewer before saving.")
    if action.action == "defer" and not action.rationale.strip():
        raise HTTPException(
            status_code=400,
            detail="Explain what evidence is still needed before deferring this case.",
        )
    if action.action == "flag_split" and not action.rationale.strip():
        # A flag changes nothing by itself; the note is its whole content and
        # what a later split starts from.
        raise HTTPException(
            status_code=400,
            detail="Say which contracts seem to belong to someone else, and why.",
        )
    case = _person_case_payload(case_id)
    clog = open_corrections()
    try:
        latest = _latest_person_events(clog)
    finally:
        clog.close()
    round_open_ids = _person_labeling_open_case_ids(case["run"], latest)
    frozen = _person_labeling_frozen_case_ids(case["run"], latest)
    # A decision on such a group would settle its packet pairs without a blind
    # label (their status turns same_as while the round still counts them open).
    _refuse_group_in_open_round(case, frozen)
    if case_id not in round_open_ids and case_id in frozen:
        # A co-minted sibling pair: not itself a labeling case, but both of
        # its persons are — deciding it mid-round would settle the family
        # the labeler is judging blind. It thaws as the packet cases decide.
        raise HTTPException(
            status_code=409,
            detail="Both entries in this case are waiting under Judge blind; finish the round first.",
        )
    if action.review_mode != "blind_labeling":
        if case_id in round_open_ids:
            # A standard decision would be made against the full payload and
            # contaminate the blind label, so the round must be worked (or
            # explicitly finished) before ordinary review resumes here.
            raise HTTPException(
                status_code=409,
                detail="This case is waiting under Judge blind. Decide it there, or finish the round first.",
            )
    if (
        action.action == "same_as"
        and case.get("lane") == "rule_exclusions"
        and not action.rationale.strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="Explain why the sources override the rule.",
        )
    # Marks (which acts are whose) ride on a flag_split of the entry's own
    # case. Changing standing marks is an explicit revision: the same action,
    # with reason_code "seam_revision" naming the event it revises.
    seam = action.seam
    revising = (
        action.action == "flag_split"
        and seam is not None
        and action.reason_code.strip() == SEAM_REVISION_REASON
    )
    if seam is not None and action.action != "flag_split":
        raise HTTPException(status_code=400, detail="Marks go with a flag, not with this decision.")
    if (
        action.action == "flag_split"
        and action.reason_code.strip() == SEAM_REVISION_REASON
        and seam is None
    ):
        raise HTTPException(status_code=400, detail="A change of marks needs the marks.")
    if seam is not None:
        if len(action.person_ids) != 1:
            raise HTTPException(status_code=400, detail="Marks belong to one entry.")
        marked_person = int(action.person_ids[0])
        if case_id != f"split:{marked_person}":
            raise HTTPException(
                status_code=400, detail="Mark the acts on the entry's own case."
            )
        if marked_person in _person_ids_in_open_round(case["run"], latest):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This entry is waiting under Judge blind; its acts can be marked "
                    "once it is judged."
                ),
            )
        _validate_seam(seam, marked_person)
    if action.action == "flag_split" and case["status"] == "flag_split":
        prior = latest.get(case_id) or {}
        if revising:
            if (
                seam.revises_event_id != case["latest_event_id"]
                or action.expected_event_id != case["latest_event_id"]
                or action.expected_status != "flag_split"
            ):
                raise HTTPException(
                    status_code=409,
                    detail="The marks changed after you opened them. Reload before changing them.",
                )
        else:
            # Flagging an already-flagged entry used to fall through to the
            # "case changed, reload" conflict below, which is false and
            # alarming. A plain re-flag can never replace standing marks.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Already flagged by {prior.get('created_by') or 'a reviewer'} on "
                    f"{_plain_date(str(prior.get('created_at') or ''))}. "
                    "Open the entry to read or lift the flag."
                ),
            )
    elif revising:
        raise HTTPException(status_code=400, detail="Nothing to change: this entry is not flagged.")
    if action.action != "bulk_rule_ack" and (
        action.expected_status != case["status"]
        or action.expected_event_id != case["latest_event_id"]
    ):
        raise HTTPException(
            status_code=409,
            detail="This case changed after you opened it. Reload before deciding.",
        )
    if action.action == "reopen":
        # An undone link is already open; only a deferral or a flag closes a
        # case in a way that reopen can undo.
        if case["status"] not in {"defer", "flag_split"}:
            raise HTTPException(status_code=409, detail="Only a deferred or flagged case can be reopened.")
    elif action.action != "bulk_rule_ack" and case["status"] != "open" and not revising:
        raise HTTPException(
            status_code=409,
            detail="This case already has a decision. Undo or reopen it before deciding again.",
        )
    _validate_person_decision_scope(case, action.person_ids, action.action)
    connection = open_db()
    try:
        from workflows.person_features import classify_entity_kind

        for person_id in action.person_ids:
            row = connection.execute(
                "SELECT first_name,father_mother,grandfather,last_name,is_deleted "
                "FROM person WHERE person_id=?",
                (person_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail=f"Person entry #{person_id} is no longer in the database.")
            if row["is_deleted"]:
                raise HTTPException(
                    status_code=409,
                    detail=f"Person entry #{person_id} is hidden. Restore it under Database before deciding.",
                )
            if (
                classify_entity_kind(
                    row["first_name"],
                    row["father_mother"],
                    row["grandfather"],
                    row["last_name"],
                )
                != "person"
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Person entry #{person_id} does not name a person "
                        "(it reads as an institution, an estate or a placeholder)."
                    ),
                )
    finally:
        connection.close()
    citations = []
    for citation in action.citations:
        item = dict(citation)
        quote = str(item.get("source_quote") or "")
        if quote:
            item["source_quote_sha256"] = hashlib.sha256(
                quote.encode("utf-8")
            ).hexdigest()
        citations.append(item)
    labeling_metadata = None
    if action.review_mode == "blind_labeling":
        labeling_metadata = _person_labeling_metadata(case_id)
        if not labeling_metadata:
            raise HTTPException(
                status_code=400,
                detail="This case is not in the current Judge blind round.",
            )
    snapshot = {
        **case["evidence_basis"],
        "case_id": case_id,
        "reviewed_person_ids": sorted(action.person_ids),
        "citations": citations,
        "labeling": labeling_metadata,
    }
    if seam is not None:
        # Outside `evidence_basis`, so the "evidence changed" check ignores it.
        root = None
        if revising:
            prior = latest.get(case_id) or {}
            root = (_event_seam(prior) or {}).get("root") or {
                "event_id": str(prior.get("event_id") or ""),
                "created_by": prior.get("created_by") or "",
                "created_at": prior.get("created_at") or "",
                "rationale": prior.get("rationale") or "",
            }
        snapshot["seam"] = {
            "version": 1,
            "assignments": sorted(
                (
                    {"investor_id": int(mark.investor_id), "side": mark.side}
                    for mark in seam.assignments
                ),
                key=lambda mark: mark["investor_id"],
            ),
            "other_entry_id": seam.other_entry_id,
            "revises_event_id": seam.revises_event_id if revising else None,
            "root": root,
        }
    clog = open_corrections()
    try:
        try:
            result = corrections_db.record_person_review(
                clog,
                action=action.action,
                person_ids=action.person_ids,
                by=reviewer,
                evidence_snapshot=snapshot,
                case_id=(
                    f"rule:{case.get('tier') or 'distinct_strong'}"
                    if action.action == "bulk_rule_ack"
                    else case_id
                ),
                reason_code=action.reason_code.strip() or None,
                rationale=action.rationale.strip() or None,
                source_tier=case.get("tier"),
                model_sha256=case["run"].get("model_sha256"),
                cache_run_id=case["run"].get("run_id"),
                match_probability=case.get("match_probability"),
            )
        except corrections_db.PersonDecisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}
    finally:
        clog.close()


@app.post("/api/person-linkage/links/{link_id}/revoke")
@serialized_write
def revoke_person_linkage(link_id: str, action: ReviewerOnly) -> dict[str, Any]:
    if not person_linkage_enabled():
        raise HTTPException(status_code=403, detail="Person identity review is not enabled.")
    reviewer = action.reviewer.strip()
    if not reviewer:
        raise HTTPException(status_code=400, detail="Enter a reviewer before undoing this link.")
    clog = open_corrections()
    try:
        result = corrections_db.revoke_person_reference_link(
            clog,
            link_id=link_id,
            by=reviewer,
            rationale=action.reason.strip() or None,
        )
        if not result:
            raise HTTPException(status_code=404, detail="No active person link with that id.")
        return {"ok": True, "event_id": result["event_id"]}
    finally:
        clog.close()


@app.post("/api/person-linkage/events/{event_id}/revoke")
@serialized_write
def revoke_person_decision(event_id: str, action: ReviewerOnly) -> dict[str, Any]:
    """Undo all links created by one person-review decision atomically."""
    if not person_linkage_enabled():
        raise HTTPException(status_code=403, detail="Person identity review is not enabled.")
    reviewer = action.reviewer.strip()
    if not reviewer:
        raise HTTPException(status_code=400, detail="Enter a reviewer before undoing this decision.")
    clog = open_corrections()
    try:
        result = corrections_db.revoke_person_review_event(
            clog,
            event_id=event_id,
            by=reviewer,
            rationale=action.reason.strip() or None,
        )
        if not result:
            raise HTTPException(status_code=404, detail="No active person decision with that id.")
        return {"ok": True, **result}
    finally:
        clog.close()


@app.get("/api/person-linkage/groups/{group_id}")
def person_linkage_group(group_id: str) -> dict[str, Any]:
    # Same gate as the case URL: no page calls this, but it must not be the one
    # door that skips the blind round.
    return person_linkage_case(f"group:{group_id}")


# ---- Analysis: read-only query execution (builder / library / console) ----
# Everything analytical runs through ONE executor: SELECT-only, on a read-only
# connection (mode=ro), single statement, row-capped and time-limited. It cannot
# modify the corpus, and a runaway query cannot hang the server.
_ANALYSIS_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|"
    r"vacuum|reindex|trigger|grant|revoke|truncate)\b",
    re.IGNORECASE,
)
ANALYSIS_ROW_CAP = 5000
# The CSV download holds every row up to this ceiling and refuses above it with
# a sentence (review 2026-10-27, 4.1 #2). The file is written to a temporary
# file in one pass and the connection closed before it is sent
# (export_readonly_csv), so the server never holds the whole result in memory.
ANALYSIS_EXPORT_CAP = 100_000
ANALYSIS_TIMEOUT_S = 6.0
ANALYSIS_VALUE_LIMIT = 1_000_000  # characters in one computed value


def _readonly_statement(sql: str) -> str:
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        raise HTTPException(status_code=400, detail="Empty query.")
    if ";" in statement:
        raise HTTPException(status_code=400, detail="Only a single SELECT statement is allowed.")
    head = statement.lstrip("(").lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise HTTPException(status_code=400, detail="Only SELECT / WITH queries are allowed.")
    if _ANALYSIS_FORBIDDEN.search(statement):
        raise HTTPException(status_code=400, detail="Only read-only SELECT queries are allowed.")
    return statement


def _readonly_connection() -> sqlite3.Connection:
    """A read-only connection that interrupts itself ANALYSIS_TIMEOUT_S from now.
    Rows come back as plain tuples, read by position: two result columns may
    share a name (SELECT s.contract_id, c.contract_id …), and a by-name lookup
    would repeat the first one's value in the second."""
    connection = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    # The time check runs only between SQLite steps, so one step that builds a
    # huge value (printf of millions of characters) could outrun it. No stored
    # value comes near this length (the longest is 8,321 characters); a larger
    # one is refused as "string or blob too big".
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, ANALYSIS_VALUE_LIMIT)
    deadline = time.monotonic() + ANALYSIS_TIMEOUT_S
    connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100_000)
    return connection


def _sql_error(exc: sqlite3.Error) -> HTTPException:
    message = str(exc)
    if "interrupt" in message.lower():
        return HTTPException(status_code=400, detail="Query timed out — narrow it and try again.")
    return HTTPException(status_code=400, detail=f"SQL error: {message}")


def run_readonly_sql(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    """Run one read-only SELECT for the screen: the first ANALYSIS_ROW_CAP rows,
    and how many there are in all. Counting stops just past the download
    ceiling (``total_more_than``), and if it runs out of time both totals are
    None: the total is then unknown rather than wrong."""
    statement = _readonly_statement(sql)
    connection = _readonly_connection()
    try:
        cursor = connection.execute(statement, params)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows: list[list[Any]] = []
        truncated = False
        for index, row in enumerate(cursor):
            if index >= ANALYSIS_ROW_CAP:
                truncated = True
                break
            rows.append(list(row))
        total_rows: int | None = len(rows)
        total_more_than: int | None = None
        if truncated:
            counted = len(rows) + 1  # the row that tripped the cap
            try:
                for _ in cursor:
                    counted += 1
                    if counted > ANALYSIS_EXPORT_CAP:
                        break
                if counted > ANALYSIS_EXPORT_CAP:
                    total_rows, total_more_than = None, ANALYSIS_EXPORT_CAP
                else:
                    total_rows = counted
            except sqlite3.OperationalError:
                total_rows = None
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "total_rows": total_rows,
            "total_more_than": total_more_than,
        }
    except sqlite3.Error as exc:
        raise _sql_error(exc)
    finally:
        connection.close()


def export_readonly_csv(
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    preamble: list[list[str]] | None = None,
    labels: list[str] | None = None,
) -> StreamingResponse:
    """The CSV download: every row, or a refusal in words. One pass under the
    time limit writes the CSV into a temporary file and counts as it goes;
    the connection is closed before the first byte is sent, so a slow or stalled
    download never holds the database's read lock (saves wait on it), and a
    refusal or a time-out is a 400 sent in place of the file. The file goes out
    with its Content-Length, so a download cut short cannot pass as complete.
    A Find download (``preamble``) opens with a UTF-8 byte-order mark, its
    question in words and a blank line, then a header row in words
    (``labels``) above the column names kept for scripts."""
    statement = _readonly_statement(sql)
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    connection = _readonly_connection()
    try:
        cursor = connection.execute(statement, params)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        if preamble is not None:
            buffer.write("﻿")
            writer.writerows(preamble)
            writer.writerow([])
        if labels is not None:
            writer.writerow(labels)
        writer.writerow([d[0] for d in cursor.description] if cursor.description else [])
        for count, row in enumerate(cursor, 1):
            if count > ANALYSIS_EXPORT_CAP:
                raise HTTPException(status_code=400, detail=(
                    f"This result has more than {ANALYSIS_EXPORT_CAP:,} rows, too many to download. "
                    "Narrow the question and try again."
                ))
            writer.writerow(row)
            if buffer.tell() > 64_000:
                spool.write(buffer.getvalue().encode("utf-8"))
                buffer.seek(0)
                buffer.truncate()
        spool.write(buffer.getvalue().encode("utf-8"))
    except sqlite3.Error as exc:
        spool.close()
        raise _sql_error(exc)
    except BaseException:
        spool.close()
        raise
    finally:
        connection.close()
    size = spool.tell()
    spool.seek(0)
    return _SpoolResponse(spool, size)


class _SpoolResponse(StreamingResponse):
    """Sends a finished temporary file and closes it (which deletes it) however
    the response ends: sent, abandoned, or cut off by the client."""

    def __init__(self, spool: Any, size: int) -> None:
        self.spool = spool

        def chunks():
            while chunk := spool.read(64 * 1024):
                yield chunk

        super().__init__(chunks(), media_type="text/csv; charset=utf-8", headers={"Content-Length": str(size)})

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.spool.close()


# The two Jewish lists, per contract, with how its Jewish partners are known:
# stated in the act (is_jewish) or the data-entry team's attribution only
# (jewish_db without is_jewish). The two layers are never merged into one mark
# (review 2026-10-27, 4.1/Q4). A hidden person's appearance counts on no side.
_JEWISH_PROVENANCE_NOTE = (
    "“Stated in the act”: every Jewish partner on the contract is described as Jewish in the act. "
    "“Attributed only”: none is; each was marked by the data-entry team (its basis is not recorded "
    "for each partner). “Partly stated, partly attributed”: some of each."
)


def _jewish_list_sql(partition: str) -> str:
    return (
        "SELECT CAST(substr(c.registration_date,1,4) AS INTEGER) AS year, c.contract_id, c.firm_name,\n"
        "       CASE WHEN j.attributed = 0 THEN 'stated in the act'\n"
        "            WHEN j.stated = 0 THEN 'attributed only'\n"
        "            ELSE 'partly stated, partly attributed' END AS provenance\n"
        "FROM contract c\n"
        "JOIN (SELECT i.contract_id,\n"
        "             SUM(CASE WHEN i.is_jewish = 1 THEN 1 ELSE 0 END) AS stated,\n"
        "             SUM(CASE WHEN i.jewish_db = 1 AND COALESCE(i.is_jewish, 0) <> 1 THEN 1 ELSE 0 END) AS attributed,\n"
        "             SUM(CASE WHEN i.is_jewish = 0 AND i.jewish_db = 0 THEN 1 ELSE 0 END) AS neither\n"
        "      FROM investor i JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "      WHERE i.is_deleted = 0 GROUP BY i.contract_id) j ON j.contract_id = c.contract_id\n"
        f"WHERE c.is_deleted = 0 AND j.stated + j.attributed > 0 AND {partition}\n"
        "ORDER BY year, c.contract_id"
    )


# Named queries, re-authored from `SQL formulas/` for SQLite. chart ∈ none|bar|line.
# "overlapping": one contract can count under several rows (several places); the
# sentence that says so, shown under the chart, which then never adds up the rows
# past its top 20.
ANALYSIS_LIBRARY: list[dict[str, Any]] = [
    {
        "id": "corpus_totals",
        "group": "Overview",
        "title": "Corpus totals",
        "description": "How many contracts, sub-contracts, people, places and currencies the database holds.",
        "chart": "none",
        "sql": "SELECT 'contracts' AS item, COUNT(*) AS n FROM contract WHERE is_deleted = 0\n"
        "UNION ALL SELECT 'sub-contracts', COUNT(*) FROM sub_contract s\n"
        "  JOIN contract c ON c.contract_id = s.main_contract_id AND c.is_deleted = 0 WHERE s.is_deleted = 0\n"
        "UNION ALL SELECT 'people', COUNT(*) FROM person WHERE is_deleted = 0\n"
        "UNION ALL SELECT 'places', COUNT(*) FROM place\n"
        "UNION ALL SELECT 'currencies', COUNT(*) FROM currency",
    },
    {
        "id": "contracts_per_year",
        "group": "Time",
        "title": "Contracts per registration year",
        "description": "",
        "chart": "line",
        "sql": "SELECT CAST(substr(registration_date, 1, 4) AS INTEGER) AS year, COUNT(*) AS contracts\n"
        "FROM contract\n"
        "WHERE is_deleted = 0 AND registration_date NOT IN ('', '0000-00-00', 'NULL') AND registration_date IS NOT NULL\n"
        "GROUP BY year ORDER BY year",
    },
    {
        "id": "currency_frequency",
        "group": "Capital & currency",
        "title": "Most-used currencies",
        "description": "How many contracts are denominated in each currency.",
        "chart": "bar",
        "sql": "SELECT cu.currency, COUNT(*) AS contracts\n"
        "FROM contract c JOIN currency cu ON cu.currency_id = c.currency_id\n"
        "WHERE c.is_deleted = 0 GROUP BY cu.currency_id ORDER BY contracts DESC",
    },
    {
        "id": "currency_totals",
        "group": "Capital & currency",
        "title": "Total capital by currency",
        "description": "Amounts are historical text, so sums are approximate.",
        "chart": "bar",
        "sql": "SELECT cu.currency, COUNT(*) AS contracts, ROUND(SUM(CAST(c.total AS REAL))) AS total_capital\n"
        "FROM contract c JOIN currency cu ON cu.currency_id = c.currency_id\n"
        "WHERE c.is_deleted = 0 AND c.total GLOB '[0-9]*'\n"
        "GROUP BY cu.currency_id ORDER BY total_capital DESC",
    },
    {
        "id": "location_frequency",
        "group": "Geography",
        "title": "Most-frequent places of activity",
        "description": "How many contracts record each place as a place of activity. Partners' residences and places of origin are not counted here.",
        "chart": "bar",
        "overlapping": "One contract can name several places.",
        "sql": "SELECT p.place_name, COUNT(*) AS contracts\n"
        "FROM contract_place cp JOIN place p ON p.place_id = cp.place_id\n"
        "JOIN contract c ON c.contract_id = cp.contract_id AND c.is_deleted = 0\n"
        "WHERE cp.is_deleted = 0 GROUP BY cp.place_id ORDER BY contracts DESC",
    },
    {
        "id": "investors_per_contract",
        "group": "Structure",
        "title": "Contracts ranked by number of partners",
        "description": "Partner appearances per contract. A person entered twice on one contract counts twice.",
        "chart": "none",
        "sql": "SELECT i.contract_id, COUNT(*) AS investors FROM investor i\n"
        "JOIN contract c ON c.contract_id = i.contract_id AND c.is_deleted = 0\n"
        "JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "WHERE i.is_deleted = 0 GROUP BY i.contract_id ORDER BY investors DESC",
    },
    {
        "id": "acts_per_contract",
        "group": "Structure",
        "title": "Contracts ranked by number of sub-contracts",
        "description": "Each contract with its count of sub-contracts (terminations, variations, …).",
        "chart": "none",
        "sql": "SELECT s.main_contract_id AS contract_id, COUNT(*) AS sub_contracts FROM sub_contract s\n"
        "JOIN contract c ON c.contract_id = s.main_contract_id AND c.is_deleted = 0\n"
        "WHERE s.is_deleted = 0 GROUP BY s.main_contract_id ORDER BY sub_contracts DESC",
    },
    {
        "id": "investments_by_role",
        "group": "Structure",
        "title": "Stakes by role (general partner vs external investor)",
        "description": "A stake shared by several partners counts once.",
        "chart": "bar",
        "sql": "SELECT v.type AS role, COUNT(*) AS investments FROM investment v\n"
        "JOIN contract c ON c.contract_id = v.contract_id AND c.is_deleted = 0\n"
        "WHERE v.is_deleted = 0 AND v.type IN ('gp', 'lp') GROUP BY v.type",
    },
    {
        "id": "women_investors",
        "group": "People & gender",
        "title": "Women partners",
        "description": "Each woman recorded as a partner, one row per woman and contract.",
        "chart": "none",
        "sql": "SELECT DISTINCT c.contract_id, CAST(substr(c.registration_date, 1, 4) AS INTEGER) AS year,\n"
        "       pe.first_name, pe.last_name\n"
        "FROM person pe JOIN investor i ON i.person_id = pe.person_id\n"
        "JOIN contract c ON c.contract_id = i.contract_id\n"
        "WHERE pe.is_woman = 1 AND pe.is_deleted = 0 AND i.is_deleted = 0 AND c.is_deleted = 0\n"
        "ORDER BY pe.last_name, pe.first_name",
    },
    {
        "id": "ec_discretion_yes",
        "group": "Structure",
        "title": "Economic activity 'at discretion'",
        "description": "Contracts where the economic activity was left to the partners' discretion.",
        "chart": "none",
        "sql": "SELECT contract_id, registration_date, firm_name FROM contract\n"
        "WHERE is_deleted = 0 AND ec_discretion = 1 ORDER BY contract_id",
    },
    {
        "id": "no_place",
        "group": "Data quality",
        "title": "Contracts with no place of activity",
        "description": "",
        "chart": "none",
        "sql": "SELECT c.contract_id, c.registration_date, c.firm_name\n"
        "FROM contract c LEFT JOIN contract_place cp ON cp.contract_id = c.contract_id AND cp.is_deleted = 0\n"
        "WHERE c.is_deleted = 0 AND cp.place_id IS NULL ORDER BY c.contract_id",
    },
    {
        "id": "no_investors",
        "group": "Data quality",
        "title": "Contracts with no partners",
        "description": "",
        "chart": "none",
        "sql": "SELECT c.contract_id, c.registration_date, c.firm_name\n"
        "FROM contract c\n"
        "WHERE c.is_deleted = 0 AND c.contract_id NOT IN (SELECT i.contract_id FROM investor i\n"
        "  JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "  WHERE i.is_deleted = 0)\n"
        "ORDER BY c.contract_id",
    },
    {
        "id": "no_economic_activity",
        "group": "Data quality",
        "title": "Contracts with no economic activity",
        "description": "",
        "chart": "none",
        "sql": "SELECT contract_id, registration_date, firm_name FROM contract\n"
        "WHERE is_deleted = 0 AND (economic_sector = 0 OR economic_sector IS NULL) ORDER BY contract_id",
    },
    # ---- queries beyond the visual builder, kept here as named saved SQL ----
    {
        "id": "jewish_all",
        "group": "People & gender",
        "title": "Contracts where all partners are Jewish (recorded or attributed)",
        "description": "Every partner is Jewish, either as stated in the act or by the "
        "data-entry team's editorial attribution.",
        "notes": _JEWISH_PROVENANCE_NOTE,
        "chart": "none",
        "sql": _jewish_list_sql("j.neither = 0"),
    },
    {
        "id": "jewish_mixed",
        "group": "People & gender",
        "title": "Contracts with mixed Jewish & non-Jewish partners (recorded or attributed)",
        "description": "At least one Jewish partner (stated in the act or editorially attributed) "
        "and at least one partner recorded as neither.",
        "notes": _JEWISH_PROVENANCE_NOTE + " Contracts 4459 and 4817 are listed only because their other "
        "partner is entered as MOLTEPLICI AZIONARI, the data-entry team's placeholder for shareholders the act "
        "does not name "
        "(checked 27 October 2026).",
        "chart": "none",
        "sql": _jewish_list_sql("j.neither > 0"),
    },
    {
        "id": "gp_lp_per_contract",
        "group": "Structure",
        "title": "General partners and external investors per contract (fewest, average, most)",
        "description": "Each person counts once, even when several share one stake or one person holds several.",
        "chart": "none",
        # Partners are PEOPLE reached through investor_group, never investment rows:
        # a joint tranche has N co-holders (counting rows collapsed them to 1), one
        # person can hold two tranches of a role (rows counted them twice), and an
        # unattached/orphan investment has no partner at all (rows counted it as 1).
        "sql": "SELECT 'gp' AS role, MIN(n) AS min, ROUND(AVG(n), 2) AS avg, MAX(n) AS max, COUNT(*) AS contracts\n"
        "FROM (SELECT v.contract_id, COUNT(DISTINCT i.person_id) AS n\n"
        "      FROM investment v\n"
        "      JOIN investor_group ig ON ig.investment_id = v.investment_id AND ig.is_deleted = 0\n"
        "      JOIN investor i ON i.investor_id = ig.investor_id AND i.is_deleted = 0\n"
        "      JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "      JOIN contract c ON c.contract_id = v.contract_id AND c.is_deleted = 0\n"
        "      WHERE v.is_deleted = 0 AND v.type = 'gp' GROUP BY v.contract_id)\n"
        "UNION ALL\n"
        "SELECT 'lp', MIN(n), ROUND(AVG(n), 2), MAX(n), COUNT(*)\n"
        "FROM (SELECT v.contract_id, COUNT(DISTINCT i.person_id) AS n\n"
        "      FROM investment v\n"
        "      JOIN investor_group ig ON ig.investment_id = v.investment_id AND ig.is_deleted = 0\n"
        "      JOIN investor i ON i.investor_id = ig.investor_id AND i.is_deleted = 0\n"
        "      JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "      JOIN contract c ON c.contract_id = v.contract_id AND c.is_deleted = 0\n"
        "      WHERE v.is_deleted = 0 AND v.type = 'lp' GROUP BY v.contract_id)",
    },
    {
        "id": "relatives_in_contract",
        "group": "People & gender",
        "title": "Possible relatives in the same contract",
        "description": "Partners who share a last name with another person in the same contract; or, where "
        "either has no last name, a father's name; or where one partner's husband has the other's last name. "
        "Grandfathers are shown, never matched.",
        "notes": "This is the rule of the project's own query “SQL relatives new”, kept until the question of "
        "what counts as evidence of a family tie (decision 12d) is answered. A shared name is a lead to read "
        "in the act, never a family tie by itself.",
        "chart": "none",
        # FT's "SQL relatives new" (SQL formulas/), re-authored: two different
        # person entries; last name; father's name only when a last name is
        # missing; a husband's last name against the other's last name. An
        # empty name is read as missing, as elsewhere in her rule (missing names
        # are NULL or '' in this corpus); today the list holds exactly the
        # person entries her query lists. One row per person entry: two
        # entries with the same name on one contract stay two rows.
        "sql": "SELECT DISTINCT i1.contract_id, p1.person_id, p1.last_name, p1.father_mother, p1.grandfather,\n"
        "       p1.first_name, i1.husband_last_name\n"
        "FROM investor i1 JOIN person p1 ON p1.person_id = i1.person_id\n"
        "JOIN investor i2 ON i2.contract_id = i1.contract_id AND i2.person_id <> i1.person_id\n"
        "JOIN person p2 ON p2.person_id = i2.person_id\n"
        "WHERE i1.is_deleted = 0 AND i2.is_deleted = 0 AND p1.is_deleted = 0 AND p2.is_deleted = 0\n"
        "  AND EXISTS (SELECT 1 FROM contract c WHERE c.contract_id = i1.contract_id AND c.is_deleted = 0)\n"
        "  AND ((COALESCE(p1.last_name, '') <> '' AND p1.last_name = p2.last_name)\n"
        "    OR ((COALESCE(p1.last_name, '') = '' OR COALESCE(p2.last_name, '') = '')\n"
        "        AND COALESCE(p1.father_mother, '') <> '' AND p1.father_mother = p2.father_mother)\n"
        "    OR (COALESCE(i1.husband_last_name, '') <> '' AND i1.husband_last_name = p2.last_name)\n"
        "    OR (COALESCE(i2.husband_last_name, '') <> '' AND p1.last_name = i2.husband_last_name))\n"
        "ORDER BY i1.contract_id, p1.last_name, p1.person_id",
    },
    {
        "id": "pct_via_proxy",
        "group": "People & gender",
        "title": "Share of partners acting via proxy",
        "description": "Percentage of partner appearances recorded as acting through a proxy.",
        "chart": "none",
        "sql": "SELECT ROUND(100.0 * SUM(CASE WHEN i.via_proxy = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_via_proxy,\n"
        "       COUNT(*) AS total_investors\n"
        "FROM investor i JOIN contract c ON c.contract_id = i.contract_id AND c.is_deleted = 0\n"
        "JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "WHERE i.is_deleted = 0",
    },
    {
        "id": "via_proxy_over_time",
        "group": "Trends over time",
        "title": "Partners acting via proxy, by decade",
        "description": "Share of partner appearances acting through a proxy, by decade "
        "(decades with ≥30 partner appearances).",
        "chart": "line",
        "sql": "SELECT CAST(substr(c.registration_date, 1, 3) || '0' AS INTEGER) AS decade,\n"
        "       COUNT(*) AS investors,\n"
        "       ROUND(100.0 * SUM(CASE WHEN i.via_proxy = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_via_proxy\n"
        "FROM investor i JOIN contract c ON c.contract_id = i.contract_id\n"
        "JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "WHERE i.is_deleted = 0 AND c.is_deleted = 0 AND c.registration_date NOT IN ('', '0000-00-00')\n"
        "GROUP BY decade HAVING COUNT(*) >= 30 ORDER BY decade",
    },
    {
        "id": "jewish_over_time",
        "group": "Trends over time",
        "title": "Jewish partners (recorded or attributed), by decade",
        "description": "Share of partner appearances by Jewish partners, stated in the act or editorially "
        "attributed (decades with ≥30 partner appearances).",
        "chart": "line",
        "sql": "SELECT CAST(substr(c.registration_date, 1, 3) || '0' AS INTEGER) AS decade,\n"
        "       COUNT(*) AS investors,\n"
        "       ROUND(100.0 * SUM(CASE WHEN i.is_jewish = 1 OR i.jewish_db = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_jewish\n"
        "FROM investor i JOIN contract c ON c.contract_id = i.contract_id\n"
        "JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "WHERE i.is_deleted = 0 AND c.is_deleted = 0 AND c.registration_date NOT IN ('', '0000-00-00')\n"
        "GROUP BY decade HAVING COUNT(*) >= 30 ORDER BY decade",
    },
    {
        "id": "jewish_provenance_over_time",
        "group": "Trends over time",
        "title": "Jewish partners by provenance, by decade",
        "description": "The same share split by how we know: stated in the act vs. the data-entry "
        "team's editorial attribution (its basis is not recorded for each partner). Decades with ≥30 partner appearances.",
        "chart": "multiline",
        "sql": "SELECT CAST(substr(c.registration_date, 1, 3) || '0' AS INTEGER) AS decade,\n"
        "       ROUND(100.0 * SUM(CASE WHEN i.is_jewish = 1 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_stated_in_act,\n"
        "       ROUND(100.0 * SUM(CASE WHEN i.jewish_db = 1 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_editorial_attribution\n"
        "FROM investor i JOIN contract c ON c.contract_id = i.contract_id\n"
        "JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "WHERE i.is_deleted = 0 AND c.is_deleted = 0 AND c.registration_date NOT IN ('', '0000-00-00')\n"
        "GROUP BY decade HAVING COUNT(*) >= 30 ORDER BY decade",
    },
    {
        "id": "joint_over_time",
        "group": "Trends over time",
        "title": "Partners holding a stake jointly, by decade",
        "description": "Share of partner appearances on a jointly held stake: one shared with another person, "
        "or recorded as joint on the partner's own entry (decades with ≥30 partner appearances).",
        "chart": "line",
        # Joint per docs/data_quality/is_joint.md: (live distinct-person share
        # count > 1) OR the stored flag. The flag alone misses shared stakes
        # whose siblings were never flagged; the structure alone misses joint
        # stakes entered as parallel rows. Neither is trusted alone. A flag on
        # an appearance with no live stake counts for nothing, as in the
        # Partners table (is_joint.md: the orphan flag is wrong).
        "sql": "WITH shared AS (\n"
        "  SELECT ig.investment_id FROM investor_group ig\n"
        "  JOIN investment v ON v.investment_id = ig.investment_id AND v.is_deleted = 0\n"
        "  JOIN investor i2 ON i2.investor_id = ig.investor_id AND i2.is_deleted = 0\n"
        "  JOIN person p2 ON p2.person_id = i2.person_id AND p2.is_deleted = 0\n"
        "  WHERE ig.is_deleted = 0\n"
        "  GROUP BY ig.investment_id HAVING COUNT(DISTINCT i2.person_id) > 1),\n"
        "on_shared AS (\n"
        "  SELECT DISTINCT ig.investor_id FROM investor_group ig\n"
        "  JOIN shared s ON s.investment_id = ig.investment_id WHERE ig.is_deleted = 0),\n"
        "on_stake AS (\n"
        "  SELECT DISTINCT ig.investor_id FROM investor_group ig\n"
        "  JOIN investment v ON v.investment_id = ig.investment_id AND v.is_deleted = 0\n"
        "  WHERE ig.is_deleted = 0)\n"
        "SELECT CAST(substr(c.registration_date, 1, 3) || '0' AS INTEGER) AS decade,\n"
        "       COUNT(*) AS investors,\n"
        "       ROUND(100.0 * SUM(CASE WHEN os.investor_id IS NOT NULL\n"
        "                               OR (i.is_joint = 1 AND st.investor_id IS NOT NULL) THEN 1 ELSE 0 END)\n"
        "             / COUNT(*), 1) AS pct_joint\n"
        "FROM investor i JOIN contract c ON c.contract_id = i.contract_id\n"
        "JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0\n"
        "LEFT JOIN on_shared os ON os.investor_id = i.investor_id\n"
        "LEFT JOIN on_stake st ON st.investor_id = i.investor_id\n"
        "WHERE i.is_deleted = 0 AND c.is_deleted = 0 AND c.registration_date NOT IN ('', '0000-00-00')\n"
        "GROUP BY decade HAVING COUNT(*) >= 30 ORDER BY decade",
    },
    {
        "id": "funding_composition",
        "group": "Capital & currency",
        "title": "How capital was itemized (cash vs non-cash)",
        "description": "Each contract's cash contributions against its stated capital, and whether a "
        "non-cash contribution is recorded.",
        # Reading notes live beside the chart, not in the menu (UI review 2026-09-10).
        "notes": "A non-cash contribution (goods, a shop, receivables) is sometimes valued inside the "
        "cash sum itself. A shortfall of cash can mean a contribution in goods, capital supplied as "
        "credit, the rounding of old fractional money, or a share the clerk never itemized; only the "
        "act says which. Cash above the stated total is shown apart: there a recorded figure may be wrong "
        "(the entry for contract 2441 gives a total of 10 scudi beside 10,000 scudi in cash; the summary "
        "of the act reads scudi 10.000). Every comparison stays "
        "inside one contract, so currencies never mix.",
        "chart": "bar",
        # The buckets are ARITHMETIC (cash vs stated total) crossed with whether the
        # source records a non-cash component (investment_non_cash text; '' and the
        # literal '0' are no-value sentinels). A shortfall is never, by itself,
        # labeled as goods/labour — that was code-review finding B3.
        # Cash above the stated total is its own bucket (review 2026-10-27,
        # 4.2): it was filed under "covers", though some are entry errors.
        "sql": "SELECT CASE bucket\n"
        "         WHEN 1 THEN 'cash equals the stated total'\n"
        "         WHEN 2 THEN 'cash equals the total — non-cash component noted'\n"
        "         WHEN 3 THEN 'cash exceeds the stated total'\n"
        "         WHEN 4 THEN 'cash short of the total — non-cash recorded'\n"
        "         WHEN 5 THEN 'cash short of the total — remainder unitemized'\n"
        "         WHEN 6 THEN 'no cash recorded — non-cash recorded'\n"
        "         ELSE 'no cash recorded — nothing itemized'\n"
        "       END AS itemization,\n"
        "       COUNT(*) AS contracts\n"
        "FROM (SELECT CASE\n"
        "               WHEN cash = total AND nc = 0 THEN 1\n"
        "               WHEN cash = total THEN 2\n"
        "               WHEN cash > total THEN 3\n"
        "               WHEN cash > 0 AND nc > 0 THEN 4\n"
        "               WHEN cash > 0 THEN 5\n"
        "               WHEN nc > 0 THEN 6\n"
        "               ELSE 7 END AS bucket\n"
        "      FROM (SELECT c.total, COALESCE(SUM(iv.investment_cash), 0) AS cash,\n"
        "                   SUM(CASE WHEN TRIM(COALESCE(iv.investment_non_cash, '')) NOT IN ('', '0') THEN 1 ELSE 0 END) AS nc\n"
        "            FROM contract c JOIN investment iv ON iv.contract_id = c.contract_id AND iv.is_deleted = 0\n"
        "            WHERE c.is_deleted = 0 AND c.total > 0 GROUP BY c.contract_id))\n"
        "GROUP BY bucket ORDER BY bucket",
    },
    {
        "id": "initial_term",
        "group": "Structure",
        "title": "Stated initial term (months)",
        "description": "Distribution of the stated initial term in months — the contracted term, not realised "
        "duration (80% of these contracts carry an automatic-renewal clause).",
        "chart": "bar",
        "sql": "SELECT duration_months AS initial_term_months, COUNT(*) AS contracts\n"
        "FROM contract WHERE is_deleted = 0 AND duration_months > 0\n"
        "GROUP BY duration_months ORDER BY contracts DESC, initial_term_months",
    },
    {
        "id": "later_acts_by_type",
        "group": "Structure",
        "title": "Sub-contracts by type",
        "description": "Registered sub-contracts by type; a contract may have several or none.",
        "chart": "bar",
        "sql": "SELECT s.sub_type, COUNT(*) AS sub_contracts\n"
        "FROM sub_contract s JOIN contract c ON c.contract_id = s.main_contract_id AND c.is_deleted = 0\n"
        "WHERE s.is_deleted = 0 AND s.sub_type NOT IN ('', '0') AND s.sub_type IS NOT NULL\n"
        "GROUP BY s.sub_type ORDER BY sub_contracts DESC",
    },
]


@app.get("/api/analysis/library")
def analysis_library() -> dict[str, Any]:
    return {"queries": ANALYSIS_LIBRARY}


class AnalysisRun(BaseModel):
    sql: str = ""


@app.post("/api/analysis/run")
def analysis_run(payload: AnalysisRun) -> dict[str, Any]:
    return run_readonly_sql(payload.sql)


class AnalysisExport(BaseModel):
    sql: str = ""
    spec: dict[str, Any] | None = None  # a builder question, in place of SQL


@app.post("/api/analysis/export")
def analysis_export(payload: AnalysisExport) -> StreamingResponse:
    if payload.spec is not None:
        sql, params, _chart = analysis_builder_sql(payload.spec)
        return export_readonly_csv(sql, tuple(params))
    return export_readonly_csv(payload.sql)


# ---- Guided builder: a structured spec → whitelisted, parameterised SQL ----
# The frontend never sends SQL here — only enum keys. Every fragment is from these
# whitelists; filter VALUES are bound as parameters. So the builder is injection-proof
# and still runs through the same read-only executor.
ANALYSIS_SUBJECTS: dict[str, dict[str, Any]] = {
    "contracts": {
        "from": "contract c",
        "where": ["c.is_deleted = 0"],
        "list_select": "c.contract_id, c.registration_date AS date, c.firm_name",
        "list_order": "c.contract_id",
    },
    "investors": {
        "from": (
            "investor i JOIN person pe ON pe.person_id = i.person_id "
            "JOIN contract c ON c.contract_id = i.contract_id"
        ),
        "where": ["i.is_deleted = 0", "c.is_deleted = 0", "pe.is_deleted = 0"],
        "list_select": "c.contract_id, CAST(substr(c.registration_date,1,4) AS INTEGER) AS year, pe.first_name, pe.last_name",
        "list_order": "pe.last_name, pe.first_name",
    },
}

# A contract with a usable registration date. Undated contracts (stored
# 0000-00-00) are left out of the year filters exactly as they are left out of
# the year and decade groupings, so one question never has two totals.
_DATED_CONTRACT = "c.registration_date NOT IN ('', '0000-00-00', 'NULL') AND c.registration_date IS NOT NULL"

# key -> (subjects, where-fragment, takes_value, value_kind)
ANALYSIS_FILTERS: dict[str, tuple[set[str], str, bool, str | None]] = {
    "reg_year_from": (
        {"contracts", "investors"},
        f"{_DATED_CONTRACT} AND CAST(substr(c.registration_date,1,4) AS INTEGER) >= ?",
        True,
        "year",
    ),
    "reg_year_to": (
        {"contracts", "investors"},
        f"{_DATED_CONTRACT} AND CAST(substr(c.registration_date,1,4) AS INTEGER) <= ?",
        True,
        "year",
    ),
    "place_is": (
        {"contracts"},
        "EXISTS (SELECT 1 FROM contract_place cp JOIN place p ON p.place_id=cp.place_id "
        "WHERE cp.contract_id=c.contract_id AND cp.is_deleted=0 AND p.place_name = ?)",
        True,
        None,
    ),
    "activity_contains": (
        {"contracts"},
        "EXISTS (SELECT 1 FROM economic_activity ea WHERE ea.ec_activity_id=c.economic_sector AND ea.activity LIKE ?)",
        True,
        "like",
    ),
    "currency_is": (
        {"contracts"},
        "EXISTS (SELECT 1 FROM currency cu WHERE cu.currency_id=c.currency_id AND cu.currency = ?)",
        True,
        None,
    ),
    "register_is": ({"contracts"}, "TRIM(c.folder) = ?", True, None),
    "ec_discretion_yes": ({"contracts"}, "c.ec_discretion = 1", False, None),
    "no_place": (
        {"contracts"},
        "NOT EXISTS (SELECT 1 FROM contract_place cp WHERE cp.contract_id=c.contract_id AND cp.is_deleted=0)",
        False,
        None,
    ),
    "no_investors": (
        {"contracts"},
        "c.contract_id NOT IN (SELECT iv.contract_id FROM investor iv "
        "JOIN person pv ON pv.person_id=iv.person_id AND pv.is_deleted=0 WHERE iv.is_deleted=0)",
        False,
        None,
    ),
    "gender_women": ({"investors"}, "pe.is_woman = 1", False, None),
    "gender_men": ({"investors"}, "pe.is_woman = 0", False, None),
    "role_gp": (
        {"investors"},
        "EXISTS (SELECT 1 FROM investor_group ig JOIN investment inv ON inv.investment_id=ig.investment_id "
        "WHERE ig.investor_id=i.investor_id AND ig.is_deleted=0 AND inv.is_deleted=0 AND inv.type='gp')",
        False,
        None,
    ),
    "role_lp": (
        {"investors"},
        "EXISTS (SELECT 1 FROM investor_group ig JOIN investment inv ON inv.investment_id=ig.investment_id "
        "WHERE ig.investor_id=i.investor_id AND ig.is_deleted=0 AND inv.is_deleted=0 AND inv.type='lp')",
        False,
        None,
    ),
    "via_proxy": ({"investors"}, "i.via_proxy = 1", False, None),
    "jewish": ({"investors"}, "(i.is_jewish = 1 OR i.jewish_db = 1)", False, None),
    # The partner's own title and profession only. The husband's, father's and
    # grandfather's title fields describe someone else and are never searched.
    "title_contains": (
        {"investors"},
        "EXISTS (SELECT 1 FROM title t WHERE t.title_id=i.title AND t.title_name LIKE ?)",
        True,
        "like",
    ),
    "profession_contains": ({"investors"}, "i.profession LIKE ?", True, "like"),
    "title_or_profession_contains": (
        {"investors"},
        "(i.profession LIKE ? OR EXISTS (SELECT 1 FROM title t WHERE t.title_id=i.title AND t.title_name LIKE ?))",
        True,
        "like",
    ),
}

# key -> (subjects, expr, label, extra_join, chart, dimension_order)
ANALYSIS_GROUPS: dict[str, tuple[set[str], str, str, str, str, bool]] = {
    "reg_year": ({"contracts", "investors"}, "CAST(substr(c.registration_date,1,4) AS INTEGER)", "year", "", "line", True),
    "decade": ({"contracts", "investors"}, "CAST(substr(c.registration_date,1,3)||'0' AS INTEGER)", "decade", "", "bar", True),
    "currency": ({"contracts"}, "cu.currency", "currency", "JOIN currency cu ON cu.currency_id=c.currency_id", "bar", False),
    "register": ({"contracts"}, "TRIM(c.folder)", "register", "", "bar", False),
    "gender": ({"investors"}, "CASE WHEN pe.is_woman=1 THEN 'women' ELSE 'men' END", "gender", "", "bar", False),
}


def build_analysis_sql(
    spec: dict[str, Any],
    currency_links: tuple[dict[str, str], dict[str, list[str]]] | None = None,
    person_links: dict[int, int] | None = None,
    excluded_person_ids: list[int] | None = None,
) -> tuple[str, list[Any], str]:
    # Human-confirmed currency same_as links (Reference worklist): canon_of maps a
    # variant phrase to its canonical, family_of maps any member to the full family.
    # Both empty until links are confirmed — every behavior below then reduces to
    # the plain per-phrase form.
    canon_of, family_of = currency_links or ({}, {})
    person_canon = person_links or {}
    excluded_people = sorted({int(value) for value in (excluded_person_ids or [])})
    subject = spec.get("subject")
    if subject not in ANALYSIS_SUBJECTS:
        raise HTTPException(status_code=400, detail="Unknown subject.")
    sub = ANALYSIS_SUBJECTS[subject]
    measure = spec.get("measure") or "count"
    group = spec.get("group_by") or ""
    from_parts = [sub["from"]]
    where = list(sub["where"])
    params: list[Any] = []
    person_params: list[Any] = []
    has_currency_filter = False

    for entry in spec.get("filters") or []:
        key = entry.get("field") if isinstance(entry, dict) else None
        if key not in ANALYSIS_FILTERS:
            raise HTTPException(status_code=400, detail=f"Unknown filter '{key}'.")
        subjects, fragment, takes_value, kind = ANALYSIS_FILTERS[key]
        if subject not in subjects:
            raise HTTPException(status_code=400, detail=f"Filter '{key}' is not valid for {subject}.")
        if takes_value:
            value = str(entry.get("value") or "").strip()
            if not value:
                raise HTTPException(status_code=400, detail=f"Filter '{key}' needs a value.")
            if key == "currency_is":
                has_currency_filter = True
                family = family_of.get(value)
                if family:
                    # the typed phrase has confirmed spelling variants — match them all
                    marks = ", ".join("?" for _ in family)
                    where.append(
                        "EXISTS (SELECT 1 FROM currency cu WHERE cu.currency_id = c.currency_id "
                        f"AND cu.currency IN ({marks}))"
                    )
                    params.extend(family)
                    continue
            bound: Any = value
            if kind == "like":
                bound = f"%{value}%"
            elif kind == "year":
                try:
                    bound = int(value)
                except ValueError:
                    bound = -1
                if not 0 <= bound <= 9999:
                    raise HTTPException(status_code=400, detail="Enter a year as a number, such as 1600.")
            where.append(fragment)
            params.extend([bound] * fragment.count("?"))
        else:
            where.append(fragment)

    # Capital measures must be currency-scoped (code-review finding B2): amounts in
    # different currencies are never added together. Unscoped with no grouping →
    # the honest per-currency table; unscoped with any other grouping → refuse in
    # plain language (the frontend renders this as the builder hint).
    if (
        measure in ("sum_total", "avg_total")
        and subject == "contracts"
        and not has_currency_filter
        and group != "currency"
    ):
        if group:
            raise HTTPException(status_code=400, detail=(
                "Capital amounts are stated in 315 different currency formulations and cannot "
                "be added together. Add a “Currency is …” filter (e.g. scudi), or group by currency."
            ))
        group = "currency"

    group_expr = group_label = None
    group_params: list[Any] = []
    chart = "none"
    dimension_order = False
    if group:
        if group not in ANALYSIS_GROUPS:
            raise HTTPException(status_code=400, detail="Unknown grouping.")
        g_subjects, group_expr, group_label, g_join, chart, dimension_order = ANALYSIS_GROUPS[group]
        if subject not in g_subjects:
            raise HTTPException(status_code=400, detail=f"Grouping '{group}' is not valid for {subject}.")
        if g_join:
            from_parts.append(g_join)
        if group in ("reg_year", "decade"):
            where.append(_DATED_CONTRACT)
        if group == "currency" and canon_of:
            # Fold confirmed spelling variants into their canonical phrase. The
            # phrases are trusted DB/link values but still bound as parameters;
            # GROUP BY uses the ordinal so the CASE (and its params) appear once.
            cases = " ".join("WHEN ? THEN ?" for _ in canon_of)
            group_expr = f"CASE cu.currency {cases} ELSE cu.currency END"
            for variant in sorted(canon_of):
                group_params.extend([variant, canon_of[variant]])

    if measure in ("sum_total", "avg_total"):
        if subject != "contracts":
            raise HTTPException(status_code=400, detail="Capital measures apply to contracts only.")
        where.append("c.total GLOB '[0-9]*'")

    if measure in ("distinct_people_entered", "distinct_people_reviewed"):
        if subject != "investors":
            raise HTTPException(status_code=400, detail="Counts of people apply to partners only.")
        sensitive = {
            entry.get("field")
            for entry in spec.get("filters") or []
        } & {"gender_women", "gender_men", "jewish", "title_contains", "profession_contains",
             "title_or_profession_contains"}
        if measure == "distinct_people_reviewed" and (group == "gender" or sensitive):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Reviewed identity families cannot yet be combined with the gender grouping or with the "
                    "gender, title, profession or Jewish filters: linked entries may disagree on those attributes. "
                    "Use “Count person entries, as entered” for this question."
                ),
            )
        if excluded_people:
            where.append(
                "i.person_id NOT IN (" + ",".join("?" for _ in excluded_people) + ")"
            )
            params.extend(excluded_people)

    from_clause = " ".join(from_parts)
    where_clause = " AND ".join(f"({w})" for w in where)

    if measure == "list":
        # No LIMIT here: the executor's screen cap applies and reports the true
        # total (17,495, not 5,000), and the download (export_readonly_csv)
        # writes every row.
        sql = f"SELECT {sub['list_select']} FROM {from_clause} WHERE {where_clause} ORDER BY {sub['list_order']}"
        return sql, params, "none"

    if measure in ("sum_total", "avg_total"):
        agg = "SUM" if measure == "sum_total" else "AVG"
        m_expr = f"ROUND({agg}(CAST(c.total AS REAL)))"
        # The average is not a total: name its column for what it holds.
        m_alias = "total_capital" if measure == "sum_total" else "average_capital"
        if group:
            order = f"{group_label} ASC" if dimension_order else f"{m_alias} DESC"
            sql = (
                f"SELECT {group_expr} AS {group_label}, COUNT(*) AS contracts, {m_expr} AS {m_alias} "
                f"FROM {from_clause} WHERE {where_clause} GROUP BY 1 ORDER BY {order}"
            )
            # the group expression appears (textually) before the WHERE params
            return sql, group_params + params, chart
        return f"SELECT {m_expr} AS {m_alias}, COUNT(*) AS contracts FROM {from_clause} WHERE {where_clause}", params, "none"

    if measure in ("distinct_people_entered", "distinct_people_reviewed"):
        person_expr = "i.person_id"
        if measure == "distinct_people_reviewed" and person_canon:
            cases = " ".join("WHEN ? THEN ?" for _ in person_canon)
            person_expr = f"CASE i.person_id {cases} ELSE i.person_id END"
            for member in sorted(person_canon):
                person_params.extend([member, person_canon[member]])
        if group:
            order = f"{group_label} ASC" if dimension_order else "people DESC"
            sql = (
                f"SELECT {group_expr} AS {group_label}, "
                f"COUNT(DISTINCT {person_expr}) AS people "
                f"FROM {from_clause} WHERE {where_clause} GROUP BY 1 ORDER BY {order}"
            )
            return sql, group_params + person_params + params, chart
        return (
            f"SELECT COUNT(DISTINCT {person_expr}) AS people "
            f"FROM {from_clause} WHERE {where_clause}",
            person_params + params,
            "none",
        )

    # count
    if group == "gender" and excluded_people:
        # Partner appearances keep institutions, estates, shareholder groups and
        # placeholders among "men" as entered; until register §10c decides how to
        # count them, the group says so (docs/data_quality/non_person_rows.md).
        marks = ",".join("?" for _ in excluded_people)
        sql = (
            f"SELECT CASE WHEN np > 0 THEN {group_label} || ', including ' || np || "
            "CASE WHEN np = 1 THEN ' appearance' ELSE ' appearances' END || "
            "' of institutions, estates, shareholder groups and placeholders' "
            f"ELSE {group_label} END AS {group_label}, n FROM ("
            f"SELECT {group_expr} AS {group_label}, COUNT(*) AS n, "
            f"SUM(CASE WHEN i.person_id IN ({marks}) THEN 1 ELSE 0 END) AS np "
            f"FROM {from_clause} WHERE {where_clause} GROUP BY 1) ORDER BY n DESC"
        )
        return sql, group_params + excluded_people + params, chart
    if group:
        order = f"{group_label} ASC" if dimension_order else "n DESC"
        sql = (
            f"SELECT {group_expr} AS {group_label}, COUNT(*) AS n "
            f"FROM {from_clause} WHERE {where_clause} GROUP BY 1 ORDER BY {order}"
        )
        return sql, group_params + params, chart
    return f"SELECT COUNT(*) AS count FROM {from_clause} WHERE {where_clause}", params, "none"


def currency_link_maps(connection: sqlite3.Connection) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Fold human-confirmed currency same_as links (Reference worklist) into two
    phrase maps: variant → canonical, and any member → the sorted full family.
    Links are stand-off and reversible; this derives from them at read time and
    returns empty maps while no links exist. Chains are followed to their root
    (cycle-safe); families whose phrases no longer resolve are skipped."""
    clog = open_corrections()
    try:
        links = [l for l in corrections_db.active_reference_links(clog, "currency") if l["rel"] == "same_as"]
    finally:
        clog.close()
    if not links:
        return {}, {}
    parent = {int(l["from_id"]): int(l["to_id"]) for l in links}

    def root(i: int) -> int:
        seen = set()
        while i in parent and i not in seen:
            seen.add(i)
            i = parent[i]
        return i

    families: dict[int, set[int]] = {}
    for member in set(parent) | set(parent.values()):
        families.setdefault(root(member), set()).add(member)
    names = {r["currency_id"]: r["currency"] for r in connection.execute("SELECT currency_id, currency FROM currency")}
    canon_of: dict[str, str] = {}
    family_of: dict[str, list[str]] = {}
    for canon_id, members in families.items():
        canon = names.get(canon_id)
        phrases = sorted(names[m] for m in members | {canon_id} if m in names)
        if not canon or len(phrases) < 2:
            continue
        for phrase in phrases:
            family_of[phrase] = phrases
            if phrase != canon:
                canon_of[phrase] = canon
    return canon_of, family_of


def person_link_maps(connection: sqlite3.Connection) -> dict[int, int]:
    """Map active reviewed same-person families to a deterministic read-time id."""
    clog = open_corrections()
    try:
        links = [
            link
            for link in corrections_db.active_reference_links(clog, "person")
            if link["rel"] == "same_as"
        ]
    finally:
        clog.close()
    if not links:
        return {}
    live = {
        int(row["person_id"])
        for row in connection.execute("SELECT person_id FROM person WHERE is_deleted=0")
    }
    parent: dict[int, int] = {}

    def root(value: int) -> int:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = root(parent[value])
        return parent[value]

    for link in links:
        left, right = int(link["from_id"]), int(link["to_id"])
        a, b = root(left), root(right)
        if a != b:
            low, high = sorted((a, b))
            parent[high] = low
    return {
        person_id: root(person_id)
        for person_id in list(parent)
        if person_id in live and root(person_id) != person_id
    }


def non_person_ids(connection: sqlite3.Connection) -> list[int]:
    """Entered person-table rows that are institutions, estates, or placeholders."""
    from workflows.person_features import classify_entity_kind

    result: list[int] = []
    # read by position, so a plain read-only connection serves as well as open_db()
    for person_id, first_name, father_mother, grandfather, last_name in connection.execute(
        """
        SELECT person_id,first_name,father_mother,grandfather,last_name
        FROM person WHERE is_deleted=0
        """
    ):
        if classify_entity_kind(first_name, father_mother, grandfather, last_name) != "person":
            result.append(int(person_id))
    return result


def analysis_builder_sql(payload: dict[str, Any]) -> tuple[str, list[Any], str]:
    """The builder's SQL for a question, with today's confirmed links applied."""
    if (
        payload.get("measure") == "distinct_people_reviewed"
        and not person_linkage_enabled()
    ):
        raise HTTPException(
            status_code=400,
            detail="Reviewed person identities are not enabled in this pilot.",
        )
    connection = open_db()
    try:
        currency_links = currency_link_maps(connection)
        person_links = person_link_maps(connection)
        excluded_people = non_person_ids(connection)
    finally:
        connection.close()
    return build_analysis_sql(
        payload,
        currency_links=currency_links,
        person_links=person_links,
        excluded_person_ids=excluded_people,
    )


@app.post("/api/analysis/build")
def analysis_build(payload: dict[str, Any]) -> dict[str, Any]:
    sql, params, chart = analysis_builder_sql(payload)
    result = run_readonly_sql(sql, tuple(params))
    return {**result, "sql": sql, "chart": chart}


# ---- Find: one set of contracts, asked across contract and partner levels ----
# The Find pane (review 2026-10-27, sections 11-12, build path 12.8, steps 1-2).
# One registry of condition kinds holds each kind's whitelisted SQL and its
# reading in words; the SQL, the sentence, the answer line, the steps of "How
# this was found" and the download's opening lines are all generated from it,
# so what she reads is what ran. The browser sends keys and values only (ids,
# registers, words and years, bound as parameters), never SQL, and everything
# runs on the read-only executor.
#
# Partner conditions are uncorrelated IN / NOT IN subqueries over live rows. The
# correlated EXISTS form took 15.2 s on "a partner who is a woman and a general
# partner" against 0.008 s this way (12.5); tests/test_find.py holds the line.

FIND_UNIT = "Contracts"
FIND_RULE = "Several values in one condition mean any of them; different conditions mean all of them."
FIND_MAX_VALUES = 50
FIND_MAX_CONDITIONS = 12
FIND_MAX_LINE = 8  # conditions about one partner
FIND_MAX_WORD = 40  # characters in one word
# One word of a title or profession: letters and digits, apostrophes inside.
_FIND_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
# A register as TRIM(folder) stores it, such as 10831 or 1263bis.
_FIND_REGISTER = re.compile(r"[0-9A-Za-z]{1,20}")

# Partner appearances that count: a live appearance of a live person. The
# contract's own hiding is tested once, on the outer contract c.
_FIND_PARTNERS = (
    "FROM investor i JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0 "
    "WHERE i.is_deleted = 0"
)

# The partner's name as the record pages write it (person_display_name).
_FIND_NAME = (
    "CASE WHEN TRIM(COALESCE(pe.first_name, '') || ' ' || COALESCE(pe.last_name, '')) <> '' "
    "THEN TRIM(COALESCE(pe.first_name, '') || ' ' || COALESCE(pe.last_name, '')) "
    "WHEN COALESCE(pe.father_mother, '') <> '' THEN 'di ' || pe.father_mother "
    "ELSE 'Unnamed person' END "
    "|| CASE WHEN COALESCE(pe.nickname, '') <> '' THEN ' (detto ' || pe.nickname || ')' ELSE '' END"
)

# Live appearances of live people on live contracts, for the pickers' counts and
# the spellings a word matches.
_FIND_APPEARANCES = (
    "FROM investor i JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0 "
    "JOIN contract c ON c.contract_id = i.contract_id AND c.is_deleted = 0 WHERE i.is_deleted = 0"
)

_FIND_YEAR = "CAST(substr(c.registration_date, 1, 4) AS INTEGER)"
# "; " between names and places, spelled so: the executor refuses any ";" (one statement only).
_FIND_SEP = "char(59, 32)"


class FindChoice(NamedTuple):
    """One choice of a condition: its reading, its SQL (0 or 1, never NULL, so
    "is not" and "every partner" can negate it), and its reading with "is not"."""

    reading: str
    sql: str
    reading_not: str


def _find_role(code: str) -> str:
    # The role lives on the stake (investment.type), reached through a live link.
    return (
        "i.investor_id IN (SELECT ig.investor_id FROM investor_group ig "
        "JOIN investment inv ON inv.investment_id = ig.investment_id AND inv.is_deleted = 0 "
        f"WHERE ig.is_deleted = 0 AND inv.type = '{code}')"
    )


# A partner holds a stake jointly (docs/data_quality/is_joint.md): a live stake
# shared by more than one live person, or the partner's own mark "joint" on an
# appearance that holds a live stake. Neither is trusted alone, and a mark with
# no live stake counts for nothing, as in the library's joint_over_time.
_FIND_JOINT = (
    "(i.investor_id IN (SELECT ig.investor_id FROM investor_group ig WHERE ig.is_deleted = 0 AND ig.investment_id IN ("
    "SELECT g.investment_id FROM investor_group g "
    "JOIN investment inv ON inv.investment_id = g.investment_id AND inv.is_deleted = 0 "
    "JOIN investor i2 ON i2.investor_id = g.investor_id AND i2.is_deleted = 0 "
    "JOIN person p2 ON p2.person_id = i2.person_id AND p2.is_deleted = 0 "
    "WHERE g.is_deleted = 0 GROUP BY g.investment_id HAVING COUNT(DISTINCT i2.person_id) > 1)) "
    "OR (COALESCE(i.is_joint, 0) = 1 AND i.investor_id IN (SELECT ig.investor_id FROM investor_group ig "
    "JOIN investment inv ON inv.investment_id = ig.investment_id AND inv.is_deleted = 0 WHERE ig.is_deleted = 0)))"
)

# Live later acts (sub-contracts); the outer contract's own hiding is tested on c.
_FIND_ACTS = "FROM sub_contract s WHERE s.is_deleted = 0"
# The act types as stored, in the order the figures list them.
FIND_ACT_TYPES = ("termination", "variation", "balance", "renewal")


def _find_act(code: str | None) -> str:
    kind = f" AND s.sub_type = '{code}'" if code else ""
    return f"c.contract_id IN (SELECT s.main_contract_id {_FIND_ACTS}{kind})"


# Conditions on the contract. A kind with "values" is read as its label and the
# names of the values chosen in its picker (ids, registers; None is "not
# recorded", read as "none"); a kind with "choices" is read as the choice.
# "label_not" and "reading_not" are the readings with the verb flipped to "is not".
FIND_CONTRACT_CONDITIONS: dict[str, dict[str, Any]] = {
    "place": {
        "menu": "operating in a place",
        "label": "operating in",
        "label_not": "not operating in",
        "values": "place",
        "noun": "place",
        "pick": "Choose a place.",
        "none": "an unrecorded place",
        # the place of activity as written in the record (contract_place)
        "sql": "c.contract_id IN (SELECT cp.contract_id FROM contract_place cp "
        "WHERE cp.is_deleted = 0 AND cp.place_id IN ({marks}))",
        "none_sql": "c.contract_id NOT IN (SELECT cp.contract_id FROM contract_place cp WHERE cp.is_deleted = 0)",
    },
    "years": {
        "menu": "registered in a range of years",
        "label": "registered",
        # Undated contracts (0000-00-00) are out of any year range, as in the builder.
        "from": f"{_DATED_CONTRACT} AND {_FIND_YEAR} >= ?",
        "to": f"{_DATED_CONTRACT} AND {_FIND_YEAR} <= ?",
    },
    # The phrases as recorded, one at a time: grouping them waits for register 12a.
    "activity": {
        "menu": "whose activity is …",
        "label": "whose activity is",
        "label_not": "whose activity is not",
        "values": "activity",
        "noun": "activity",
        "pick": "Choose an activity.",
        "none": "not recorded",
        "sql": "COALESCE(c.economic_sector, 0) IN ({marks})",
        "none_value": 0,
    },
    "currency": {
        "menu": "whose currency is …",
        "label": "whose currency is",
        "label_not": "whose currency is not",
        "values": "currency",
        "noun": "currency",
        "pick": "Choose a currency.",
        "none": "not recorded",
        "sql": "COALESCE(c.currency_id, 0) IN ({marks})",
        "none_value": 0,
    },
    "register": {
        "menu": "whose register is …",
        "label": "whose register is",
        "label_not": "whose register is not",
        "values": "register",
        "noun": "register",
        "pick": "Choose a register.",
        "none": "not recorded",
        "sql": "TRIM(COALESCE(c.folder, '')) IN ({marks})",
        "none_value": "",
    },
    "discretion": {
        "menu": "with an open formula for the place or the activity (discretion)",
        "choices": {
            "place": FindChoice(
                "with an open formula for the place (such as “e in ogni altro luogo”)",
                "COALESCE(c.pl_discretion, 0) = 1",
                "with no open formula for the place",
            ),
            "activity": FindChoice(
                "with an open formula for the activity (such as “et altre cose”)",
                "COALESCE(c.ec_discretion, 0) = 1",
                "with no open formula for the activity",
            ),
        },
    },
    # Later acts as the database types them (glossary: "Balance / renewal /
    # termination / variation"; their mapping onto the Italian terms is pending).
    "act": {
        "menu": "with a later act (termination, variation, balance or renewal)",
        "choices": {
            "any": FindChoice("with at least one later act (sub-contract)", _find_act(None), "with no later act (sub-contract)"),
            "termination": FindChoice("with a recorded termination", _find_act("termination"), "with no recorded termination"),
            "variation": FindChoice("with a recorded variation", _find_act("variation"), "with no recorded variation"),
            "balance": FindChoice("with a recorded balance", _find_act("balance"), "with no recorded balance"),
            "renewal": FindChoice("with a recorded renewal", _find_act("renewal"), "with no recorded renewal"),
        },
    },
    "partnered": {
        "menu": "with no partners recorded",
        "choices": {
            "no": FindChoice(
                "with no partners recorded",
                f"c.contract_id NOT IN (SELECT i.contract_id {_FIND_PARTNERS})",
                "with at least one partner recorded",
            ),
        },
    },
}

# Conditions on one partner, as the contract conditions.
FIND_PARTNER_CONDITIONS: dict[str, dict[str, Any]] = {
    "jewish": {
        "menu": "is Jewish",
        "choices": {
            "either": FindChoice(
                "is Jewish (stated in the act or editorial attribution)",
                "(COALESCE(i.is_jewish, 0) = 1 OR COALESCE(i.jewish_db, 0) = 1)",
                "is not Jewish (neither stated in the act nor by editorial attribution)",
            ),
            "stated": FindChoice(
                "is Jewish (stated in the act)", "COALESCE(i.is_jewish, 0) = 1", "is not stated Jewish in the act"
            ),
            "attributed": FindChoice(
                "is Jewish (editorial attribution)", "COALESCE(i.jewish_db, 0) = 1", "is not Jewish by editorial attribution"
            ),
        },
    },
    "woman": {
        "menu": "is a woman",
        "choices": {"yes": FindChoice("is a woman", "COALESCE(pe.is_woman, 0) = 1", "is not a woman")},
    },
    "role": {
        "menu": "is a general partner, or an external investor",
        "choices": {
            "gp": FindChoice("is a general partner", _find_role("gp"), "is not a general partner"),
            "lp": FindChoice(
                "is an external investor (accomandante)", _find_role("lp"), "is not an external investor (accomandante)"
            ),
        },
    },
    "proxy": {
        "menu": "acts through a proxy",
        "choices": {"yes": FindChoice("acts through a proxy", "COALESCE(i.via_proxy, 0) = 1", "does not act through a proxy")},
    },
    "joint": {
        "menu": "holds a stake jointly with someone else",
        "choices": {
            "yes": FindChoice(
                "holds a stake jointly (shared with another person, or marked as held jointly)",
                _FIND_JOINT,
                "holds no stake jointly (none shared with another person, none marked as held jointly)",
            )
        },
    },
    "residence": {
        "menu": "lives in a place",
        "label": "lives in",
        "label_not": "does not live in",
        "values": "place",
        "noun": "place",
        "pick": "Choose a place.",
        "none": "an unrecorded place",
        "sql": "COALESCE(i.place_of_residence, 0) IN ({marks})",
        "none_value": 0,
    },
    "origin": {
        "menu": "comes from a place",
        "label": "comes from",
        "label_not": "does not come from",
        "values": "place",
        "noun": "place",
        "pick": "Choose a place.",
        "none": "an unrecorded place",
        "sql": "COALESCE(i.place_of_origin, 0) IN ({marks})",
        "none_value": 0,
    },
    # The partner's own title and profession only: the husband's, father's and
    # grandfather's titles describe someone else and are never searched (12.2).
    # Its SQL is written from the stored spellings each word matches (find_lookups).
    "word": {
        "menu": "has a title or profession containing a word",
        "label": "has a title or profession containing",
        "label_not": "has no title or profession containing",
        "values": "word",
        "noun": "word",
        "pick": "Type a word.",
        "note": "Only the partner's own title and profession are searched, never a husband's, father's or "
        "grandfather's title. Case and accents are ignored; the stored spellings it matched are listed.",
    },
}

# quantifier -> (its reading on the first partner line, on a later line)
FIND_QUANTIFIERS: dict[str, tuple[str, str]] = {
    "any": ("with at least one partner who", "and at least one partner who"),
    "no": ("with no partner who", "and no partner who"),
    "every": ("in which every partner", "and in which every partner"),
}
# A later "at least one" line may be met by the same partner as an earlier line;
# its reading says so, so the two readings are never confused (12.9).
_FIND_SAME_OR_ANOTHER = "and at least one partner, the same or another, who"
# The answers to "about which partner?" when a condition about a partner is
# added beside a partner line: never guessed (12.2).
FIND_PLACEMENTS = {"same": "The same partner", "another": "Another partner, or the same one"}
# Two sets combined (the set history, 12.8 step 4): op -> (opening, the word between them).
FIND_COMBINE: dict[str, tuple[str, str]] = {
    "and": ("that are both", "and"),
    "or": ("that are either", "or"),
    "not": ("that are", "but not"),
}
FIND_MAX_DEPTH = 3  # sets combined within combined sets
_FIND_STATED = FIND_PARTNER_CONDITIONS["jewish"]["choices"]["stated"].sql
_FIND_ATTRIBUTED = FIND_PARTNER_CONDITIONS["jewish"]["choices"]["attributed"].sql
_FIND_NON_PEOPLE = "institutions, estates, shareholder groups and placeholders"
_FIND_ALL = "All contracts"


def _find_kind_words(kind: str, entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "menu": entry["menu"]}
    if "choices" in entry:
        out["choices"] = [
            {"key": key, "reading": choice.reading, "reading_not": choice.reading_not}
            for key, choice in entry["choices"].items()
        ]
    elif "values" in entry:
        out.update({key: entry.get(key) for key in ("label", "label_not", "values", "pick", "none", "note")})
    else:
        out["label"] = entry["label"]
    return out


def find_registry() -> dict[str, Any]:
    """The condition kinds and their readings, for the page's menus and chips."""
    return {
        "unit": FIND_UNIT,
        "rule": FIND_RULE,
        "contract_conditions": [_find_kind_words(k, v) for k, v in FIND_CONTRACT_CONDITIONS.items()],
        "quantifiers": [{"key": k, "reading": first, "later": later} for k, (first, later) in FIND_QUANTIFIERS.items()],
        "same_or_another": _FIND_SAME_OR_ANOTHER,
        "placements": FIND_PLACEMENTS,
        "combine": [{"key": k, "opening": first, "between": between} for k, (first, between) in FIND_COMBINE.items()],
        "partner_conditions": [_find_kind_words(k, v) for k, v in FIND_PARTNER_CONDITIONS.items()],
    }


def _find_int(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().isdigit():
        raise HTTPException(status_code=400, detail=f"Enter {what} as a whole number.")
    number = int(str(value).strip())
    if not low <= number <= high:
        raise HTTPException(status_code=400, detail=f"Enter {what} between {low} and {high}.")
    return number


def _find_fold(text: Any) -> str:
    """How a word and a stored spelling are compared: case, accents and the
    curly apostrophe ignored. For finding only; what is shown stays as stored."""
    return _lookup_norm(text).replace("’", "'")


def _find_values(entry: dict[str, Any], values_in: Any) -> list[Any]:
    """A condition's values in normal form: each once, in order, "not recorded"
    (None) last."""
    if not isinstance(values_in, list) or not values_in:
        raise HTTPException(status_code=400, detail=f"Choose at least one {entry['noun']}.")
    if len(values_in) > FIND_MAX_VALUES:
        raise HTTPException(status_code=400, detail=f"One condition holds up to {FIND_MAX_VALUES} values.")
    kind = entry["values"]
    values: dict[Any, Any] = {}
    none = False
    for value in values_in:
        if value is None:
            if "none" not in entry or entry["none"] is None:
                raise HTTPException(status_code=400, detail=f"Choose a {entry['noun']}.")
            none = True
        elif kind in ("place", "activity", "currency"):
            number = _find_int(value, f"{'an' if kind == 'activity' else 'a'} {kind} number", 1, 10**9)
            values[number] = number
        elif kind == "register":
            text = value.strip() if isinstance(value, str) else str(value) if isinstance(value, int) and not isinstance(value, bool) else ""
            if not _FIND_REGISTER.fullmatch(text):
                raise HTTPException(status_code=400, detail="Enter a register as it is written, such as 10831.")
            values[text] = text
        else:
            text = value.strip() if isinstance(value, str) else ""
            if len(text) > FIND_MAX_WORD or not _FIND_WORD.fullmatch(text):
                raise HTTPException(status_code=400, detail="Enter one word, in letters, such as mercante.")
            values.setdefault(_find_fold(text), text)
    ordered = [values[key] for key in sorted(values)]
    return ordered + [None] if none else ordered


def _find_not(condition: dict[str, Any]) -> bool:
    negated = condition.get("not", False)
    if not isinstance(negated, bool):
        raise HTTPException(status_code=400, detail="A condition is either “is” or “is not”.")
    return negated


def _find_condition(entry: dict[str, Any], condition: dict[str, Any]) -> dict[str, Any]:
    """One condition with values or a choice, in normal form."""
    if "values" in entry:
        return {"kind": condition["kind"], "not": _find_not(condition), "values": _find_values(entry, condition.get("values"))}
    choices = entry["choices"]
    choice = condition.get("choice") or (next(iter(choices)) if len(choices) == 1 else None)
    if not isinstance(choice, str) or choice not in choices:
        raise HTTPException(status_code=400, detail=f"Choose a value for “{entry['menu']}”.")
    return {"kind": condition["kind"], "not": _find_not(condition), "choice": choice}


def find_normalize(spec: Any, depth: int = 0) -> dict[str, Any]:
    """A Find question read against the registry; anything unknown is refused in words.
    A question may combine two earlier sets (``combine`` and ``sets``)."""
    if not isinstance(spec, dict):
        raise HTTPException(status_code=400, detail="A question must be a set of conditions.")
    contract_in, partners_in = spec.get("contract") or [], spec.get("partners") or []
    if not isinstance(contract_in, list) or not isinstance(partners_in, list):
        raise HTTPException(status_code=400, detail="A question must be a set of conditions.")
    if len(contract_in) + len(partners_in) > FIND_MAX_CONDITIONS:
        raise HTTPException(status_code=400, detail=f"A question holds up to {FIND_MAX_CONDITIONS} conditions.")
    contract: list[dict[str, Any]] = []
    for condition in contract_in:
        kind = condition.get("kind") if isinstance(condition, dict) else None
        if not isinstance(kind, str) or kind not in FIND_CONTRACT_CONDITIONS:
            raise HTTPException(status_code=400, detail=f"Unknown condition '{kind}'.")
        if kind == "years":
            low = condition.get("from")
            high = condition.get("to")
            low = None if low in (None, "") else _find_int(low, "a year", 0, 9999)
            high = None if high in (None, "") else _find_int(high, "a year", 0, 9999)
            if low is None and high is None:
                raise HTTPException(status_code=400, detail="Enter a first year, a last year, or both.")
            if low is not None and high is not None and low > high:
                raise HTTPException(status_code=400, detail="The first year is after the last year.")
            contract.append({"kind": "years", "from": low, "to": high})
        else:
            contract.append(_find_condition(FIND_CONTRACT_CONDITIONS[kind], condition))
    partners: list[dict[str, Any]] = []
    for clause in partners_in:
        quantifier = clause.get("quantifier") if isinstance(clause, dict) else None
        if not isinstance(quantifier, str) or quantifier not in FIND_QUANTIFIERS:
            raise HTTPException(status_code=400, detail="Choose at least one, no, or every partner.")
        conditions_in = clause.get("conditions")
        if not isinstance(conditions_in, list) or not conditions_in:
            raise HTTPException(status_code=400, detail="Say what the partner is.")
        if len(conditions_in) > FIND_MAX_LINE:
            raise HTTPException(status_code=400, detail=f"One partner line holds up to {FIND_MAX_LINE} conditions.")
        conditions = []
        for condition in conditions_in:
            kind = condition.get("kind") if isinstance(condition, dict) else None
            if not isinstance(kind, str) or kind not in FIND_PARTNER_CONDITIONS:
                raise HTTPException(status_code=400, detail=f"Unknown partner condition '{kind}'.")
            conditions.append(_find_condition(FIND_PARTNER_CONDITIONS[kind], condition))
        partners.append({"quantifier": quantifier, "conditions": conditions})
    out: dict[str, Any] = {"contract": contract, "partners": partners}
    if spec.get("combine") is not None or spec.get("sets") is not None:
        combine, sets_in = spec.get("combine"), spec.get("sets")
        if not isinstance(combine, str) or combine not in FIND_COMBINE:
            raise HTTPException(status_code=400, detail="Combine two sets with “and”, “or” or “but not”.")
        if not isinstance(sets_in, list) or len(sets_in) != 2:
            raise HTTPException(status_code=400, detail="Combine exactly two sets.")
        if depth >= FIND_MAX_DEPTH:
            raise HTTPException(status_code=400, detail=f"Sets can be combined up to {FIND_MAX_DEPTH} deep.")
        out["combine"] = combine
        out["sets"] = [find_normalize(s, depth + 1) for s in sets_in]
    return out


def _find_flat(spec: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any], str | None]]:
    """Every condition of a normalized question, combined sets included: (registry,
    condition, the quantifier of its partner line or None)."""
    out = [(FIND_CONTRACT_CONDITIONS, c, None) for c in spec["contract"]]
    out += [(FIND_PARTNER_CONDITIONS, c, line["quantifier"]) for line in spec["partners"] for c in line["conditions"]]
    for sub in spec.get("sets", []):
        out += _find_flat(sub)
    return out


def _marks(values: list[Any]) -> str:
    return ", ".join("?" for _ in values)


def _join_or(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} or {words[-1]}"


def _find_shown(kind: str, value: Any, name: str | None) -> str:
    """A value as the sentence names it: places and registers as stored,
    activities and currencies as stored in quotation marks, and an id whose
    entry is gone from its list (economic_activity 1725, place 116) as such."""
    if name is None:
        return f"entry {value}, missing from the {kind} list"
    return f"“{name}”" if kind in ("activity", "currency") else name


def _find_word_sql(words: list[str], matches: dict[str, dict[str, Any]]) -> tuple[str, list[Any]]:
    """The partner's own title or profession is one of the stored spellings the
    words matched (find_lookups); 0 when none does."""
    missing = [word for word in words if word not in matches]
    if missing:
        raise HTTPException(status_code=400, detail=f"The word “{missing[0]}” was not looked up.")
    titles = sorted({int(t) for word in words for t in matches[word]["titles"]})
    professions = sorted({p for word in words for p in matches[word]["professions"]})
    pieces, params = [], []
    if titles:
        pieces.append(f"COALESCE(i.title, 0) IN ({_marks(titles)})")
        params.extend(titles)
    if professions:
        pieces.append(f"COALESCE(i.profession, '') IN ({_marks(professions)})")
        params.extend(professions)
    if not pieces:
        return "0", []
    return (pieces[0] if len(pieces) == 1 else f"({' OR '.join(pieces)})"), params


def _find_compile(entry: dict[str, Any], condition: dict[str, Any], names: dict[str, Any]) -> tuple[str, list[Any], str]:
    """One condition as (SQL that is 0 or 1, its parameters, its reading)."""
    negated = condition["not"]
    if "choices" in entry:
        choice = entry["choices"][condition["choice"]]
        sql, params, reading = choice.sql, [], choice.reading_not if negated else choice.reading
    else:
        kind = entry["values"]
        values = condition["values"]
        known = [v for v in values if v is not None]
        if kind == "word":
            sql, params = _find_word_sql(known, names.get("words", {}))
            shown = [f"“{word}”" for word in known]
        else:
            lookup = names.get(kind, {})
            missing = [v for v in known if v not in lookup]
            if missing:
                what = "register" if kind == "register" else f"{kind} number"
                raise HTTPException(status_code=400, detail=f"There is no {what} {missing[0]} in the database.")
            shown = [_find_shown(kind, v, lookup[v]) for v in known]
            pieces: list[str] = []
            params = list(known)
            if None in values and "none_value" in entry:
                params.append(entry["none_value"])
            if params:
                pieces.append(entry["sql"].format(marks=_marks(params)))
            if None in values and "none_sql" in entry:
                pieces.append(entry["none_sql"])
            sql = pieces[0] if len(pieces) == 1 else f"({' OR '.join(pieces)})"
        if None in values:
            shown.append(entry["none"])
        reading = f"{entry['label_not'] if negated else entry['label']} {_join_or(shown)}"
    return (f"NOT ({sql})" if negated else sql), params, reading


def build_find_sql(
    spec: Any,
    place_names: dict[int, Any],
    non_person_ids: list[int] | None = None,
    lookups: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Find question as SQL and words. ``place_names`` names every place id the
    question uses; ``non_person_ids`` are the person rows that are institutions,
    estates, shareholder groups or placeholders (non_person_ids()), which "no
    partner who" and "every partner" leave out (12.2); ``lookups`` (find_lookups)
    names the other values and holds the spellings each word matched."""
    spec = find_normalize(spec)
    names: dict[str, Any] = {**(lookups or {}), "place": place_names}
    non_people = sorted({int(v) for v in (non_person_ids or [])})
    where: list[tuple[str, list[Any], bool]] = [("c.is_deleted = 0", [], False)]  # (sql, params, is a year)
    # The parts of the sentence, each with the fragments of `where` it wrote.
    parts: list[dict[str, Any]] = []

    def part(level: str, reading: str, fragments: list[tuple[str, list[Any], bool]]) -> None:
        parts.append({"level": level, "reading": reading, "start": len(where), "end": len(where) + len(fragments)})
        where.extend(fragments)

    marks: list[dict[str, Any]] = []  # what carries the data-entry team's mark "inferred"
    sets: list[dict[str, Any]] = []  # combined sets, for How this was found
    list_select_params: list[Any] = []  # parameters of the List's own columns
    membership: list[str] = []
    if spec.get("combine"):
        built = [build_find_sql(sub, place_names, non_person_ids, lookups) for sub in spec["sets"]]
        opening, between = FIND_COMBINE[spec["combine"]]
        words = [b["sentence"][len(FIND_UNIT):].strip() or "all contracts" for b in built]
        member = [f"c.contract_id IN (SELECT c.contract_id FROM contract c WHERE {b['where_sql']})" for b in built]
        joined = {"and": f"{member[0]} AND {member[1]}", "or": f"({member[0]} OR {member[1]})",
                  "not": f"{member[0]} AND NOT {member[1]}"}[spec["combine"]]
        part("combined", f"{opening} “{words[0]}” {between} “{words[1]}”",
             [(joined, built[0]["total_params"] + built[1]["total_params"], False)])
        sets = [{"sentence": b["sentence"], "total_sql": b["total_sql"], "total_params": b["total_params"]} for b in built]
        if spec["combine"] == "or":
            # which of the two sets each contract is in
            for k, (b, test) in enumerate(zip(built, member), 1):
                membership.append(f"CASE WHEN {test} THEN 'yes' ELSE '' END AS in_set_{k}")
                list_select_params.extend(b["total_params"])
    for condition in spec["contract"]:
        entry = FIND_CONTRACT_CONDITIONS[condition["kind"]]
        if condition["kind"] == "years":
            low, high = condition["from"], condition["to"]
            if low is not None and high is not None:
                reading = f"{entry['label']} from {low} to {high}"
            elif low is not None:
                reading = f"{entry['label']} from {low} on"
            else:
                reading = f"{entry['label']} up to {high}"
            part("contract", reading, [(entry[end], [year], True) for end, year in (("from", low), ("to", high)) if year is not None])
            continue
        sql, params, reading = _find_compile(entry, condition, names)
        part("contract", reading, [(sql, params, False)])
        ids = [v for v in condition.get("values", []) if v is not None]
        if not condition["not"] and ids and condition["kind"] in ("place", "activity"):
            marks.append({"kind": condition["kind"], "ids": ids, "names": [_find_shown(condition["kind"], v, names[condition["kind"]][v]) for v in ids]})

    person_only = f" AND i.person_id NOT IN ({_marks(non_people)})" if non_people else ""
    seen_any = False
    why: list[dict[str, Any]] = []  # the List's columns naming the partners who match
    splits: list[dict[str, Any]] = []  # provenance counts for the answer line
    for number, clause in enumerate(spec["partners"], 1):
        quantifier = clause["quantifier"]
        compiled = [_find_compile(FIND_PARTNER_CONDITIONS[c["kind"]], c, names) for c in clause["conditions"]]
        first, later = FIND_QUANTIFIERS[quantifier]
        opening = (
            first if number == 1 else _FIND_SAME_OR_ANOTHER if quantifier == "any" and seen_any else later
        )
        clause_reading = f"{opening} {' and '.join(reading for _s, _p, reading in compiled)}"
        test = " AND ".join(f"({sql})" for sql, _p, _r in compiled)
        test_params = [p for _s, params, _r in compiled for p in params]
        people = person_only if quantifier != "any" else ""
        params_people = list(non_people) if people else []
        if quantifier == "any":
            fragment = f"c.contract_id IN (SELECT i.contract_id {_FIND_PARTNERS} AND {test})"
            fragment_params: list[Any] = test_params
        elif quantifier == "no":
            fragment = f"c.contract_id NOT IN (SELECT i.contract_id {_FIND_PARTNERS} AND {test}{people})"
            fragment_params = test_params + params_people
        else:
            # every partner (people only) meets the test, and there is at least one
            fragment = (
                f"c.contract_id IN (SELECT i.contract_id {_FIND_PARTNERS}{people}) AND "
                f"c.contract_id NOT IN (SELECT i.contract_id {_FIND_PARTNERS}{people} AND NOT ({test}))"
            )
            fragment_params = params_people + params_people + test_params
        part("partner", clause_reading, [(fragment, fragment_params, False)])
        if quantifier == "no":
            continue
        seen_any = True
        layer_at = next(
            (k for k, c in enumerate(clause["conditions"]) if c["kind"] == "jewish" and not c["not"]), None
        )
        jewish = clause["conditions"][layer_at]["choice"] if layer_at is not None else None
        why.append({
            "number": number, "test": test, "test_params": test_params, "people": people,
            "params": params_people, "jewish": jewish,
        })
        if jewish:
            # Within this set, the contracts whose matching partner is in the other layer.
            others = [(sql, params) for k, (sql, params, _r) in enumerate(compiled) if k != layer_at]
            layer = _FIND_ATTRIBUTED if jewish == "stated" else _FIND_STATED
            other_test = " AND ".join([f"({sql})" for sql, _p in others] + [f"({layer})"])
            splits.append({
                "number": number,
                "choice": jewish,
                "alone": not others,
                "reading": clause_reading,
                "sql": f"c.contract_id IN (SELECT i.contract_id {_FIND_PARTNERS} AND {other_test}{people})",
                "params": [p for _s, params in others for p in params] + params_people,
            })

    sentence = " ".join([FIND_UNIT, ", ".join(p["reading"] for p in parts)]).strip()

    def conjoin(fragments: list[tuple[str, list[Any], bool]]) -> tuple[str, list[Any]]:
        return " AND ".join(f"({sql})" for sql, _p, _y in fragments), [p for _s, params, _y in fragments for p in params]

    where_sql, where_params = conjoin(where)

    # ---- the List: every row, in date order with the undated last ----
    columns = ["c.contract_id", "c.registration_date", "c.firm_name", "pl.places"] + membership
    labels = ["Contract", "Registration date", "Firm name", "Places of activity"] + [
        f"In set {k}" for k in range(1, len(membership) + 1)]
    joins = [
        f"LEFT JOIN (SELECT contract_id, group_concat(place, {_FIND_SEP}) AS places FROM ("
        "SELECT cp.contract_id, p.place_name || CASE WHEN cp.place_db = 1 THEN ' (marked as inferred)' ELSE '' END AS place "
        "FROM contract_place cp JOIN place p ON p.place_id = cp.place_id WHERE cp.is_deleted = 0 "
        "ORDER BY cp.contract_id, p.place_name) GROUP BY contract_id) pl ON pl.contract_id = c.contract_id"
    ]
    join_params: list[Any] = []
    several = len(why) > 1
    for entry in why:
        n = entry["number"]
        suffix = f"_{n}" if several else ""
        tail = f" (line {n})" if several else ""
        alias = f"w{n}"
        layer = "0"
        if entry["jewish"]:
            layer = f"CASE WHEN ({_FIND_STATED}) THEN 1 WHEN ({_FIND_ATTRIBUTED}) THEN 2 ELSE 0 END"
            picks = []
            if entry["jewish"] in ("either", "stated"):
                picks.append((1, f"stated_in_the_act{suffix}", f"Matching partners, stated in the act{tail}"))
            if entry["jewish"] in ("either", "attributed"):
                picks.append((2, f"editorial_attribution{suffix}", f"Matching partners, editorial attribution{tail}"))
            aggregates = ", ".join(
                f"group_concat(CASE WHEN layer = {code} THEN name END, {_FIND_SEP}) AS {name}" for code, name, _l in picks
            )
            for _code, name, label in picks:
                columns.append(f"{alias}.{name}")
                labels.append(label)
        else:
            aggregates = f"group_concat(name, {_FIND_SEP}) AS matching_partners{suffix}"
            columns.append(f"{alias}.matching_partners{suffix}")
            labels.append(f"Matching partners{tail}")
        joins.append(
            f"LEFT JOIN (SELECT contract_id, {aggregates} FROM ("
            f"SELECT DISTINCT i.contract_id, {_FIND_NAME} AS name, {layer} AS layer "
            f"{_FIND_PARTNERS} AND {entry['test']}{entry['people']} ORDER BY 1, 2"
            f") GROUP BY contract_id) {alias} ON {alias}.contract_id = c.contract_id"
        )
        join_params.extend(entry["test_params"] + entry["params"])
    list_sql = (
        f"SELECT {', '.join(columns)}\nFROM contract c\n" + "\n".join(joins) + f"\nWHERE {where_sql}\n"
        f"ORDER BY CASE WHEN {_DATED_CONTRACT} THEN 0 ELSE 1 END, c.registration_date, c.contract_id"
    )

    # ---- the answer line's counts, over the same set ----
    selects = ["COUNT(*) AS total", f"SUM(CASE WHEN {_DATED_CONTRACT} THEN 0 ELSE 1 END) AS undated"]
    select_params: list[Any] = []
    for split in splits:
        selects.append(f"SUM(CASE WHEN {split['sql']} THEN 1 ELSE 0 END) AS split_{split['number']}")
        select_params.extend(split["params"])
    quantifiers = {clause["quantifier"] for clause in spec["partners"]}
    if "no" in quantifiers:
        selects.append(f"SUM(CASE WHEN c.contract_id IN (SELECT i.contract_id {_FIND_PARTNERS}) THEN 0 ELSE 1 END) AS no_partners")
    if quantifiers & {"no", "every"} and non_people:
        selects.append(
            f"SUM(CASE WHEN c.contract_id IN (SELECT i.contract_id {_FIND_PARTNERS} "
            f"AND i.person_id IN ({_marks(non_people)})) THEN 1 ELSE 0 END) AS non_people"
        )
        select_params.extend(non_people)
    counts_sql = f"SELECT {', '.join(selects)} FROM contract c WHERE {where_sql}"

    # Undated contracts that meet everything but the years, which leave them out.
    undated_sql = None
    undated_params: list[Any] = []
    if any(is_year for _sql, _p, is_year in where):
        rest_sql, undated_params = conjoin([f for f in where if not f[2]])
        undated_sql = f"SELECT COUNT(*) FROM contract c WHERE {rest_sql} AND NOT ({_DATED_CONTRACT})"

    # ---- How this was found: the steps, each condition on its own, and what rests on inference ----
    count = "SELECT COUNT(*) FROM contract c WHERE "
    steps = [{"level": "all", "reading": _FIND_ALL, "where": where[0][0], "sql": count + where[0][0], "params": []}]
    for p in parts:
        upto_sql, upto_params = conjoin(where[: p["end"]])
        alone_sql, alone_params = conjoin(where[:1] + where[p["start"]: p["end"]])
        steps.append({
            "level": p["level"], "reading": p["reading"], "where": upto_sql, "sql": count + upto_sql,
            "params": upto_params, "alone_sql": count + alone_sql, "alone_params": alone_params,
        })
    inference: list[dict[str, Any]] = []
    for split in splits:
        if split["choice"] != "stated":
            # the contracts of the set that rest on editorial attribution alone, by half-century
            inference.append({
                "what": "attribution", "number": split["number"], "reading": split["reading"],
                "sql": f"SELECT CASE WHEN {_DATED_CONTRACT} THEN ({_FIND_YEAR} / 50) * 50 END AS half, COUNT(*) "
                f"FROM contract c WHERE {where_sql} AND NOT ({split['sql']}) GROUP BY half ORDER BY half",
                "params": where_params + split["params"],
            })
    for mark in marks:
        if mark["kind"] == "place":
            sql = (
                f"{count}{where_sql} AND c.contract_id IN (SELECT cp.contract_id FROM contract_place cp "
                f"WHERE cp.is_deleted = 0 AND cp.place_db = 1 AND cp.place_id IN ({_marks(mark['ids'])}))"
            )
            params = where_params + mark["ids"]
        else:
            sql, params = f"{count}{where_sql} AND COALESCE(c.economic_sector_db, 0) = 1", list(where_params)
        inference.append({"what": mark["kind"], "names": mark["names"], "sql": sql, "params": params})

    searched: dict[str, dict[str, Any]] = {}
    for line in spec["partners"]:
        for condition in line["conditions"]:
            if condition["kind"] == "word":
                for word in condition["values"]:
                    found = names["words"][word]
                    searched[word] = {
                        "word": word,
                        "titles": sorted(found["titles"].values(), key=_find_fold),
                        "professions": list(found["professions"]),
                    }

    return {
        "spec": spec,
        "sentence": sentence,
        "list_sql": list_sql,
        "list_params": list_select_params + join_params + where_params,
        "labels": labels,
        "counts_sql": counts_sql,
        "counts_params": select_params + where_params,
        "total_sql": count + where_sql,
        "total_params": where_params,
        "where_sql": where_sql,
        "lines": why,
        "sets": sets,
        "undated_sql": undated_sql,
        "undated_params": undated_params,
        "splits": splits,
        "steps": steps,
        "inference": inference,
        "searched": list(searched.values()),
        "quantifiers": sorted(quantifiers),
        "names": _find_names_used(spec, names),
    }


def _find_names_used(spec: dict[str, Any], names: dict[str, Any]) -> dict[str, dict[str, str]]:
    """The stored name of every value the question uses, for the page's chips."""
    used: dict[str, dict[str, str]] = {}
    for registry, condition, _quantifier in _find_flat(spec):
        kind = registry[condition["kind"]].get("values")
        if kind in (None, "word"):
            continue
        for value in condition["values"]:
            if value is not None:
                name = names[kind][value]
                used.setdefault(kind, {})[str(value)] = name if name is not None else _find_shown(kind, value, None)
    return used


_FIND_LIVE_CONTRACTS = "SELECT c.contract_id FROM contract c WHERE c.is_deleted = 0"
# Each list's names, and where an id missing from its list is still in use on a
# live record, so it can be named (economic_activity 1725, place 116).
_FIND_LISTS = {
    "place": (
        "SELECT place_id, place_name FROM place WHERE place_id IN ({marks})",
        "SELECT 1 FROM contract_place cp WHERE cp.is_deleted = 0 AND cp.place_id = :id "
        f"AND cp.contract_id IN ({_FIND_LIVE_CONTRACTS}) "
        f"UNION ALL SELECT 1 {_FIND_APPEARANCES} AND (i.place_of_residence = :id OR i.place_of_origin = :id) LIMIT 1",
    ),
    "activity": (
        "SELECT ec_activity_id, activity FROM economic_activity WHERE ec_activity_id IN ({marks})",
        f"SELECT 1 FROM contract WHERE economic_sector = :id AND contract_id IN ({_FIND_LIVE_CONTRACTS}) LIMIT 1",
    ),
    "currency": (
        "SELECT currency_id, currency FROM currency WHERE currency_id IN ({marks})",
        f"SELECT 1 FROM contract WHERE currency_id = :id AND contract_id IN ({_FIND_LIVE_CONTRACTS}) LIMIT 1",
    ),
}


def find_lookups(connection: sqlite3.Connection, normals: list[dict[str, Any]]) -> dict[str, Any]:
    """What build_find_sql needs from the database for these (normalized)
    questions: the stored name of each value (None for an id whose entry is gone
    from its list but still in use), the registers live contracts hold, the
    stored spellings each word matches among live partners, and the non-person
    rows when "no" or "every" is asked."""
    wanted: dict[str, set[Any]] = {"place": set(), "activity": set(), "currency": set(), "register": set(), "word": set()}
    needs_people = False
    for spec in normals:
        for registry, condition, quantifier in _find_flat(spec):
            needs_people = needs_people or quantifier not in (None, "any")
            kind = registry[condition["kind"]].get("values")
            if kind:
                wanted[kind].update(v for v in condition["values"] if v is not None)
    lookups: dict[str, Any] = {}
    for kind, (names_sql, uses_sql) in _FIND_LISTS.items():
        ids = sorted(wanted[kind])
        found: dict[int, str | None] = {}
        if ids:
            found = {int(r[0]): r[1] for r in connection.execute(names_sql.format(marks=_marks(ids)), ids)}
        for missing in (i for i in ids if i not in found):
            if connection.execute(uses_sql, {"id": missing}).fetchone():
                found[missing] = None
        lookups[kind] = found
    registers: dict[str, str] = {}
    if wanted["register"]:
        stored = {
            r[0] for r in connection.execute(
                f"SELECT DISTINCT TRIM(COALESCE(folder, '')) FROM contract WHERE contract_id IN ({_FIND_LIVE_CONTRACTS})"
            )
        }
        registers = {v: v for v in wanted["register"] if v in stored}
    lookups["register"] = registers
    words: dict[str, dict[str, Any]] = {}
    if wanted["word"]:
        # the partners' own titles and professions, as live partners hold them
        titles = [
            (int(r[0]), r[1])
            for r in connection.execute(f"SELECT t.title_id, t.title_name FROM title t WHERE t.title_id IN (SELECT i.title {_FIND_APPEARANCES})")
        ]
        professions = [
            r[0] for r in connection.execute(
                f"SELECT DISTINCT i.profession {_FIND_APPEARANCES} AND TRIM(COALESCE(i.profession, '')) <> ''"
            )
        ]
        for word in wanted["word"]:
            needle = _find_fold(word)
            words[word] = {
                "titles": {tid: name for tid, name in titles if needle in _find_fold(name)},
                "professions": sorted(p for p in professions if needle in _find_fold(p)),
            }
    lookups["words"] = words
    lookups["non_people"] = non_person_ids(connection) if needs_people else []
    return lookups


def _find_partner_values(column: str) -> str:
    # A place of residence or origin, with the live contracts that have a live partner from it.
    return (
        f"SELECT COALESCE(i.{column}, 0), (SELECT p.place_name FROM place p WHERE p.place_id = i.{column}), "
        f"COUNT(DISTINCT i.contract_id), NULL {_FIND_APPEARANCES} GROUP BY 1"
    )


# Each picker's values, read in one pass: (value, stored name, live contracts
# the condition alone finds, of which the data-entry team marked as inferred).
_FIND_VALUE_SQL = {
    "place": (
        "SELECT cp.place_id, p.place_name, COUNT(DISTINCT cp.contract_id), "
        "COUNT(DISTINCT CASE WHEN cp.place_db = 1 THEN cp.contract_id END) "
        "FROM contract_place cp JOIN contract c ON c.contract_id = cp.contract_id AND c.is_deleted = 0 "
        "LEFT JOIN place p ON p.place_id = cp.place_id WHERE cp.is_deleted = 0 GROUP BY cp.place_id"
    ),
    "residence": _find_partner_values("place_of_residence"),
    "origin": _find_partner_values("place_of_origin"),
    "activity": (
        "SELECT COALESCE(c.economic_sector, 0), ea.activity, COUNT(*), SUM(COALESCE(c.economic_sector_db, 0) = 1) "
        "FROM contract c LEFT JOIN economic_activity ea ON ea.ec_activity_id = c.economic_sector "
        "WHERE c.is_deleted = 0 GROUP BY 1"
    ),
    "currency": (
        "SELECT COALESCE(c.currency_id, 0), cu.currency, COUNT(*), NULL "
        "FROM contract c LEFT JOIN currency cu ON cu.currency_id = c.currency_id WHERE c.is_deleted = 0 GROUP BY 1"
    ),
    "register": (
        "SELECT TRIM(COALESCE(c.folder, '')), NULL, COUNT(*), NULL FROM contract c WHERE c.is_deleted = 0 GROUP BY 1"
    ),
}


def find_values(connection: sqlite3.Connection, kind: str, q: str = "", limit: int = 12) -> list[dict[str, Any]]:
    """A picker's values: the stored spelling, the live contracts the condition
    alone finds with it ("in the corpus") and how many of those carry the
    data-entry team's mark "inferred". Matching ignores case and accents; "not
    recorded" is a value with its count; nothing is grouped."""
    entry = FIND_CONTRACT_CONDITIONS.get(kind) or FIND_PARTNER_CONDITIONS.get(kind)
    if kind not in _FIND_VALUE_SQL or entry is None:
        raise HTTPException(status_code=400, detail=f"There is no list of values for '{kind}'.")
    values_kind = entry["values"]
    none_count = 0
    rows = []
    for value, name, contracts, inferred in connection.execute(_FIND_VALUE_SQL[kind]):
        if "none_value" in entry and value == entry["none_value"]:
            none_count = int(contracts)
            continue
        shown = value if values_kind == "register" else name if name is not None else _find_shown(values_kind, value, None)
        rows.append((value, shown, int(contracts), int(inferred or 0)))
    if "none_sql" in entry:
        # no live place of activity at all, counted by the engine itself
        query = build_find_sql({"contract": [{"kind": kind, "values": [None]}]}, {})
        none_count = int(connection.execute(query["total_sql"], query["total_params"]).fetchone()[0])
    needle = _lookup_norm(q)
    scored = []
    for value, shown, contracts, inferred in rows:
        norm = _lookup_norm(shown)
        if needle and needle not in norm:
            continue
        # the exact spelling first, then the most used
        scored.append((0 if needle and norm == needle else 1, -contracts, norm, value, shown, contracts, inferred))
    scored.sort(key=lambda t: t[:3])
    out = [
        {"value": value, "name": shown, "contracts": contracts, "inferred": inferred}
        for *_rank, value, shown, contracts, inferred in scored[:limit]
    ]
    if none_count and (not needle or "not recorded".startswith(needle)):
        out.append({"value": None, "name": "not recorded", "contracts": none_count, "inferred": 0})
    return out


# ---- Find, step 3: views over the same set (Count, Over time, Compare) ----
# Each view groups or dates the contracts of one question, read through the
# question's own WHERE, so a view never counts a contract the List does not hold.
# Values are named as stored, "not recorded" stays a value, and nothing is merged
# (register 12a, 12c). Capital is not offered here yet: adding totals waits for
# the currency decisions (register §6).

FIND_GROUPINGS: dict[str, dict[str, Any]] = {
    "decade": {"label": "decade of registration"},
    "half": {"label": "half-century of registration"},
    "place": {"label": "place of activity"},
    "activity": {"label": "activity, as recorded"},
    "currency": {"label": "currency, as recorded"},
    "register": {"label": "register"},
    "provenance": {"label": "how the matching partner is known to be Jewish"},
    "role": {"label": "role of the matching partner"},
}
FIND_TOP_ROWS = 20  # a Count table's rows; the rest are "all others"
FIND_TOP_COLUMNS = 8  # its columns
FIND_SERIES = 4  # series on one chart: the categorical palette's safe count
FIND_MAX_BESIDE = 3  # questions set beside this one
_FIND_PERIOD_WIDTH = {"decade": 10, "half": 50}
_FIND_GROUP_EXPR = {
    "activity": ("COALESCE(c.economic_sector, 0)", 0),
    "currency": ("COALESCE(c.currency_id, 0)", 0),
    "register": ("TRIM(COALESCE(c.folder, ''))", ""),
}
_FIND_GROUP_WORDS = {
    "provenance": {"stated": "a matching partner stated Jewish in the act", "attributed": "editorial attribution alone"},
    "role": {"gp": "general partner", "lp": "external investor (accomandante)"},
}


def _find_period(width: int) -> str:
    return f"CASE WHEN {_DATED_CONTRACT} THEN ({_FIND_YEAR} / {width}) * {width} END"


def _find_exec(connection: sqlite3.Connection, sql: str, params: list[Any]) -> list[Any]:
    return connection.execute(_readonly_statement(sql), list(params)).fetchall()


def _find_provenance(query: dict[str, Any]) -> dict[str, Any] | None:
    """The split that tells the matching partner stated Jewish in the act from
    editorial attribution alone: a Jewish condition read as either layer, or as
    attribution."""
    return next((s for s in query["splits"] if s["choice"] != "stated"), None)


def find_groupings(query: dict[str, Any]) -> list[dict[str, str]]:
    """The groupings this question can be counted or split by."""
    out = []
    for key, entry in FIND_GROUPINGS.items():
        if (key == "provenance" and not _find_provenance(query)) or (key == "role" and not query["lines"]):
            continue
        out.append({"key": key, "label": entry["label"]})
    return out


def _find_group_rows(connection: sqlite3.Connection, query: dict[str, Any], key: str) -> tuple[list[int], dict[int, set[Any]]]:
    """The set's contracts, and the values each holds in the grouping (None: not
    recorded, no registration date, or no stake recorded). Place and role can
    give a contract several values."""
    if key not in {g["key"] for g in find_groupings(query)}:
        raise HTTPException(status_code=400, detail=f"This question cannot be grouped by '{key}'.")
    where, params = query["where_sql"], list(query["total_params"])
    members = f"SELECT c.contract_id FROM contract c WHERE {where}"
    ids = [int(r[0]) for r in _find_exec(connection, members, params)]
    if key in _FIND_PERIOD_WIDTH:
        rows = _find_exec(connection, f"SELECT c.contract_id, {_find_period(_FIND_PERIOD_WIDTH[key])} FROM contract c WHERE {where}", params)
    elif key in _FIND_GROUP_EXPR:
        expr, none = _FIND_GROUP_EXPR[key]
        rows = [(cid, None if v == none else v) for cid, v in _find_exec(connection, f"SELECT c.contract_id, {expr} FROM contract c WHERE {where}", params)]
    elif key == "place":
        rows = _find_exec(
            connection,
            f"SELECT cp.contract_id, cp.place_id FROM contract_place cp WHERE cp.is_deleted = 0 AND cp.contract_id IN ({members})",
            params,
        )
    elif key == "provenance":
        split = _find_provenance(query)
        rows = _find_exec(
            connection,
            f"SELECT c.contract_id, CASE WHEN {split['sql']} THEN 'stated' ELSE 'attributed' END FROM contract c WHERE {where}",
            split["params"] + params,
        )
    else:
        # the role on the stake of each partner who meets the first partner line
        line = query["lines"][0]
        matching = f"{_FIND_PARTNERS} AND {line['test']}{line['people']} AND i.contract_id IN ({members})"
        gp, lp = _find_role("gp"), _find_role("lp")
        rows = []
        for value, test in (("gp", gp), ("lp", lp), (None, f"NOT ({gp}) AND NOT ({lp})")):
            found = _find_exec(connection, f"SELECT DISTINCT i.contract_id {matching} AND {test}", line["test_params"] + line["params"] + params)
            rows += [(r[0], value) for r in found]
    values: dict[int, set[Any]] = {i: set() for i in ids}
    for cid, value in rows:
        values[int(cid)].add(value)
    for held in values.values():
        if not held:
            held.add(None)
    return ids, values


def _find_group_names(connection: sqlite3.Connection, key: str, values: set[Any]) -> dict[Any, str]:
    names: dict[Any, str] = {}
    lists = {"place": "place", "activity": "activity", "currency": "currency"}
    if key in lists:
        ids = sorted(v for v in values if v is not None)
        sql = _FIND_LISTS[lists[key]][0]
        found = {int(r[0]): r[1] for r in connection.execute(sql.format(marks=_marks(ids)), ids)} if ids else {}
        for v in ids:
            names[v] = found.get(v) or f"entry {v}, missing from the {lists[key]} list"
    for v in values:
        if v is None:
            names[v] = "no registration date" if key in _FIND_PERIOD_WIDTH else "no stake recorded" if key == "role" else "not recorded"
        elif key in _FIND_PERIOD_WIDTH:
            names[v] = f"{v}–{v + _FIND_PERIOD_WIDTH[key] - 1}"
        elif key in _FIND_GROUP_WORDS:
            names[v] = _FIND_GROUP_WORDS[key][v]
        elif key == "register":
            names[v] = v
    return names


def _find_groups(values: dict[int, set[Any]]) -> dict[Any, set[int]]:
    groups: dict[Any, set[int]] = {}
    for cid, held in values.items():
        for value in held:
            groups.setdefault(value, set()).add(cid)
    return groups


def _find_cut(key: str, groups: dict[Any, set[int]], names: dict[Any, str], top: int) -> tuple[list[Any], list[Any]]:
    """The values shown, in order (periods by date, the rest by size; "not
    recorded" last), and the values gathered into "all others"."""
    if key in _FIND_PERIOD_WIDTH:
        return sorted(v for v in groups if v is not None) + ([None] if None in groups else []), []
    ranked = sorted((v for v in groups if v is not None), key=lambda v: (-len(groups[v]), _lookup_norm(names[v])))
    if len(ranked) > top + 1:
        ranked, rest = ranked[:top], ranked[top:]
    else:
        rest = []
    return ranked + ([None] if None in groups else []), rest


def _find_several(key: str, values: dict[int, set[Any]]) -> str | None:
    n = sum(1 for held in values.values() if len(held) > 1)
    if not n:
        return None
    what = {"place": "name more than one place", "role": "have matching partners in more than one role, or with no stake"}
    return f"{n:,} of the {len(values):,} {what.get(key, 'hold more than one value')} and {'is' if n == 1 else 'are'} counted under each."


def find_count(connection: sqlite3.Connection, query: dict[str, Any], by: str, by2: str | None = None) -> dict[str, Any]:
    """Count: the set grouped once or twice, each row and cell with its
    contracts; percentages are the page's, from these counts and their bases."""
    if by2 is not None and by2 == by:
        raise HTTPException(status_code=400, detail="Choose two different groupings.")
    ids, values = _find_group_rows(connection, query, by)
    groups = _find_groups(values)
    names = _find_group_names(connection, by, set(groups))
    shown, rest = _find_cut(by, groups, names, FIND_TOP_ROWS)
    notes = [note for note in [_find_several(by, values)] if note]
    # the values shown, "all others", then "not recorded"
    rows = [{"value": v, "name": names[v], "ids": sorted(groups[v])} for v in shown if v is not None]
    if rest:
        others = sorted(set().union(*(groups[v] for v in rest)))
        rows.append({"value": "others", "name": f"all others ({len(rest):,} values)", "ids": others})
        notes.append(
            f"The {FIND_TOP_ROWS} largest of {len(groups) - (None in groups):,} values are shown; "
            f"“all others” counts the {len(others):,} contracts of the other {len(rest):,}, each once."
        )
    if None in shown:
        rows.append({"value": None, "name": names[None], "ids": sorted(groups[None])})
    out: dict[str, Any] = {
        "total": len(ids),
        "by": {"key": by, "label": FIND_GROUPINGS[by]["label"]},
        "rows": rows,
        "notes": notes,
    }
    if by2:
        _ids, values2 = _find_group_rows(connection, query, by2)
        groups2 = _find_groups(values2)
        names2 = _find_group_names(connection, by2, set(groups2))
        shown2, rest2 = _find_cut(by2, groups2, names2, FIND_TOP_COLUMNS)
        columns = [{"value": v, "name": names2[v], "ids": groups2[v]} for v in shown2 if v is not None]
        if rest2:
            columns.append({"value": "others", "name": f"all others ({len(rest2):,} values)",
                            "ids": set().union(*(groups2[v] for v in rest2))})
        if None in shown2:
            columns.append({"value": None, "name": names2[None], "ids": groups2[None]})
            notes.append(f"The {FIND_TOP_COLUMNS} largest columns are shown; “all others” gathers the other {len(rest2):,}.")
        several = _find_several(by2, values2)
        if several:
            notes.append(several)
        for row in rows:
            members = set(row["ids"])
            row["cells"] = [{"ids": sorted(members & column["ids"])} for column in columns]
        out["by2"] = {"key": by2, "label": FIND_GROUPINGS[by2]["label"]}
        out["columns"] = [{"value": c["value"], "name": c["name"], "ids": sorted(c["ids"])} for c in columns]
    return out


def find_time(
    connection: sqlite3.Connection, queries: list[dict[str, Any]], period: str = "decade", split: str | None = None
) -> dict[str, Any]:
    """Over time and Compare: counts per period on the corpus's own axis (every
    period from the first registration to the last, zeros kept, a partial first
    or last period named), for the question, for the question split by a
    grouping, or for the same question with one chip changed (queries[1:]).
    Each series carries its bases: its question's earlier steps, period by period."""
    if period not in _FIND_PERIOD_WIDTH:
        raise HTTPException(status_code=400, detail="Choose decades or half-centuries.")
    if split and (split in _FIND_PERIOD_WIDTH or len(queries) > 1):
        raise HTTPException(status_code=400, detail="Split one question by a grouping other than time.")
    width = _FIND_PERIOD_WIDTH[period]
    head = queries[0]
    low, high = _find_exec(
        connection, f"SELECT MIN({_FIND_YEAR}), MAX({_FIND_YEAR}) FROM contract c WHERE {head['steps'][0]['where']} AND {_DATED_CONTRACT}", []
    )[0]
    periods = list(range(low // width * width, high // width * width + 1, width)) if low is not None else []
    partial = {}
    if periods and low % width:
        partial["first"] = f"{low}–{periods[0] + width - 1}"
    if periods and high % width != width - 1:
        partial["last"] = f"{periods[-1]}–{high}"
    at = {p: k for k, p in enumerate(periods)}

    def by_period(where: str, params: list[Any], extra: str = "", extra_params: list[Any] | None = None) -> tuple[list[int], int, list[int]]:
        counts, parts, undated = [0] * len(periods), [0] * len(periods), 0
        for row in _find_exec(
            connection, f"SELECT {_find_period(width)} AS p, COUNT(*){extra} FROM contract c WHERE {where} GROUP BY p",
            (extra_params or []) + params,
        ):
            if row[0] is None:
                undated = int(row[1])
            else:
                counts[at[row[0]]] = int(row[1])
                if extra:
                    parts[at[row[0]]] = int(row[2] or 0)
        return counts, undated, parts

    def bases(query: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"label": step["reading"], "counts": by_period(step["where"], step["params"])[0]} for step in query["steps"][:-1]]

    def members(query: dict[str, Any]) -> set[int]:
        return {int(r[0]) for r in _find_exec(connection, f"SELECT c.contract_id FROM contract c WHERE {query['where_sql']}", query["total_params"])}

    series: list[dict[str, Any]] = []
    notes: list[str] = []
    held: list[set[int]] = []
    if split:
        ids, values = _find_group_rows(connection, head, split)
        groups = _find_groups(values)
        names = _find_group_names(connection, split, set(groups))
        dated = {
            int(r[0]): r[1]
            for r in _find_exec(connection, f"SELECT c.contract_id, {_find_period(width)} FROM contract c WHERE {head['where_sql']}", head["total_params"])
        }
        ranked = sorted(groups, key=lambda v: (-len(groups[v]), v is None, _lookup_norm(names[v])))
        chosen, rest = ranked[:FIND_SERIES], ranked[FIND_SERIES:]
        shared = bases(head)
        sets = [(names[v], groups[v]) for v in chosen]
        if rest:
            sets.append((f"all others ({len(rest):,} values)", set().union(*(groups[v] for v in rest))))
            notes.append(f"The {FIND_SERIES} largest values are drawn; “all others” gathers the other {len(rest):,}.")
        for label, contracts in sets:
            counts, undated = [0] * len(periods), 0
            for cid in contracts:
                if dated.get(cid) is None:
                    undated += 1
                else:
                    counts[at[dated[cid]]] += 1
            series.append({"label": label, "counts": counts, "undated": undated, "total": len(contracts), "bases": shared})
            held.append(contracts)
    else:
        provenance = _find_provenance(head) if len(queries) == 1 else None
        readings = [[step["reading"] for step in q["steps"][1:]] for q in queries]
        for k, query in enumerate(queries):
            extra = f", SUM(CASE WHEN {provenance['sql']} THEN 1 ELSE 0 END)" if provenance else ""
            counts, undated, stated = by_period(query["where_sql"], query["total_params"], extra, provenance["params"] if provenance else None)
            label = query["sentence"]
            if len(queries) > 1:
                # the part of the sentence this question changes
                differ = next((i for i, (a, b) in enumerate(zip(readings[0], readings[k] if k else readings[1])) if a != b), None)
                if differ is not None:
                    label = readings[k][differ]
            entry = {"label": label, "sentence": query["sentence"], "counts": counts, "undated": undated,
                     "total": sum(counts) + undated, "bases": bases(query)}
            if provenance:
                entry["parts"] = [
                    {"label": _FIND_GROUP_WORDS["provenance"]["stated"], "counts": stated},
                    {"label": _FIND_GROUP_WORDS["provenance"]["attributed"], "counts": [n - s for n, s in zip(counts, stated)]},
                ]
            series.append(entry)
            if len(queries) > 1:
                held.append(members(query))
    if len(held) > 1:
        both = sum(1 for cid in set().union(*held) if sum(cid in h for h in held) > 1)
        if both:
            notes.insert(0, f"{both:,} {'contract is' if both == 1 else 'contracts are'} in more than one of these and drawn in each.")
    return {"period": period, "width": width, "periods": periods, "partial": partial, "series": series, "notes": notes}


# ---- Find, step 4: See their partners ----
# The set turned into partner appearances (12.3, from IPUMS's choice of returning
# the matching person or everyone in the household): every partner on the
# contracts, or only those who met a partner line. An appearance is one entered
# person on one contract; person entries are counted as entered, not yet reviewed
# as people (12.2).

FIND_PARTNER_CHOICES = {
    "every": "every partner on these contracts",
    "matched": "only the partners who matched",
}
FIND_PARTNER_LABELS = [
    "Partner", "Contract", "Registration date", "Firm name", "Role on the stake", "Jewish, stated in the act",
    "Jewish, editorial attribution", "Woman", "Through a proxy", "Lives in", "Comes from", "Title", "Profession",
    "Person entry",
]


def find_partners_sql(query: dict[str, Any], which: str) -> dict[str, Any]:
    """The partner appearances of a set, as SQL: the List and its two counts
    (appearances, and person entries as entered)."""
    if which not in FIND_PARTNER_CHOICES:
        raise HTTPException(status_code=400, detail="Choose every partner, or only the partners who matched.")
    members = f"SELECT c.contract_id FROM contract c WHERE {query['where_sql']}"
    params = list(query["total_params"])
    match = ""
    if which == "matched":
        if not query["lines"]:
            raise HTTPException(status_code=400, detail="No partner line in this question, so no partner matched.")
        match = " AND (" + " OR ".join(f"({line['test']}{line['people']})" for line in query["lines"]) + ")"
        params += [p for line in query["lines"] for p in line["test_params"] + line["params"]]
    source = (
        "FROM investor i JOIN person pe ON pe.person_id = i.person_id AND pe.is_deleted = 0 "
        f"JOIN contract c ON c.contract_id = i.contract_id WHERE i.is_deleted = 0 AND i.contract_id IN ({members}){match}"
    )
    role = (
        "(SELECT group_concat(CASE inv.type WHEN 'gp' THEN 'general partner' WHEN 'lp' THEN "
        f"'external investor (accomandante)' ELSE inv.type END, {_FIND_SEP}) FROM investor_group ig "
        "JOIN investment inv ON inv.investment_id = ig.investment_id AND inv.is_deleted = 0 "
        "WHERE ig.investor_id = i.investor_id AND ig.is_deleted = 0)"
    )
    columns = [
        f"{_FIND_NAME} AS partner", "c.contract_id", "c.registration_date", "c.firm_name", f"{role} AS role",
        "CASE WHEN COALESCE(i.is_jewish, 0) = 1 THEN 'yes' ELSE '' END AS jewish_stated",
        "CASE WHEN COALESCE(i.jewish_db, 0) = 1 THEN 'yes' ELSE '' END AS jewish_attributed",
        "CASE WHEN COALESCE(pe.is_woman, 0) = 1 THEN 'yes' ELSE '' END AS woman",
        "CASE WHEN COALESCE(i.via_proxy, 0) = 1 THEN 'yes' ELSE '' END AS proxy",
        "(SELECT p.place_name FROM place p WHERE p.place_id = i.place_of_residence) AS lives_in",
        "(SELECT p.place_name FROM place p WHERE p.place_id = i.place_of_origin) AS comes_from",
        "(SELECT t.title_name FROM title t WHERE t.title_id = i.title) AS title",
        "i.profession", "i.person_id",
    ]
    return {
        "list_sql": (
            f"SELECT {', '.join(columns)}\n{source}\n"
            f"ORDER BY CASE WHEN {_DATED_CONTRACT} THEN 0 ELSE 1 END, c.registration_date, c.contract_id, partner"
        ),
        "counts_sql": f"SELECT COUNT(*), COUNT(DISTINCT i.person_id) {source}",
        "params": params,
        "labels": FIND_PARTNER_LABELS,
    }


def _count_words(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def find_answer(query: dict[str, Any], counts: dict[str, int], undated_left_out: int | None) -> list[str]:
    """The answer line, in sentences: the unit and its count, the provenance
    split, what the count rests on. Generated from the same query as the List."""
    total = int(counts.get("total") or 0)
    answer = [f"{_count_words(total, 'contract', 'contracts')}."]
    several = len(query["splits"]) > 1
    for split in query["splits"]:
        n = int(counts.get(f"split_{split['number']}") or 0)
        partner = "a partner" if split["alone"] else "a matching partner"
        if split["choice"] == "either":
            text = (
                f"{n:,} with {partner} stated Jewish in the act; "
                f"{total - n:,} on editorial attribution alone."
            )
        elif n == 0:
            continue
        elif split["choice"] == "stated":
            text = f"{n:,} of them also {'has' if n == 1 else 'have'} {partner} Jewish by editorial attribution."
        else:
            text = f"{n:,} of them also {'has' if n == 1 else 'have'} {partner} stated Jewish in the act."
        answer.append(f"For “{split['reading']}”: {text}" if several else text)
    answer.append("Counting contracts, each once.")
    undated = int(counts.get("undated") or 0)
    if undated_left_out:
        answer.append(
            f"{_count_words(undated_left_out, 'contract', 'contracts')} with no registration date "
            f"{'meets' if undated_left_out == 1 else 'meet'} the other conditions and "
            f"{'is' if undated_left_out == 1 else 'are'} left out by the years."
        )
    elif undated:
        answer.append(
            f"{undated:,} {'has' if undated == 1 else 'have'} no registration date and "
            f"{'is' if undated == 1 else 'are'} included."
        )
    no_partners = int(counts.get("no_partners") or 0)
    if no_partners:
        answer.append(f"{no_partners:,} {'has' if no_partners == 1 else 'have'} no partners recorded.")
    if "non_people" in counts:
        which = " and ".join(
            f"“{reading}”" for key, reading in (("no", "no partner"), ("every", "every partner"))
            if key in query["quantifiers"]
        )
        n = int(counts.get("non_people") or 0)
        among = (
            "none of these contracts has one among its partners" if n == 0
            else "1 of these contracts has one among its partners" if n == 1
            else f"{n:,} of these contracts have one among their partners"
        )
        answer.append(f"For {which}, {_FIND_NON_PEOPLE} are left out; {among}.")
    return answer


def _find_build_all(specs: list[Any]) -> list[Any]:
    """Several questions built against one read of today's names, spellings and
    non-person rows; a question that does not fit stands as its refusal."""
    normals: list[Any] = []
    for spec in specs:
        try:
            normals.append(find_normalize(spec))
        except HTTPException as refused:
            normals.append(refused)
    valid = [n for n in normals if not isinstance(n, HTTPException)]
    lookups: dict[str, Any] = {"place": {}, "non_people": []}
    if valid:
        connection = open_db()
        try:
            lookups = find_lookups(connection, valid)
        finally:
            connection.close()
    built: list[Any] = []
    for normal in normals:
        try:
            if isinstance(normal, HTTPException):
                raise normal
            built.append(build_find_sql(normal, lookups["place"], lookups["non_people"], lookups))
        except HTTPException as refused:
            built.append(refused)
    return built


def find_prepare(spec: Any) -> dict[str, Any]:
    """build_find_sql with today's names, spellings and non-person rows."""
    query = _find_build_all([spec])[0]
    if isinstance(query, HTTPException):
        raise query
    return query


def _find_rows(statements: list[tuple[str, list[Any]]]) -> list[list[tuple[Any, ...]]]:
    """Several SELECTs on one read-only connection, under one time limit."""
    connection = _readonly_connection()
    try:
        return [connection.execute(_readonly_statement(sql), params).fetchall() for sql, params in statements]
    except sqlite3.Error as exc:
        raise _sql_error(exc)
    finally:
        connection.close()


def _find_counts(query: dict[str, Any]) -> tuple[dict[str, int], int | None]:
    result = run_readonly_sql(query["counts_sql"], tuple(query["counts_params"]))
    counts = {name: (value or 0) for name, value in zip(result["columns"], result["rows"][0])}
    undated = None
    if query["undated_sql"]:
        undated = int(run_readonly_sql(query["undated_sql"], tuple(query["undated_params"]))["rows"][0][0] or 0)
    return counts, undated


def _find_near_misses(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Near misses, as questions of their own (12.4): for the first "operating
    in" condition, the contracts not operating there (elsewhere, or in no recorded
    place) where a partner who meets the first "at least one" line lived in, or
    came from, one of its places."""
    place_at = next(
        (k for k, c in enumerate(spec["contract"]) if c["kind"] == "place" and not c["not"] and None not in c["values"]),
        None,
    )
    if place_at is None:
        return []
    line_at = next((k for k, line in enumerate(spec["partners"]) if line["quantifier"] == "any"), None)
    out = []
    for kind in ("residence", "origin"):
        if line_at is not None and any(c["kind"] == kind for c in spec["partners"][line_at]["conditions"]):
            continue
        near = json.loads(json.dumps(spec))
        near["contract"][place_at]["not"] = True
        added = {"kind": kind, "not": False, "values": list(spec["contract"][place_at]["values"])}
        if line_at is None:
            near["partners"].append({"quantifier": "any", "conditions": [added]})
        else:
            near["partners"][line_at]["conditions"].append(added)
        out.append(near)
    return out


def _find_left_out(connection: sqlite3.Connection, spec: dict[str, Any], lookups: dict[str, Any]) -> list[dict[str, Any]]:
    """Other stored spellings of a place asked for, whose names contain it
    (register 12c: nothing is grouped), each with the question that adds it."""
    targets = [((k,), c) for k, c in enumerate(spec["contract"]) if c["kind"] == "place"] + [
        ((n, k), c)
        for n, line in enumerate(spec["partners"])
        for k, c in enumerate(line["conditions"])
        if c["kind"] in ("residence", "origin")
    ]
    out: list[dict[str, Any]] = []
    for where, condition in targets:
        if condition["not"]:
            continue
        for value in condition["values"]:
            name = lookups["place"].get(value) if value is not None else None
            if not name:
                continue
            for candidate in find_values(connection, condition["kind"], name, limit=50):
                if candidate["value"] is None or candidate["value"] in condition["values"]:
                    continue
                wider = json.loads(json.dumps(spec))
                target = wider["contract"][where[0]] if len(where) == 1 else wider["partners"][where[0]]["conditions"][where[1]]
                target["values"] = condition["values"] + [candidate["value"]]
                out.append({"chosen": name, "name": candidate["name"], "contracts": candidate["contracts"], "spec": wider})
    return out[:12]


def _find_inference(query: dict[str, Any], results: list[list[tuple[Any, ...]]], total: int) -> list[str]:
    """What the set rests on beyond the acts: editorial attribution, and the
    data-entry team's mark "inferred" on a place or an activity."""
    if not total:
        return []
    several = sum(1 for item in query["inference"] if item["what"] == "attribution") > 1
    lines = []
    for item, rows in zip(query["inference"], results):
        if item["what"] == "attribution":
            alone = sum(int(n) for _half, n in rows)
            if not alone:
                text = f"None of the {total:,} {'rests' if total == 1 else 'rest'} on editorial attribution alone."
            else:
                text = f"{alone:,} of the {total:,} {'rests' if alone == 1 else 'rest'} on editorial attribution alone"
                dated = [(int(n), half) for half, n in rows if half is not None]
                if dated:
                    most, half = max(dated, key=lambda t: (t[0], -t[1]))
                    text += f"; {most:,} of them {'was' if most == 1 else 'were'} registered in {half}–{half + 49}"
                text += "."
            lines.append(f"For “{item['reading']}”: {text}" if several else text)
        else:
            n = int(rows[0][0] or 0)
            if not n:
                continue
            if item["what"] == "place":
                verb = "names" if n == 1 else "name"
                lines.append(f"{n:,} of the {total:,} {verb} {_join_or(item['names'])} with the data-entry team's mark “inferred”.")
            else:
                verb = "has its" if n == 1 else "have their"
                lines.append(f"{n:,} of the {total:,} {verb} activity marked as inferred by the data-entry team.")
    return lines


def find_how(spec: Any) -> dict[str, Any]:
    """How this was found (12.4): what is counted; the steps with their counts,
    and each condition's count on its own; other spellings left out, with what
    each would add; near misses as questions of their own; what rests on
    attribution or on the data-entry team's mark; the spellings a word matched;
    and the query that ran."""
    normal = find_normalize(spec)
    connection = open_db()
    try:
        left_out = _find_left_out(connection, normal, find_lookups(connection, [normal]))
    finally:
        connection.close()
    near = _find_near_misses(normal)
    built = _find_build_all([normal] + near + [entry["spec"] for entry in left_out])
    query = built[0]
    if isinstance(query, HTTPException):
        raise query
    statements: list[tuple[str, list[Any]]] = []
    for step in query["steps"]:
        statements.append((step["sql"], step["params"]))
        if "alone_sql" in step:
            statements.append((step["alone_sql"], step["alone_params"]))
    others = [q for q in built[1:] if not isinstance(q, HTTPException)]
    statements += [(q["total_sql"], q["total_params"]) for q in others]
    statements += [(item["sql"], item["params"]) for item in query["inference"]]
    results = iter(_find_rows(statements))

    steps = []
    for step in query["steps"]:
        out = {"reading": step["reading"], "count": int(next(results)[0][0] or 0)}
        if "alone_sql" in step:
            out["alone"] = int(next(results)[0][0] or 0)
        steps.append(out)
    total = steps[-1]["count"]
    others_total = {id(q): int(next(results)[0][0] or 0) for q in others}
    near_misses = [
        {"sentence": q["sentence"], "count": others_total[id(q)], "spec": q["spec"]}
        for q in built[1: 1 + len(near)]
        if not isinstance(q, HTTPException) and others_total[id(q)]
    ]
    spellings = [
        {"chosen": entry["chosen"], "name": entry["name"], "contracts": entry["contracts"], "adds": others_total[id(q)] - total}
        for entry, q in zip(left_out, built[1 + len(near):])
        if not isinstance(q, HTTPException)
    ]
    inference = _find_inference(query, [next(results) for _ in query["inference"]], total)

    combined = [
        {"sentence": entry["sentence"], "count": int(rows[0][0] or 0)}
        for entry, rows in zip(query["sets"], _find_rows([(e["total_sql"], e["total_params"]) for e in query["sets"]]))
    ] if query["sets"] else []
    edits, last = find_edits(normal)
    as_of = (
        f"The last edit logged to the records and fields this question selects by was on {_find_day(last)} "
        f"({edits:,} in all)." if edits else "No edit has been logged to the records and fields this question selects by."
    )
    counted = ["Contracts, each counted once however many of its partners match."]
    if normal["partners"]:
        counted.append("A partner is one person's appearance on one contract, as entered.")
    if query["searched"]:
        counted.append(FIND_PARTNER_CONDITIONS["word"]["note"])
    return {
        "counted": counted,
        "steps": steps,
        "left_out": spellings,
        "near_misses": near_misses,
        "inference": inference,
        "searched": query["searched"],
        "sets": combined,
        "as_of": as_of,
        "sql": query["list_sql"],
        "params": query["list_params"],
    }


# ---- Find, step 4: saved questions, what changed, and See their partners ----

# The records and fields each condition selects by, for "edits logged since"
# (12.4): table -> fields; "*" is every field, None the record itself (created,
# hidden, restored). The change log does not record which figures moved, so a
# saved question says how many edits touched what it reads, never which figure.
_FIND_READS: dict[str, dict[str, set[str | None]]] = {
    "place": {"contract_place": {"*"}},
    "years": {"contract": {"registration_date"}},
    "activity": {"contract": {"economic_sector"}},
    "currency": {"contract": {"currency_id"}},
    "register": {"contract": {"folder"}},
    "discretion": {"contract": {"pl_discretion", "ec_discretion"}},
    "act": {"sub_contract": {"*"}},
    "partnered": {"investor": {None}, "person": {None}},
    "joint": {"investment": {"*"}, "investor_group": {"*"}, "investor": {"is_joint"}},
    "jewish": {"investor": {"is_jewish", "jewish_db"}},
    "woman": {"person": {"is_woman"}},
    "role": {"investment": {"*"}, "investor_group": {"*"}},
    "proxy": {"investor": {"via_proxy"}},
    "residence": {"investor": {"place_of_residence"}},
    "origin": {"investor": {"place_of_origin"}},
    "word": {"investor": {"title", "profession"}},
}
FIND_QUESTION_NAME = 120  # characters in a saved question's name
FIND_SEARCH_LIMIT = 8000  # characters in a saved question's address


def _find_reads(spec: dict[str, Any]) -> dict[str, set[str | None]]:
    reads: dict[str, set[str | None]] = {"contract": {None}}
    for _registry, condition, quantifier in _find_flat(spec):
        if quantifier is not None:
            reads.setdefault("investor", set()).add(None)
            reads.setdefault("person", set()).add(None)
        for table, fields in _FIND_READS[condition["kind"]].items():
            reads.setdefault(table, set()).update(fields)
    return reads


def _find_corrections_path() -> Path:
    return corrections_db.default_path()


def find_edits(
    spec: dict[str, Any], since: str = "", reads: dict[str, set[str | None]] | None = None
) -> tuple[int, str | None]:
    """How many applied (or reverted) edits the change log holds, after ``since``,
    on the records and fields this question selects by (or on ``reads``), and when
    the last was. The log is opened read-only."""
    path = _find_corrections_path()
    if not path.exists():
        return 0, None
    clauses: list[str] = []
    params: list[Any] = [since]
    for table, fields in sorted((reads if reads is not None else _find_reads(spec)).items()):
        if "*" in fields:
            clauses.append("r.db_table = ?")
            params.append(table)
        else:
            named = sorted(f for f in fields if f)
            field = f" OR r.field IN ({_marks(named)})" if named else ""
            clauses.append(f"(r.db_table = ? AND (r.field IS NULL{field}))")
            params += [table, *named]
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        count, last = connection.execute(
            "SELECT COUNT(*), MAX(e.at) FROM change_event e JOIN change_request r ON r.request_id = e.request_id "
            f"WHERE e.event IN ('applied', 'reverted') AND e.at > ? AND ({' OR '.join(clauses)})",
            params,
        ).fetchone()
    except sqlite3.Error:
        return 0, None
    finally:
        connection.close()
    return int(count or 0), last


def _find_day(stamp: str | None) -> str:
    if not stamp:
        return ""
    moment = datetime.fromisoformat(stamp)
    return f"{moment.day} {moment:%B %Y}"


def _find_ids_text(ids: list[int]) -> str:
    shown = ", ".join(str(i) for i in ids[:10])
    return shown + (f" and {len(ids) - 10:,} more" if len(ids) > 10 else "")


def find_owner(request: Request) -> str:
    """Whose saved questions these are: the Cloudflare Access email, or "" locally."""
    return request.headers.get("cf-access-authenticated-user-email", "").strip().lower()


def _find_saved(owner: str) -> dict[str, dict[str, Any]]:
    """The owner's saved questions, folded from the append-only log, oldest first."""
    saved: dict[str, dict[str, Any]] = {}
    for event in load_jsonl(FIND_QUESTIONS_PATH):
        if event.get("owner", "") != owner:
            continue
        qid = str(event.get("id"))
        if event.get("event") == "saved":
            saved[qid] = {k: event.get(k) for k in ("id", "name", "search", "spec", "sentence", "count", "ids", "at")}
        elif qid in saved and event.get("event") == "renamed":
            saved[qid]["name"] = event.get("name")
        elif qid in saved and event.get("event") == "removed":
            del saved[qid]
    return saved


def _find_append(event: dict[str, Any]) -> None:
    FIND_QUESTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FIND_QUESTIONS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({**event, "at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")


def _find_members(query: dict[str, Any]) -> list[int]:
    rows = _find_rows([(f"SELECT c.contract_id FROM contract c WHERE {query['where_sql']} ORDER BY c.contract_id",
                        query["total_params"])])[0]
    return [int(r[0]) for r in rows]


def find_question_today(question: dict[str, Any]) -> dict[str, Any]:
    """A saved question asked again: today's count, which contracts entered or
    left, whether its words now read differently, and the edits logged since to
    what it selects by (12.4). Every sentence is generated here."""
    saved_line = f"Saved {_find_day(question['at'])}: {_count_words(int(question['count']), 'contract', 'contracts')}."
    try:
        query = find_prepare(question["spec"])
    except HTTPException as refused:
        return {"changed": True, "today": None, "lines": [saved_line, f"It no longer fits the conditions: {refused.detail}"]}
    ids = _find_members(query)
    before, now = set(question.get("ids") or []), set(ids)
    entered, left = sorted(now - before), sorted(before - now)
    if not entered and not left:
        today = f"Today: {len(ids):,}, no change."
    else:
        moves = []
        if entered:
            moves.append(f"{_find_ids_text(entered)} entered")
        if left:
            moves.append(f"{_find_ids_text(left)} left")
        today = f"Today: {len(ids):,} ({'; '.join(moves)})."
    lines = [saved_line, today]
    reworded = query["sentence"] != question["sentence"]
    if reworded:
        lines.append(f"Its words now read: {query['sentence']}.")
    edits, last = find_edits(find_normalize(question["spec"]), question["at"])
    lines.append(
        "No edits have been logged since to the records and fields it selects by." if not edits
        else f"{edits:,} {'edit has' if edits == 1 else 'edits have'} been logged since to the records and fields "
        f"it selects by, the last on {_find_day(last)}."
    )
    return {"changed": bool(entered or left or reworded), "today": len(ids), "lines": lines}


# ---- Find, the landing: the corpus first, and the starting questions ----
# Before the first condition the pane shows the corpus (review 2026-10-27, 7 and
# 12.1): every decade of registration on the shared axis, and the standing
# figures, each computed now from the database through the Find engine and each
# opening a Find question. Their words are the review's proposal, still before
# Francesca (register 12e), so they live here and change in one place. The
# starting questions are the library's 25 entries grouped as she asks (12.1):
# each opens as a Find whose count equals the library query's (tests/test_find.py
# and scripts/find_smoke.py hold each to it), or stays a library query, said so,
# while Find cannot ask it; the questions that wait for one of her decisions are
# listed without a number.

FIND_FIGURES: dict[str, dict[str, Any]] = {
    "contracts": {
        "label": "Contracts registered",
        "value": "{total}",
        "detail": "{dated} dated; {undated} not",
        "definition": "Every contract, except any a reviewer has set aside.",
    },
    "decades": {
        "label": "Registrations by decade",
        "value": "{decades} decades",
        "detail": "most in {decade} ({count})",
        "definition": "By registration date.",
    },
    "acts": {
        "label": "Sub-contracts (later acts)",
        "value": "{acts}",
        "detail": "on {contracts} contracts: {types}",
        "orphans": "{orphans} more point to a missing contract",
        "types": {"termination": ("termination", "terminations"), "variation": ("variation", "variations"),
                  "balance": ("balance", "balances"), "renewal": ("renewal", "renewals")},
        "other_type": "{n} of type “{type}”",
        "definition": "Every sub-contract, except any set aside.",
    },
    "termination": {
        "label": "Contracts with a recorded termination",
        "value": "{n}",
        "detail": "{share} of the {total} contracts",
        "definition": "At least one termination act.",
    },
    "woman": {
        "label": "Contracts with a woman partner",
        "value": "{n}",
        "detail": "of the {base} with partners recorded ({share}); as general partner on {gp}, as external investor on {lp}",
        "definition": "A partner recorded as a woman; her role as recorded for the money she put in.",
    },
    "places": {
        "label": "Place of activity",
        "value": "{places}",
        "detail": "of these {inferred} marked as inferred by the data-entry team (meaning to be confirmed)",
        "definition": "The place as written in the record; a contract counts once under each place it names.",
    },
    "proxy": {
        "label": "Partner appearances through a proxy",
        "value": "{n}",
        "detail": "of {all} ({share})",
        "definition": "An appearance is one partner on one contract.",
    },
    "joint": {
        "label": "Partner appearances on a shared stake",
        "value": "{n}",
        "detail": "of {all} ({share})",
        "definition": "The stake is shared by more than one person, or the partner is marked as holding it jointly "
        "and holds a stake.",
    },
}
FIND_FIGURE_PLACES = 3  # places named in the figure
FIND_FIGURE_AS_OF = ("Last edit logged to what it counts: {day}.", "No edit logged to what it counts.")

FIND_STARTING_LABELS = {"library": "Library query"}
# Each item: a Find question (``spec``, ``view``; ``library`` and ``same`` name
# the library entry it answers and how the two are compared), a library entry
# Find cannot ask yet (``library``, ``why``), or a question that waits for a
# decision (``open``, ``waits``). A title left out is the library entry's own.
_WOMAN = {"partners": [{"quantifier": "any", "conditions": [{"kind": "woman"}]}]}
_PROXY = {"partners": [{"quantifier": "any", "conditions": [{"kind": "proxy"}]}]}
_APPEARANCE_SHARE = "A share of partner appearances, by decade."
FIND_STARTING: list[dict[str, Any]] = [
    {"group": "When and for how long", "items": [
        {"library": "contracts_per_year", "why": "Counts by single year; Find counts by decade and half-century."},
        {"library": "later_acts_by_type", "why": "Counts later acts, not contracts."},
        {"library": "acts_per_contract", "why": "Counts the later acts on each contract."},
        {"library": "initial_term", "why": "Counts contracts by the term they state."},
        {"open": "How long a partnership lasted, and when it ended",
         "waits": "Waits for decision 12b: which act ends a partnership, and on which date."},
    ]},
    {"group": "Where", "items": [
        {"spec": {}, "view": {"view": "count", "by": "place"}, "library": "location_frequency", "same": "place"},
        {"open": "Florence and Livorno with their contado, terra and distretto",
         "waits": "Waits for decision 12c: which spellings count as each city, and what the mark “inferred” means."},
    ]},
    {"group": "For what business", "items": [
        {"spec": {"contract": [{"kind": "discretion", "choice": "activity"}]}, "library": "ec_discretion_yes",
         "same": "contracts"},
        {"open": "Activities compared across places and periods",
         "waits": "Waits for decision 12a: how the recorded phrases are grouped."},
    ]},
    {"group": "With what money", "items": [
        {"spec": {}, "view": {"view": "count", "by": "currency"}, "library": "currency_frequency", "same": "currency"},
        {"library": "currency_totals", "why": "Adds up capital, which Find does not do yet."},
        {"library": "funding_composition", "why": "Sets each contract's cash against its stated capital."},
        {"open": "Capital over time, and general partners' capital against external investors'",
         "waits": "Waits for the register's question on exchange rates between currencies (§6)."},
    ]},
    {"group": "Who invested", "items": [
        {"spec": _WOMAN, "library": "women_investors", "same": "contracts"},
        {"title": "Women partners, by role", "spec": _WOMAN, "view": {"view": "count", "by": "role"}},
        {"spec": {"partners": [
            {"quantifier": "any", "conditions": [{"kind": "jewish", "choice": "either"}]},
            {"quantifier": "any", "conditions": [{"kind": "jewish", "choice": "either", "not": True}]},
        ]}, "library": "jewish_mixed", "same": "contracts"},
        {"library": "jewish_all",
         "why": "Find's “every partner” leaves out institutions and placeholders; this list counts them."},
        {"title": "Partners acting through a proxy, by role", "spec": _PROXY, "view": {"view": "count", "by": "role"}},
        {"library": "pct_via_proxy", "why": "A share of partner appearances."},
        {"library": "via_proxy_over_time", "why": _APPEARANCE_SHARE},
        {"library": "investors_per_contract", "why": "Counts the partners on each contract."},
        {"library": "gp_lp_per_contract", "why": "Fewest, average and most per contract."},
        {"library": "investments_by_role", "why": "Counts stakes, not contracts."},
        {"library": "jewish_over_time", "why": _APPEARANCE_SHARE},
        {"library": "jewish_provenance_over_time", "why": _APPEARANCE_SHARE},
        {"library": "joint_over_time", "why": _APPEARANCE_SHARE},
    ]},
    {"group": "Who came back and who was related", "items": [
        {"library": "relatives_in_contract", "why": "Leads to read in the acts, not a count of family ties."},
        {"open": "How many people invested, and who came back",
         "waits": "Waits for the register's question on whether entries with the same name are one person (§5)."},
        {"open": "Family ties between partners, counted",
         "waits": "Waits for decision 12d: what counts as evidence of a family tie."},
    ]},
    {"group": "Checking the data", "items": [
        {"spec": {"contract": [{"kind": "place", "values": [None]}]}, "library": "no_place", "same": "contracts"},
        {"spec": {"contract": [{"kind": "partnered", "choice": "no"}]}, "library": "no_investors", "same": "contracts"},
        {"spec": {"contract": [{"kind": "activity", "values": [None]}]}, "library": "no_economic_activity",
         "same": "contracts"},
        {"library": "corpus_totals", "why": "Counts the rows of each list."},
    ]},
]


def _find_build_on(connection: sqlite3.Connection, specs: list[Any]) -> list[dict[str, Any]]:
    """Several questions built against one read of this connection's names."""
    normals = [find_normalize(spec) for spec in specs]
    lookups = find_lookups(connection, normals)
    return [build_find_sql(n, lookups["place"], lookups["non_people"], lookups) for n in normals]


def _find_total(connection: sqlite3.Connection, query: dict[str, Any]) -> int:
    return int(_find_exec(connection, query["total_sql"], query["total_params"])[0][0] or 0)


def _find_share(n: int, base: int) -> str:
    return f"{100 * n / base:.1f}%" if base else "–"


def _find_link(text: str, query: dict[str, Any], view: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"text": text, "spec": query["spec"], "view": view or {}}


def _find_segments(template: str, values: dict[str, Any]) -> list[dict[str, Any]]:
    """A figure's words as segments: plain text, and the numbers that open a Find
    question (``spec`` and ``view``, which the page writes as its address)."""
    out: list[dict[str, Any]] = []
    for k, piece in enumerate(re.split(r"\{(\w+)\}", template)):
        if k % 2 == 0:
            if piece:
                out.append({"text": piece})
            continue
        value = values[piece]
        out.extend(value if isinstance(value, list) else [value] if isinstance(value, dict) else [{"text": str(value)}])
    return out


def find_figures(connection: sqlite3.Connection) -> dict[str, Any]:
    """The corpus line, the decade chart's figure and the standing figures, each
    computed through the Find engine on this connection. Each figure carries the
    records and fields it reads (``reads``), for its as-of line."""
    places = [v for v in find_values(connection, "place", "", limit=FIND_FIGURE_PLACES + 1) if v["value"] is not None]
    places = places[:FIND_FIGURE_PLACES]
    gp, lp = ({"kind": "role", "choice": code} for code in ("gp", "lp"))
    specs = {
        "all": {},
        "partnered": {"contract": [{"kind": "partnered", "not": True, "choice": "no"}]},
        "act": {"contract": [{"kind": "act", "choice": "any"}]},
        "termination": {"contract": [{"kind": "act", "choice": "termination"}]},
        "woman": _WOMAN,
        "woman_gp": {"partners": [{"quantifier": "any", "conditions": [{"kind": "woman"}, gp]}]},
        "woman_lp": {"partners": [{"quantifier": "any", "conditions": [{"kind": "woman"}, lp]}]},
        "proxy": _PROXY,
        "joint": {"partners": [{"quantifier": "any", "conditions": [{"kind": "joint"}]}]},
        **{f"place_{v['value']}": {"contract": [{"kind": "place", "values": [v["value"]]}]} for v in places},
    }
    q = dict(zip(specs, _find_build_on(connection, list(specs.values()))))
    n = {name: _find_total(connection, query) for name, query in q.items()}
    words = FIND_FIGURES
    total, undated = (int(x or 0) for x in _find_exec(connection, q["all"]["counts_sql"], q["all"]["counts_params"])[0][:2])

    def fmt(x: int) -> str:
        return f"{x:,}"

    def figure(key: str, values: dict[str, Any], reads: dict[str, set[str | None]], detail: str | None = None) -> dict[str, Any]:
        entry = words[key]
        return {
            "key": key, "label": entry["label"], "value": _find_segments(entry["value"], values),
            "detail": _find_segments(detail if detail is not None else entry["detail"], values),
            "definition": entry["definition"], "reads": reads,
        }

    def reads_of(*names: str) -> dict[str, set[str | None]]:
        out: dict[str, set[str | None]] = {}
        for name in names:
            for table, fields in _find_reads(q[name]["spec"]).items():
                out.setdefault(table, set()).update(fields)
        return out

    dated_reads = {"contract": {None, "registration_date"}}
    # the decades on the shared axis, from the same view the Over time tab draws
    time = find_time(connection, [q["all"]], "decade")
    counts = time["series"][0]["counts"] if time["series"] else []
    top = max(range(len(counts)), key=lambda k: (counts[k], -k)) if counts else None
    decades = figure("decades", {
        "decades": _find_link(fmt(len(time["periods"])), q["all"], {"view": "time"}),
        "decade": f"{time['periods'][top]}–{time['periods'][top] + 9}" if top is not None else "–",
        "count": fmt(counts[top]) if top is not None else "0",
    }, dated_reads)

    acts_rows = _find_exec(
        connection,
        f"SELECT s.sub_type, COUNT(*) {_FIND_ACTS} AND s.main_contract_id IN "
        f"(SELECT c.contract_id FROM contract c WHERE {q['all']['where_sql']}) GROUP BY s.sub_type",
        q["all"]["total_params"],
    )
    acts = {kind: int(count) for kind, count in acts_rows}
    orphans = int(_find_exec(
        connection, f"SELECT COUNT(*) {_FIND_ACTS} AND s.main_contract_id NOT IN (SELECT contract_id FROM contract)", []
    )[0][0] or 0)
    entry = words["acts"]
    ordered = [t for t in FIND_ACT_TYPES if acts.get(t)] + sorted((t for t in acts if t not in FIND_ACT_TYPES), key=str)
    types = ", ".join(
        f"{fmt(acts[t])} {entry['types'][t][acts[t] != 1]}" if t in entry["types"]
        else entry["other_type"].format(n=fmt(acts[t]), type=t if t not in (None, "") else "not recorded")
        for t in ordered
    )
    acts_detail = entry["detail"] + (f"; {entry['orphans']}" if orphans else "")
    acts_figure = figure("acts", {
        "acts": fmt(sum(acts.values())), "contracts": _find_link(fmt(n["act"]), q["act"]), "types": types,
        "orphans": fmt(orphans),
    }, {"sub_contract": {"*"}}, acts_detail)

    places_value: list[dict[str, Any]] = []
    for k, v in enumerate(places):
        if k:
            places_value.append({"text": ", "})
        places_value.append(_find_link(f"{v['name']} {fmt(v['contracts'])}", q[f"place_{v['value']}"]))
    inferred = [fmt(v["inferred"]) for v in places]
    places_figure = figure("places", {"places": places_value, "inferred": _join_and(inferred)},
                           reads_of(*(f"place_{v['value']}" for v in places)) if places else {"contract_place": {"*"}},
                           detail=words["places"]["detail"] if any(v["inferred"] for v in places) else "")

    partners_every = find_partners_sql(q["all"], "every")
    appearances = int(_find_exec(connection, partners_every["counts_sql"], partners_every["params"])[0][0] or 0)

    def matched(name: str) -> int:
        built = find_partners_sql(q[name], "matched")
        return int(_find_exec(connection, built["counts_sql"], built["params"])[0][0] or 0)

    partners_view = {"view": "partners", "partners": "matched"}
    figures = [
        figure("contracts", {"total": _find_link(fmt(total), q["all"], {"all": True}), "dated": fmt(total - undated),
                             "undated": fmt(undated)}, dated_reads),
        acts_figure,
        figure("termination", {"n": _find_link(fmt(n["termination"]), q["termination"]),
                               "share": _find_share(n["termination"], total), "total": fmt(total)},
               reads_of("termination")),
        figure("woman", {"n": _find_link(fmt(n["woman"]), q["woman"]), "base": fmt(n["partnered"]),
                         "share": _find_share(n["woman"], n["partnered"]),
                         "gp": _find_link(fmt(n["woman_gp"]), q["woman_gp"]),
                         "lp": _find_link(fmt(n["woman_lp"]), q["woman_lp"])},
               reads_of("woman", "woman_gp", "partnered")),
        places_figure,
        *(figure(name, {"n": _find_link(fmt(count), q[name], partners_view), "all": fmt(appearances),
                        "share": _find_share(count, appearances)}, reads_of(name))
          for name, count in (("proxy", matched("proxy")), ("joint", matched("joint")))),
    ]
    return {"answer": find_answer(q["all"], {"total": total, "undated": undated}, None), "chart": decades,
            "figures": figures}


def _join_and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}" if words else ""


def find_starting(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """The starting questions, grouped as she asks: each Find question with its
    sentence and today's count, each library entry with why it stays one, and
    each question that waits for a decision, without a number."""
    library = {q["id"]: q for q in ANALYSIS_LIBRARY}
    asked = [item for group in FIND_STARTING for item in group["items"] if "spec" in item]
    built = {id(item): query for item, query in zip(asked, _find_build_on(connection, [i["spec"] for i in asked]))}
    out = []
    for group in FIND_STARTING:
        items = []
        for item in group["items"]:
            if "open" in item:
                items.append({"kind": "open", "title": item["open"], "waits": item["waits"]})
                continue
            entry = library.get(item.get("library", ""), {})
            title = item.get("title") or entry.get("title", "")
            if "spec" in item:
                query = built[id(item)]
                by = item.get("view", {}).get("by")
                says = f"{query['sentence']}, counted by {FIND_GROUPINGS[by]['label']}" if by else query["sentence"]
                items.append({"kind": "find", "title": title, "says": says, "spec": query["spec"],
                              "view": item.get("view", {}), "count": _find_total(connection, query),
                              "library": item.get("library")})
            else:
                items.append({"kind": "library", "title": title, "says": item["why"], "library": item["library"]})
        out.append({"group": group["group"], "items": items})
    return out


def find_starting_check(connection: sqlite3.Connection, item: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    """A starting question beside the library entry it stands for, in the form both
    share: the contracts listed (``same`` "contracts"), or each recorded value with
    its contracts (``same`` a grouping, "place" or "currency"). Equal when the Find
    asks what the library query counts; values missing from their list (the lost
    entries) are left out of both, as the library's join leaves them out."""
    entry = next(q for q in ANALYSIS_LIBRARY if q["id"] == item["library"])
    cursor = connection.execute(_readonly_statement(entry["sql"]))
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    query = _find_build_on(connection, [item["spec"]])[0]
    if item["same"] == "contracts":
        at = columns.index("contract_id")
        found = _find_exec(connection, f"SELECT c.contract_id FROM contract c WHERE {query['where_sql']}", query["total_params"])
        return sorted({int(r[at]) for r in rows}), sorted(int(r[0]) for r in found)
    _ids, values = _find_group_rows(connection, query, item["same"])
    groups = _find_groups(values)
    ids = sorted(v for v in groups if v is not None)
    named = {int(r[0]): r[1] for r in connection.execute(_FIND_LISTS[item["same"]][0].format(marks=_marks(ids)), ids)} if ids else {}
    return sorted((r[0], int(r[1])) for r in rows), sorted((named[v], len(groups[v])) for v in ids if v in named)


class FindRequest(BaseModel):
    spec: dict[str, Any] = Field(default_factory=dict)


@app.get("/api/analysis/find/registry")
def analysis_find_registry() -> dict[str, Any]:
    return find_registry()


@app.post("/api/analysis/find")
def analysis_find(payload: FindRequest) -> dict[str, Any]:
    query = find_prepare(payload.spec)
    counts, undated = _find_counts(query)
    listing = run_readonly_sql(query["list_sql"], tuple(query["list_params"]))
    return {
        **listing,
        "labels": query["labels"],
        "sentence": query["sentence"],
        "answer": find_answer(query, counts, undated),
        "total": int(counts.get("total") or 0),
        "names": query["names"],
        "groupings": find_groupings(query),
        "sql": query["list_sql"],
        "params": query["list_params"],
    }


@app.post("/api/analysis/find/how")
def analysis_find_how(payload: FindRequest) -> dict[str, Any]:
    return find_how(payload.spec)


class FindCounts(BaseModel):
    specs: list[Any] = Field(default_factory=list)


FIND_MAX_COUNTS = 40  # questions counted in one request (a picker's values)


@app.post("/api/analysis/find/counts")
def analysis_find_counts(payload: FindCounts) -> dict[str, Any]:
    """How many contracts each of several questions finds: the counts inside the
    pickers, a value in this question and in the corpus, and the same partner
    against another (12.2), each with its sentence in parts. A question that does
    not fit counts as null."""
    if len(payload.specs) > FIND_MAX_COUNTS:
        raise HTTPException(status_code=400, detail=f"Up to {FIND_MAX_COUNTS} questions are counted at once.")
    built = _find_build_all(payload.specs)
    ready = [q for q in built if not isinstance(q, HTTPException)]
    totals = iter(_find_rows([(q["total_sql"], q["total_params"]) for q in ready]))
    return {
        "counts": [
            None if isinstance(q, HTTPException)
            else {"total": int(next(totals)[0][0] or 0), "parts": [s["reading"] for s in q["steps"][1:]],
                  "sentence": q["sentence"]}
            for q in built
        ]
    }


class FindExport(BaseModel):
    spec: dict[str, Any] = Field(default_factory=dict)
    link: str = ""


@app.post("/api/analysis/find/export")
def analysis_find_export(payload: FindExport) -> StreamingResponse:
    """Every row of the List, opening with the question, its answer, the date
    and the link that reopens it (12.4)."""
    query = find_prepare(payload.spec)
    counts, undated = _find_counts(query)
    link = payload.link.strip()
    if not re.fullmatch(r"https?://[^\s]{1,4000}", link):
        link = ""
    today = datetime.now(timezone.utc)
    downloaded = f"Downloaded on {today.day} {today:%B %Y}" + (f" from {link}" if link else "")
    preamble = [[query["sentence"]], [" ".join(find_answer(query, counts, undated))], [downloaded]]
    return export_readonly_csv(query["list_sql"], tuple(query["list_params"]), preamble=preamble, labels=query["labels"])


class FindCountRequest(BaseModel):
    spec: dict[str, Any] = Field(default_factory=dict)
    by: str = "decade"
    by2: str | None = None


def _find_view(run: Any) -> Any:
    connection = _readonly_connection()
    try:
        return run(connection)
    except sqlite3.Error as exc:
        raise _sql_error(exc)
    finally:
        connection.close()


@app.post("/api/analysis/find/count")
def analysis_find_count(payload: FindCountRequest) -> dict[str, Any]:
    """Count (12.3): the set grouped once or twice, with each row's and cell's
    contracts, so every cell opens its contracts in the List."""
    query = find_prepare(payload.spec)
    return _find_view(lambda connection: find_count(connection, query, payload.by, payload.by2 or None))


class FindTimeRequest(BaseModel):
    spec: dict[str, Any] = Field(default_factory=dict)
    period: str = "decade"
    split: str | None = None
    beside: list[Any] = Field(default_factory=list)


@app.post("/api/analysis/find/time")
def analysis_find_time(payload: FindTimeRequest) -> dict[str, Any]:
    """Over time and Compare (12.3): counts per period on the shared axis, for
    the question, split by a grouping, or beside the same question with one chip
    changed, each series with its bases."""
    if len(payload.beside) > FIND_MAX_BESIDE:
        raise HTTPException(status_code=400, detail=f"Up to {FIND_MAX_BESIDE} questions can be set beside this one.")
    queries = _find_build_all([payload.spec] + list(payload.beside))
    for query in queries:
        if isinstance(query, HTTPException):
            raise query
    names: dict[str, dict[str, str]] = {}
    for query in queries:
        for kind, named in query["names"].items():
            names.setdefault(kind, {}).update(named)
    return {**_find_view(lambda connection: find_time(connection, queries, payload.period, payload.split or None)), "names": names}


@app.get("/api/analysis/find/questions")
def analysis_find_questions(request: Request) -> dict[str, Any]:
    """The signed-in person's saved questions, each asked again today (12.1, 12.4)."""
    owner = find_owner(request)
    out = []
    for question in _find_saved(owner).values():
        today = find_question_today(question)
        out.append({k: question[k] for k in ("id", "name", "search", "sentence", "count", "at")} | today)
    return {"owner": owner, "questions": out}


class FindSave(BaseModel):
    name: str = ""
    search: str = ""
    spec: dict[str, Any] = Field(default_factory=dict)


def _find_name(text: str) -> str:
    name = " ".join(text.split())
    if not name or len(name) > FIND_QUESTION_NAME:
        raise HTTPException(status_code=400, detail=f"Give the question a name of up to {FIND_QUESTION_NAME} characters.")
    return name


@app.post("/api/analysis/find/questions")
@serialized_write
def analysis_find_save(payload: FindSave, request: Request) -> dict[str, Any]:
    """Save a question under a name: its address (question and view), its
    sentence, today's count and which contracts it holds, so it can say later
    what changed. The criteria are what reruns; the contracts are the receipt."""
    name = _find_name(payload.name)
    search = payload.search.strip().lstrip("?")
    if len(search) > FIND_SEARCH_LIMIT or any(ch in search for ch in "\n\r#"):
        raise HTTPException(status_code=400, detail="This question's address is too long to save.")
    query = find_prepare(payload.spec)
    ids = _find_members(query)
    qid = uuid.uuid4().hex[:12]
    _find_append({"event": "saved", "id": qid, "owner": find_owner(request), "name": name, "search": search,
                  "spec": query["spec"], "sentence": query["sentence"], "count": len(ids), "ids": ids})
    return {"id": qid, "name": name, "count": len(ids)}


class FindRename(BaseModel):
    name: str = ""


def _find_own(qid: str, request: Request) -> str:
    owner = find_owner(request)
    if qid not in _find_saved(owner):
        raise HTTPException(status_code=404, detail="There is no such saved question.")
    return owner


@app.post("/api/analysis/find/questions/{qid}/rename")
@serialized_write
def analysis_find_rename(qid: str, payload: FindRename, request: Request) -> dict[str, Any]:
    owner = _find_own(qid, request)
    name = _find_name(payload.name)
    _find_append({"event": "renamed", "id": qid, "owner": owner, "name": name})
    return {"id": qid, "name": name}


@app.post("/api/analysis/find/questions/{qid}/remove")
@serialized_write
def analysis_find_remove(qid: str, request: Request) -> dict[str, Any]:
    """Remove a saved question from the list (append-only: the log keeps it)."""
    owner = _find_own(qid, request)
    _find_append({"event": "removed", "id": qid, "owner": owner})
    return {"id": qid, "removed": True}


class FindPartnersRequest(BaseModel):
    spec: dict[str, Any] = Field(default_factory=dict)
    which: str = "every"
    link: str = ""


def _find_partner_counts(query: dict[str, Any]) -> dict[str, Any]:
    choices = ["every"] + (["matched"] if query["lines"] else [])
    built = [find_partners_sql(query, which) for which in choices]
    counted = _find_rows([(b["counts_sql"], b["params"]) for b in built])
    return {
        which: {"reading": FIND_PARTNER_CHOICES[which], "appearances": int(rows[0][0] or 0), "people": int(rows[0][1] or 0)}
        for which, rows in zip(choices, counted)
    }


def _find_partner_line(counts: dict[str, Any], which: str) -> str:
    chosen = counts[which]
    return (
        f"Partners: {chosen['reading']}. {_count_words(chosen['appearances'], 'appearance', 'appearances')} of "
        f"{_count_words(chosen['people'], 'person entry', 'person entries')}, as entered, not yet reviewed as people."
    )


@app.post("/api/analysis/find/partners")
def analysis_find_partners(payload: FindPartnersRequest) -> dict[str, Any]:
    """See their partners (12.3): both choices counted, and the chosen one listed."""
    query = find_prepare(payload.spec)
    counts = _find_partner_counts(query)
    built = find_partners_sql(query, payload.which)
    listing = run_readonly_sql(built["list_sql"], tuple(built["params"]))
    return {**listing, "labels": built["labels"], "counts": counts, "which": payload.which,
            "line": _find_partner_line(counts, payload.which), "sentence": query["sentence"], "sql": built["list_sql"]}


@app.post("/api/analysis/find/partners/export")
def analysis_find_partners_export(payload: FindPartnersRequest) -> StreamingResponse:
    """Every partner appearance of the set as a CSV, opening with the question,
    which partners and how many, the date and the link."""
    query = find_prepare(payload.spec)
    counts = _find_partner_counts(query)
    built = find_partners_sql(query, payload.which)
    link = payload.link.strip()
    if not re.fullmatch(r"https?://[^\s]{1,4000}", link):
        link = ""
    today = datetime.now(timezone.utc)
    downloaded = f"Downloaded on {today.day} {today:%B %Y}" + (f" from {link}" if link else "")
    preamble = [[query["sentence"]], [_find_partner_line(counts, payload.which)], [downloaded]]
    return export_readonly_csv(built["list_sql"], tuple(built["params"]), preamble=preamble, labels=built["labels"])


@app.get("/api/analysis/find/landing")
def analysis_find_landing() -> dict[str, Any]:
    """The corpus before the first condition (12.1): the corpus line, the decade
    chart's figure and the standing figures, each with its as-of line."""
    landing = _find_view(find_figures)
    for figure in [landing["chart"], *landing["figures"]]:
        edits, last = find_edits({}, reads=figure.pop("reads"))
        figure["as_of"] = FIND_FIGURE_AS_OF[0].format(day=_find_day(last)) if edits else FIND_FIGURE_AS_OF[1]
    return landing


@app.get("/api/analysis/find/starting")
def analysis_find_starting() -> dict[str, Any]:
    """The starting questions in the rail, grouped as she asks (12.1)."""
    return {"labels": FIND_STARTING_LABELS, "groups": _find_view(find_starting)}


@app.get("/api/analysis/find/values")
def analysis_find_values(kind: str, q: str = "") -> dict[str, Any]:
    """A picker's values with their counts in the corpus (find_values); for a
    word, the word itself with the contracts it finds and the stored spellings it
    matches. The counts within this question come from /find/counts."""
    if kind == "word":
        word = q.strip()
        if len(word) > FIND_MAX_WORD or not _FIND_WORD.fullmatch(word):
            return {"values": []}
        query = find_prepare({"partners": [{"quantifier": "any", "conditions": [{"kind": "word", "values": [word]}]}]})
        total = int(_find_rows([(query["total_sql"], query["total_params"])])[0][0][0] or 0)
        found = query["searched"][0]
        return {"values": [{"value": word, "name": word, "contracts": total, "inferred": 0,
                            "titles": found["titles"], "professions": found["professions"]}]}
    connection = open_db()
    try:
        return {"values": find_values(connection, kind, q, limit=50 if kind == "register" else 12)}
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
        clog = open_corrections()
        try:
            # Resolved while main.db is open: names, phrases and stakes are read here.
            record["history"] = record_history(connection, clog, table, record_id)
        finally:
            clog.close()
    finally:
        connection.close()
    return record


# ---- Record change history ---------------------------------------------------
# A record page lists every logged change that concerns it, not only changes to
# its own row (UI review 2026-10-14; LOG 2026-10-27). A contract: its own row,
# its partners (removed ones too), their stakes and stake links, its places, the
# arrival, hiding and restoring of its sub-contracts, and its partners' person
# entries while they are partners on it. A person: their entry, their
# appearances on contracts and the stakes they hold. A sub-contract: its own row.
# "Not an issue" decisions from Needs review show as quiet lines. Pure reading:
# the op-log is never changed. One user action (several rows) reads as one line.

# Labels for columns that are not in CORRECTABLE_FIELDS (the page builders name
# them inline: relink fields, the editorial attribution, a place's address).
HISTORY_LABELS: dict[tuple[str, str], str] = {
    ("contract", "economic_sector"): "Economic activity",
    ("contract", "currency_id"): "Currency",
    ("investor", "title"): "Title",
    ("investor", "title_father_mother"): "Father/mother's title",
    ("investor", "title_grandfather"): "Grandfather's title",
    ("investor", "title_husband"): "Husband's title",
    ("investor", "place_of_residence"): "Place of residence",
    ("investor", "place_of_origin"): "Place of origin",
    ("investor", "jewish_db"): "Jewish — editorial attribution",
    ("contract_place", "address"): "Address",
}
# Reasons the server or the editor writes by itself: not shown as a quoted reason.
_MACHINE_REASONS = {"Edited directly on the record page.", "Cleared on the record page.", "added a place", "relink", "revert"}
_MACHINE_REASON_PREFIXES = ("is_joint re-derived", "joint stake gained a partner")
_ACTION_GAP_SECONDS = 2.0  # older operations of one action are 11–17 ms apart; separate actions ≥ 8.7 s
_MERGE_EDITS_SECONDS = 600  # repeated saves of one field within 10 minutes read as one line


def _history_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _reader_reason(reason: Any) -> tuple[str | None, str | None]:
    """(reason a person typed, provenance line) from a logged reason."""
    text = str(reason or "").strip()
    if not text or text in _MACHINE_REASONS or text.startswith(_MACHINE_REASON_PREFIXES):
        return None, None
    for prefix in ("added partner on contract", "added investor on contract"):
        if text.startswith(prefix):
            _, _, note = text.partition("; ")
            return (note.strip() or None), None
    if text.startswith("created with contract"):
        text = text.partition("; ")[2].strip()
    if text.startswith("source:"):
        return None, text[len("source:"):].strip() or None
    return text, None


def _history_field_label(table: str, field: str | None) -> str:
    if not field:
        return ""
    spec = CORRECTABLE_FIELDS.get(table, {}).get(field)
    if spec:
        return spec["label"]
    return HISTORY_LABELS.get((table, field), field.replace("_", " "))


def _history_value(connection: sqlite3.Connection, table: str, field: str | None, raw: Any) -> str | None:
    """A logged value as the page shows it: Yes/No, the phrase (not its number),
    role words, "not recorded" for the placeholder date; None for empty."""
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw)
    kind = RELINK_FIELDS.get((table, field or ""))
    if kind:
        if text in ("0", "0.0"):
            return None
        meta = LOOKUP_KINDS[kind]
        return lookup_value(connection, meta["table"], meta["id"], raw, meta["value"]) or "not in the list"
    spec = CORRECTABLE_FIELDS.get(table, {}).get(field or "", {})
    if spec.get("input_type") == "bool" or (table, field) == ("investor", "jewish_db"):
        return "Yes" if text.strip().lower() in ("1", "true", "yes") else "No"
    if (table, field) == ("investment", "type"):
        return data_quality.ROLE_WORDS.get(text, text)
    if text == "0000-00-00":
        return "not recorded"
    return text


def _cash_text(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _role_text(code: Any, cash: Any) -> str:
    """'external investor (accomandante), 6000' — role in prose with its capital."""
    role = data_quality.ROLE_WORDS.get(str(code or ""), str(code or "")).lower()
    cash_text = _cash_text(cash)
    return ", ".join(part for part in (role, cash_text) if part)


def _pk_value(op: dict[str, Any], column: str) -> int | None:
    try:
        return int(json.loads(op["pk"]).get(column))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


class _HistoryScope:
    """What one record page gathers, plus the names its lines need. Rows are read
    in any state (hidden, removed, created on the platform)."""

    def __init__(
        self, connection: sqlite3.Connection, table: str, record_id: str, clog: sqlite3.Connection | None = None
    ) -> None:
        self.connection = connection
        self.clog = clog
        self.table = table
        self.record_id = int(record_id)
        self.targets: dict[str, list[dict[str, int]]] = {}
        self.investors: dict[int, dict[str, Any]] = {}  # investor_id -> person_id, contract_id, is_deleted
        self.investments: dict[int, dict[str, Any]] = {}  # investment_id -> contract_id, type, cash
        self.links: list[tuple[int, int, int]] = []  # (investor_id, investment_id, is_deleted)
        self.sub_contracts: dict[int, dict[str, Any]] = {}
        self.person_windows: dict[int, tuple[datetime | None, datetime | None] | None] = {}
        self._persons: dict[int, str] = {}
        self._contracts: dict[int, str] = {}
        own_column = PRIMARY_KEY_COLUMN[table]
        self.targets[table] = [{own_column: self.record_id}]
        if table == "contract":
            self._gather_contract()
        elif table == "person":
            self._gather_person()

    def _rows(self, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def _gather_links(self, investor_ids: list[int], investment_ids: list[int]) -> None:
        seen: set[tuple[int, int]] = set()
        for column, ids in (("investor_id", investor_ids), ("investment_id", investment_ids)):
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                if not chunk:
                    continue
                marks = ",".join("?" * len(chunk))
                for row in self._rows(
                    f"SELECT investor_id, investment_id, is_deleted FROM investor_group WHERE {column} IN ({marks})",
                    tuple(chunk),
                ):
                    key = (int(row["investor_id"]), int(row["investment_id"]))
                    if key not in seen:
                        seen.add(key)
                        self.links.append((key[0], key[1], int(row["is_deleted"] or 0)))
        self.targets["investor_group"] = [{"investor_id": a, "investment_id": b} for a, b, _ in self.links]

    def _gather_contract(self) -> None:
        cid = self.record_id
        for row in self._rows(
            "SELECT investor_id, person_id, contract_id, is_deleted FROM investor WHERE contract_id = ?", (cid,)
        ):
            self.investors[int(row["investor_id"])] = dict(row)
        for row in self._rows(
            "SELECT investment_id, contract_id, type, investment_cash, is_deleted FROM investment WHERE contract_id = ?",
            (cid,),
        ):
            self.investments[int(row["investment_id"])] = dict(row)
        self._gather_links(list(self.investors), list(self.investments))
        self.targets["investor"] = [{"investor_id": i} for i in self.investors]
        self.targets["investment"] = [{"investment_id": i} for i in self.investments]
        self.targets["contract_place"] = [
            {"contract_id": cid, "place_id": int(row["place_id"])}
            for row in self._rows("SELECT place_id FROM contract_place WHERE contract_id = ?", (cid,))
        ]
        for row in self._rows(
            "SELECT contract_id, sub_type FROM sub_contract WHERE main_contract_id = ?", (cid,)
        ):
            self.sub_contracts[int(row["contract_id"])] = dict(row)
        self.targets["sub_contract"] = [{"contract_id": i} for i in self.sub_contracts]
        persons = sorted({int(r["person_id"]) for r in self.investors.values() if r["person_id"] is not None})
        self.targets["person"] = [{"person_id": p} for p in persons]

    def _gather_person(self) -> None:
        pid = self.record_id
        for row in self._rows(
            "SELECT investor_id, person_id, contract_id, is_deleted FROM investor WHERE person_id = ?", (pid,)
        ):
            self.investors[int(row["investor_id"])] = dict(row)
        self._gather_links(list(self.investors), [])
        stake_ids = sorted({b for a, b, _ in self.links if a in self.investors})
        for start in range(0, len(stake_ids), 500):
            chunk = stake_ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self._rows(
                f"SELECT investment_id, contract_id, type, investment_cash, is_deleted FROM investment "
                f"WHERE investment_id IN ({marks})",
                tuple(chunk),
            ):
                self.investments[int(row["investment_id"])] = dict(row)
        self.targets["investor"] = [{"investor_id": i} for i in self.investors]
        self.targets["investment"] = [{"investment_id": i} for i in self.investments]

    # -- names ------------------------------------------------------------------
    def person_name(self, person_id: Any) -> str:
        try:
            pid = int(person_id)
        except (TypeError, ValueError):
            return "an unnamed person"
        if pid not in self._persons:
            row = self.connection.execute(
                "SELECT first_name, last_name, nickname, father_mother, grandfather FROM person WHERE person_id = ?",
                (pid,),
            ).fetchone()
            self._persons[pid] = (
                person_display_name(row["first_name"], row["last_name"], row["nickname"], row["father_mother"], row["grandfather"])
                if row
                else f"person entry #{pid}"
            )
        return self._persons[pid]

    def contract_label(self, contract_id: Any) -> str:
        try:
            cid = int(contract_id)
        except (TypeError, ValueError):
            return "a contract"
        if cid not in self._contracts:
            row = self.connection.execute(
                "SELECT firm_name, registration_date FROM contract WHERE contract_id = ?", (cid,)
            ).fetchone()
            firm = (row["firm_name"] or "").strip() if row else ""
            date = display_date(row["registration_date"]) if row else ""
            self._contracts[cid] = firm or (f"contract {cid} ({date})" if date else f"contract {cid}")
        return self._contracts[cid]

    def investor_person(self, investor_id: Any) -> int | None:
        try:
            iid = int(investor_id)
        except (TypeError, ValueError):
            return None
        if iid not in self.investors:
            row = self.connection.execute(
                "SELECT investor_id, person_id, contract_id, is_deleted FROM investor WHERE investor_id = ?", (iid,)
            ).fetchone()
            if not row:
                return None
            self.investors[iid] = dict(row)
        person = self.investors[iid].get("person_id")
        return int(person) if person is not None else None

    def stake_holders(self, investment_id: int, *, exclude: int | None = None) -> list[str]:
        names: list[str] = []
        for investor_id, stake_id, _deleted in self.links:
            if stake_id == investment_id and investor_id != exclude:
                person = self.investor_person(investor_id)
                name = self.person_name(person)
                if name not in names:
                    names.append(name)
        if not names:
            for row in self._rows(
                "SELECT investor_id FROM investor_group WHERE investment_id = ?", (investment_id,)
            ):
                if int(row["investor_id"]) != exclude:
                    name = self.person_name(self.investor_person(row["investor_id"]))
                    if name not in names:
                        names.append(name)
        return names

    def holders_at(self, investment_id: int, at: datetime, *, exclude: int | None = None) -> list[str]:
        """Who held a stake at a moment: its partner links that existed then and
        were not removed (with or without their partner) before it."""
        links = self._rows(
            "SELECT g.investor_id, g.is_deleted AS link_deleted, i.is_deleted AS investor_deleted "
            "FROM investor_group g LEFT JOIN investor i ON i.investor_id = g.investor_id WHERE g.investment_id = ?",
            (investment_id,),
        )
        ops: list[dict[str, Any]] = []
        if self.clog is not None and links:
            ops = corrections_db.history_for_rows(self.clog, {
                "investor_group": [{"investor_id": int(r["investor_id"]), "investment_id": investment_id} for r in links],
                "investor": [{"investor_id": int(r["investor_id"])} for r in links],
            })
        names: list[str] = []
        for row in links:
            iid = int(row["investor_id"])
            if iid == exclude:
                continue
            mine = [
                op for op in ops
                if (op["db_table"] == "investor_group" and _pk_value(op, "investor_id") == iid)
                or (op["db_table"] == "investor" and _pk_value(op, "investor_id") == iid)
            ]
            created = [op for op in mine if op["db_table"] == "investor_group" and op["op"] == "create"]
            if created and _history_time(created[0]["created_at"]) >= at:
                continue  # joined later
            switches = [op for op in mine if op["op"] in ("delete", "restore")]
            before = [op for op in switches if _history_time(op["created_at"]) < at]
            after = [op for op in switches if _history_time(op["created_at"]) >= at]
            if before:
                live = before[-1]["op"] == "restore"
            elif after:
                live = after[0]["op"] == "delete"
            else:
                live = not int(row["link_deleted"] or 0) and not int(row["investor_deleted"] or 0)
            if live:
                name = self.person_name(self.investor_person(iid))
                if name not in names:
                    names.append(name)
        return names

    def stake(self, investment_id: int) -> dict[str, Any]:
        if investment_id not in self.investments:
            row = self.connection.execute(
                "SELECT investment_id, contract_id, type, investment_cash, is_deleted FROM investment WHERE investment_id = ?",
                (investment_id,),
            ).fetchone()
            self.investments[investment_id] = dict(row) if row else {}
        return self.investments[investment_id]


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _person_windows(scope: _HistoryScope, ops: list[dict[str, Any]]) -> None:
    """On a contract page a partner's person-entry changes count while the person
    is a partner on it: from their addition on the platform (if they were added
    here) to their last removal (if all their rows here are removed)."""
    margin = timedelta(seconds=_ACTION_GAP_SECONDS)
    by_person: dict[int, list[int]] = {}
    for iid, row in scope.investors.items():
        if row.get("person_id") is not None:
            by_person.setdefault(int(row["person_id"]), []).append(iid)
    for person, rows in by_person.items():
        created: dict[int, datetime] = {}
        removed: dict[int, datetime] = {}
        for op in ops:
            if op["db_table"] != "investor":
                continue
            iid = _pk_value(op, "investor_id")
            if iid not in rows:
                continue
            at = _history_time(op["created_at"])
            if op["op"] == "create":
                created[iid] = min(at, created.get(iid, at))
            elif op["op"] == "delete":
                removed[iid] = max(at, removed.get(iid, at))
            elif op["op"] == "restore":
                removed.pop(iid, None)
        start = min(created.values()) - margin if len(created) == len(rows) else None
        if all(int(scope.investors[i].get("is_deleted") or 0) for i in rows):
            if len(removed) < len(rows):
                scope.person_windows[person] = None  # hidden before the platform: never a partner here
                continue
            end: datetime | None = max(removed.values()) + margin
        else:
            end = None
        scope.person_windows[person] = (start, end)


def _in_scope(scope: _HistoryScope, op: dict[str, Any]) -> bool:
    if scope.table == "contract":
        if op["db_table"] == "sub_contract":
            return op["op"] in ("create", "delete", "restore")
        if op["db_table"] == "person":
            window = scope.person_windows.get(_pk_value(op, "person_id") or -1)
            if window is None:
                return False
            start, end = window
            at = _history_time(op["created_at"])
            return (start is None or at >= start) and (end is None or at <= end)
    if op["db_table"] == "investor" and op.get("field") == "is_joint" and op["op"] == "update":
        return False  # a derived flag: a step of its action, never a line of its own
    return True


def _lookup_creates(clog: sqlite3.Connection, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A phrase minted by an action (a new place, title, …) is logged on its own
    row; fetch those creates so they fold into the action that minted them."""
    wanted: dict[str, set[int]] = {}

    def want(kind: str, value: Any) -> None:
        try:
            ident = int(value)
        except (TypeError, ValueError):
            return
        if ident:
            wanted.setdefault(kind, set()).add(ident)

    for op in ops:
        if op["op"] in ("update", "relink"):
            kind = RELINK_FIELDS.get((op["db_table"], op.get("field") or ""))
            if kind:
                want(kind, op.get("after_value"))
        elif op["op"] == "create" and isinstance(op.get("after_value"), dict):
            snap = op["after_value"]
            if op["db_table"] == "contract_place":
                want("place", snap.get("place_id"))
            for (table, field), kind in RELINK_FIELDS.items():
                if table == op["db_table"]:
                    want(kind, snap.get(field))
    targets = {
        LOOKUP_KINDS[kind]["table"]: [{LOOKUP_KINDS[kind]["id"]: i} for i in ids] for kind, ids in wanted.items()
    }
    return [op for op in corrections_db.history_for_rows(clog, targets) if op["op"] == "create"] if targets else []


def _group_actions(ops: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """One user action per group: by its action id, or (older operations, logged
    before action ids existed) by the same author within two seconds."""
    groups: list[list[dict[str, Any]]] = []
    by_action: dict[str, list[dict[str, Any]]] = {}
    previous: dict[str, Any] | None = None
    previous_group: list[dict[str, Any]] | None = None
    for op in ops:
        run_id = str(op.get("run_id") or "")
        if run_id.startswith("action-"):
            group = by_action.get(run_id)
            if group is None:
                group = by_action[run_id] = []
                groups.append(group)
            group.append(op)
            previous, previous_group = op, None
            continue
        # The two-second rule is for operations logged before action ids, which
        # carry no run_id; an inline save ("apply-…") is always its own action.
        if (
            previous_group is not None
            and previous is not None
            and not run_id
            and not previous.get("run_id")
            and previous["created_by"] == op["created_by"]
            and (_history_time(op["created_at"]) - _history_time(previous["created_at"])).total_seconds()
            <= _ACTION_GAP_SECONDS
        ):
            previous_group.append(op)
        else:
            previous_group = [op]
            groups.append(previous_group)
        previous = op
    return groups


def _describe_action(scope: _HistoryScope, group: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One history line for one action, in the reader's words."""
    connection = scope.connection

    def ops_of(table: str, *kinds: str) -> list[dict[str, Any]]:
        return [op for op in group if op["db_table"] == table and (not kinds or op["op"] in kinds)]

    lookup_tables = {meta["table"]: kind for kind, meta in LOOKUP_KINDS.items()}
    lookups = [op for op in group if op["db_table"] in lookup_tables and op["op"] == "create"]
    new_phrases = []
    for op in lookups:
        meta = LOOKUP_KINDS[lookup_tables[op["db_table"]]]
        snap = op.get("after_value") if isinstance(op.get("after_value"), dict) else {}
        phrase = snap.get(meta["value"]) or lookup_value(
            connection, meta["table"], meta["id"], _pk_value(op, meta["id"]), meta["value"]
        )
        if phrase:
            new_phrases.append(f"“{phrase}”")
    details: list[str] = []
    if new_phrases:
        details.append(f"new to the list: {', '.join(new_phrases)}")
    entry: dict[str, Any] = {
        "kind": "edit", "tag": "", "subject": None, "link": None, "field": None,
        "before": None, "after": None, "text": None, "detail": None,
    }
    own_column = PRIMARY_KEY_COLUMN[scope.table]
    own_pk = corrections_db.pk_json({own_column: scope.record_id})
    on_person_page = scope.table == "person"

    def partner_subject(investor_id: int | None) -> None:
        if on_person_page:
            row = scope.investors.get(investor_id or -1, {})
            entry["tag"] = "Partner"
            entry["subject"] = f"on {scope.contract_label(row.get('contract_id'))}"
            entry["link"] = {"kind": "contract", "id": str(row.get("contract_id"))} if row.get("contract_id") else None
        else:
            person = scope.investor_person(investor_id)
            entry["tag"] = "Partner"
            entry["subject"] = scope.person_name(person)
            entry["link"] = {"kind": "person", "id": str(person)} if person is not None else None

    def stake_subject(investment_id: int) -> None:
        stake = scope.stake(investment_id)
        holders = scope.stake_holders(investment_id)
        entry["tag"] = "Stake"
        if on_person_page:
            entry["subject"] = f"on {scope.contract_label(stake.get('contract_id'))}"
            entry["link"] = {"kind": "contract", "id": str(stake.get("contract_id"))} if stake.get("contract_id") else None
        else:
            entry["subject"] = _join_names(holders) or _role_text(stake.get("type"), stake.get("investment_cash")).capitalize()

    def edit(op: dict[str, Any]) -> None:
        entry["field"] = _history_field_label(op["db_table"], op.get("field"))
        if op.get("field") == "document":
            entry["kind"], entry["field"], entry["text"] = "edit", None, "narrative edited"
            return
        entry["before"] = _history_value(connection, op["db_table"], op.get("field"), op.get("before_value"))
        entry["after"] = _history_value(connection, op["db_table"], op.get("field"), op.get("after_value"))

    investor_life = ops_of("investor", "create", "delete", "restore")
    own_life = [op for op in group if op["db_table"] == scope.table and op["pk"] == own_pk and op["op"] in ("create", "delete", "restore")]
    sub_life = ops_of("sub_contract", "create", "delete", "restore") if scope.table == "contract" else []
    place_ops = ops_of("contract_place")
    stake_life = ops_of("investment", "create", "delete", "restore")
    updates = [op for op in group if op["op"] in ("update", "relink") and not (op["db_table"] == "investor" and op.get("field") == "is_joint")]
    person_creates = ops_of("person", "create")

    if investor_life:
        lead = investor_life[0]
        iid = _pk_value(lead, "investor_id")
        partner_subject(iid)
        if lead["op"] == "create":
            entry["kind"] = "added"
            own_stake = ops_of("investment", "create")
            joined = ops_of("investor_group", "create")
            if own_stake:
                snap = own_stake[0].get("after_value") if isinstance(own_stake[0].get("after_value"), dict) else {}
                entry["text"] = f"added as {_role_text(snap.get('type'), snap.get('investment_cash'))}"
            elif joined:
                stake_id = _pk_value(joined[0], "investment_id") or 0
                stake = scope.stake(stake_id)
                others = scope.holders_at(stake_id, _history_time(lead["created_at"]), exclude=iid)
                role = _role_text(stake.get("type"), stake.get("investment_cash"))
                entry["text"] = (
                    f"added, sharing the stake of {_join_names(others)} ({role})" if others else f"added, holding an existing stake ({role})"
                )
            else:
                entry["text"] = "added"
            if on_person_page:
                entry["text"] = entry["text"].replace("added", "added to the contract", 1)
            if person_creates:
                details.insert(0, "new person entry")
        elif lead["op"] == "delete":
            entry["kind"] = "removed"
            had_stake = bool(ops_of("investor_group")) or any(a == iid for a, _b, _d in scope.links)
            if not had_stake:
                entry["text"] = "removed (an entry with no role or stake)"
            elif ops_of("investment", "delete"):
                entry["text"] = "removed from the contract, with the stake"
            else:
                entry["text"] = "removed from the contract; the stake stayed"
        else:
            entry["kind"], entry["text"] = "restored", "restored to the contract"
    elif own_life:
        lead = own_life[0]
        entry["tag"] = {"contract": "Contract", "sub_contract": "Sub-contract", "person": "Person entry"}[scope.table]
        entry["kind"], entry["text"] = {
            "create": ("created", "added to the database"),
            "delete": ("hidden", "hidden"),
            "restore": ("restored", "restored from hidden"),
        }[lead["op"]]
    elif sub_life:
        lead = sub_life[0]
        sid = _pk_value(lead, "contract_id") or 0
        sub_type = (scope.sub_contracts.get(sid) or {}).get("sub_type")
        entry["tag"] = "Sub-contract"
        entry["subject"] = f"Sub-contract {sid}" + (f" ({sub_type})" if sub_type else "")
        entry["link"] = {"kind": "sub_contract", "id": str(sid)}
        entry["kind"], entry["text"] = {
            "create": ("added", "added"), "delete": ("hidden", "hidden"), "restore": ("restored", "restored from hidden"),
        }[lead["op"]]
    elif place_ops:
        lead = next((op for op in place_ops if op["op"] in ("create", "delete", "restore")), place_ops[0])
        place_id = _pk_value(lead, "place_id")
        entry["tag"] = "Place"
        entry["subject"] = lookup_value(connection, "place", "place_id", place_id, "place_name") or f"place #{place_id}"
        entry["link"] = {"kind": "place", "id": str(place_id)} if place_id else None
        if lead["op"] == "update":
            edit(lead)
        else:
            entry["kind"], entry["text"] = {
                "create": ("added", "added"), "delete": ("removed", "removed"), "restore": ("restored", "restored"),
            }[lead["op"]]
            address = next((op for op in place_ops if op["op"] == "update"), None)
            if address:
                edit(address)
    elif stake_life:
        lead = stake_life[0]
        stake_id = _pk_value(lead, "investment_id") or 0
        stake = scope.stake(stake_id)
        entry["tag"] = "Stake"
        role = _role_text(stake.get("type"), stake.get("investment_cash"))
        if on_person_page:
            stake_subject(stake_id)
        else:
            entry["subject"] = role.capitalize() if role else "A stake"
        holders = scope.stake_holders(stake_id)
        if lead["op"] == "delete":
            entry["kind"] = "removed"
            entry["text"] = f"removed; it had been held by {_join_names(holders)}" if holders else "removed; no partner was attached"
            if on_person_page:
                entry["text"] = f"removed ({role})"
        elif lead["op"] == "restore":
            entry["kind"], entry["text"] = "restored", "restored"
        else:
            entry["kind"], entry["text"] = "added", "added"
    elif updates:
        lead = updates[0]
        table = lead["db_table"]
        if table == "investor":
            partner_subject(_pk_value(lead, "investor_id"))
        elif table == "investment":
            stake_subject(_pk_value(lead, "investment_id") or 0)
        elif table == "person" and scope.table != "person":
            person = _pk_value(lead, "person_id")
            entry["tag"], entry["subject"] = "Person entry", scope.person_name(person)
            entry["link"] = {"kind": "person", "id": str(person)}
        elif table == "contract_place":
            place_id = _pk_value(lead, "place_id")
            entry["tag"] = "Place"
            entry["subject"] = lookup_value(connection, "place", "place_id", place_id, "place_name") or f"place #{place_id}"
            entry["link"] = {"kind": "place", "id": str(place_id)} if place_id else None
        else:
            entry["tag"] = {"contract": "Contract", "sub_contract": "Sub-contract", "person": "Person entry"}.get(table, "Record")
        edit(lead)
        if lead.get("field") == "jewish_db" and str(lead.get("after_value")) in ("0", "False"):
            entry["text"], entry["field"], entry["before"], entry["after"] = "Jewish — editorial attribution refuted", None, None, None
    elif scope.table != "person" and ops_of("person", "delete", "restore"):
        lead = ops_of("person", "delete", "restore")[0]
        person = _pk_value(lead, "person_id")
        entry["tag"], entry["subject"] = "Person entry", scope.person_name(person)
        entry["link"] = {"kind": "person", "id": str(person)}
        entry["kind"], entry["text"] = ("hidden", "hidden") if lead["op"] == "delete" else ("restored", "restored from hidden")
    elif ops_of("investor_group", "create", "delete", "restore"):
        lead = ops_of("investor_group", "create", "delete", "restore")[0]
        iid = _pk_value(lead, "investor_id")
        stake_subject(_pk_value(lead, "investment_id") or 0)
        name = scope.person_name(scope.investor_person(iid))
        entry["kind"], entry["text"] = {
            "create": ("added", f"{name} now holds it"),
            "delete": ("removed", f"{name} no longer holds it"),
            "restore": ("restored", f"{name} holds it again"),
        }[lead["op"]]
    elif person_creates and scope.table == "person":
        lead = person_creates[0]
        entry["tag"], entry["kind"], entry["text"] = "Person entry", "created", "added to the database"
    else:
        return None  # only phrase creates or derived flags: nothing a reader did here

    reason, source = None, None
    for op in [lead] + [op for op in group if op is not lead]:
        typed, src = _reader_reason(op.get("reason"))
        reason = reason or typed
        source = source or src
    statuses = {op["status"] for op in group}
    conflicts = [op for op in group if op["status"] == "conflict"]
    note = None
    if conflicts:
        # Only a rebuild of the database marks an operation "conflict" (replay found
        # the row changed, or the new row's number already taken).
        if len(conflicts) < len(group):
            note = "Part of this change was not re-applied when the database was last rebuilt. Check the record."
        elif lead["op"] in ("update", "relink"):
            note = (
                "Not applied when the database was last rebuilt: the value had changed before it could be "
                "applied. Check the field and edit it again if the change is still needed."
            )
        elif lead["op"] == "create":
            note = "This addition was not re-applied when the database was last rebuilt. Check the record and add it again if needed."
        else:
            note = "This was not re-applied when the database was last rebuilt. Check the record."
    entry.update(
        {
            "id": lead["request_id"],
            "detail": "; ".join(details) or None,
            "by": lead["created_by"],
            "at": lead["created_at"],
            "reason": reason,
            "source": source,
            "status": "conflict" if statuses == {"conflict"} else ("mixed" if conflicts else "applied"),
            "status_note": note,
            "undone": None,
            "edits": 1,
            "chain": None,
            "request_ids": [op["request_id"] for op in group],
            "_target": (lead["db_table"], lead["pk"], lead.get("field")),
            "_order": lead.get("first_event_id") or 0,
        }
    )
    return entry


def _dismissal_entries(table: str, record_id: str) -> list[dict[str, Any]]:
    """Needs-review items marked "Not an issue" for this record (decided 2026-10-27:
    the only trace of why an item left the list). An undo marks the line undone."""
    prefix = f"{table}:{record_id}:"
    rows = [row for row in load_jsonl(FLAG_DISMISSALS_PATH) if str(row.get("key") or "").startswith(prefix)]
    meta = flag_group_meta()
    entries: list[dict[str, Any]] = []
    open_by_key: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = str(row["key"])
        if row.get("action") == "restore":
            entry = open_by_key.pop(key, None)
            if entry is not None:
                entry["undone"] = {"by": row.get("reviewer"), "at": row.get("at")}
            continue
        group = key[len(prefix):].split(":", 1)[0]
        label = (meta.get(group) or {}).get("label") or group.replace("_", " ")
        entry = {
            "id": f"dismissal-{index}",
            "kind": "dismissed",
            "tag": "Needs review",
            "subject": None,
            "link": None,
            "field": None,
            "before": None,
            "after": None,
            "text": f"“{label}” marked not an issue",
            "detail": None,
            "by": row.get("reviewer") or "",
            "at": row.get("at") or "",
            "reason": (row.get("reason") or "").strip() or None,
            "source": None,
            "status": "applied",
            "status_note": None,
            "undone": None,
            "edits": 1,
            "chain": None,
            "request_ids": [],
            "_target": None,
            "_order": 0,
        }
        open_by_key[key] = entry
        entries.append(entry)
    return entries


def _merge_repeated_edits(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Several saves of one field by one reviewer within ten minutes, with nothing
    in between, read as one line with the chain of values ("Yes → No → Yes")."""
    merged: list[dict[str, Any]] = []
    for entry in entries:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and entry["kind"] == "edit" == prev["kind"]
            and entry["_target"] == prev["_target"]
            and entry["field"] and entry["field"] == prev["field"]
            and entry["tag"] == prev["tag"] and entry["subject"] == prev["subject"]
            and entry["by"] == prev["by"]
            and entry["status"] == "applied" == prev["status"]
            and not entry["undone"] and not prev["undone"]
            and (_history_time(entry["at"]) - _history_time(prev["at"])).total_seconds() <= _MERGE_EDITS_SECONDS
        ):
            prev["chain"] = (prev["chain"] or [prev["before"], prev["after"]]) + [entry["after"]]
            prev["after"] = entry["after"]
            prev["edits"] += 1
            prev["at"] = entry["at"]
            if entry["reason"] and entry["reason"] != prev["reason"]:
                prev["reason"] = f"{prev['reason']}; {entry['reason']}" if prev["reason"] else entry["reason"]
            prev["request_ids"] += entry["request_ids"]
            continue
        merged.append(entry)
    return merged


def record_history(
    connection: sqlite3.Connection, clog: sqlite3.Connection, table: str, record_id: str
) -> dict[str, Any]:
    """The record page's history: {entries (newest first), created}. `created` is
    the record's own creation on the platform (for the provenance banner), never a
    partner or stake created on it."""
    scope = _HistoryScope(connection, table, record_id, clog)
    ops = corrections_db.history_for_rows(clog, scope.targets)
    if table == "contract":
        _person_windows(scope, ops)
    ops = [op for op in ops if _in_scope(scope, op)]
    created = None
    own_pk = corrections_db.pk_json({PRIMARY_KEY_COLUMN[table]: int(record_id)})
    own_create = next((op for op in ops if op["db_table"] == table and op["pk"] == own_pk and op["op"] == "create"), None)
    if own_create:
        reason, source = _reader_reason(own_create.get("reason"))
        created = {"by": own_create["created_by"], "at": own_create["created_at"], "source": source, "reason": reason}
    known = {op["request_id"] for op in ops}
    extra = [op for op in _lookup_creates(clog, ops) if op["request_id"] not in known]
    combined = sorted(ops + extra, key=lambda op: (op["created_at"], op.get("first_event_id") or 0))
    entries: list[dict[str, Any]] = []
    for group in _group_actions(combined):
        if not any(op["request_id"] in known for op in group):
            continue  # a phrase minted by an action on another page
        entry = _describe_action(scope, group)
        if entry is None:
            continue
        # An undo ("revert", no longer offered on the pages) marks what it undid.
        if any(op.get("reason") == "revert" for op in group if op["op"] == "update"):
            target = next((e for e in reversed(entries) if e["_target"] == entry["_target"] and not e["undone"]), None)
            if target is not None:
                target["undone"] = {"by": entry["by"], "at": entry["at"]}
                continue
        entries.append(entry)
    entries = _merge_repeated_edits(entries)
    entries += _dismissal_entries(table, record_id)
    entries.sort(key=lambda e: (_history_time(e["at"]), e["_order"]), reverse=True)
    for entry in entries:
        entry.pop("_target", None)
        entry.pop("_order", None)
    return {"entries": entries, "created": created}


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
        except Exception:
            # compensation-lite (§0 Phase A4): the flip committed but its op-log
            # entry failed — put it back so no unlogged write survives a rebuild
            with connection:
                connection.execute(
                    f"UPDATE {table} SET is_deleted = ? WHERE {col} = ?", (0 if hidden else 1, record_id)
                )
            raise
        finally:
            clog.close()
    finally:
        connection.close()
    return {"ok": True, "is_deleted": hidden}


@app.post("/api/db/record/{table}/{record_id}/hide")
@serialized_write
def hide_record(table: str, record_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_hidden(table, record_id, hidden=True, action=action)


@app.post("/api/db/record/{table}/{record_id}/restore")
@serialized_write
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
        try:
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
        except Exception:
            # compensation-lite (§0 Phase A4): unlogged flip → put it back
            with connection:
                connection.execute(
                    "UPDATE investor SET is_joint = ? WHERE investor_id = ?", (current, m["investor_id"])
                )
            raise


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
        try:
            corrections_db.record_operation(
                clog, op="delete" if removed else "restore", db_table="investor",
                pk={"investor_id": int(investor_id)}, by=action.reviewer, reason=reason, note=reason,
            )
        except Exception:
            # compensation-lite (§0 Phase A4): unlogged flip → put it back
            with connection:
                connection.execute(
                    "UPDATE investor SET is_deleted = ? WHERE investor_id = ?", (0 if removed else 1, investor_id)
                )
            raise
        for link in links:
            with connection:
                connection.execute(
                    "UPDATE investor_group SET is_deleted = ? WHERE investor_id = ? AND investment_id = ?",
                    (1 if removed else 0, investor_id, link["investment_id"]),
                )
            try:
                corrections_db.record_operation(
                    clog, op="delete" if removed else "restore", db_table="investor_group",
                    pk={"investor_id": int(investor_id), "investment_id": int(link["investment_id"])},
                    by=action.reviewer, reason=reason, note=reason,
                )
            except Exception:
                # every earlier pair in the cascade is applied AND logged — undo
                # only this link's unlogged flip, then abort (replay-consistent)
                with connection:
                    connection.execute(
                        "UPDATE investor_group SET is_deleted = ? WHERE investor_id = ? AND investment_id = ?",
                        (0 if removed else 1, investor_id, link["investment_id"]),
                    )
                raise

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
@serialized_write
def remove_partner(cid: str, investor_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_partner_removed(cid, investor_id, removed=True, action=action)


@app.post("/api/db/contract/{cid}/partner/{investor_id}/restore")
@serialized_write
def restore_partner(cid: str, investor_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_partner_removed(cid, investor_id, removed=False, action=action)


# ---------------------------------------------------------------------------
# Remove / restore a STAKE that has no partner attached (an `investment` row with
# no live investor_group link). The remove-partner cascade deliberately leaves the
# investment in place (decision 2026-06-17, docs/remove_partner_design.md); this
# is the separate, audited step that design left open for the reviewer who finds
# the stake was entered in error. Only an unattached stake can be removed — one
# with a partner is removed through the partner, never from under them.
# ---------------------------------------------------------------------------


def _set_stake_removed(cid: str, investment_id: str, *, removed: bool, action: RecordAction) -> dict[str, Any]:
    if removed and not action.reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to remove a stake.")
    connection = open_db()
    clog = open_corrections()
    try:
        row = connection.execute(
            "SELECT is_deleted, contract_id FROM investment WHERE investment_id = ?", (investment_id,)
        ).fetchone()
        if not row or str(row["contract_id"]) != str(cid):
            raise HTTPException(status_code=404, detail="Stake not found on this contract.")
        if bool(row["is_deleted"]) == removed:
            raise HTTPException(status_code=409, detail="Stake is already in that state.")
        if removed:
            holders = connection.execute(
                """SELECT COUNT(*) AS c FROM investor_group g JOIN investor i ON i.investor_id = g.investor_id
                   WHERE g.investment_id = ? AND g.is_deleted = 0 AND i.is_deleted = 0""",
                (investment_id,),
            ).fetchone()["c"]
            if holders:
                raise HTTPException(
                    status_code=409,
                    detail="This stake still has a partner attached — remove the partner instead.",
                )
        with connection:
            connection.execute(
                "UPDATE investment SET is_deleted = ? WHERE investment_id = ?",
                (1 if removed else 0, investment_id),
            )
        try:
            corrections_db.record_operation(
                clog, op="delete" if removed else "restore", db_table="investment",
                pk={"investment_id": int(investment_id)},
                by=action.reviewer, reason=action.reason.strip() or None,
            )
        except Exception:
            # compensation-lite (§0 Phase A4): unlogged flip → put it back
            with connection:
                connection.execute(
                    "UPDATE investment SET is_deleted = ? WHERE investment_id = ?",
                    (0 if removed else 1, investment_id),
                )
            raise
    finally:
        clog.close()
        connection.close()
    return {"ok": True, "removed": removed}


@app.post("/api/db/contract/{cid}/stake/{investment_id}/remove")
@serialized_write
def remove_stake(cid: str, investment_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_stake_removed(cid, investment_id, removed=True, action=action)


@app.post("/api/db/contract/{cid}/stake/{investment_id}/restore")
@serialized_write
def restore_stake(cid: str, investment_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_stake_removed(cid, investment_id, removed=False, action=action)


class AttributionRefute(BaseModel):
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)


@app.post("/api/db/investor/{investor_id}/refute-jewish-attribution")
@serialized_write
def refute_jewish_attribution(investor_id: str, payload: AttributionRefute) -> dict[str, Any]:
    """The one sanctioned write to the legacy editorial layer: a reviewer judges a
    2010s Jewish attribution erroneous and clears it (jewish_db 1→0), audited with
    a required reason. The platform never *sets* jewish_db — new identifications
    enter only as `is_jewish` (stated in the act) through the normal edit path.
    The seed dump keeps the original 1 forever; replay applies this correction.
    See docs/data_quality/jewish_db.md."""
    connection = open_db()
    clog = open_corrections()
    try:
        row = connection.execute(
            "SELECT jewish_db, is_deleted FROM investor WHERE investor_id = ?", (investor_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Partner not found.")
        if row["jewish_db"] not in (1, "1"):
            raise HTTPException(status_code=409, detail="No editorial attribution is set on this partner.")
        with connection:
            connection.execute("UPDATE investor SET jewish_db = 0 WHERE investor_id = ?", (investor_id,))
        try:
            corrections_db.record_operation(
                clog, op="update", db_table="investor", pk={"investor_id": int(investor_id)},
                field="jewish_db", before_value=1, after_value=0,
                by=payload.reviewer, reason=payload.reason.strip(),
            )
        except Exception:
            # compensation-lite (§0 Phase A4): an unlogged refute must not stand
            with connection:
                connection.execute("UPDATE investor SET jewish_db = 1 WHERE investor_id = ?", (investor_id,))
            raise
    finally:
        clog.close()
        connection.close()
    return {"ok": True}


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
def global_search(q: str = "", expand: str = "", limit: int = 0) -> dict[str, Any]:
    """Global FTS search over the database (contracts, acts, people).

    The derived search.db is rebuilt on demand when main.db has changed (an
    applied correction, hide, or created row) — a full rebuild is ~0.5 s, so
    freshness is checked per request at negligible cost. A purely numeric query
    additionally returns direct id-jump cards (historians think in act numbers).
    `limit` is how many hits the expanded group returns (0 = the default 50);
    "Show more" asks again with a larger limit.
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
                        "meta": "",
                    }
                )
        finally:
            connection.close()

    search_path = search_index.ensure_fresh(db_path())
    payload = search_index.search(
        search_path,
        term,
        expand_kind=expand if expand in search_index.KIND_ORDER else None,
        expand_limit=limit or 50,
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
    needle = _lookup_norm(q)
    connection = open_db()
    try:
        # The same count the Reference page shows for the term (live contracts only).
        usage = _reference_counts(connection, {"economic_activity": "activity"}.get(kind, kind))
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
@serialized_write
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
        try:
            corrections_db.record_operation(
                clog, op="update", db_table=table, pk={pk_col: int(record_id)}, field=action.field,
                before_value=old_id, after_value=new_id, by=action.reviewer, reason=reason,
            )
        except Exception:
            # compensation-lite (§0 Phase A4): unlogged FK change → restore it
            # (a phrase minted above stays — it is separately create-logged)
            with connection:
                connection.execute(f"UPDATE {table} SET {action.field} = ? WHERE {pk_col} = ?", (old_id, record_id))
            raise
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
@serialized_write
def add_place(cid: str, payload: PlaceAdd) -> dict[str, Any]:
    connection = open_db()
    clog = open_corrections()
    try:
        if not connection.execute(
            "SELECT 1 FROM contract WHERE contract_id = ? AND is_deleted = 0", (cid,)
        ).fetchone():
            raise HTTPException(status_code=404, detail="Contract not found.")
        reason = payload.reason.strip() or "added a place"
        place_id = _resolve_lookup_id(connection, "place", payload.place, reviewer=payload.reviewer, reason=reason)
        if place_id is None:
            raise HTTPException(status_code=400, detail="A place is required.")
        addr = payload.address.strip() or None
        existing = connection.execute(
            "SELECT is_deleted, address FROM contract_place WHERE contract_id = ? AND place_id = ?", (cid, place_id)
        ).fetchone()
        if existing and not existing["is_deleted"]:
            raise HTTPException(status_code=409, detail="That place is already on this contract.")
        if existing:  # a soft-deleted link → restore it rather than duplicate the PK
            # A blank address on re-add means "restore the link as it was" — it never
            # clears the stored address (that's the audited Edit-address control's job).
            # A typed address that differs is a real field change: it must carry its own
            # update op (before/after), or it would be invisible in the history and
            # silently revert on the next seed+replay rebuild.
            old_addr = existing["address"]
            new_addr = addr if addr is not None and normalize_value(addr) != normalize_value(old_addr) else None
            with connection:
                if new_addr is not None:
                    connection.execute(
                        "UPDATE contract_place SET is_deleted = 0, address = ? WHERE contract_id = ? AND place_id = ?",
                        (new_addr, cid, place_id),
                    )
                else:
                    connection.execute(
                        "UPDATE contract_place SET is_deleted = 0 WHERE contract_id = ? AND place_id = ?",
                        (cid, place_id),
                    )
            try:
                corrections_db.record_operation(
                    clog, op="restore", db_table="contract_place",
                    pk={"place_id": int(place_id), "contract_id": int(cid)}, by=payload.reviewer, reason=reason,
                )
            except Exception:
                # compensation-lite (§0 Phase A4): undo the whole unlogged restore
                with connection:
                    connection.execute(
                        "UPDATE contract_place SET is_deleted = 1, address = ? WHERE contract_id = ? AND place_id = ?",
                        (old_addr, cid, place_id),
                    )
                raise
            if new_addr is not None:
                try:
                    corrections_db.record_operation(
                        clog, op="update", db_table="contract_place",
                        pk={"place_id": int(place_id), "contract_id": int(cid)}, field="address",
                        before_value=old_addr, after_value=new_addr, by=payload.reviewer, reason=reason,
                    )
                except Exception:
                    # the restore op above IS logged — undo only the address change
                    with connection:
                        connection.execute(
                            "UPDATE contract_place SET address = ? WHERE contract_id = ? AND place_id = ?",
                            (old_addr, cid, place_id),
                        )
                    raise
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
        try:
            corrections_db.record_operation(
                clog, op="delete" if removed else "restore", db_table="contract_place",
                pk={"place_id": int(place_id), "contract_id": int(cid)},
                by=action.reviewer, reason=action.reason.strip() or None,
            )
        except Exception:
            # compensation-lite (§0 Phase A4): unlogged flip → put it back
            with connection:
                connection.execute(
                    "UPDATE contract_place SET is_deleted = ? WHERE contract_id = ? AND place_id = ?",
                    (0 if removed else 1, cid, place_id),
                )
            raise
    finally:
        clog.close()
        connection.close()
    return {"ok": True, "removed": removed}


@app.post("/api/db/contract/{cid}/place/{place_id}/remove")
@serialized_write
def remove_place(cid: str, place_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_place_removed(cid, place_id, removed=True, action=action)


@app.post("/api/db/contract/{cid}/place/{place_id}/restore")
@serialized_write
def restore_place(cid: str, place_id: str, action: RecordAction) -> dict[str, Any]:
    return _set_place_removed(cid, place_id, removed=False, action=action)


@app.post("/api/db/contract/{cid}/place/{place_id}/address")
@serialized_write
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
        try:
            corrections_db.record_operation(
                clog, op="update", db_table="contract_place",
                pk={"place_id": int(place_id), "contract_id": int(cid)}, field="address",
                before_value=old, after_value=new, by=payload.reviewer, reason=payload.reason.strip() or None,
            )
        except Exception:
            # compensation-lite (§0 Phase A4): unlogged edit → restore the old address
            with connection:
                connection.execute(
                    "UPDATE contract_place SET address = ? WHERE contract_id = ? AND place_id = ?", (old, cid, place_id)
                )
            raise
    finally:
        clog.close()
        connection.close()
    return {"ok": True, "value": payload.address.strip()}


# ---------------------------------------------------------------------------
# "Needs review" — DB-intrinsic data-quality flags (Tier 1). Computed live over
# main.db (no Word dependency, is_deleted-filtered); fixing a record removes its
# flag on the next load. Dismiss = "reviewed, not an error" (append-only log).
# An undo appends a "restore" row; a key's last row decides (older rows carry no
# action and are dismissals).
# ---------------------------------------------------------------------------


def dismissed_flag_keys() -> set[str]:
    dismissed: set[str] = set()
    for row in load_jsonl(FLAG_DISMISSALS_PATH):
        key = row.get("key")
        if not key:
            continue
        if row.get("action") == "restore":
            dismissed.discard(key)
        else:
            dismissed.add(key)
    return dismissed


WORD_DATE_GROUP_META = {
    "label": "Word summary gives another date",
    "severity": "medium",
    "explanation": (
        "The Word summary gives a different registration date from the database."
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


def _attach_flag_meta(connection: sqlite3.Connection, items: list[dict[str, Any]]) -> None:
    """Give every flag the one-line locator the browse rows carry (date · folio ·
    register; '#id · n contracts' for people), so the worklist reads like the list
    it sits in. One query per table, chunked under SQLite's parameter limit."""
    by_table: dict[str, set[str]] = {}
    for f in items:
        by_table.setdefault(f["table"], set()).add(str(f["pk"]))
    meta: dict[tuple[str, str], str] = {}

    def chunks(values: set[str]) -> Any:
        ordered = sorted(values)
        for start in range(0, len(ordered), 400):
            yield ordered[start:start + 400]

    for pks in chunks(by_table.get("contract", set())):
        marks = ",".join("?" * len(pks))
        for r in connection.execute(
            f"SELECT contract_id, registration_date, folio, folder, series FROM contract WHERE contract_id IN ({marks})", pks
        ):
            meta[("contract", str(r["contract_id"]))] = search_meta(
                r["registration_date"], r["folio"], r["folder"], series=r["series"]
            )
    for pks in chunks(by_table.get("sub_contract", set())):
        marks = ",".join("?" * len(pks))
        for r in connection.execute(
            f"SELECT contract_id, registration_date, folio, folder, series, sub_type FROM sub_contract "
            f"WHERE contract_id IN ({marks})", pks
        ):
            meta[("sub_contract", str(r["contract_id"]))] = search_meta(
                r["registration_date"], r["folio"], r["folder"], r["sub_type"], series=r["series"]
            )
    for pks in chunks(by_table.get("person", set())):
        marks = ",".join("?" * len(pks))
        for r in connection.execute(
            f"SELECT p.person_id, (SELECT COUNT(DISTINCT iv.contract_id) FROM investor iv "
            f"WHERE iv.person_id = p.person_id AND iv.is_deleted = 0) AS n FROM person p WHERE p.person_id IN ({marks})",
            pks,
        ):
            meta[("person", str(r["person_id"]))] = _person_search_meta(r["person_id"], r["n"])
    for f in items:
        f["meta"] = meta.get((f["table"], str(f["pk"])), "")


PERSON_SPLIT_GROUP_META = {
    "label": "Flagged as possibly two people",
    "severity": "medium",
    "explanation": (
        "A reviewer flagged this entry in People as possibly two people, with a note. "
        "Read the note, then the contracts; if the flag is wrong, lift it on the entry's page."
    ),
}


def _person_split_flag_items(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Needs-review items for every standing split flag.

    The first worklist group that reads the op-log rather than `main.db`:
    read-only, a handful of rows, and the one place outside People where a
    flag can be found by whoever later has to act on it.
    """
    if not person_linkage_enabled():
        return []
    clog = open_corrections()
    try:
        latest = _latest_person_events(clog)
    finally:
        clog.close()
    flags = _person_standing_split_flags(latest)
    if not flags:
        return []
    marks = ",".join("?" for _ in flags)
    names = {
        int(row["person_id"]): person_display_name(
            row["first_name"],
            row["last_name"],
            row["nickname"],
            row["father_mother"],
            row["grandfather"],
        )
        for row in connection.execute(
            "SELECT person_id, first_name, father_mother, grandfather, last_name, nickname "
            f"FROM person WHERE person_id IN ({marks}) AND is_deleted=0",
            [int(flag["person_id"]) for flag in flags],
        )
    }
    items: list[dict[str, Any]] = []
    for flag in flags:
        person_id = int(flag["person_id"])
        if person_id not in names:
            continue
        note = str(flag["rationale"] or "").strip()
        marks = flag.get("marks") or {}
        marked = (
            f" · {marks['other']} of {marks['total']} appearances marked as someone else's"
            if marks
            else ""
        )
        items.append(
            {
                # The root flag's event id in the key: a lifted and later
                # re-flagged entry is a new item, so an old dismissal cannot
                # hide it, while a change of marks keeps the item (and its
                # dismissal) it had.
                "key": f"person:{person_id}:person_split_flagged:{flag['root_event_id']}",
                "group": "person_split_flagged",
                "table": "person",
                "pk": str(person_id),
                "title": (
                    f"{names[person_id]} — {flag['created_by'] or 'a reviewer'}, "
                    f"{_plain_date(str(flag['created_at']))}"
                    + (f": {note}" if note else "")
                    + marked
                ),
                "severity": PERSON_SPLIT_GROUP_META["severity"],
                "explanation": PERSON_SPLIT_GROUP_META["explanation"],
                "fix": {"kind": "identity"},
            }
        )
    return items


def flag_group_meta() -> dict[str, dict[str, Any]]:
    """Every Needs-review group's label, severity and explanation, by group id."""
    return {
        **data_quality.GROUP_META,
        **jewish_review.GROUP_META,
        "word_date_differs": WORD_DATE_GROUP_META,
        "person_split_flagged": PERSON_SPLIT_GROUP_META,
    }


@app.get("/api/db/flags")
def db_flags() -> dict[str, Any]:
    dismissed = dismissed_flag_keys()
    connection = open_db()
    try:
        items = (
            data_quality.flags(connection)
            + _word_date_flags(connection)
            + jewish_review.flags(connection)
            + _person_split_flag_items(connection)
        )
        items = [f for f in items if f["key"] not in dismissed]
        _attach_flag_meta(connection, items)
    finally:
        connection.close()
    meta_lookup = flag_group_meta()
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
            "key": key, "action": "dismiss", "reviewer": action.reviewer, "reason": action.reason.strip(),
            "at": datetime.now(timezone.utc).isoformat(),
        }) + "\n")
    return {"ok": True, "dismissed": key}


class FlagRestore(BaseModel):
    reviewer: str = Field(min_length=1)


@app.post("/api/db/flags/{key:path}/restore")
def restore_flag(key: str, action: FlagRestore) -> dict[str, Any]:
    """Undo a dismissal: the item comes back on the next load (append-only)."""
    if key not in dismissed_flag_keys():
        raise HTTPException(status_code=409, detail="This item is not dismissed, so there is nothing to undo.")
    FLAG_DISMISSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FLAG_DISMISSALS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "key": key, "action": "restore", "reviewer": action.reviewer,
            "at": datetime.now(timezone.utc).isoformat(),
        }) + "\n")
    return {"ok": True, "restored": key}


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
@serialized_write
def create_contract(payload: ContractCreate) -> dict[str, Any]:
    reject_placeholder_text(**{
        "Firm name": payload.firm_name, "Folio": payload.folio, "Series": payload.series,
        "Folder": payload.folder, "Economic activity": payload.economic_activity, "Narrative": payload.document,
    })
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
                    detail=f"Contract {payload.register_number} already exists — if this belongs to that contract, add it as a sub-contract instead.",
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
@serialized_write
def create_sub_contract(payload: SubContractCreate) -> dict[str, Any]:
    reject_placeholder_text(**{
        "Sub-firm name": payload.sub_firm_name, "Folio": payload.folio, "Series": payload.series,
        "Folder": payload.folder, "Narrative": payload.document,
    })
    if payload.sub_type not in SUB_TYPES:
        raise HTTPException(status_code=400, detail=f"sub_type must be one of {SUB_TYPES}.")
    if not DATE_OR_BLANK.match(payload.registration_date):
        raise HTTPException(status_code=400, detail="Registration date must be YYYY-MM-DD.")
    if payload.end_date and not DATE_OR_BLANK.match(payload.end_date):
        raise HTTPException(status_code=400, detail="End date must be YYYY-MM-DD (or empty).")
    connection = open_db()
    try:
        parent = connection.execute(
            "SELECT contract_id FROM contract WHERE contract_id = ? AND is_deleted = 0",
            (payload.main_contract_id,),
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
               LEFT JOIN investor i ON i.person_id = p.person_id AND i.is_deleted = 0
                    AND EXISTS (SELECT 1 FROM contract cc WHERE cc.contract_id = i.contract_id AND cc.is_deleted = 0)
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
        "results": _person_hits_with_identity(
            [
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
        )
    }


@app.get("/api/db/same-surname")
def same_surname(last_name: str = "", first_name: str = "") -> dict[str, Any]:
    """The near-match wall: existing persons whose surname matches (diacritic
    and case insensitive), shown before a new person may be created. Persons
    sharing the typed first name (same matching) come first; `total` is the
    uncapped count, so the wall never presents 12 of 86 as the whole set."""
    needle = _lookup_norm(last_name)
    if not needle:
        return {"results": [], "total": 0}
    first = _lookup_norm(first_name)
    connection = open_db()
    try:
        rows = connection.execute(
            """SELECT p.person_id, p.first_name, p.father_mother, p.last_name, p.nickname,
                      count(DISTINCT i.contract_id) AS appearances,
                      group_concat(DISTINCT pl.place_name) AS residences
               FROM person p
               LEFT JOIN investor i ON i.person_id = p.person_id AND i.is_deleted = 0
                    AND EXISTS (SELECT 1 FROM contract cc WHERE cc.contract_id = i.contract_id AND cc.is_deleted = 0)
               LEFT JOIN place pl ON pl.place_id = i.place_of_residence
               WHERE p.is_deleted = 0 AND trim(coalesce(p.last_name, '')) <> ''
               GROUP BY p.person_id""",
        ).fetchall()
    finally:
        connection.close()
    matched = [r for r in rows if _lookup_norm(r["last_name"]) == needle]
    matched.sort(
        key=lambda r: (
            0 if first and _lookup_norm(r["first_name"]) == first else 1,
            -r["appearances"],
            person_display_name(r["first_name"], r["last_name"], r["nickname"]),
        )
    )
    hits = [
        {
            "person_id": str(r["person_id"]),
            "display_name": person_display_name(r["first_name"], r["last_name"], r["nickname"]),
            "father_mother": (r["father_mother"] or "").strip(),
            "residences": (r["residences"] or "").replace(",", ", "),
            "appearances": r["appearances"],
        }
        for r in matched[:12]
    ]
    return {"results": _person_hits_with_identity(hits), "total": len(matched)}


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
               LEFT JOIN person p ON p.person_id = i.person_id AND p.is_deleted = 0
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
@serialized_write
def create_investor(payload: InvestorCreate) -> dict[str, Any]:
    """Add a person to a contract: person (reused or new) + investor row +
    capital tranche (own, or joining an existing one) + the group link — one
    audited operation group, replay-safe. `is_joint` is derived from the
    resulting group structure, including on existing siblings."""
    if payload.person_id is None and payload.new_person is None:
        raise HTTPException(status_code=400, detail="Pick an existing person or describe a new one.")
    reject_placeholder_text(**{
        "Partnership name": payload.partnership_name, "Non-cash contribution": payload.investment_non_cash,
        "Title": payload.title, "Residence": payload.residence, "Origin": payload.origin,
        "Profession": payload.profession, "Note": payload.note,
        **({
            "First name": payload.new_person.first_name, "Father / mother": payload.new_person.father_mother,
            "Last name": payload.new_person.last_name,
        } if payload.new_person else {}),
    })
    if payload.join_investment_id is None and payload.role not in ("gp", "lp"):
        raise HTTPException(status_code=400, detail="Choose general partner or external investor (accomandante).")
    connection = open_db()
    try:
        contract = connection.execute(
            "SELECT contract_id FROM contract WHERE contract_id = ? AND is_deleted = 0",
            (payload.contract_id,),
        ).fetchone()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found.")
        reviewer = payload.reviewer.strip()
        reason = f"added partner on contract {payload.contract_id}"
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
                raise HTTPException(status_code=404, detail="That stake does not belong to this contract.")
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
                    try:
                        corrections_db.record_operation(
                            clog,
                            op="update",
                            db_table="investor",
                            pk={"investor_id": int(sib["investor_id"])},
                            by=reviewer,
                            field="is_joint",
                            before_value="0",
                            after_value="1",
                            reason=f"joint stake gained a partner ({reason})",
                        )
                    except Exception:
                        # compensation-lite (§0 Phase A4): unlogged flip → put it back
                        with connection:
                            connection.execute(
                                "UPDATE investor SET is_joint = 0 WHERE investor_id = ?",
                                (sib["investor_id"],),
                            )
                        raise
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
        identity = person_identity_statuses([int(person["person_id"]) for person in persons])
        for person in persons:
            status = identity[int(person["person_id"])]
            person["identity_hint"] = _person_identity_hint(status)
        return {
            "contract_id": str(contract_id),
            "contract_title": contract["firm_name"] or f"Contract {contract_id}",
            "contract_date": display_date(contract["registration_date"]),
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
@serialized_write
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
@serialized_write
def approve_correction(proposal_id: str, action: CorrectionAction) -> dict[str, Any]:
    return _transition(proposal_id, action, to_status="approved", from_status="proposed")


@app.post("/api/corrections/{proposal_id}/reject")
@serialized_write
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
    replay. A failure here propagates: the callers compensate by restoring the
    pre-edit value, so no unlogged main.db write survives (§0 Phase A4)."""
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
@serialized_write
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

    connection = open_db()  # busy_timeout + the tests' patch point (§0 Phase A)
    try:
        if field not in table_columns(connection, table):
            raise HTTPException(status_code=400, detail=f"'{field}' is not a column of {table}.")
        current = read_db_value(connection, table, pk_value, field)
        if normalize_value(current) != normalize_value(proposal.get("current_value")):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This field was changed while you were editing it (it now reads "
                    f"‘{normalize_value(current)}’). The record was not changed; reload the "
                    f"page to see the current value."
                ),
            )
        new_value: Any = proposal["proposed_value"]
        if proposal.get("change_type") == "clear":
            new_value = None  # set the column to SQL NULL (renders as "—")
        elif meta["input_type"] in ("number", "bool"):
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
    try:
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
    except Exception:
        # compensation-lite (§0 Phase A4): the UPDATE committed but its op-log
        # entry failed — restore the pre-edit value so no unlogged change
        # survives into a rebuild; the proposal stays 'approved' for a retry
        undo = open_db()
        try:
            with undo:
                undo.execute(f"UPDATE {table} SET {field} = ? WHERE {pk_col} = ?", (current, pk_value))
        finally:
            undo.close()
        raise
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
@serialized_write
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

    connection = open_db()  # busy_timeout + the tests' patch point (§0 Phase A)
    try:
        if field not in table_columns(connection, table):
            raise HTTPException(status_code=400, detail=f"'{field}' is not a column of {table}.")
        current = read_db_value(connection, table, pk_value, field)
        # Drift guard (finding A4/D4, mirror of apply's): revert may only undo the
        # value THIS proposal applied. If the field was edited again since, a blind
        # revert would silently destroy that newer work — refuse instead.
        if normalize_value(current) != normalize_value(proposal.get("proposed_value")):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This field has been edited since this correction was applied (now "
                    f"'{normalize_value(current)}') — re-check before reverting."
                ),
            )
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
    try:
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
    except Exception:
        # compensation-lite (§0 Phase A4): unlogged revert → put the value back
        undo = open_db()
        try:
            with undo:
                undo.execute(f"UPDATE {table} SET {field} = ? WHERE {pk_col} = ?", (current, pk_value))
        finally:
            undo.close()
        raise
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
