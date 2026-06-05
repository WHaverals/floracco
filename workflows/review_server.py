"""Local API server for the FlorAcco Word-DB-image review app.

Run:
    uv run uvicorn workflows.review_server:app --reload

The server reads derived QA outputs and writes review decisions only. It does
not update SQLite, Word files, image files, or any source corpus artifact.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from workflows.word_pipeline import act_components_for_review


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QA_PACKET_PATH = PROJECT_ROOT / "data/derived/word-pipeline/06_qa_packet/word_db_match_qa_packet.jsonl"
LINK_CANDIDATES_PATH = (
    PROJECT_ROOT
    / "data/derived/word-pipeline/05_db_candidate_matches/source_entry_db_link_candidates.jsonl"
)
SOURCE_ENTRIES_PATH = (
    PROJECT_ROOT / "data/derived/word-pipeline/04_source_entries/source_entries.jsonl"
)
IMAGE_CANDIDATES_PATH = (
    PROJECT_ROOT
    / "data/derived/word-pipeline/07_image_links/source_entry_image_candidates.jsonl"
)
REGISTER_SUMMARY_PATH = (
    PROJECT_ROOT / "data/derived/word-pipeline/05_db_candidate_matches/register_match_summary.csv"
)
REVIEW_DIR = PROJECT_ROOT / "data/derived/word-pipeline/08_review_decisions"
DECISIONS_PATH = REVIEW_DIR / "review_decisions.csv"
CORRECTIONS_DIR = PROJECT_ROOT / "data/derived/word-pipeline/10_corrections"
PROPOSALS_PATH = CORRECTIONS_DIR / "corrections_proposals.jsonl"
EVENTS_PATH = CORRECTIONS_DIR / "corrections_events.jsonl"
# Derived "possibly needs correction" queue (built by workflows/correction_candidates.py)
# plus its append-only human dismissal log. Candidates are hypotheses, never writes.
CANDIDATES_PATH = CORRECTIONS_DIR / "correction_candidates.jsonl"
CANDIDATE_DISMISSALS_PATH = CORRECTIONS_DIR / "correction_candidate_dismissals.jsonl"
DEFAULT_DB_PATH = PROJECT_ROOT / "data/sqlite/main.db"
load_dotenv(PROJECT_ROOT / ".env")

# Fields a reviewer may correct in v1: scalar values only, safe to edit as plain
# text/date/number/enum. Foreign keys (person/currency/place ids) are deliberately
# excluded — they need entity pickers and a different UX. The primary key column
# per table is also fixed here.
PRIMARY_KEY_COLUMN = {"contract": "contract_id", "sub_contract": "contract_id", "person": "person_id"}
CORRECTABLE_FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "contract": {
        "firm_name": {"label": "Firm name", "input_type": "text"},
        "registration_date": {"label": "Registration date", "input_type": "date"},
        "start_date": {"label": "Start date", "input_type": "date"},
        "folio": {"label": "Folio", "input_type": "text"},
        "total": {"label": "Total capital", "input_type": "number"},
        "duration_months": {"label": "Duration (months)", "input_type": "number"},
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
    },
    "person": {
        "first_name": {"label": "First name", "input_type": "text"},
        "father_mother": {"label": "Father / mother", "input_type": "text"},
        "grandfather": {"label": "Grandfather", "input_type": "text"},
        "last_name": {"label": "Last name", "input_type": "text"},
        "nickname": {"label": "Nickname", "input_type": "text"},
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
    "review_priority",
    "recommended_review_bucket",
    "main_judgment",
    "image_judgment",
    "field_correction_needed",
    "next_action",
    "review_note",
    "image_candidate_paths",
    "selected_db_row_ids",
    "rejected_db_row_ids",
    "suggested_relationship_type",
]


class ReviewDecision(BaseModel):
    reviewer: str = Field(min_length=1)
    # Content-stable entry identity; review decisions are keyed on this so they
    # survive re-segmentation. source_entry_id is kept for display only.
    source_entry_key: str = ""
    source_entry_id: str = ""
    suggested_db_row_id: str = ""
    register_id: str = ""
    review_priority: str = ""
    recommended_review_bucket: str = ""
    main_judgment: str
    image_judgment: str
    field_correction_needed: str
    next_action: str
    review_note: str = ""
    image_candidate_paths: str = ""
    selected_db_row_ids: list[str] = Field(default_factory=list)
    rejected_db_row_ids: list[str] = Field(default_factory=list)
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
    suggested_db_row_id = str(row.get("suggested_db_row_ids") or row.get("top_db_row_id") or "")
    return entry_identity(row), suggested_db_row_id


def review_id_for(entry_identity_value: str, suggested_db_row_id: str) -> str:
    return f"{entry_identity_value}__{suggested_db_row_id}"


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
        source_entry_id, suggested_db_row_id = review_key(row)
        row["case_index"] = index
        row["review_id"] = review_id_for(source_entry_id, suggested_db_row_id)
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
    if not path_text:
        raise HTTPException(status_code=404, detail="No image path provided.")
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    allowed_roots = [PROJECT_ROOT.resolve()]
    images_root = os.getenv("FLORACCO_IMAGES_ROOT")
    if images_root:
        allowed_roots.append(Path(images_root).expanduser().resolve())
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Image path is outside allowed project roots.")
    if not resolved.exists() or not resolved.is_file():
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
        }
    return metrics


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    rows = load_qa_rows()
    decisions = decisions_by_review_id()
    return {
        "qa_packet_path": str(QA_PACKET_PATH.relative_to(PROJECT_ROOT)),
        "decisions_path": str(DECISIONS_PATH.relative_to(PROJECT_ROOT)),
        "total_cases": len(rows),
        "reviewed_cases": sum(1 for row in rows if row["review_id"] in decisions),
        "priorities": sorted({str(row.get("review_priority") or "") for row in rows if row.get("review_priority")}),
        "buckets": sorted(
            {str(row.get("recommended_review_bucket") or "") for row in rows if row.get("recommended_review_bucket")}
        ),
        "registers": sorted({str(row.get("register_id") or "") for row in rows if row.get("register_id")}),
    }


# Word-entry match statuses, in the order they read as a coverage funnel
# (most-trusted → least). Mirrors register_match_summary.csv columns.
WORD_STATUS_FIELDS = [
    "matched_high_confidence",
    "matched_candidate",
    "matched_multiple",
    "ambiguous",
    "word_only",
]
WORD_STATUS_LABELS = {
    "matched_high_confidence": "High-confidence",
    "matched_candidate": "Candidate",
    "matched_multiple": "Multi-row",
    "ambiguous": "Ambiguous",
    "word_only": "Word-only",
}
PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def mtime_iso(path: Path) -> str | None:
    """File modification time as ISO-8601 UTC, or None if the file is absent."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def load_register_summary() -> list[dict[str, Any]]:
    """Per-register match counts from the pipeline (status counts + row totals)."""
    if not REGISTER_SUMMARY_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with REGISTER_SUMMARY_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            clean: dict[str, Any] = {"register_id": row.get("register_id") or ""}
            for key, value in row.items():
                if key == "register_id":
                    continue
                try:
                    clean[key] = int(value)
                except (TypeError, ValueError):
                    clean[key] = 0
            rows.append(clean)
    return rows


def is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    """Read-only progress/coverage/exports snapshot for the dashboard.

    Aggregates the human logs (decisions, corrections) and the derived pipeline
    outputs (QA packet, register summary). Writes nothing. Numbers sourced from
    *derived* files move on a pipeline rebuild; numbers from *logs* are
    authoritative — the UI labels each accordingly via ``freshness``.
    """
    qa_rows = load_qa_rows()
    decisions = decisions_by_review_id()
    decision_rows = list(decisions.values())

    # --- Reconcile progress (queue is the QA packet) ---
    def grouped(field: str) -> dict[str, dict[str, int]]:
        groups: dict[str, dict[str, int]] = {}
        for row in qa_rows:
            label = str(row.get(field) or "—")
            bucket = groups.setdefault(label, {"total": 0, "reviewed": 0})
            bucket["total"] += 1
            if row["review_id"] in decisions:
                bucket["reviewed"] += 1
        return groups

    by_priority = [
        {"label": label, **counts}
        for label, counts in sorted(
            grouped("review_priority").items(), key=lambda kv: PRIORITY_ORDER.get(kv[0], 9)
        )
    ]
    by_bucket = [
        {"label": label, **counts}
        for label, counts in sorted(grouped("recommended_review_bucket").items(), key=lambda kv: -kv[1]["total"])
    ]

    verdicts = {"confirmed": 0, "rejected": 0, "not_sure": 0}
    for row in decision_rows:
        action = str(row.get("next_action") or "")
        if action == "approve_link":
            verdicts["confirmed"] += 1
        elif action == "reject_link":
            verdicts["rejected"] += 1
        else:
            verdicts["not_sure"] += 1

    # --- Coverage (from the per-register match summary) ---
    registers = load_register_summary()
    status_totals = {field: sum(r.get(field, 0) for r in registers) for field in WORD_STATUS_FIELDS}
    register_table = sorted(
        (r for r in registers if r.get("word_entry_count", 0) or r.get("db_row_count", 0)),
        key=lambda r: -r.get("word_entry_count", 0),
    )
    images_with = sum(1 for row in qa_rows if str(row.get("image_candidate_paths") or "").strip())
    images_need_review = sum(1 for row in qa_rows if is_truthy(row.get("image_candidates_need_review")))

    # --- Corrections funnel + applied-write audit ---
    proposals = load_proposals()
    proposal_status = Counter(str(p.get("status") or "unknown") for p in proposals)
    events = load_jsonl(EVENTS_PATH)
    applied_events = [event for event in events if event.get("event") == "applied"]
    recent_applied = sorted(applied_events, key=lambda e: str(e.get("at") or ""), reverse=True)[:10]

    candidates = load_candidates()
    dismissals = load_candidate_dismissals()
    handled = open_proposals_by_field()
    candidate_total = len(candidates)
    candidate_dismissed = sum(1 for c in candidates if str(c.get("candidate_key")) in dismissals)
    candidate_handled = sum(
        1
        for c in candidates
        if str(c.get("candidate_key")) not in dismissals
        and (str(c.get("db_row_id")), str(c.get("field"))) in handled
    )
    candidate_open = candidate_total - candidate_dismissed - candidate_handled

    return {
        "freshness": {
            "qa_packet_built_at": mtime_iso(QA_PACKET_PATH),
            "matches_built_at": mtime_iso(LINK_CANDIDATES_PATH),
            "decisions_updated_at": (
                max((str(r.get("updated_at") or "") for r in decision_rows), default="")
                or mtime_iso(DECISIONS_PATH)
            ),
            "corrections_updated_at": mtime_iso(PROPOSALS_PATH),
        },
        "reconcile": {
            "total_cases": len(qa_rows),
            "reviewed_cases": sum(1 for row in qa_rows if row["review_id"] in decisions),
            "by_priority": by_priority,
            "by_bucket": by_bucket,
            "decisions": verdicts,
            "decisions_logged": len(decision_rows),
        },
        "coverage": {
            "word_entry_total": sum(status_totals.values()),
            "word_status_totals": status_totals,
            "word_status_labels": WORD_STATUS_LABELS,
            "db_row_total": sum(r.get("db_row_count", 0) for r in registers),
            "db_only_total": sum(r.get("db_only", 0) for r in registers),
            "registers": register_table,
            "images": {"with_candidates": images_with, "need_review": images_need_review, "queue_rows": len(qa_rows)},
        },
        "corrections": {
            "proposals_total": len(proposals),
            "proposals_by_status": dict(proposal_status),
            "applied_writes": len(applied_events),
            "recent_applied": [
                {
                    "db_row_id": event.get("db_row_id"),
                    "field": event.get("field"),
                    "pre_image": event.get("pre_image"),
                    "post_image": event.get("post_image"),
                    "by": event.get("by"),
                    "at": event.get("at"),
                }
                for event in recent_applied
            ],
            "candidates": {
                "total": candidate_total,
                "open": candidate_open,
                "handled": candidate_handled,
                "dismissed": candidate_dismissed,
                "by_strength": dict(Counter(str(c.get("strength") or "unknown") for c in candidates)),
                "by_family": dict(Counter(str(c.get("family") or "unknown") for c in candidates)),
            },
        },
    }


# Downloadable exports — stream the existing human/derived files as attachments.
# Read-only: nothing is generated or written, just served for the reviewer/FT.
EXPORT_FILES: dict[str, tuple[Path, str]] = {
    "decisions": (DECISIONS_PATH, "text/csv"),
    "proposals": (PROPOSALS_PATH, "application/x-ndjson"),
    "candidates": (CANDIDATES_PATH, "application/x-ndjson"),
}


@app.get("/api/export/{name}")
def export_file(name: str) -> FileResponse:
    entry = EXPORT_FILES.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown export.")
    path, media_type = entry
    if not path.exists():
        raise HTTPException(status_code=404, detail="Nothing to export yet.")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/api/cases")
def cases(
    priority: str = "All",
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
        if priority != "All" and row.get("review_priority") != priority:
            continue
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
            "review_priority": row.get("review_priority"),
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
    decision_row["updated_at"] = datetime.now(timezone.utc).isoformat()
    entry_identity_value = decision.source_entry_key or decision.source_entry_id
    decision_row["review_id"] = review_id_for(entry_identity_value, decision.suggested_db_row_id)

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


def contract_detail(connection: sqlite3.Connection, raw_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM contract WHERE contract_id = ?", (raw_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contract not found.")
    data = dict(row)
    currency = lookup_value(connection, "currency", "currency_id", data.get("currency_id"), "currency")
    sector = lookup_value(
        connection, "economic_activity", "ec_activity_id", data.get("economic_sector"), "activity"
    )
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
        record_field("Automatic renewal", "contract", None, None, yes_no(data.get("automatic_renewal")), corrections),
        record_field("Economic sector", "contract", None, None, display_text(sector), corrections),
    ]

    places = connection.execute(
        """
        SELECT cp.address AS address, p.place_name AS place_name
        FROM contract_place cp
        LEFT JOIN place p ON p.place_id = cp.place_id
        WHERE cp.contract_id = ?
        """,
        (raw_id,),
    ).fetchall()
    investors = connection.execute(
        """
        SELECT i.person_id AS person_id, i.profession AS profession,
               i.is_widow AS is_widow, i.is_guardian AS is_guardian, i.is_joint AS is_joint,
               i.place_of_residence AS place_of_residence,
               p.first_name AS first_name, p.last_name AS last_name, p.nickname AS nickname
        FROM investor i
        LEFT JOIN person p ON p.person_id = i.person_id
        WHERE i.contract_id = ?
        """,
        (raw_id,),
    ).fetchall()
    investments = connection.execute(
        "SELECT type, partnership_name, investment_cash, investment_non_cash FROM investment WHERE contract_id = ?",
        (raw_id,),
    ).fetchall()
    subs = connection.execute(
        """
        SELECT contract_id, sub_type, registration_date, folio, sub_firm_name
        FROM sub_contract WHERE main_contract_id = ?
        ORDER BY registration_date
        """,
        (raw_id,),
    ).fetchall()

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
    if investors:
        sections.append(
            {
                "title": f"Investors ({len(investors)})",
                "columns": ["Person", "Profession", "Residence", "Role"],
                "link_table": "person",
                "rows": [
                    {
                        "id": str(inv["person_id"]),
                        "cells": [
                            person_display_name(
                                inv["first_name"], inv["last_name"], inv["nickname"]
                            ),
                            display_text(inv["profession"]),
                            display_text(
                                lookup_value(
                                    connection, "place", "place_id", inv["place_of_residence"], "place_name"
                                )
                            ),
                            ", ".join(
                                flag
                                for flag, present in (
                                    ("widow", inv["is_widow"]),
                                    ("guardian", inv["is_guardian"]),
                                    ("joint", inv["is_joint"]),
                                )
                                if present in (1, "1", True)
                            )
                            or "—",
                        ],
                    }
                    for inv in investors
                ],
            }
        )
    if places:
        sections.append(
            {
                "title": f"Places ({len(places)})",
                "columns": ["Place", "Address"],
                "link_table": None,
                "rows": [
                    {
                        "id": "",
                        "cells": [display_text(pl["place_name"]), display_text(pl["address"])],
                    }
                    for pl in places
                ],
            }
        )
    if investments:
        sections.append(
            {
                "title": f"Investments ({len(investments)})",
                "columns": ["Type", "Partnership", "Cash", "Non-cash"],
                "link_table": None,
                "rows": [
                    {
                        "id": "",
                        "cells": [
                            display_text(iv["type"]),
                            display_text(iv["partnership_name"]),
                            display_text(iv["investment_cash"]),
                            display_text(iv["investment_non_cash"]),
                        ],
                    }
                    for iv in investments
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
        "sections": sections,
        "document": clean_document(data.get("document")),
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
        record_field("Recorded as woman", "person", None, None, yes_no(data.get("is_woman")), corrections),
    ]

    contracts = connection.execute(
        """
        SELECT i.contract_id AS contract_id, i.profession AS profession,
               c.firm_name AS firm_name, c.registration_date AS registration_date
        FROM investor i
        LEFT JOIN contract c ON c.contract_id = i.contract_id
        WHERE i.person_id = ?
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
            "investor — not a direct person-to-document link. Each is labelled "
            "with its contract."
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


@app.get("/api/db/search")
def db_search(
    table: str, q: str = "", limit: int = Query(default=60, ge=1, le=300)
) -> dict[str, Any]:
    if table not in DB_BROWSE_TABLES:
        raise HTTPException(status_code=400, detail="Unknown table.")
    term = q.strip()
    like = f"%{term}%"
    connection = open_db()
    try:
        if table == "contract":
            where = (
                "WHERE firm_name LIKE ? OR folio LIKE ? OR CAST(contract_id AS TEXT) LIKE ?"
                if term
                else ""
            )
            params = [like, like, like] if term else []
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM contract {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"SELECT contract_id, registration_date, folio, firm_name FROM contract {where} "
                "ORDER BY registration_date LIMIT ?",
                (*params, limit),
            ).fetchall()
            results = [
                {
                    "id": str(r["contract_id"]),
                    "row_id": f"contract:{r['contract_id']}",
                    "title": r["firm_name"] or f"Contract {r['contract_id']}",
                    "meta": " · ".join(
                        part.strip()
                        for part in (r["registration_date"], r["folio"])
                        if part and part.strip()
                    ),
                }
                for r in rows
            ]
        elif table == "sub_contract":
            where = (
                "WHERE sub_firm_name LIKE ? OR folio LIKE ? OR CAST(contract_id AS TEXT) LIKE ?"
                if term
                else ""
            )
            params = [like, like, like] if term else []
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM sub_contract {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"SELECT contract_id, registration_date, folio, sub_firm_name, sub_type FROM sub_contract {where} "
                "ORDER BY registration_date LIMIT ?",
                (*params, limit),
            ).fetchall()
            results = [
                {
                    "id": str(r["contract_id"]),
                    "row_id": f"sub_contract:{r['contract_id']}",
                    "title": r["sub_firm_name"] or f"Sub-contract {r['contract_id']}",
                    "meta": " · ".join(
                        part.strip()
                        for part in (r["sub_type"], r["registration_date"], r["folio"])
                        if part and part.strip()
                    ),
                }
                for r in rows
            ]
        else:  # person
            where = (
                "WHERE first_name LIKE ? OR last_name LIKE ? OR nickname LIKE ? "
                "OR CAST(person_id AS TEXT) LIKE ?"
                if term
                else ""
            )
            params = [like, like, like, like] if term else []
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM person {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"SELECT person_id, first_name, last_name, nickname FROM person {where} "
                "ORDER BY last_name, first_name LIMIT ?",
                (*params, limit),
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
        return {"table": table, "total": total, "shown": len(results), "results": results}
    finally:
        connection.close()


@app.get("/api/db/record/{table}/{record_id}")
def db_record(table: str, record_id: str) -> dict[str, Any]:
    if table not in DB_BROWSE_TABLES:
        raise HTTPException(status_code=400, detail="Unknown table.")
    connection = open_db()
    try:
        if table == "contract":
            return contract_detail(connection, record_id)
        if table == "sub_contract":
            return sub_contract_detail(connection, record_id)
        return person_detail(connection, record_id)
    finally:
        connection.close()


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
            WHERE i.contract_id = ?
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
                "SELECT COUNT(DISTINCT contract_id) AS c FROM investor WHERE person_id = ?",
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


@app.get("/api/word-entry/{source_entry_id}")
def word_entry(source_entry_id: str) -> dict[str, Any]:
    """Clean reading text + manuscript image(s) for one Word source entry.

    Powers the slide-in panel opened from a database record's linked Word
    source. Read-only; serves derived pipeline outputs.
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
        if meta["input_type"] == "number":
            new_value = int(proposal["proposed_value"])
        with connection:  # transaction: commit on success, rollback on error
            connection.execute(
                f"UPDATE {table} SET {field} = ? WHERE {pk_col} = ?", (new_value, pk_value)
            )
    finally:
        connection.close()

    now = datetime.now(timezone.utc).isoformat()
    run_id = f"apply-{now[:10]}"
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
        if meta["input_type"] == "number":
            restore = int(proposal["current_value"]) if proposal["current_value"] != "" else None
        with connection:
            connection.execute(
                f"UPDATE {table} SET {field} = ? WHERE {pk_col} = ?", (restore, pk_value)
            )
    finally:
        connection.close()

    now = datetime.now(timezone.utc).isoformat()
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
