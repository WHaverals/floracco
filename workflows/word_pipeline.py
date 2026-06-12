"""Stage 0/1 Word corpus pipeline.

This script is deliberately conservative:

- It never edits original Word files.
- It never writes to SQLite.
- It writes only regenerable outputs under ``data/derived/word-pipeline``.

Usage:
    uv run python workflows/word_pipeline.py inventory
    uv run python workflows/word_pipeline.py normalize
    uv run python workflows/word_pipeline.py validate-normalized
    uv run python workflows/word_pipeline.py extract-registers
    uv run python workflows/word_pipeline.py segment-entries
    uv run python workflows/word_pipeline.py match-db
    uv run python workflows/word_pipeline.py qa-packet
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from dotenv import load_dotenv


DEFAULT_OUTPUT_ROOT = Path("data/derived/word-pipeline")
MACOS_SOFFICE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
WORD_EXTENSIONS = {".doc", ".docx"}
WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
RELATIONSHIP_NAMESPACE = "{http://schemas.openxmlformats.org/package/2006/relationships}"
BRACKET_LABEL_RE = re.compile(r"\[[^\]]+\]")
FOLIO_NUMBER_PATTERN = r"(?:\d+(?:\[[^\]]+\]|\([^)]+\)|bis|[a-qu-z])?|n\.n)"
FOLIO_RE = re.compile(
    rf"\bc{{1,2}}\.\s*{FOLIO_NUMBER_PATTERN}\s*[rv]?\b",
    flags=re.IGNORECASE,
)
FOLIO_HEADING_RE = re.compile(
    rf"""
    ^\s*
    (?P<prefix>c{{1,2}})\.\s*
    (?P<start>{FOLIO_NUMBER_PATTERN})
    \s*
    (?P<start_side>[rv])?
    (?:
        \s*[-–]\s*
        (?:
            (?P<end>{FOLIO_NUMBER_PATTERN})?
            \s*
            (?P<end_side>[rv])?
        )
    )?
    (?P<trailing>\.?\s*(?:$|\s+.+$))
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)
DATE_RE = re.compile(
    r"\b(?:[0-3]?\d\s+[A-Za-zàèéìòù]+\s+1[4-8]\d{2}|1[4-8]\d{2})\b",
    flags=re.IGNORECASE,
)
EXTRACTION_ALLOWED_STATUSES = {"ok"}
# Margin notes ("A margine: vedi la disdetta ... a carta 48"). The transcribers
# append the margin note at the END of the act it annotates (verified against DB
# sub-contract rows: the note's date/carta match the *preceding* act's later
# sub-contracts; see LOG.md 2026-06-11). A margin note is therefore a
# cross-reference belonging to the previous entry's tail — never a date-context
# line, and never a source for the next entry's registration date.
MARGIN_NOTE_RE = re.compile(r"^\s*\(?\s*(?:a|in|nel)\s+margine\b", flags=re.IGNORECASE)
# A full day-month-year date ("18 settembre 1604"), as opposed to the bare
# year-like numbers DATE_RE also accepts. Bare numbers on an event-label line
# are act numbers, not years ("[nuova] 1775" = act 1775; 897 label lines carry
# such a number vs. 21 that carry a real full date).
FULL_DATE_RE = re.compile(
    r"\b[0-3]?\d\s+[A-Za-zàèéìòù]+\s+1[4-8]\d{2}\b",
    flags=re.IGNORECASE,
)
ENTRY_LABEL_RE = re.compile(r"^\s*\[(?P<label>[^\]]+)\]\s*(?P<trailing>.*)$")
EVENT_LABEL_MAP = {
    "nuova": "new_contract",
    "nuovo": "new_contract",
    "disdetta": "termination",
    "disdetta rescissa": "rescinded_termination",
    "cessione": "assignment",
    "modifica": "modification",
    "bilancio": "balance",
    "rinnovo": "renewal",
    "ratifica": "ratification",
    "proroga": "extension",
    "dichiarazione": "declaration",
    "variazione": "variation",
    "variation": "variation",
    "dissoluzione": "dissolution",
    "cessazione": "termination",
    "conferma": "confirmation",
    "divisione degli utili": "profit_distribution",
    "restituzione capitali": "capital_return",
    "restituzione di capitali": "capital_return",
    "stralcio di accomandita finita": "winding_up",
}
EVENT_LABEL_KEYWORDS = [
    ("new_contract", re.compile(r"\bnuov[ao]\b", flags=re.IGNORECASE)),
    ("termination", re.compile(r"\b(?:disdetta|termination|cessazione)\b", flags=re.IGNORECASE)),
    ("assignment", re.compile(r"\bcessione\b", flags=re.IGNORECASE)),
    ("modification", re.compile(r"\bmodifica\b", flags=re.IGNORECASE)),
    ("balance", re.compile(r"\bbilancio\b", flags=re.IGNORECASE)),
    ("renewal", re.compile(r"\b(?:rinnovo|rinnuovazione)\b", flags=re.IGNORECASE)),
    ("ratification", re.compile(r"\bratifica\b", flags=re.IGNORECASE)),
    ("extension", re.compile(r"\bproroga\b", flags=re.IGNORECASE)),
    ("declaration", re.compile(r"\bdichiarazione\b", flags=re.IGNORECASE)),
    ("variation", re.compile(r"\b(?:variazione|variation)\b", flags=re.IGNORECASE)),
    ("dissolution", re.compile(r"\bdissoluzione\b", flags=re.IGNORECASE)),
    ("continuation", re.compile(r"\bcontinuazione\b", flags=re.IGNORECASE)),
    ("increase", re.compile(r"\b(?:aumento|accrescimento)\b", flags=re.IGNORECASE)),
    ("registration", re.compile(r"\bregistrazione\b", flags=re.IGNORECASE)),
    ("winding_up", re.compile(r"\bstralcio\b", flags=re.IGNORECASE)),
    ("profit_distribution", re.compile(r"\bdivisione degli utili\b", flags=re.IGNORECASE)),
    ("capital_return", re.compile(r"\brestituzione (?:di )?capitali\b", flags=re.IGNORECASE)),
    ("confirmation", re.compile(r"\bconferma\b", flags=re.IGNORECASE)),
]
PLUS_EVENT_RE = re.compile(
    r"\+\s*\[?\s*(?:nuov[ao]|disdetta|cessione|modifica|bilancio|rinnovo|ratifica|proroga|dichiarazione|variazione|variation|dissoluzione|cessazione)\b",
    flags=re.IGNORECASE,
)
EVENT_NUMBER_RE = re.compile(r"\b(?P<number>\d{1,5})(?:\b|[^\d])")
REFERENCED_EVENT_RE = re.compile(r"\bdi\s+(?P<number>\d{1,5})\b", flags=re.IGNORECASE)
# Enumerated back-references like "di 669 e 798", "di 3533, 4086, 3243" or
# "di 3249 e di 3223": one Word act can disdire / cedere / modificare several
# accomandite at once, each a separate DB row carrying the same narrative. Capture
# the whole run of numbers (anchored on the first "di N", then comma/semicolon/`e`/
# `ed`/`di`-joined numbers), not just the first — otherwise a genuine combined act
# looks like a single-act sibling pile-up and a real co-act gets demoted. The run
# stops at the first token that is not a separator + number, so it never reaches
# the narrative body (e.g. "…798 Agnolo di Girolamo" stops after 798).
ENUMERATED_REFERENCED_RE = re.compile(
    r"\bdi\s+\d{1,5}(?:\s*(?:,|;|\be\b|\bed\b)\s*(?:di\s+)?\d{1,5})*",
    flags=re.IGNORECASE,
)
ITALIAN_MONTHS = {
    "gennaio": "01",
    "febbraio": "02",
    "marzo": "03",
    "aprile": "04",
    "maggio": "05",
    "giugno": "06",
    "luglio": "07",
    "agosto": "08",
    "settembre": "09",
    "ottobre": "10",
    "novembre": "11",
    "dicembre": "12",
    # Historical spellings and transcription slips observed in the corpus
    # (~27 dates total). Without these the date fails to parse and a margin/body
    # date can stand in for the act's own date (see LOG.md 2026-06-11,
    # contract:4395 "4 gennaro 1691/1692"). Unambiguous variants only; French
    # forms and "stante" (= the current month) are deliberately not mapped.
    "gennaro": "01",
    "genaio": "01",
    "febbraro": "02",
    "febbario": "02",
    "febraio": "02",
    "magio": "05",
    "giungo": "06",
    "giguno": "06",
    "agostro": "08",
    "ettembre": "09",
    "otobre": "10",
    "novembe": "11",
    "november": "11",
    "decembre": "12",
}
# Maps a normalized Word event label to the DB sub_contract.sub_type values it is
# compatible with. The DB only ever stores four sub_types
# (balance / renewal / termination / variation; see docs/data_dictionary.md and
# the original input rules), so every value below MUST be one of those four,
# except "new_contract" which targets the `contract` table rather than a sub_type.
#
# INTERPRETIVE LAYER (FT review pending): the narrative-label -> DB-sub_type
# mapping below encodes an editorial judgment about how Italian act labels were
# coded into the four DB categories. It is intentionally permissive (a label may
# be compatible with more than one sub_type) because the goal is candidate
# recall, not truth. Confirm these groupings with FT before they harden into
# anything authoritative. See LOG.md (2026-05-29) for the rationale and history.
DB_SUB_TYPES = {"termination", "renewal", "balance", "variation"}
DB_EVENT_TYPE_MAP = {
    "new_contract": {"contract"},
    "termination": {"termination"},
    "rescinded_termination": {"termination"},
    "assignment": {"variation", "termination"},
    "modification": {"variation"},
    "balance": {"balance"},
    "renewal": {"renewal"},
    "ratification": {"variation"},
    "extension": {"renewal", "variation"},
    "declaration": {"variation"},
    "variation": {"variation"},
    "dissolution": {"termination"},
    "confirmation": {"renewal", "variation"},
    "profit_distribution": {"balance", "variation"},
    "capital_return": {"variation", "termination"},
    "winding_up": {"termination", "variation"},
    "continuation": {"renewal", "variation"},
    "increase": {"variation"},
    "registration": {"variation"},
}
MATCH_OUTPUT_ALLOWED_STATUSES = {"candidate", "needs_review"}
CONTRACT_ID_COMPATIBLE_LABELS = {
    "combined_event",
    "confirmation",
    "continuation",
    "extension",
    "increase",
    "registration",
    "renewal",
}
MATCH_STATUS_LABELS = {
    "matched_high_confidence": "High-confidence candidate match",
    "matched_candidate": "Candidate match, review before accepting",
    "matched_multiple": "One Word entry appears to match multiple DB rows",
    "ambiguous": "Ambiguous match: plausible candidates need review",
    "word_only": "Word entry has no plausible DB match yet",
}
SIGNAL_LABELS = {
    "contract_id_exact": "Word event number exactly matches DB contract_id",
    "contract_id_from_event_number": "Word event number points to this DB contract_id",
    "component_contract_id_exact": "A component event number points to this DB contract_id",
    "main_contract_id_referenced": "Word reference number matches DB sub_contract.main_contract_id",
    "main_contract_id_from_event_number": "Word event number matches DB sub_contract.main_contract_id",
    "component_main_contract_id": "A component event number points to this related sub-contract row",
    "registration_date_exact": "Registration dates match exactly",
    "registration_date_stile_fiorentino": "Registration date matches after Florentine-calendar (stile fiorentino) year shift",
    "registration_year_match": "Entry is dated by year only, and the year matches the DB registration date",
    "date_in_narrative": "A date mentioned in the narrative (not the act's own date line) matches the DB registration date",
    "folio_exact": "Folio range matches exactly",
    "folio_within": "DB folio falls inside the Word entry's folio span",
    "folio_overlap": "Word and DB folio ranges overlap",
    "folio_adjacent": "Word and DB folios are one folio apart (possible original/current numbering)",
    "folio_partial": "At least one folio endpoint matches",
    "event_type_compatible": "Word event type is compatible with DB table/type",
    "component_event_type_compatible": "One Word event component is compatible with DB table/type",
    "text_similarity_good": "Word and DB narratives are textually similar by a recorded metric",
    "token_coverage_good": "Most distinctive Word terms also appear in the DB narrative",
    "shared_phrase_good": "Word and DB share a long phrase",
    "person_name_overlap": "DB person name appears in the Word entry",
    "firm_name_overlap": "DB firm or partnership name appears in the Word entry",
    "place_overlap": "DB place or address appears in the Word entry",
    "amount_overlap": "DB amount appears in the Word entry",
    "date_field_overlap": "A DB date field appears in the Word entry",
}
CONFLICT_LABELS = {
    "db_register_missing": "DB row has missing or malformed register metadata",
    "db_register_differs": "DB register differs from the Word register",
    "registration_date_differs": "Registration dates differ",
    "folio_differs": "Folio ranges differ",
    "event_type_table_differs": "Word event type points to a different DB table/type",
    "text_similarity_low": "Word and DB narratives have low text similarity",
}
MATCH_STOPWORDS = {
    "alla",
    "allo",
    "come",
    "con",
    "dalla",
    "dalle",
    "degli",
    "della",
    "delle",
    "detta",
    "dette",
    "detti",
    "detto",
    "dice",
    "essere",
    "firenze",
    "gli",
    "il",
    "in",
    "la",
    "le",
    "lo",
    "nel",
    "nella",
    "per",
    "ragione",
    "signor",
    "signora",
    "signori",
    "societa",
    "società",
    "sotto",
    "una",
}


@dataclass(frozen=True)
class WordFile:
    source_file: str
    source_path: str
    file_name: str
    extension: str
    file_size_bytes: int
    sha256: str
    is_status_file: bool
    status_from_filename: str
    archive: str | None
    series: str | None
    folder: str | None
    register_id: str | None
    normalized_file_name: str | None
    needs_normalization: bool


@dataclass(frozen=True)
class FolderRecord:
    register_id: str
    archive: str | None
    series: str | None
    folder: str
    source: str
    path_or_table: str


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    register_id: str | None = None
    source_file: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_repo_path(path: Path | str) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return project_root() / path


def load_config(args: argparse.Namespace) -> dict[str, Path]:
    load_dotenv(resolve_repo_path(".env"))
    data_root = Path(args.data_root or os.getenv("FLORACCO_DATA_ROOT", "data/corpus"))
    images_root = Path(args.images_root or os.getenv("FLORACCO_IMAGES_ROOT", data_root / "img"))
    db_path = Path(args.db_path or os.getenv("FLORACCO_DB_PATH", "data/sqlite/main.db"))
    output_root = Path(args.output_root or DEFAULT_OUTPUT_ROOT)
    return {
        "data_root": resolve_repo_path(data_root),
        "word_root": resolve_repo_path(data_root) / "word",
        "images_root": resolve_repo_path(images_root),
        "db_path": resolve_repo_path(db_path),
        "output_root": resolve_repo_path(output_root),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    ensure_dir(path.parent)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, title: str, lines: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"# {title}\n\n")
        handle.write(f"Generated: {utc_now()}\n\n")
        for line in lines:
            handle.write(f"{line}\n")


def infer_status(file_name: str) -> str:
    lower = file_name.lower()
    if lower.startswith("0_") or "list of archival sources" in lower:
        return "status_file"
    if "track changes" in lower:
        return "track_changes"
    if "clean_input completed" in lower:
        return "clean_input_completed"
    if "clean_input in progress" in lower:
        return "clean_input_in_progress"
    return "unknown"


def infer_folder(text: str) -> str | None:
    match = re.search(r"(?<!\d)(108\d{2}|1263bis|1262|1263)(?!\d)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return normalize_folder(match.group(1))


def normalize_folder(folder: str | None) -> str | None:
    if folder is None:
        return None
    folder = str(folder).strip()
    if not folder:
        return None
    if folder.lower() == "1263bis":
        return "1263bis"
    return folder


def series_for_folder(folder: str | None, raw_text: str = "") -> str | None:
    if folder is None:
        return None
    if folder.startswith("108"):
        return "Mercanzia"
    if folder.startswith("126") or "camera" in raw_text.lower() or re.search(r"\bCC\b", raw_text):
        return "Dipartimento esecutivo della Camera di Commercio"
    return None


def register_id_for(series: str | None, folder: str | None) -> str | None:
    if not folder or not series:
        return None
    if series == "Mercanzia":
        return f"Mercanzia_{folder}"
    if "Camera di Commercio" in series:
        return f"Camera_di_Commercio_{folder}"
    clean_series = re.sub(r"\W+", "_", series).strip("_")
    return f"{clean_series}_{folder}"


def normalized_name_for(register_id: str | None) -> str | None:
    if not register_id:
        return None
    return f"{register_id}.docx"


def inspect_word_file(path: Path, word_root: Path) -> WordFile:
    file_name = path.name
    status = infer_status(file_name)
    folder = infer_folder(file_name)
    series = series_for_folder(folder, file_name)
    register_id = register_id_for(series, folder)
    extension = path.suffix.lower()
    normalized_file_name = normalized_name_for(register_id)
    return WordFile(
        source_file=file_name,
        source_path=str(path.relative_to(project_root())),
        file_name=file_name,
        extension=extension,
        file_size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        is_status_file=status == "status_file",
        status_from_filename=status,
        archive="ASF" if register_id else None,
        series=series,
        folder=folder,
        register_id=register_id,
        normalized_file_name=normalized_file_name,
        needs_normalization=extension == ".doc",
    )


def find_word_files(word_root: Path) -> list[WordFile]:
    if not word_root.exists():
        return []
    paths = sorted(
        path
        for path in word_root.iterdir()
        if path.is_file() and path.suffix.lower() in WORD_EXTENSIONS
    )
    return [inspect_word_file(path, word_root) for path in paths]


def db_folder_records(db_path: Path) -> tuple[list[FolderRecord], list[Issue]]:
    issues: list[Issue] = []
    if not db_path.exists():
        return [], [Issue("error", "db_missing", f"SQLite database not found: {db_path}")]
    query = """
        SELECT DISTINCT archive, series, folder, 'contract' AS source_table FROM contract
        UNION
        SELECT DISTINCT archive, series, folder, 'sub_contract' AS source_table FROM sub_contract
        ORDER BY series, folder
    """
    records_by_register: dict[str, FolderRecord] = {}
    table_names_by_register: dict[str, set[str]] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(query):
            raw_archive = str(row["archive"] or "").strip()
            raw_series = str(row["series"] or "").strip()
            raw_folder = str(row["folder"] or "").strip()
            combined = f"{raw_series} {raw_folder}"
            folder = infer_folder(combined) or normalize_folder(raw_folder)
            if not folder:
                issues.append(
                    Issue(
                        "warning",
                        "db_folder_unrecognized",
                        f"Could not infer canonical register folder from DB values: series={raw_series!r}, folder={raw_folder!r}",
                    )
                )
                continue
            series = series_for_folder(folder, combined)
            archive = raw_archive.upper() if raw_archive else "ASF"
            register_id = register_id_for(series, folder) or f"Unknown_{folder}"
            table_names_by_register.setdefault(register_id, set()).add(str(row["source_table"]))
            records_by_register[register_id] = FolderRecord(
                register_id=register_id,
                archive=archive,
                series=series,
                folder=folder,
                source="db",
                path_or_table=",".join(sorted(table_names_by_register[register_id])),
            )
    return sorted(records_by_register.values(), key=lambda record: record.register_id), issues


def image_folder_records(images_root: Path) -> tuple[list[FolderRecord], list[Issue]]:
    issues: list[Issue] = []
    if not images_root.exists():
        return [], [Issue("warning", "images_root_missing", f"Images root not found: {images_root}")]
    records: list[FolderRecord] = []
    for path in sorted(child for child in images_root.iterdir() if child.is_dir()):
        folder = infer_folder(path.name)
        if not folder:
            issues.append(
                Issue(
                    "warning",
                    "image_folder_unrecognized",
                    f"Could not infer register folder from image folder: {path.name}",
                )
            )
            continue
        series = series_for_folder(folder, path.name)
        register_id = register_id_for(series, folder) or f"Unknown_{folder}"
        records.append(
            FolderRecord(
                register_id=register_id,
                archive="ASF",
                series=series,
                folder=folder,
                source="images",
                path_or_table=str(path.relative_to(project_root())),
            )
        )
    return records, issues


def build_coverage(
    word_files: list[WordFile],
    db_records: list[FolderRecord],
    image_records: list[FolderRecord],
) -> tuple[list[dict[str, Any]], list[Issue]]:
    issues: list[Issue] = []
    word_by_register = {
        word.register_id: word for word in word_files if word.register_id and not word.is_status_file
    }
    db_by_register = {record.register_id: record for record in db_records}
    image_by_register = {record.register_id: record for record in image_records}
    register_ids = sorted(set(word_by_register) | set(db_by_register) | set(image_by_register))
    rows: list[dict[str, Any]] = []
    for register_id in register_ids:
        word = word_by_register.get(register_id)
        db = db_by_register.get(register_id)
        image = image_by_register.get(register_id)
        row = {
            "register_id": register_id,
            "folder": (word.folder if word else db.folder if db else image.folder if image else None),
            "series": (word.series if word else db.series if db else image.series if image else None),
            "word_present": bool(word),
            "word_file": word.source_file if word else None,
            "word_status": word.status_from_filename if word else None,
            "db_present": bool(db),
            "image_present": bool(image),
            "image_folder": image.path_or_table if image else None,
        }
        rows.append(row)
        if not word:
            issues.append(Issue("warning", "word_missing_for_register", "No Word file for register", register_id))
        if not db:
            issues.append(Issue("warning", "db_missing_for_register", "No DB rows for register", register_id))
        if not image:
            issues.append(Issue("warning", "image_missing_for_register", "No image folder for register", register_id))
    return rows, issues


def run_inventory(args: argparse.Namespace) -> int:
    config = load_config(args)
    inventory_dir = config["output_root"] / "00_inventory"
    ensure_dir(inventory_dir)

    word_files = find_word_files(config["word_root"])
    db_records, db_issues = db_folder_records(config["db_path"])
    image_records, image_issues = image_folder_records(config["images_root"])
    coverage_rows, coverage_issues = build_coverage(word_files, db_records, image_records)

    issues: list[Issue] = []
    if not config["word_root"].exists():
        issues.append(Issue("error", "word_root_missing", f"Word root not found: {config['word_root']}"))
    if not word_files:
        issues.append(Issue("error", "word_files_missing", "No Word files found"))
    for word in word_files:
        if not word.is_status_file and not word.register_id:
            issues.append(
                Issue(
                    "warning",
                    "word_register_unrecognized",
                    "Could not infer register from Word filename",
                    source_file=word.source_file,
                )
            )
    issues.extend(db_issues)
    issues.extend(image_issues)
    issues.extend(coverage_issues)

    word_rows = [asdict(word) for word in word_files]
    db_rows = [asdict(record) for record in db_records]
    image_rows = [asdict(record) for record in image_records]
    issue_rows = [asdict(issue) for issue in issues]

    write_jsonl(inventory_dir / "word_files.jsonl", word_rows)
    write_csv(inventory_dir / "word_files.csv", word_rows)
    write_jsonl(inventory_dir / "db_folders.jsonl", db_rows)
    write_csv(inventory_dir / "db_folders.csv", db_rows)
    write_jsonl(inventory_dir / "image_folders.jsonl", image_rows)
    write_csv(inventory_dir / "image_folders.csv", image_rows)
    write_jsonl(inventory_dir / "register_coverage.jsonl", coverage_rows)
    write_csv(inventory_dir / "register_coverage.csv", coverage_rows)
    write_jsonl(inventory_dir / "issues.jsonl", issue_rows)

    register_word_count = sum(1 for word in word_files if not word.is_status_file)
    summary_lines = [
        f"- Word root: `{config['word_root'].relative_to(project_root())}`",
        f"- DB path: `{config['db_path'].relative_to(project_root())}`",
        f"- Images root: `{config['images_root'].relative_to(project_root())}`",
        f"- Word files found: {len(word_files)}",
        f"- Register Word files found: {register_word_count}",
        f"- DB registers found: {len(db_records)}",
        f"- Image folders found: {len(image_records)}",
        f"- Issues recorded: {len(issues)}",
        "",
        "Outputs:",
        "- `word_files.jsonl` / `word_files.csv`",
        "- `db_folders.jsonl` / `db_folders.csv`",
        "- `image_folders.jsonl` / `image_folders.csv`",
        "- `register_coverage.jsonl` / `register_coverage.csv`",
        "- `issues.jsonl`",
    ]
    write_summary(inventory_dir / "inventory_summary.md", "Word Pipeline Inventory Summary", summary_lines)

    print(f"Wrote inventory to {inventory_dir.relative_to(project_root())}")
    print(f"Word files: {len(word_files)} ({register_word_count} registers)")
    print(f"DB registers: {len(db_records)}")
    print(f"Image folders: {len(image_records)}")
    print(f"Issues: {len(issues)}")
    return 1 if any(issue.severity == "error" for issue in issues) else 0


def find_soffice(explicit_path: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    found = shutil.which("soffice")
    if found:
        candidates.append(Path(found))
    candidates.append(MACOS_SOFFICE)
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def run_soffice_convert(
    soffice: Path,
    source: Path,
    outdir: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="floracco-soffice-") as user_installation:
        profile_uri = Path(user_installation).resolve().as_uri()
        command = [
            str(soffice),
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "docx",
            "--outdir",
            str(outdir),
            str(source),
        ]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )


def normalize_one(
    word: WordFile,
    normalized_dir: Path,
    soffice: Path,
    force: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    source_path = resolve_repo_path(word.source_path)
    output_name = word.normalized_file_name
    if output_name is None:
        return {
            **asdict(word),
            "action": "skipped",
            "status": "error",
            "message": "Could not infer normalized filename",
        }
    output_path = normalized_dir / output_name
    if output_path.exists() and not force:
        return {
            **asdict(word),
            "action": "skipped",
            "status": "exists",
            "normalized_path": str(output_path.relative_to(project_root())),
            "normalized_sha256": sha256_file(output_path),
            "message": "Output exists; use --force to overwrite",
        }
    if output_path.exists() and force:
        output_path.unlink()

    if word.extension == ".docx":
        shutil.copy2(source_path, output_path)
        return {
            **asdict(word),
            "action": "copied",
            "status": "ok",
            "normalized_path": str(output_path.relative_to(project_root())),
            "normalized_sha256": sha256_file(output_path),
            "message": "Copied .docx processing copy",
        }

    if word.extension != ".doc":
        return {
            **asdict(word),
            "action": "skipped",
            "status": "unsupported_extension",
            "message": f"Unsupported Word extension: {word.extension}",
        }

    try:
        completed = run_soffice_convert(soffice, source_path, normalized_dir, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return {
            **asdict(word),
            "action": "converted",
            "status": "timeout",
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
            "message": f"LibreOffice conversion timed out after {timeout_seconds} seconds",
        }
    converted_path = normalized_dir / f"{source_path.stem}.docx"
    if completed.returncode != 0 or not converted_path.exists():
        return {
            **asdict(word),
            "action": "converted",
            "status": "error",
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "message": "LibreOffice conversion failed",
        }
    converted_path.replace(output_path)
    return {
        **asdict(word),
        "action": "converted",
        "status": "ok",
        "normalized_path": str(output_path.relative_to(project_root())),
        "normalized_sha256": sha256_file(output_path),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "message": "Converted .doc to normalized .docx processing copy",
    }


def run_normalize(args: argparse.Namespace) -> int:
    config = load_config(args)
    normalized_dir = config["output_root"] / "01_normalized_docx"
    ensure_dir(normalized_dir)

    soffice = find_soffice(args.soffice)
    if not soffice:
        print(
            "LibreOffice executable not found. Install LibreOffice or pass --soffice PATH.",
            file=sys.stderr,
        )
        return 2

    word_files = [word for word in find_word_files(config["word_root"]) if not word.is_status_file]
    manifest_rows = []
    for index, word in enumerate(word_files, start=1):
        print(f"[{index}/{len(word_files)}] {word.source_file}", flush=True)
        manifest_rows.append(
            normalize_one(word, normalized_dir, soffice, args.force, args.timeout_seconds)
        )

    write_jsonl(normalized_dir / "normalization_manifest.jsonl", manifest_rows)
    write_csv(normalized_dir / "normalization_manifest.csv", manifest_rows)

    ok_count = sum(1 for row in manifest_rows if row["status"] == "ok")
    exists_count = sum(1 for row in manifest_rows if row["status"] == "exists")
    error_count = sum(1 for row in manifest_rows if row["status"] == "error")
    timeout_count = sum(1 for row in manifest_rows if row["status"] == "timeout")
    summary_lines = [
        f"- Source Word root: `{config['word_root'].relative_to(project_root())}`",
        f"- Normalized DOCX directory: `{normalized_dir.relative_to(project_root())}`",
        f"- LibreOffice executable: `{soffice}`",
        f"- Force overwrite: {args.force}",
        f"- Register Word files considered: {len(word_files)}",
        f"- OK conversions/copies: {ok_count}",
        f"- Existing outputs skipped: {exists_count}",
        f"- Errors: {error_count}",
        f"- Timeouts: {timeout_count}",
        "",
        "Manual check required before extraction:",
        "- Open several normalized `.docx` files.",
        "- Confirm tracked changes/comments survived conversion.",
        "- Confirm bracket tags and front matter are intact.",
    ]
    write_summary(normalized_dir / "normalization_summary.md", "Word Normalization Summary", summary_lines)

    print(f"Wrote normalized DOCX files to {normalized_dir.relative_to(project_root())}")
    print(f"LibreOffice: {soffice}")
    print(f"OK: {ok_count}; existing/skipped: {exists_count}; errors: {error_count}; timeouts: {timeout_count}")
    return 1 if error_count or timeout_count else 0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_docx_part(path: Path, part_name: str) -> bytes | None:
    try:
        with ZipFile(path) as archive:
            if part_name not in archive.namelist():
                return None
            return archive.read(part_name)
    except BadZipFile:
        return None


def text_from_document_xml(document_xml: bytes) -> str:
    root = ET.fromstring(document_xml)
    return "".join(node.text or "" for node in root.iter(f"{WORD_NAMESPACE}t"))


def count_comments(comments_xml: bytes | None) -> int:
    if comments_xml is None:
        return 0
    root = ET.fromstring(comments_xml)
    return sum(1 for _ in root.iter(f"{WORD_NAMESPACE}comment"))


def inspect_docx(path: Path) -> dict[str, Any]:
    base: dict[str, Any] = {
        "path": str(path.relative_to(project_root())),
        "exists": path.exists(),
        "valid_docx": False,
    }
    if not path.exists():
        return {**base, "status": "missing", "message": "DOCX file is missing"}

    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                return {
                    **base,
                    "valid_docx": True,
                    "part_count": len(names),
                    "status": "invalid",
                    "message": "DOCX has no word/document.xml part",
                }
            document_xml = archive.read("word/document.xml")
            comments_xml = archive.read("word/comments.xml") if "word/comments.xml" in names else None
    except BadZipFile:
        return {**base, "status": "invalid", "message": "File is not a valid zip/DOCX"}

    text = text_from_document_xml(document_xml)
    bracket_labels = BRACKET_LABEL_RE.findall(text)
    folios = FOLIO_RE.findall(text)
    dates = DATE_RE.findall(text)
    return {
        **base,
        "valid_docx": True,
        "status": "ok",
        "message": "DOCX structure is readable",
        "part_count": len(names),
        "document_xml_sha256": sha256_bytes(document_xml),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_characters": len(text),
        "paragraph_count": document_xml.count(b"<w:p"),
        "table_count": document_xml.count(b"<w:tbl"),
        "insertion_count": document_xml.count(b"<w:ins"),
        "deletion_count": document_xml.count(b"<w:del"),
        "move_from_count": document_xml.count(b"<w:moveFrom"),
        "move_to_count": document_xml.count(b"<w:moveTo"),
        "comment_range_count": document_xml.count(b"<w:commentRangeStart"),
        "comment_reference_count": document_xml.count(b"<w:commentReference"),
        "comments_part_present": comments_xml is not None,
        "comment_count": count_comments(comments_xml),
        "bracket_label_count": len(bracket_labels),
        "bracket_label_examples": "; ".join(bracket_labels[:8]),
        "folio_count": len(folios),
        "folio_examples": "; ".join(folios[:8]),
        "date_like_count": len(dates),
        "date_like_examples": "; ".join(dates[:8]),
        "front_matter_snippet": re.sub(r"\s+", " ", text[:600]).strip(),
    }


def read_manifest_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_manifest_row(row: dict[str, str]) -> dict[str, Any]:
    normalized_path_raw = row.get("normalized_path") or ""
    normalized_path = resolve_repo_path(normalized_path_raw) if normalized_path_raw else Path()
    normalized = inspect_docx(normalized_path) if normalized_path_raw else {
        "status": "missing",
        "message": "Manifest row has no normalized_path",
    }

    source_path = resolve_repo_path(row.get("source_path") or "")
    source_extension = row.get("extension")
    source_docx: dict[str, Any] | None = None
    if source_extension == ".docx" and source_path.exists():
        source_docx = inspect_docx(source_path)

    issues: list[str] = []
    if normalized.get("status") != "ok":
        issues.append(str(normalized.get("message", "normalized file is not readable")))
    if row.get("status") != "ok":
        issues.append(f"normalization status is {row.get('status')}")
    if normalized.get("text_characters", 0) == 0:
        issues.append("normalized file has no extracted text")
    if normalized.get("paragraph_count", 0) == 0:
        issues.append("normalized file has no Word paragraphs")
    if normalized.get("bracket_label_count", 0) == 0:
        issues.append("no bracket labels found")
    if normalized.get("folio_count", 0) == 0:
        issues.append("no folio references found")
    if normalized.get("date_like_count", 0) == 0:
        issues.append("no date-like strings found")
    if row.get("status_from_filename") == "track_changes":
        revision_count = (
            normalized.get("insertion_count", 0)
            + normalized.get("deletion_count", 0)
            + normalized.get("comment_reference_count", 0)
        )
        if revision_count == 0:
            issues.append("filename says track changes, but no insertions/deletions/comments found")

    source_compare: dict[str, Any] = {}
    if source_docx:
        source_compare = {
            "source_docx_readable": source_docx.get("status") == "ok",
            "source_text_sha256": source_docx.get("text_sha256"),
            "source_text_matches_normalized": source_docx.get("text_sha256") == normalized.get("text_sha256"),
            "source_insertion_count": source_docx.get("insertion_count"),
            "source_deletion_count": source_docx.get("deletion_count"),
            "source_comment_reference_count": source_docx.get("comment_reference_count"),
            "source_revision_counts_match_normalized": (
                source_docx.get("insertion_count") == normalized.get("insertion_count")
                and source_docx.get("deletion_count") == normalized.get("deletion_count")
                and source_docx.get("comment_reference_count") == normalized.get("comment_reference_count")
            ),
        }
        if source_compare["source_docx_readable"] and not source_compare["source_text_matches_normalized"]:
            issues.append("copied .docx text differs from source .docx")
        if source_compare["source_docx_readable"] and not source_compare["source_revision_counts_match_normalized"]:
            issues.append("copied .docx revision counts differ from source .docx")
    else:
        source_compare = {
            "source_docx_readable": None,
            "source_text_sha256": None,
            "source_text_matches_normalized": None,
            "source_insertion_count": None,
            "source_deletion_count": None,
            "source_comment_reference_count": None,
            "source_revision_counts_match_normalized": None,
        }

    validation_status = "ok" if not issues else "review"
    return {
        "validation_status": validation_status,
        "validation_issues": "; ".join(issues),
        "register_id": row.get("register_id"),
        "source_file": row.get("source_file"),
        "source_path": row.get("source_path"),
        "source_extension": source_extension,
        "normalization_action": row.get("action"),
        "normalization_status": row.get("status"),
        "normalized_path": normalized_path_raw,
        "normalized_sha256": row.get("normalized_sha256"),
        "normalized_docx_status": normalized.get("status"),
        "normalized_docx_message": normalized.get("message"),
        **{key: value for key, value in normalized.items() if key not in {"path", "status", "message", "exists"}},
        **source_compare,
    }


def run_validate_normalized(args: argparse.Namespace) -> int:
    config = load_config(args)
    normalized_dir = config["output_root"] / "01_normalized_docx"
    validation_dir = config["output_root"] / "02_validation"
    ensure_dir(validation_dir)

    manifest_path = normalized_dir / "normalization_manifest.csv"
    manifest_rows = read_manifest_rows(manifest_path)
    rows = [validate_manifest_row(row) for row in manifest_rows]
    issues = [
        {
            "severity": "warning",
            "code": "normalized_docx_needs_review",
            "message": row["validation_issues"],
            "register_id": row["register_id"],
            "source_file": row["source_file"],
        }
        for row in rows
        if row["validation_status"] != "ok"
    ]

    if not manifest_rows:
        issues.append(
            {
                "severity": "error",
                "code": "normalization_manifest_missing",
                "message": f"Normalization manifest not found or empty: {manifest_path}",
                "register_id": None,
                "source_file": None,
            }
        )

    write_jsonl(validation_dir / "normalized_docx_validation.jsonl", rows)
    write_csv(validation_dir / "normalized_docx_validation.csv", rows)
    write_jsonl(validation_dir / "issues.jsonl", issues)

    ok_count = sum(1 for row in rows if row["validation_status"] == "ok")
    review_count = sum(1 for row in rows if row["validation_status"] == "review")
    copied_docx_count = sum(1 for row in rows if row["source_extension"] == ".docx")
    copied_docx_match_count = sum(
        1 for row in rows if row["source_extension"] == ".docx" and row["source_text_matches_normalized"] is True
    )
    summary_lines = [
        f"- Normalized DOCX directory: `{normalized_dir.relative_to(project_root())}`",
        f"- Manifest: `{manifest_path.relative_to(project_root())}`",
        f"- Files validated: {len(rows)}",
        f"- Validation OK: {ok_count}",
        f"- Needs review: {review_count}",
        f"- Existing `.docx` copies compared to source: {copied_docx_match_count}/{copied_docx_count} text matches",
        "",
        "What this validation checks:",
        "- each normalized file is a readable `.docx` zip with `word/document.xml`;",
        "- Word XML contains paragraphs and extractable text;",
        "- tracked-change files still contain insertions, deletions, or comment references;",
        "- bracket labels, folio references, date-like strings, and front matter snippets are present;",
        "- already-`.docx` originals match their normalized copies at text and revision-count level.",
        "",
        "Important limit:",
        "- legacy `.doc` files are binary Word files, so this script cannot inspect their original revision XML directly; it validates the converted `.docx` and records source checksums for traceability.",
    ]
    write_summary(validation_dir / "validation_summary.md", "Normalized DOCX Validation Summary", summary_lines)

    print(f"Wrote validation outputs to {validation_dir.relative_to(project_root())}")
    print(f"Files validated: {len(rows)}")
    print(f"OK: {ok_count}; needs review: {review_count}")
    return 1 if any(issue["severity"] == "error" for issue in issues) else 0


def word_attr(local_name: str) -> str:
    return f"{WORD_NAMESPACE}{local_name}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def element_text(element: ET.Element, include_deleted: bool = False) -> str:
    text_parts: list[str] = []
    for node in element.iter():
        if node.tag == word_attr("t"):
            text_parts.append(node.text or "")
        elif include_deleted and node.tag == word_attr("delText"):
            text_parts.append(node.text or "")
        elif node.tag == word_attr("tab"):
            text_parts.append("\t")
        elif node.tag in {word_attr("br"), word_attr("cr")}:
            text_parts.append("\n")
    return "".join(text_parts)


def paragraph_style(paragraph: ET.Element) -> str | None:
    paragraph_properties = paragraph.find(word_attr("pPr"))
    if paragraph_properties is None:
        return None
    style = paragraph_properties.find(word_attr("pStyle"))
    if style is None:
        return None
    return style.attrib.get(word_attr("val"))


def extract_ids(paragraph: ET.Element, tag_name: str) -> list[str]:
    ids: list[str] = []
    for element in paragraph.iter(word_attr(tag_name)):
        value = element.attrib.get(word_attr("id"))
        if value is not None:
            ids.append(value)
    return ids


def normalize_folio_token(number: str | None, side: str | None) -> str | None:
    if not number:
        return None
    number = re.sub(r"\s+", "", number)
    if number.lower() == "n.n":
        return "n.n"
    side = side.lower() if side else ""
    return f"{number}{side}"


def parse_folio_heading(text: str) -> dict[str, Any] | None:
    stripped = re.sub(r"\s+", " ", text.strip())
    if not stripped:
        return None
    stripped = re.sub(r"\s*[–-]\s*$", "", stripped)
    stripped = re.sub(r"^\((c{1,2}\.\s*n\.n)\)", r"\1", stripped, flags=re.IGNORECASE)
    match = FOLIO_HEADING_RE.match(stripped)
    if not match:
        return None

    start = normalize_folio_token(match.group("start"), match.group("start_side"))
    end_number = match.group("end")
    end_side = match.group("end_side")
    if end_number is None and end_side is not None:
        end_number = match.group("start")
    end = normalize_folio_token(end_number, end_side) if end_number or end_side else None

    raw = stripped
    trailing_text = (match.group("trailing") or "").strip()
    if trailing_text == ".":
        trailing_text = ""
    is_range = end is not None and end != start
    label = f"{match.group('prefix').lower()}. {start}"
    if is_range:
        label = f"{label}-{end}"
    return {
        "raw": raw,
        "label": label,
        "prefix": f"{match.group('prefix').lower()}.",
        "start": start,
        "end": end or start,
        "is_range": is_range,
        "trailing_text": trailing_text,
        "is_heading": True,
    }


def extract_inline_folio_mentions(text: str) -> list[str]:
    heading = parse_folio_heading(text)
    mentions = FOLIO_RE.findall(text)
    if heading and mentions:
        return mentions[1:]
    return mentions


def revision_aware_text(element: ET.Element) -> str:
    parts: list[str] = []

    def walk(node: ET.Element, state: str = "plain") -> None:
        name = local_name(node.tag)
        if name in {"ins", "del", "moveFrom", "moveTo"}:
            revision_id = node.attrib.get(word_attr("id"), "")
            author = node.attrib.get(word_attr("author"), "")
            date = node.attrib.get(word_attr("date"), "")
            marker = name.upper()
            parts.append(f"<{marker} id=\"{revision_id}\" author=\"{author}\" date=\"{date}\">")
            for child in node:
                walk(child, name)
            parts.append(f"</{marker}>")
            return
        if name == "commentRangeStart":
            parts.append(f"<COMMENT_START id=\"{node.attrib.get(word_attr('id'), '')}\">")
            return
        if name == "commentRangeEnd":
            parts.append(f"<COMMENT_END id=\"{node.attrib.get(word_attr('id'), '')}\">")
            return
        if name == "commentReference":
            parts.append(f"<COMMENT_REF id=\"{node.attrib.get(word_attr('id'), '')}\">")
            return
        if name == "footnoteReference":
            parts.append(f"<FOOTNOTE_REF id=\"{node.attrib.get(word_attr('id'), '')}\">")
            return
        if name == "endnoteReference":
            parts.append(f"<ENDNOTE_REF id=\"{node.attrib.get(word_attr('id'), '')}\">")
            return
        if name == "t":
            parts.append(node.text or "")
            return
        if name == "delText":
            if state in {"del", "moveFrom"}:
                parts.append(node.text or "")
            return
        if name == "tab":
            parts.append("\t")
            return
        if name in {"br", "cr"}:
            parts.append("\n")
            return
        for child in node:
            walk(child, state)

    walk(element)
    return "".join(parts)


def parse_comments_xml(comments_xml: bytes | None) -> dict[str, dict[str, Any]]:
    if comments_xml is None:
        return {}
    root = ET.fromstring(comments_xml)
    comments: dict[str, dict[str, Any]] = {}
    for comment in root.iter(word_attr("comment")):
        comment_id = comment.attrib.get(word_attr("id"))
        if comment_id is None:
            continue
        comments[comment_id] = {
            "comment_id": comment_id,
            "author": comment.attrib.get(word_attr("author")),
            "date": comment.attrib.get(word_attr("date")),
            "initials": comment.attrib.get(word_attr("initials")),
            "text": element_text(comment),
            "revision_aware_text": revision_aware_text(comment),
        }
    return comments


def parse_note_xml(notes_xml: bytes | None, note_tag: str) -> dict[str, dict[str, Any]]:
    if notes_xml is None:
        return {}
    root = ET.fromstring(notes_xml)
    notes: dict[str, dict[str, Any]] = {}
    for note in root.iter(word_attr(note_tag)):
        note_id = note.attrib.get(word_attr("id"))
        if note_id is None:
            continue
        notes[note_id] = {
            "note_id": note_id,
            "note_kind": "footnote" if note_tag == "footnote" else "endnote",
            "note_type": note.attrib.get(word_attr("type")),
            "text": element_text(note),
            "revision_aware_text": revision_aware_text(note),
        }
    return notes


def parse_relationships(relationships_xml: bytes | None) -> dict[str, dict[str, str | None]]:
    if relationships_xml is None:
        return {}
    root = ET.fromstring(relationships_xml)
    relationships: dict[str, dict[str, str | None]] = {}
    for relationship in root.iter(f"{RELATIONSHIP_NAMESPACE}Relationship"):
        relationship_id = relationship.attrib.get("Id")
        if relationship_id is None:
            continue
        relationships[relationship_id] = {
            "relationship_id": relationship_id,
            "type": relationship.attrib.get("Type"),
            "target": relationship.attrib.get("Target"),
            "target_mode": relationship.attrib.get("TargetMode"),
        }
    return relationships


def revision_rows_for_element(
    element: ET.Element,
    *,
    register_id: str,
    source_file: str,
    normalized_path: str,
    part_name: str,
    paragraph_index: int | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for revision in element.iter():
        kind = local_name(revision.tag)
        if kind not in {"ins", "del", "moveFrom", "moveTo"}:
            continue
        revision_id = revision.attrib.get(word_attr("id"))
        rows.append(
            {
                "register_id": register_id,
                "source_file": source_file,
                "normalized_path": normalized_path,
                "part_name": part_name,
                "paragraph_index": paragraph_index,
                "revision_kind": kind,
                "revision_id": revision_id,
                "author": revision.attrib.get(word_attr("author")),
                "date": revision.attrib.get(word_attr("date")),
                "text": element_text(revision, include_deleted=True),
                "revision_aware_text": revision_aware_text(revision),
            }
        )
    return rows


def extract_register(row: dict[str, str]) -> dict[str, list[dict[str, Any]] | dict[str, Any]]:
    register_id = row["register_id"]
    source_file = row["source_file"]
    normalized_path = row["normalized_path"]
    path = resolve_repo_path(normalized_path)

    with ZipFile(path) as archive:
        names = set(archive.namelist())
        document_xml = archive.read("word/document.xml")
        comments_xml = archive.read("word/comments.xml") if "word/comments.xml" in names else None
        footnotes_xml = archive.read("word/footnotes.xml") if "word/footnotes.xml" in names else None
        endnotes_xml = archive.read("word/endnotes.xml") if "word/endnotes.xml" in names else None
        relationships_xml = archive.read("word/_rels/document.xml.rels") if "word/_rels/document.xml.rels" in names else None

    document_root = ET.fromstring(document_xml)
    comments_by_id = parse_comments_xml(comments_xml)
    footnotes_by_id = parse_note_xml(footnotes_xml, "footnote")
    endnotes_by_id = parse_note_xml(endnotes_xml, "endnote")
    relationships_by_id = parse_relationships(relationships_xml)

    paragraph_rows: list[dict[str, Any]] = []
    revision_rows: list[dict[str, Any]] = []
    all_text_parts: list[str] = []
    current_folio: str | None = None
    current_folio_start: str | None = None
    current_folio_end: str | None = None
    front_matter_boundary: int | None = None

    for paragraph_index, paragraph in enumerate(document_root.iter(word_attr("p"))):
        current_text = element_text(paragraph)
        if current_text:
            all_text_parts.append(current_text)
        labels = BRACKET_LABEL_RE.findall(current_text)
        folio_heading = parse_folio_heading(current_text)
        inline_folios = extract_inline_folio_mentions(current_text)
        folios = [folio_heading["label"]] if folio_heading else inline_folios
        dates = DATE_RE.findall(current_text)
        if folio_heading:
            current_folio = folio_heading["label"]
            current_folio_start = folio_heading["start"]
            current_folio_end = folio_heading["end"]
            if front_matter_boundary is None:
                front_matter_boundary = paragraph_index
        comment_ids = sorted(
            set(
                extract_ids(paragraph, "commentRangeStart")
                + extract_ids(paragraph, "commentReference")
            ),
            key=lambda value: int(value) if value.isdigit() else value,
        )
        footnote_ids = extract_ids(paragraph, "footnoteReference")
        endnote_ids = extract_ids(paragraph, "endnoteReference")
        has_insertions = any(True for _ in paragraph.iter(word_attr("ins")))
        has_deletions = any(True for _ in paragraph.iter(word_attr("del")))
        has_moves = any(True for _ in paragraph.iter(word_attr("moveFrom"))) or any(
            True for _ in paragraph.iter(word_attr("moveTo"))
        )
        paragraph_xml = ET.tostring(paragraph, encoding="utf-8")
        paragraph_rows.append(
            {
                "register_id": register_id,
                "source_file": source_file,
                "normalized_path": normalized_path,
                "part_name": "word/document.xml",
                "paragraph_index": paragraph_index,
                "style": paragraph_style(paragraph),
                "current_folio_context": current_folio,
                "current_folio_start": current_folio_start,
                "current_folio_end": current_folio_end,
                "current_text": current_text,
                "revision_aware_text": revision_aware_text(paragraph),
                "text_characters": len(current_text),
                "paragraph_xml_sha256": sha256_bytes(paragraph_xml),
                "bracket_label_count": len(labels),
                "bracket_labels": labels,
                "folio_candidates": folios,
                "folio_heading": folio_heading,
                "inline_folio_mentions": inline_folios,
                "date_candidates": dates,
                "comment_ids": comment_ids,
                "footnote_ids": footnote_ids,
                "endnote_ids": endnote_ids,
                "has_insertions": has_insertions,
                "has_deletions": has_deletions,
                "has_moves": has_moves,
            }
        )
        revision_rows.extend(
            revision_rows_for_element(
                paragraph,
                register_id=register_id,
                source_file=source_file,
                normalized_path=normalized_path,
                part_name="word/document.xml",
                paragraph_index=paragraph_index,
            )
        )

    comment_rows = [
        {
            "register_id": register_id,
            "source_file": source_file,
            "normalized_path": normalized_path,
            "part_name": "word/comments.xml",
            **comment,
        }
        for comment in comments_by_id.values()
    ]

    note_rows: list[dict[str, Any]] = []
    for note in [*footnotes_by_id.values(), *endnotes_by_id.values()]:
        if note["note_type"] in {"separator", "continuationSeparator"} and not note["text"]:
            continue
        note_rows.append(
            {
                "register_id": register_id,
                "source_file": source_file,
                "normalized_path": normalized_path,
                "part_name": "word/footnotes.xml" if note["note_kind"] == "footnote" else "word/endnotes.xml",
                **note,
            }
        )

    relationship_rows = [
        {
            "register_id": register_id,
            "source_file": source_file,
            "normalized_path": normalized_path,
            "part_name": "word/_rels/document.xml.rels",
            **relationship,
        }
        for relationship in relationships_by_id.values()
    ]

    full_text = "\n".join(all_text_parts)
    front_matter_paragraphs = (
        paragraph_rows[:front_matter_boundary]
        if front_matter_boundary is not None
        else paragraph_rows[: min(25, len(paragraph_rows))]
    )
    front_matter_text = "\n".join(
        str(paragraph["current_text"]) for paragraph in front_matter_paragraphs if paragraph["current_text"]
    )
    label_count = sum(int(paragraph["bracket_label_count"]) for paragraph in paragraph_rows)
    folio_heading_count = sum(1 for paragraph in paragraph_rows if paragraph["folio_heading"])
    inline_folio_mention_count = sum(
        len(paragraph["inline_folio_mentions"]) for paragraph in paragraph_rows
    )
    folio_count = folio_heading_count + inline_folio_mention_count
    date_like_count = sum(len(paragraph["date_candidates"]) for paragraph in paragraph_rows)
    register_row = {
        "register_id": register_id,
        "source_file": source_file,
        "source_path": row.get("source_path"),
        "source_extension": row.get("source_extension"),
        "normalized_path": normalized_path,
        "normalized_sha256": row.get("normalized_sha256"),
        "document_xml_sha256": sha256_bytes(document_xml),
        "current_text_sha256": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        "paragraph_count": len(paragraph_rows),
        "revision_count": len(revision_rows),
        "comment_count": len(comment_rows),
        "note_count": len(note_rows),
        "relationship_count": len(relationship_rows),
        "bracket_label_count": label_count,
        "folio_count": folio_count,
        "folio_heading_count": folio_heading_count,
        "inline_folio_mention_count": inline_folio_mention_count,
        "date_like_count": date_like_count,
        "front_matter_paragraph_count": len(front_matter_paragraphs),
        "front_matter_text": front_matter_text,
        "front_matter_text_sha256": hashlib.sha256(front_matter_text.encode("utf-8")).hexdigest(),
    }

    return {
        "register": register_row,
        "paragraphs": paragraph_rows,
        "revisions": revision_rows,
        "comments": comment_rows,
        "notes": note_rows,
        "relationships": relationship_rows,
    }


def run_extract_registers(args: argparse.Namespace) -> int:
    config = load_config(args)
    validation_dir = config["output_root"] / "02_validation"
    extraction_dir = config["output_root"] / "03_extracted_registers"
    ensure_dir(extraction_dir)

    validation_path = validation_dir / "normalized_docx_validation.csv"
    validation_rows = read_manifest_rows(validation_path)
    rows_to_extract = [
        row for row in validation_rows if row.get("validation_status") in EXTRACTION_ALLOWED_STATUSES
    ]
    issues: list[dict[str, Any]] = []
    if not validation_rows:
        issues.append(
            {
                "severity": "error",
                "code": "validation_missing",
                "message": f"Validation CSV not found or empty: {validation_path}",
                "register_id": None,
                "source_file": None,
            }
        )

    registers: list[dict[str, Any]] = []
    paragraphs: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    for index, row in enumerate(rows_to_extract, start=1):
        print(f"[{index}/{len(rows_to_extract)}] {row['register_id']}", flush=True)
        try:
            extracted = extract_register(row)
        except (BadZipFile, ET.ParseError, KeyError, FileNotFoundError) as exc:
            issues.append(
                {
                    "severity": "error",
                    "code": "register_extraction_failed",
                    "message": str(exc),
                    "register_id": row.get("register_id"),
                    "source_file": row.get("source_file"),
                }
            )
            continue
        registers.append(extracted["register"])  # type: ignore[arg-type]
        paragraphs.extend(extracted["paragraphs"])  # type: ignore[arg-type]
        revisions.extend(extracted["revisions"])  # type: ignore[arg-type]
        comments.extend(extracted["comments"])  # type: ignore[arg-type]
        notes.extend(extracted["notes"])  # type: ignore[arg-type]
        relationships.extend(extracted["relationships"])  # type: ignore[arg-type]

    skipped_count = len(validation_rows) - len(rows_to_extract)
    if skipped_count:
        for row in validation_rows:
            if row.get("validation_status") not in EXTRACTION_ALLOWED_STATUSES:
                issues.append(
                    {
                        "severity": "warning",
                        "code": "register_skipped_due_to_validation_status",
                        "message": f"Skipped extraction because validation_status={row.get('validation_status')}",
                        "register_id": row.get("register_id"),
                        "source_file": row.get("source_file"),
                    }
                )

    write_jsonl(extraction_dir / "registers.jsonl", registers)
    write_csv(extraction_dir / "registers.csv", registers)
    write_jsonl(extraction_dir / "paragraphs.jsonl", paragraphs)
    write_jsonl(extraction_dir / "revisions.jsonl", revisions)
    write_jsonl(extraction_dir / "comments.jsonl", comments)
    write_jsonl(extraction_dir / "footnotes.jsonl", notes)
    write_jsonl(extraction_dir / "relationships.jsonl", relationships)
    write_jsonl(extraction_dir / "issues.jsonl", issues)

    summary_lines = [
        f"- Validation input: `{validation_path.relative_to(project_root())}`",
        f"- Registers extracted: {len(registers)}",
        f"- Registers skipped: {skipped_count}",
        f"- Paragraph rows: {len(paragraphs)}",
        f"- Revision rows: {len(revisions)}",
        f"- Comment rows: {len(comments)}",
        f"- Footnote/endnote rows: {len(notes)}",
        f"- Relationship rows: {len(relationships)}",
        f"- Issues: {len(issues)}",
        "",
        "What this stage preserves:",
        "- paragraph order and paragraph-level SHA-256 hashes;",
        "- current text plus revision-aware text with insertion/deletion/comment/footnote markers;",
        "- insertion/deletion/move metadata including author, date, ID, and text;",
        "- comment text and footnote/endnote text as separate evidence rows;",
        "- bracket labels, folio headings/ranges, inline folio mentions, date candidates, and current folio context.",
        "",
        "What this stage does not do:",
        "- it does not segment contracts or acts;",
        "- it does not accept or reject tracked changes;",
        "- it does not write to SQLite.",
    ]
    write_summary(extraction_dir / "extraction_summary.md", "Register Extraction Summary", summary_lines)

    print(f"Wrote extraction outputs to {extraction_dir.relative_to(project_root())}")
    print(f"Registers: {len(registers)}; paragraphs: {len(paragraphs)}; revisions: {len(revisions)}")
    print(f"Comments: {len(comments)}; notes: {len(notes)}; issues: {len(issues)}")
    return 1 if any(issue["severity"] == "error" for issue in issues) else 0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


COMPOUND_LABEL_SPLIT_PLUS_SLASH_RE = re.compile(r"\s*[+/]\s*")
COMPOUND_LABEL_SPLIT_E_RE = re.compile(r"\s+e\s+", flags=re.IGNORECASE)
EDITORIAL_LABEL_PREFIXES = (
    "manca ",
    "nuova formula",
    "senza data",
    "senza testimoni",
    "non ci sono testimoni",
)


def looks_like_event_label_part(part: str) -> bool:
    cleaned = re.sub(r"[^\w\sàèéìòù'-]+", " ", part.strip(), flags=re.IGNORECASE)
    label = re.sub(r"\s+", " ", cleaned).strip().lower()
    if not label or len(label) > 50:
        return False
    if label in EVENT_LABEL_MAP:
        return True
    return any(pattern.search(label) for _, pattern in EVENT_LABEL_KEYWORDS)


def split_compound_event_label_inner(label_inner: str) -> list[str]:
    """Split one bracket label on +, /, or e when each part looks like an act label.

    Grounded in source_entries: ~64 labels use [X+Y], [X/Y], or [X e Y] (e.g.
    [Bilancio e modifica], [Disdetta/Rinnovo], [nuovo + variazione]).
    """
    inner = label_inner.strip()
    if COMPOUND_LABEL_SPLIT_PLUS_SLASH_RE.search(inner):
        parts = [part.strip() for part in COMPOUND_LABEL_SPLIT_PLUS_SLASH_RE.split(inner) if part.strip()]
    elif COMPOUND_LABEL_SPLIT_E_RE.search(inner):
        parts = [part.strip() for part in COMPOUND_LABEL_SPLIT_E_RE.split(inner) if part.strip()]
    else:
        return [inner]
    if len(parts) < 2:
        return [inner]
    if all(looks_like_event_label_part(part) for part in parts):
        return parts
    return [inner]


def normalize_event_label(raw_label: str | None) -> str | None:
    if raw_label is None:
        return None
    label = re.sub(r"\s+", " ", raw_label.strip().strip("[]").strip()).lower()
    exact_label = EVENT_LABEL_MAP.get(label)
    if exact_label is not None:
        return exact_label
    if re.search(r"\bnon\s+(?:è|e)\s+un['’]?accomandita\b", label, flags=re.IGNORECASE):
        return "without_accomandita"
    guesses = event_label_guesses(raw_label)
    if not guesses:
        return None
    if len(guesses) == 1:
        return guesses[0]
    return "combined_event"


def event_label_guesses_single(label: str) -> list[str]:
    label = re.sub(r"\s+", " ", label.strip().strip("[]").strip()).lower()
    exact_label = EVENT_LABEL_MAP.get(label)
    if exact_label is not None:
        return [exact_label]
    if re.search(r"\bnon\s+(?:è|e)\s+un['’]?accomandita\b", label, flags=re.IGNORECASE):
        return ["without_accomandita"]
    guesses: list[str] = []
    for guess, pattern in EVENT_LABEL_KEYWORDS:
        if pattern.search(label) and guess not in guesses:
            guesses.append(guess)
    return guesses


def event_label_guesses(raw_label: str) -> list[str]:
    label_inner = re.sub(r"\s+", " ", raw_label.strip().strip("[]").strip())
    if label_inner.lower().startswith(EDITORIAL_LABEL_PREFIXES):
        return []
    parts = split_compound_event_label_inner(label_inner)
    if len(parts) > 1:
        guesses: list[str] = []
        for part in parts:
            for guess in event_label_guesses_single(part):
                if guess not in guesses:
                    guesses.append(guess)
        return guesses
    return event_label_guesses_single(label_inner)


def count_event_labels(text: str) -> int:
    bracket_count = sum(max(1, len(event_label_guesses(label))) for label in BRACKET_LABEL_RE.findall(text) if event_label_guesses(label))
    plus_count = len(PLUS_EVENT_RE.findall(BRACKET_LABEL_RE.sub("", text)))
    return bracket_count + plus_count


def immediate_component_number(segment: str) -> int | None:
    prefix = re.sub(r"\s+", " ", segment.strip())[:90]
    match = re.match(
        r"^(?:di|del|della|a|o|rinnovo(?:\s+del(?:la)?)?|nuov[ao])?\s*(?P<number>\d{2,5})\b",
        prefix,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return int(match.group("number"))


def immediate_component_reference_number(segment: str) -> int | None:
    prefix = re.sub(r"\s+", " ", segment.strip())[:90]
    referenced = REFERENCED_EVENT_RE.search(prefix)
    return int(referenced.group("number")) if referenced else None


def event_components_for_text(text: str) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    matches = list(re.finditer(r"\[(?P<label>[^\]]+)\]", text or ""))
    for index, match in enumerate(matches):
        label_inner = match.group("label").strip()
        label_parts = split_compound_event_label_inner(label_inner)
        part_labels = [f"[{part}]" for part in label_parts]
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.end() : next_start]
        referenced_number = immediate_component_reference_number(segment)
        event_number = immediate_component_number(segment)
        for raw_label in part_labels:
            guesses = event_label_guesses(raw_label)
            if not guesses:
                continue
            for guess in guesses:
                components.append(
                    {
                        "raw_label": raw_label,
                        "label_guess": guess,
                        "event_number": event_number,
                        "referenced_event_number": referenced_number,
                        "segment_text": segment.strip()[:500],
                    }
                )
    return components


def event_components_for_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(entry.get("current_text") or "")
    components = event_components_for_text(text)
    if components:
        return components
    raw_label = entry.get("event_label_raw")
    label_guess = entry.get("event_label_guess")
    if not raw_label or not label_guess:
        return []
    return [
        {
            "raw_label": raw_label,
            "label_guess": label_guess,
            "event_number": integer_or_none(entry.get("event_number_raw")),
            "referenced_event_number": integer_or_none(entry.get("referenced_event_number_raw")),
            "segment_text": str(entry.get("event_label_trailing_text") or "")[:500],
        }
    ]


def component_label_guesses(entry: dict[str, Any]) -> set[str]:
    return {component["label_guess"] for component in event_components_for_entry(entry)}


def component_contract_ids(entry: dict[str, Any]) -> set[int]:
    return {
        int(component["event_number"])
        for component in event_components_for_entry(entry)
        if component.get("label_guess") == "new_contract" and component.get("event_number") is not None
    }


def component_sub_contract_main_ids(entry: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for component in event_components_for_entry(entry):
        if component.get("label_guess") == "new_contract":
            continue
        for key in ("referenced_event_number", "event_number"):
            if component.get(key) is not None:
                ids.add(int(component[key]))
    return ids


GUESS_TO_LINK_COMPONENT_TYPES: dict[str, frozenset[str]] = {
    "new_contract": frozenset({"contract", "new contract"}),
    "termination": frozenset({"termination"}),
    "balance": frozenset({"balance"}),
    "modification": frozenset({"modification", "variation"}),
    "renewal": frozenset({"renewal"}),
    "assignment": frozenset({"assignment", "cession"}),
    "ratification": frozenset({"ratification"}),
}


def format_act_component_display(component: dict[str, Any]) -> str:
    label = str(component.get("raw_label") or "").strip()
    number = component.get("event_number")
    if number is None:
        number = component.get("referenced_event_number")
    if number is not None and str(number) not in label:
        return f"{label} {number}".strip()
    return label


def component_link_id_exact(component: dict[str, Any], link: dict[str, Any]) -> bool:
    guess = str(component.get("label_guess") or "")
    event_number = component.get("event_number")
    referenced_event_number = component.get("referenced_event_number")
    db_table = str(link.get("db_table") or "")
    db_contract_id = link.get("db_contract_id")
    db_main_contract_id = link.get("db_main_contract_id")
    if guess == "new_contract":
        return (
            db_table == "contract"
            and event_number is not None
            and db_contract_id is not None
            and int(db_contract_id) == int(event_number)
        )
    target = referenced_event_number if referenced_event_number is not None else event_number
    if target is None or db_main_contract_id is None:
        return False
    return db_table == "sub_contract" and int(db_main_contract_id) == int(target)


def component_link_type_heuristic(component: dict[str, Any], link: dict[str, Any]) -> bool:
    guess = str(component.get("label_guess") or "")
    allowed = GUESS_TO_LINK_COMPONENT_TYPES.get(guess)
    if not allowed:
        return False
    link_types = {
        str(link.get("component_label") or "").strip().lower(),
        str(link.get("db_sub_type") or "").strip().lower(),
    }
    return bool(allowed & link_types)


def act_components_for_review(
    entry: dict[str, Any],
    links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map parsed Word act components to suggested DB link rows for review UI."""
    parsed_components = event_components_for_entry(entry)
    if not parsed_components:
        return []

    used_link_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for component in parsed_components:
        matched_link: dict[str, Any] | None = None
        mapping_confidence = "unmapped"

        for link in links:
            link_id = str(link.get("db_row_id") or "")
            if not link_id or link_id in used_link_ids:
                continue
            if component_link_id_exact(component, link):
                matched_link = link
                mapping_confidence = "exact"
                break

        if matched_link is None:
            for link in links:
                link_id = str(link.get("db_row_id") or "")
                if not link_id or link_id in used_link_ids:
                    continue
                if component_link_type_heuristic(component, link):
                    matched_link = link
                    mapping_confidence = "heuristic"
                    break

        if matched_link is not None:
            used_link_ids.add(str(matched_link.get("db_row_id") or ""))

        rows.append(
            {
                "raw_label": component.get("raw_label"),
                "label_guess": component.get("label_guess"),
                "label_display": format_act_component_display(component),
                "event_number": component.get("event_number"),
                "referenced_event_number": component.get("referenced_event_number"),
                "suggested_db_row_id": matched_link.get("db_row_id") if matched_link else None,
                "link_component_label": matched_link.get("component_label") if matched_link else None,
                "mapping_confidence": mapping_confidence,
                "link_score": matched_link.get("score") if matched_link else None,
            }
        )
    return rows


def parse_entry_label(text: str) -> dict[str, Any] | None:
    stripped_text = text.strip()
    if re.match(r"^Senza accomandita\b", stripped_text, flags=re.IGNORECASE):
        return {
            "event_label_raw": "Senza accomandita",
            "event_label_guess": "without_accomandita",
            "event_number_raw": None,
            "referenced_event_number_raw": None,
            "referenced_event_numbers_raw": [],
            "event_label_trailing_text": stripped_text,
            "event_label_count": 0,
        }
    match = ENTRY_LABEL_RE.match(stripped_text)
    if not match:
        return None
    raw_label = f"[{match.group('label').strip()}]"
    label_guess = normalize_event_label(raw_label)
    if label_guess is None:
        return None
    trailing = match.group("trailing").strip()
    event_number = None
    referenced_event_number = None
    referenced_event_numbers: list[str] = []
    enumerated = ENUMERATED_REFERENCED_RE.search(trailing)
    if enumerated:
        # Preserve order, drop duplicates (e.g. "di 1263 ; 1263").
        seen: set[str] = set()
        for value in re.findall(r"\d{1,5}", enumerated.group(0)):
            if value not in seen:
                seen.add(value)
                referenced_event_numbers.append(value)
    referenced = REFERENCED_EVENT_RE.search(trailing)
    if referenced:
        referenced_event_number = referenced.group("number")
    elif referenced_event_numbers:
        referenced_event_number = referenced_event_numbers[0]
    number = EVENT_NUMBER_RE.search(trailing)
    if number:
        event_number = number.group("number")
    return {
        "event_label_raw": raw_label,
        "event_label_guess": label_guess,
        "event_number_raw": event_number,
        "referenced_event_number_raw": referenced_event_number,
        "referenced_event_numbers_raw": referenced_event_numbers,
        "event_label_trailing_text": trailing,
        "event_label_count": count_event_labels(stripped_text),
    }


def is_date_context_paragraph(paragraph: dict[str, Any]) -> bool:
    text = str(paragraph.get("current_text") or "").strip()
    if not text:
        return False
    # A margin note is a cross-reference to another act (usually carrying that
    # act's date), not the dating of the act that follows. Treating it as date
    # context let the backward extension steal the previous act's margin note
    # and hijack the next entry's registration date (493 entries; LOG 2026-06-11).
    if MARGIN_NOTE_RE.match(text):
        return False
    if re.match(r"^\[manca la data\b", text, flags=re.IGNORECASE) and paragraph.get("date_candidates"):
        return True
    if paragraph.get("date_candidates") and len(text) <= 80 and not paragraph.get("bracket_labels"):
        return True
    return False


def trim_trailing_blank_paragraphs(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    end = len(paragraphs)
    while end > 0 and not str(paragraphs[end - 1].get("current_text") or "").strip():
        end -= 1
    return paragraphs[:end]


def group_rows_by_register(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["register_id"]), []).append(row)
    for group in grouped.values():
        group.sort(key=lambda row: int(row["paragraph_index"]))
    return grouped


def make_source_entry_id(register_id: str, ordinal: int) -> str:
    return f"{register_id}_entry_{ordinal:05d}"


def make_source_entry_key(
    *,
    register_id: str,
    folio_start: str | None,
    folio_end: str | None,
    date_candidates: list[str] | None,
    event_label_guess: str | None,
    event_number_raw: str | None,
) -> str:
    """Content-stable identifier for a source entry.

    The positional ``source_entry_id`` (register + sequential ordinal) is renumbered
    whenever segmentation changes, which would orphan any human review decision keyed
    on it. This key is derived from the entry's stable historical coordinates
    (register, folio span, earliest ISO date, event label and number) and therefore
    does not move when other entries are added, removed, or re-ordered. Collisions
    (entries that share all those coordinates) are disambiguated in
    ``run_segment_entries`` using a content hash, never the ordinal.
    """
    iso_dates = sorted(
        {iso for candidate in (date_candidates or []) if (iso := parse_italian_date(candidate))}
    )
    natural = "|".join(
        [
            str(register_id or ""),
            str(folio_start or ""),
            str(folio_end or ""),
            iso_dates[0] if iso_dates else "",
            str(event_label_guess or ""),
            str(event_number_raw or ""),
        ]
    )
    digest = hashlib.sha256(natural.encode("utf-8")).hexdigest()[:12]
    return f"{register_id}_e{digest}"


def disambiguate_source_entry_keys(entries: list[dict[str, Any]]) -> None:
    """Make ``source_entry_key`` unique without falling back to positional ordinals.

    Entries that share every natural coordinate (same folio span, date, label, and
    event number) receive a stable suffix derived from their text content hash, so
    the key stays reproducible across re-segmentation. Only genuine content
    duplicates fall back to a positional index.
    """
    by_key: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_key.setdefault(entry["source_entry_key"], []).append(entry)
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        for entry in group:
            content_hash = str(entry.get("current_text_sha256") or "")[:8]
            entry["source_entry_key"] = f"{key}_{content_hash}" if content_hash else key
        seen: dict[str, int] = {}
        for entry in group:
            current = entry["source_entry_key"]
            if current in seen:
                seen[current] += 1
                entry["source_entry_key"] = f"{current}_{seen[current]}"
            else:
                seen[current] = 0


def issue_row(
    severity: str,
    code: str,
    message: str,
    register_id: str,
    source_entry_id: str | None = None,
    paragraph_index: int | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "register_id": register_id,
        "source_entry_id": source_entry_id,
        "paragraph_index": paragraph_index,
    }


def build_entry_from_paragraphs(
    *,
    register_id: str,
    source_file: str | None,
    normalized_path: str | None,
    ordinal: int,
    paragraphs: list[dict[str, Any]],
    label_info: dict[str, Any],
    status: str = "candidate",
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    source_entry_id = make_source_entry_id(register_id, ordinal)
    issues: list[dict[str, Any]] = []
    start_paragraph = int(paragraphs[0]["paragraph_index"])
    end_paragraph = int(paragraphs[-1]["paragraph_index"])
    text_parts = [str(paragraph.get("current_text") or "") for paragraph in paragraphs]
    revision_text_parts = [str(paragraph.get("revision_aware_text") or "") for paragraph in paragraphs]
    current_text = "\n".join(text_parts).strip()
    revision_aware = "\n".join(revision_text_parts).strip()
    comment_ids = sorted({cid for paragraph in paragraphs for cid in paragraph.get("comment_ids", [])})
    footnote_ids = sorted({fid for paragraph in paragraphs for fid in paragraph.get("footnote_ids", [])})
    endnote_ids = sorted({eid for paragraph in paragraphs for eid in paragraph.get("endnote_ids", [])})
    bracket_labels = [label for paragraph in paragraphs for label in paragraph.get("bracket_labels", [])]

    label_paragraphs = [
        paragraph
        for paragraph in paragraphs
        if parse_entry_label(str(paragraph.get("current_text") or "")) is not None
    ]
    label_paragraph = label_paragraphs[0] if label_paragraphs else paragraphs[0]
    label_paragraph_index = int(label_paragraph["paragraph_index"])

    # Date harvesting works over the entry HEAD BLOCK — the contiguous run of
    # signal paragraphs (folio heading, date line, label line, blanks) before the
    # first body paragraph — in ANY order, because registers differ: most write
    # folio → date → label, but e.g. Mercanzia 10856 writes folio → label → date.
    # The label line itself contributes only a full day-month-year date (its bare
    # numbers are act numbers, not years); margin notes never contribute (they
    # are cross-references to other acts and belong to the previous entry).
    date_candidates: list[str] = []
    head_text_parts: list[str] = []
    for paragraph in paragraphs:
        paragraph_text = str(paragraph.get("current_text") or "").strip()
        if not paragraph_text:
            continue
        if MARGIN_NOTE_RE.match(paragraph_text):
            continue
        is_label_line = parse_entry_label(paragraph_text) is not None
        if not (
            is_label_line
            or paragraph.get("folio_heading")
            or is_date_context_paragraph(paragraph)
        ):
            break
        if is_label_line:
            date_candidates.extend(FULL_DATE_RE.findall(paragraph_text))
            # The label line joins head_text only when it carries a full date;
            # its bare numbers are act numbers and never parse as dates anyway.
            if FULL_DATE_RE.search(paragraph_text):
                head_text_parts.append(paragraph_text)
        else:
            date_candidates.extend(paragraph.get("date_candidates", []))
            head_text_parts.append(paragraph_text)
    date_candidates_source = "head" if date_candidates else "none"
    if not date_candidates:
        date_candidates = [
            candidate for paragraph in paragraphs for candidate in paragraph.get("date_candidates", [])
        ]
        if date_candidates:
            date_candidates_source = "body_fallback"
    # Prefer a full day-month-year date for the headline registration date; a
    # bare year is kept (year-only dating is real in early registers) but marked
    # with day vs. year precision so downstream display and matching can tell
    # "dated by year" from "dated to the day".
    full_date_candidates = [
        candidate for candidate in date_candidates if re.search(r"[A-Za-zàèéìòù]", candidate)
    ]
    registration_date_raw = (
        full_date_candidates[0]
        if full_date_candidates
        else (date_candidates[0] if date_candidates else None)
    )
    if registration_date_raw and full_date_candidates and date_candidates_source == "head":
        # Restore the double-date suffix that DATE_RE truncates ("19 febbraio
        # 1694/95" → candidate "19 febbraio 1694"): the suffix is the document's
        # own statement of the modern-calendar year and must stay visible.
        expanded = re.search(
            re.escape(registration_date_raw) + r"\s*/\s*\d{2,4}",
            "\n".join(head_text_parts),
        )
        if expanded:
            registration_date_raw = expanded.group(0)
    registration_date_precision = (
        "day" if full_date_candidates else ("year" if date_candidates else None)
    )

    folio_heading_paragraphs = [paragraph for paragraph in paragraphs if paragraph.get("folio_heading")]
    folio_start = None
    folio_end = None
    folio_raw = None
    if folio_heading_paragraphs:
        first_folio = folio_heading_paragraphs[0]["folio_heading"]
        last_folio = folio_heading_paragraphs[-1]["folio_heading"]
        folio_start = first_folio.get("start")
        folio_end = last_folio.get("end")
        folio_raw = first_folio.get("raw")
    else:
        folio_start = label_paragraph.get("current_folio_start")
        folio_end = label_paragraph.get("current_folio_end")
        folio_raw = label_paragraph.get("current_folio_context")

    if not folio_start:
        issues.append(
            issue_row("warning", "entry_missing_folio", "Entry has no folio context", register_id, source_entry_id)
        )
    if not date_candidates:
        issues.append(
            issue_row("warning", "entry_missing_date", "Entry has no date candidate", register_id, source_entry_id)
        )
    if len(label_paragraphs) > 1 or int(label_info.get("event_label_count") or 0) > 1:
        issues.append(
            issue_row(
                "warning",
                "entry_multiple_event_labels",
                "Entry contains more than one event-label paragraph",
                register_id,
                source_entry_id,
            )
        )

    entry_status = status if not issues else "needs_review"
    source_entry_key = make_source_entry_key(
        register_id=register_id,
        folio_start=folio_start,
        folio_end=folio_end,
        date_candidates=date_candidates,
        event_label_guess=label_info.get("event_label_guess"),
        event_number_raw=label_info.get("event_number_raw"),
    )
    entry = {
        "source_entry_id": source_entry_id,
        "source_entry_key": source_entry_key,
        "register_id": register_id,
        "source_file": source_file,
        "normalized_path": normalized_path,
        "entry_ordinal": ordinal,
        "parser_status": entry_status,
        "start_paragraph_index": start_paragraph,
        "end_paragraph_index": end_paragraph,
        "label_paragraph_index": label_paragraph_index,
        "paragraph_count": len(paragraphs),
        "folio_raw": folio_raw,
        "folio_start": folio_start,
        "folio_end": folio_end,
        "registration_date_raw": registration_date_raw,
        "registration_date_precision": registration_date_precision,
        "date_candidates": date_candidates,
        "date_candidates_source": date_candidates_source,
        # Raw head-block text (folio/date lines, label line only when it carries
        # a full date; margin notes excluded). Date matching parses the act's own
        # dates from THIS, not from the truncated DATE_RE candidate strings,
        # because double-dated forms ("29 gennaio 1634/35") state the modern year
        # in the suffix that DATE_RE drops.
        "head_text": "\n".join(head_text_parts),
        **label_info,
        "bracket_labels": bracket_labels,
        "comment_ids": comment_ids,
        "footnote_ids": footnote_ids,
        "endnote_ids": endnote_ids,
        "has_revisions": any(
            paragraph.get("has_insertions") or paragraph.get("has_deletions") or paragraph.get("has_moves")
            for paragraph in paragraphs
        ),
        "current_text": current_text,
        "revision_aware_text": revision_aware,
        "current_text_sha256": hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
        "revision_aware_text_sha256": hashlib.sha256(revision_aware.encode("utf-8")).hexdigest(),
        "issue_codes": [issue["code"] for issue in issues],
    }
    entry_paragraphs = [
        {
            "source_entry_id": source_entry_id,
            "register_id": register_id,
            "entry_ordinal": ordinal,
            "paragraph_index": int(paragraph["paragraph_index"]),
            "paragraph_role": "label"
            if int(paragraph["paragraph_index"]) == label_paragraph_index
            else "margin_note"
            if MARGIN_NOTE_RE.match(str(paragraph.get("current_text") or "").strip())
            else "folio"
            if paragraph.get("folio_heading")
            else "date"
            if is_date_context_paragraph(paragraph)
            else "body",
        }
        for paragraph in paragraphs
    ]
    return entry, entry_paragraphs, issues


def segment_register(
    register: dict[str, Any],
    paragraphs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    register_id = str(register["register_id"])
    source_file = register.get("source_file")
    normalized_path = register.get("normalized_path")
    front_matter_limit = int(register.get("front_matter_paragraph_count") or 0)
    entries: list[dict[str, Any]] = []
    entry_paragraphs: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    unsegmented: list[dict[str, Any]] = []

    active_start: int | None = None
    active_label: dict[str, Any] | None = None
    next_ordinal = 1

    def flush(end_exclusive: int) -> None:
        nonlocal active_start, active_label, next_ordinal
        if active_start is None or active_label is None:
            return
        block = trim_trailing_blank_paragraphs(paragraphs[active_start:end_exclusive])
        if not block:
            return
        entry, mapping, entry_issues = build_entry_from_paragraphs(
            register_id=register_id,
            source_file=source_file,
            normalized_path=normalized_path,
            ordinal=next_ordinal,
            paragraphs=block,
            label_info=active_label,
        )
        entries.append(entry)
        entry_paragraphs.extend(mapping)
        issues.extend(entry_issues)
        next_ordinal += 1
        active_start = None
        active_label = None

    for index, paragraph in enumerate(paragraphs):
        paragraph_index = int(paragraph["paragraph_index"])
        if paragraph_index < front_matter_limit:
            unsegmented.append(
                {
                    "register_id": register_id,
                    "paragraph_index": paragraph_index,
                    "classification": "front_matter",
                    "current_text": paragraph.get("current_text"),
                }
            )
            continue
        label_info = parse_entry_label(str(paragraph.get("current_text") or ""))
        if label_info is not None:
            start = index
            while start > 0:
                previous = paragraphs[start - 1]
                previous_index = int(previous["paragraph_index"])
                if previous_index < front_matter_limit:
                    break
                if active_start is not None and start - 1 < active_start:
                    break
                if previous.get("folio_heading") or is_date_context_paragraph(previous) or not str(previous.get("current_text") or "").strip():
                    start -= 1
                    continue
                break
            flush(start)
            active_start = start
            active_label = label_info
    flush(len(paragraphs))

    assigned = {row["paragraph_index"] for row in entry_paragraphs}
    first_assigned_index = min(assigned) if assigned else None
    front_matter_indexes = {
        row["paragraph_index"] for row in unsegmented if row["classification"] == "front_matter"
    }
    for paragraph in paragraphs:
        paragraph_index = int(paragraph["paragraph_index"])
        if paragraph_index in assigned or paragraph_index in front_matter_indexes:
            continue
        text = str(paragraph.get("current_text") or "").strip()
        if not text:
            classification = "blank_unassigned"
        elif first_assigned_index is not None and paragraph_index < first_assigned_index:
            classification = "preamble_unassigned"
        elif paragraph.get("folio_heading") or is_date_context_paragraph(paragraph):
            classification = "context_unassigned"
        else:
            classification = "unsegmented"
            issues.append(
                issue_row(
                    "warning",
                    "paragraph_unsegmented",
                    "Non-front-matter paragraph was not assigned to a source entry",
                    register_id,
                    paragraph_index=paragraph_index,
                )
            )
        unsegmented.append(
            {
                "register_id": register_id,
                "paragraph_index": paragraph_index,
                "classification": classification,
                "current_text": paragraph.get("current_text"),
            }
        )

    return entries, entry_paragraphs, unsegmented, issues


def run_segment_entries(args: argparse.Namespace) -> int:
    config = load_config(args)
    extraction_dir = config["output_root"] / "03_extracted_registers"
    segmentation_dir = config["output_root"] / "04_source_entries"
    ensure_dir(segmentation_dir)

    registers = read_jsonl(extraction_dir / "registers.jsonl")
    paragraphs = read_jsonl(extraction_dir / "paragraphs.jsonl")
    paragraphs_by_register = group_rows_by_register(paragraphs)
    all_entries: list[dict[str, Any]] = []
    all_entry_paragraphs: list[dict[str, Any]] = []
    all_unsegmented: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    if not registers or not paragraphs:
        all_issues.append(
            {
                "severity": "error",
                "code": "extraction_missing",
                "message": f"Missing extraction inputs under {extraction_dir}",
                "register_id": None,
                "source_entry_id": None,
                "paragraph_index": None,
            }
        )

    for register in sorted(registers, key=lambda row: str(row["register_id"])):
        register_id = str(register["register_id"])
        register_paragraphs = paragraphs_by_register.get(register_id, [])
        print(f"[{len(summary_rows) + 1}/{len(registers)}] {register_id}", flush=True)
        entries, mappings, unsegmented, issues = segment_register(register, register_paragraphs)
        all_entries.extend(entries)
        all_entry_paragraphs.extend(mappings)
        all_unsegmented.extend(unsegmented)
        all_issues.extend(issues)
        summary_rows.append(
            {
                "register_id": register_id,
                "paragraph_count": len(register_paragraphs),
                "front_matter_paragraph_count": register.get("front_matter_paragraph_count"),
                "entry_count": len(entries),
                "entry_paragraph_count": len(mappings),
                "unsegmented_count": sum(1 for row in unsegmented if row["classification"] == "unsegmented"),
                "context_unassigned_count": sum(
                    1 for row in unsegmented if row["classification"] == "context_unassigned"
                ),
                "issue_count": len(issues),
            }
        )

    disambiguate_source_entry_keys(all_entries)

    write_jsonl(segmentation_dir / "source_entries.jsonl", all_entries)
    write_csv(segmentation_dir / "source_entries.csv", all_entries)
    write_jsonl(segmentation_dir / "entry_paragraphs.jsonl", all_entry_paragraphs)
    write_jsonl(segmentation_dir / "unsegmented_paragraphs.jsonl", all_unsegmented)
    write_csv(segmentation_dir / "register_segmentation_summary.csv", summary_rows)
    write_jsonl(segmentation_dir / "issues.jsonl", all_issues)

    issue_count = len(all_issues)
    review_count = sum(1 for entry in all_entries if entry["parser_status"] == "needs_review")
    summary_lines = [
        f"- Extraction input: `{extraction_dir.relative_to(project_root())}`",
        f"- Registers segmented: {len(registers)}",
        f"- Source entries: {len(all_entries)}",
        f"- Entry paragraphs: {len(all_entry_paragraphs)}",
        f"- Unsegmented paragraph rows: {len(all_unsegmented)}",
        f"- Entries needing review: {review_count}",
        f"- Issues: {issue_count}",
        "",
        "What this stage does:",
        "- groups paragraph-level evidence into candidate source acts/entries;",
        "- uses folio-heading, date-context, and event-label paragraphs as parser signals;",
        "- preserves raw labels, event-number text, comments, footnotes, and revision-aware text.",
        "",
        "What this stage does not do:",
        "- it does not match entries to SQLite;",
        "- it does not extract structured contract fields;",
        "- it does not approve tracked changes or update source data.",
    ]
    write_summary(segmentation_dir / "segmentation_summary.md", "Source Entry Segmentation Summary", summary_lines)

    print(f"Wrote source-entry outputs to {segmentation_dir.relative_to(project_root())}")
    print(f"Entries: {len(all_entries)}; issues: {issue_count}; entries needing review: {review_count}")
    return 1 if any(issue["severity"] == "error" for issue in all_issues) else 0


def normalize_match_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"[^\wàèéìòù]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_tokens(text: str | None) -> list[str]:
    normalized = normalize_match_text(text)
    return [
        token
        for token in normalized.split()
        if len(token) > 2 and token not in MATCH_STOPWORDS and not token.isdigit()
    ]


def phrase_in_text(text: str | None, phrase: str | None) -> bool:
    normalized_phrase = normalize_match_text(phrase)
    if len(normalized_phrase) < 4:
        return False
    return normalized_phrase in normalize_match_text(text)


def number_in_text(text: str | None, value: Any) -> bool:
    if value in (None, ""):
        return False
    digits = re.sub(r"\D+", "", str(value))
    if not digits:
        return False
    if int(digits) == 0:
        return False
    compact_text = re.sub(r"\D+", "", str(text or ""))
    if digits in compact_text:
        return True
    grouped = r"[., ]?".join(re.escape(digit) for digit in digits)
    return bool(re.search(grouped, str(text or "")))


def narrative_similarity_metrics(word_text: str | None, db_text: str | None) -> dict[str, Any]:
    word_match_text = normalize_match_text(word_text)[:4000]
    db_match_text = normalize_match_text(db_text)[:4000]
    ratio = 0.0
    if word_match_text and db_match_text:
        ratio = difflib.SequenceMatcher(None, word_match_text, db_match_text).ratio()
    word_tokens = match_tokens(word_text)
    db_tokens = match_tokens(db_text)
    word_token_set = set(word_tokens)
    db_token_set = set(db_tokens)
    shared_tokens = word_token_set & db_token_set
    # Order-aware containment: fraction of the SHORTER token sequence covered by
    # contiguous matching blocks shared with the longer one. Unlike the symmetric
    # SequenceMatcher ratio (which penalizes length asymmetry), this scores ~1.0
    # when the DB `document` is a snippet that is faithfully contained in the Word
    # segment (or vice versa) -- the common case in this corpus.
    matcher = difflib.SequenceMatcher(a=word_tokens, b=db_tokens, autojunk=True)
    matching_blocks = matcher.get_matching_blocks()
    matched_token_count = sum(block.size for block in matching_blocks)
    shorter_token_count = min(len(word_tokens), len(db_tokens))
    # Require a minimum length on the shorter side so a tiny DB document cannot
    # claim full containment from a handful of incidental tokens.
    containment = matched_token_count / shorter_token_count if shorter_token_count >= 5 else 0.0
    shared_phrases: list[str] = []
    for block in sorted(matching_blocks, key=lambda item: item.size, reverse=True):
        if block.size < 4:
            continue
        phrase = " ".join(word_tokens[block.a : block.a + min(block.size, 18)])
        if phrase and phrase not in shared_phrases:
            shared_phrases.append(phrase)
        if len(shared_phrases) >= 6:
            break
    return {
        "narrative_similarity_ratio": round(ratio, 4),
        "text_containment_ratio": round(min(containment, 1.0), 4),
        "word_token_coverage_in_db": round(len(shared_tokens) / len(word_token_set), 4) if word_token_set else 0,
        "db_token_coverage_in_word": round(len(shared_tokens) / len(db_token_set), 4) if db_token_set else 0,
        "shared_distinctive_token_count": len(shared_tokens),
        "shared_phrase_count": len(shared_phrases),
        "longest_shared_phrase_words": max((len(phrase.split()) for phrase in shared_phrases), default=0),
        "shared_phrases": shared_phrases,
    }


def add_field_hit(hits: list[dict[str, str]], field: str, label: str, value: Any) -> None:
    if value in (None, ""):
        return
    text = str(value).strip()
    if not text:
        return
    hit = {"field": field, "label": label, "value": text}
    if hit not in hits:
        hits.append(hit)


def structured_field_overlap(entry_text: str | None, db_row: dict[str, Any]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    phrase_fields = [
        ("firm_name", "firm name", db_row.get("firm_name")),
        ("sub_firm_name", "sub-firm name", db_row.get("sub_firm_name")),
        ("sub_type", "sub-contract type", db_row.get("sub_type")),
        ("currency", "currency", db_row.get("currency")),
    ]
    for field, label, value in phrase_fields:
        if phrase_in_text(entry_text, value):
            add_field_hit(hits, field, label, value)
    for value in db_row.get("person_names") or []:
        if phrase_in_text(entry_text, value):
            add_field_hit(hits, "person_name", "person name", value)
    for value in db_row.get("person_last_names") or []:
        if phrase_in_text(entry_text, value):
            add_field_hit(hits, "person_last_name", "person last name", value)
    for value in db_row.get("professions") or []:
        if phrase_in_text(entry_text, value):
            add_field_hit(hits, "profession", "profession", value)
    for value in db_row.get("partnership_names") or []:
        if phrase_in_text(entry_text, value):
            add_field_hit(hits, "partnership_name", "investment partnership name", value)
    for value in db_row.get("investment_non_cash_values") or []:
        if phrase_in_text(entry_text, value):
            add_field_hit(hits, "investment_non_cash", "non-cash investment", value)
    for value in db_row.get("place_names") or []:
        if phrase_in_text(entry_text, value):
            add_field_hit(hits, "place_name", "place name", value)
    for value in db_row.get("addresses") or []:
        if phrase_in_text(entry_text, value):
            add_field_hit(hits, "address", "address", value)
    number_fields = [
        ("total", "total capital", db_row.get("total")),
        ("duration_months", "duration in months", db_row.get("duration_months")),
        ("renewal_months", "renewal in months", db_row.get("renewal_months")),
        ("automatic_renewal_months", "automatic renewal in months", db_row.get("automatic_renewal_months")),
    ]
    for field, label, value in number_fields:
        if number_in_text(entry_text, value):
            add_field_hit(hits, field, label, value)
    for value in db_row.get("investment_cash_amounts") or []:
        if number_in_text(entry_text, value):
            add_field_hit(hits, "investment_cash", "investment cash amount", value)
    for field in ["registration_date", "start_date", "end_date"]:
        if db_row.get(field) and phrase_in_text(entry_text, db_row.get(field)):
            add_field_hit(hits, field, field.replace("_", " "), db_row.get(field))
    return hits


def field_overlap_summary(hits: list[dict[str, str]], limit: int = 10) -> str:
    if not hits:
        return "No structured DB field overlap was recorded."
    return "; ".join(f"{hit['label']}: {hit['value']}" for hit in hits[:limit])


def is_date_like_line(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped and DATE_RE.fullmatch(stripped))


def entry_text_for_similarity(entry: dict[str, Any]) -> str:
    lines = [line.strip() for line in str(entry.get("current_text") or "").splitlines()]
    body_lines: list[str] = []
    for line in lines:
        if not line:
            continue
        if parse_folio_heading(line):
            continue
        if is_date_like_line(line):
            continue
        if parse_entry_label(line):
            continue
        body_lines.append(line)
    return "\n".join(body_lines) or str(entry.get("current_text") or "")


def parse_italian_date(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(
        r"\b(?P<day>[0-3]?\d)\s+(?P<month>[A-Za-zàèéìòù]+)\s+(?P<year>1[4-8]\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    month = ITALIAN_MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    day = int(match.group("day"))
    if day < 1 or day > 31:
        return None
    return f"{match.group('year')}-{month}-{day:02d}"


def parse_italian_date_candidates(text: str | None) -> set[str]:
    if not text:
        return set()
    dates: set[str] = set()
    for match in re.finditer(
        r"\b(?P<day>[0-3]?\d)\s+(?P<month>[A-Za-zàèéìòù]+)\s+"
        r"(?P<year>1[4-8]\d{2})(?:/(?P<short_year>\d{2}))?\b",
        text,
        flags=re.IGNORECASE,
    ):
        month = ITALIAN_MONTHS.get(match.group("month").lower())
        if month is None:
            continue
        day = int(match.group("day"))
        if day < 1 or day > 31:
            continue
        year = match.group("year")
        dates.add(f"{year}-{month}-{day:02d}")
        if match.group("short_year"):
            dates.add(f"{year[:2]}{match.group('short_year')}-{month}-{day:02d}")
    return dates


def entry_date_candidates(entry: dict[str, Any]) -> set[str]:
    candidates = parse_italian_date_candidates(entry.get("registration_date_raw"))
    candidates.update(parse_italian_date_candidates(entry.get("current_text")))
    return candidates


# Tuscany abolished the Florentine new-year (25 March) and adopted the common
# 1-January start on 1 January 1750; the shift only applies to earlier dates.
STILE_FIORENTINO_CUTOFF_YEAR = 1750


def stile_fiorentino_modern_date(year: int, month: int, day: int) -> str | None:
    """Modern-calendar equivalent of a Florentine-style date, or None if unshifted.

    The Florentine year began 25 March (*ab Incarnatione*), so a document date in
    the window [1 Jan .. 24 Mar] lags the modern year by one (e.g. "22 febbraio
    1499" Florentine == 22 February 1500 modern). The encoded DB dates use the
    modern calendar; the Word narratives preserve the document's stated date.
    Returns the shifted ISO date only inside the window and before the 1750
    reform; otherwise None (no shift applies).
    """
    if year >= STILE_FIORENTINO_CUTOFF_YEAR:
        return None
    if month in (1, 2) or (month == 3 and day <= 24):
        return f"{year + 1}-{month:02d}-{day:02d}"
    return None


def stile_fiorentino_modern_dates(text: str | None) -> set[str]:
    if not text:
        return set()
    dates: set[str] = set()
    for match in re.finditer(
        r"\b(?P<day>[0-3]?\d)\s+(?P<month>[A-Za-zàèéìòù]+)\s+(?P<year>1[4-8]\d{2})\b",
        text,
        flags=re.IGNORECASE,
    ):
        month = ITALIAN_MONTHS.get(match.group("month").lower())
        if month is None:
            continue
        day = int(match.group("day"))
        if day < 1 or day > 31:
            continue
        shifted = stile_fiorentino_modern_date(int(match.group("year")), int(month), day)
        if shifted:
            dates.add(shifted)
    return dates


def entry_stile_fiorentino_dates(entry: dict[str, Any]) -> set[str]:
    candidates = stile_fiorentino_modern_dates(entry.get("registration_date_raw"))
    candidates.update(stile_fiorentino_modern_dates(entry.get("current_text")))
    return candidates


def entry_head_dates(entry: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """The act's OWN dating, from the entry head block: (iso_dates, stile_dates, bare_years).

    ``date_candidates`` holds head-block dates when ``date_candidates_source`` is
    "head"; on "body_fallback" the candidates came from anywhere in the narrative
    and must not be treated as the act's own date line (they only support the
    weak ``date_in_narrative`` signal). Bare years are returned separately:
    year-only dating is real in early registers but too coarse for an exact
    date signal or a date conflict.
    """
    if (entry.get("date_candidates_source") or "head") != "head":
        return set(), set(), set()
    # Parse from the raw head-block text when available: the DATE_RE candidate
    # strings truncate double-dated forms ("29 gennaio 1634/35" → "29 gennaio
    # 1634"), and the /35 suffix is the document's own statement of the modern
    # year — losing it would downgrade ~1,250 exact matches to stile-shifted.
    head_text = entry.get("head_text")
    if head_text is None:
        head_text = "\n".join(str(candidate) for candidate in entry.get("date_candidates") or [])
    iso_dates = parse_italian_date_candidates(head_text)
    stile_dates = stile_fiorentino_modern_dates(head_text) if iso_dates else set()
    years: set[str] = set()
    if not iso_dates:
        for candidate in entry.get("date_candidates") or []:
            year = re.fullmatch(r"\s*(1[4-8]\d{2})\s*", str(candidate))
            if year:
                years.add(year.group(1))
    return iso_dates, stile_dates, years


def strip_db_folio_annotations(folio: str | None) -> str:
    """Reduce a DB folio string to its current-numbering token.

    DB folio values frequently carry inline annotations that block parsing and
    cause false folio conflicts, e.g. ``94r[ORIG.93r]`` (original foliation),
    ``118v-119r/117v-118r`` (current/original split), or ``76v=774``. We keep the
    leading current-numbering part and drop the annotation. The raw value is
    preserved separately on the DB row for review and original-numbering checks.
    """
    text = re.sub(r"\[[^\]]*\]", "", str(folio or ""))
    text = re.split(r"[/=]", text)[0]
    return text.strip()


def parse_db_folio(folio: str | None) -> tuple[str | None, str | None]:
    if folio is None:
        return None, None
    cleaned = re.sub(r"\s+", "", strip_db_folio_annotations(folio))
    if not cleaned:
        return None, None
    # Recto-verso written without a separator: "24rv" / "109vr" -> 24r .. 24v.
    # Checked before parse_folio_heading, which would otherwise absorb the "v".
    rv = re.fullmatch(r"(?P<number>\d+(?:bis)?)(?:rv|vr)", cleaned, flags=re.IGNORECASE)
    if rv:
        number = rv.group("number")
        return normalize_folio_token(number, "r"), normalize_folio_token(number, "v")
    parsed = parse_folio_heading(f"c. {cleaned}")
    if parsed:
        return parsed["start"], parsed["end"]
    if re.fullmatch(rf"{FOLIO_NUMBER_PATTERN}[rv]?", cleaned, flags=re.IGNORECASE):
        token = re.match(rf"(?P<number>{FOLIO_NUMBER_PATTERN})(?P<side>[rv])?", cleaned, flags=re.IGNORECASE)
        if token:
            normalized = normalize_folio_token(token.group("number"), token.group("side"))
            return normalized, normalized
    return cleaned, cleaned


def folio_sort_key(token: str | None) -> tuple[int, int, int, int] | None:
    """Orderable key for a folio token: (number, bis, letter, side).

    Side recto (`r`) sorts before verso (`v`). Returns None for tokens with no
    leading number (e.g. ``n.n``), which fall back to string comparison.
    """
    if not token:
        return None
    text = str(token).strip().lower()
    number_match = re.match(r"(\d+)", text)
    if not number_match:
        return None
    number = int(number_match.group(1))
    rest = text[number_match.end():]
    bis = 1 if "bis" in rest else 0
    letter = 0
    paren = re.search(r"\(([a-z])\)", rest)
    if paren:
        letter = ord(paren.group(1)) - ord("a") + 1
    side = 0
    for char in reversed(rest):
        if char == "v":
            side = 1
            break
        if char == "r":
            side = 0
            break
    return (number, bis, letter, side)


def folio_span_keys(
    start: str | None, end: str | None
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    start_key = folio_sort_key(start)
    if start_key is None:
        return None
    end_key = folio_sort_key(end) or start_key
    return (start_key, end_key) if start_key <= end_key else (end_key, start_key)


def folio_relationship(entry: dict[str, Any], db_row: dict[str, Any]) -> str:
    """Relate a Word entry's folio span to a DB row's folio span.

    A Word source entry usually spans the whole act (often several folia), while
    a DB row may sit on any folio within that span. Endpoint-only comparison
    therefore mislabels in-span DB rows as different. This compares the two
    ranges as intervals: ``exact`` > ``within`` (one range contains the other) >
    ``overlap`` (ranges intersect) > ``off_by_one`` (adjacent folio numbers,
    a common original-vs-current numbering signature) > ``different``.
    """
    entry_start = entry.get("folio_start")
    entry_end = entry.get("folio_end")
    db_start = db_row.get("folio_start")
    db_end = db_row.get("folio_end")
    if not entry_start or not db_start:
        return "missing"
    if entry_start == db_start and (entry_end or entry_start) == (db_end or db_start):
        return "exact"
    entry_span = folio_span_keys(entry_start, entry_end)
    db_span = folio_span_keys(db_start, db_end)
    if entry_span is None or db_span is None:
        # Unorderable tokens (e.g. n.n): fall back to endpoint string comparison.
        if entry_start == db_start or (entry_end and entry_end == db_end):
            return "overlap"
        return "different"
    (entry_lo, entry_hi), (db_lo, db_hi) = entry_span, db_span
    if entry_lo == db_lo and entry_hi == db_hi:
        return "exact"
    if (entry_lo <= db_lo and db_hi <= entry_hi) or (db_lo <= entry_lo and entry_hi <= db_hi):
        return "within"
    if entry_lo <= db_hi and db_lo <= entry_hi:
        return "overlap"
    if max(db_lo[0] - entry_hi[0], entry_lo[0] - db_hi[0]) == 1:
        return "off_by_one"
    return "different"


def integer_or_none(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return int(match.group(0))


def append_context_value(mapping: dict[int, list[str]], key: int, value: Any) -> None:
    if value in (None, ""):
        return
    text = str(value).strip()
    if not text:
        return
    values = mapping.setdefault(key, [])
    if text not in values:
        values.append(text)


def append_context_number(mapping: dict[int, list[int]], key: int, value: Any) -> None:
    if value in (None, ""):
        return
    try:
        number = int(value)
    except (TypeError, ValueError):
        return
    if number == 0:
        return
    values = mapping.setdefault(key, [])
    if number not in values:
        values.append(number)


def load_db_match_context(connection: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    context: dict[int, dict[str, Any]] = {}
    person_names: dict[int, list[str]] = {}
    person_last_names: dict[int, list[str]] = {}
    professions: dict[int, list[str]] = {}
    partnership_names: dict[int, list[str]] = {}
    investment_cash_amounts: dict[int, list[int]] = {}
    investment_non_cash_values: dict[int, list[str]] = {}
    place_names: dict[int, list[str]] = {}
    addresses: dict[int, list[str]] = {}

    for row in connection.execute(
        """
        SELECT i.contract_id, p.first_name, p.father_mother, p.grandfather, p.last_name,
               i.profession
        FROM investor i
        JOIN person p ON p.person_id = i.person_id
        """
    ):
        contract_id = int(row["contract_id"])
        name_parts = [
            str(row[field] or "").strip()
            for field in ["first_name", "father_mother", "grandfather", "last_name"]
            if str(row[field] or "").strip()
        ]
        append_context_value(person_names, contract_id, " ".join(name_parts))
        append_context_value(person_last_names, contract_id, row["last_name"])
        append_context_value(professions, contract_id, row["profession"])

    for row in connection.execute(
        """
        SELECT contract_id, partnership_name, investment_cash, investment_non_cash
        FROM investment
        """
    ):
        contract_id = int(row["contract_id"])
        append_context_value(partnership_names, contract_id, row["partnership_name"])
        append_context_number(investment_cash_amounts, contract_id, row["investment_cash"])
        append_context_value(investment_non_cash_values, contract_id, row["investment_non_cash"])

    for row in connection.execute(
        """
        SELECT cp.contract_id, p.place_name, cp.address
        FROM contract_place cp
        JOIN place p ON p.place_id = cp.place_id
        """
    ):
        contract_id = int(row["contract_id"])
        append_context_value(place_names, contract_id, row["place_name"])
        append_context_value(addresses, contract_id, row["address"])

    contract_ids = set().union(
        person_names,
        person_last_names,
        professions,
        partnership_names,
        investment_cash_amounts,
        investment_non_cash_values,
        place_names,
        addresses,
    )
    for contract_id in contract_ids:
        context[contract_id] = {
            "person_names": person_names.get(contract_id, []),
            "person_last_names": person_last_names.get(contract_id, []),
            "professions": professions.get(contract_id, []),
            "partnership_names": partnership_names.get(contract_id, []),
            "investment_cash_amounts": investment_cash_amounts.get(contract_id, []),
            "investment_non_cash_values": investment_non_cash_values.get(contract_id, []),
            "place_names": place_names.get(contract_id, []),
            "addresses": addresses.get(contract_id, []),
        }
    return context


def load_db_match_rows(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    if not db_path.exists():
        return [], [
            {
                "severity": "error",
                "code": "db_missing",
                "message": f"SQLite database not found: {db_path}",
                "source_entry_id": None,
                "db_row_id": None,
                "register_id": None,
            }
        ]
    rows: list[dict[str, Any]] = []
    queries = [
        (
            "contract",
            """
            SELECT contract_id, archive, series, folder, folio, registration_date,
                   firm_name, NULL AS sub_firm_name, start_date, NULL AS end_date,
                   duration_months, automatic_renewal_months, NULL AS renewal_months,
                   total, currency_id, document, NULL AS sub_type, NULL AS main_contract_id
            FROM contract
            """,
        ),
        (
            "sub_contract",
            """
            SELECT contract_id, archive, series, folder, folio, registration_date,
                   NULL AS firm_name, sub_firm_name, NULL AS start_date, end_date,
                   NULL AS duration_months, NULL AS automatic_renewal_months, renewal_months,
                   NULL AS total, NULL AS currency_id, document, sub_type, main_contract_id
            FROM sub_contract
            """,
        ),
    ]
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        context_by_contract_id = load_db_match_context(connection)
        currency_by_id = {
            int(row["currency_id"]): row["currency"]
            for row in connection.execute("SELECT currency_id, currency FROM currency")
        }
        for table_name, query in queries:
            for row in connection.execute(query):
                raw_folder = str(row["folder"] or "").strip()
                combined = f"{row['series'] or ''} {raw_folder}"
                folder = infer_folder(combined) or normalize_folder(raw_folder)
                if not folder:
                    issues.append(
                        {
                            "severity": "warning",
                            "code": "db_row_folder_unrecognized",
                            "message": f"Could not infer register for {table_name}:{row['contract_id']}",
                            "source_entry_id": None,
                            "db_row_id": f"{table_name}:{row['contract_id']}",
                            "register_id": None,
                        }
                    )
                series = series_for_folder(folder, combined) if folder else None
                register_id = register_id_for(series, folder) if folder else None
                folio_start, folio_end = parse_db_folio(row["folio"])
                document = row["document"] or ""
                main_context_id = int(row["main_contract_id"] or row["contract_id"])
                db_context = context_by_contract_id.get(main_context_id, {})
                rows.append(
                    {
                        "db_row_id": f"{table_name}:{row['contract_id']}",
                        "db_table": table_name,
                        "contract_id": row["contract_id"],
                        "main_contract_id": row["main_contract_id"],
                        "archive": row["archive"],
                        "series": row["series"],
                        "folder": folder,
                        "register_id": register_id,
                        "folio_raw": row["folio"],
                        "folio_start": folio_start,
                        "folio_end": folio_end,
                        "registration_date": row["registration_date"],
                        "firm_name": row["firm_name"],
                        "sub_firm_name": row["sub_firm_name"],
                        "start_date": row["start_date"],
                        "end_date": row["end_date"],
                        "duration_months": row["duration_months"],
                        "automatic_renewal_months": row["automatic_renewal_months"],
                        "renewal_months": row["renewal_months"],
                        "total": row["total"],
                        "currency_id": row["currency_id"],
                        "currency": currency_by_id.get(int(row["currency_id"])) if row["currency_id"] else None,
                        "sub_type": row["sub_type"],
                        "person_names": db_context.get("person_names", []),
                        "person_last_names": db_context.get("person_last_names", []),
                        "professions": db_context.get("professions", []),
                        "partnership_names": db_context.get("partnership_names", []),
                        "investment_cash_amounts": db_context.get("investment_cash_amounts", []),
                        "investment_non_cash_values": db_context.get("investment_non_cash_values", []),
                        "place_names": db_context.get("place_names", []),
                        "addresses": db_context.get("addresses", []),
                        "document_characters": len(document),
                        "document_text_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                        "_match_text": normalize_match_text(document)[:4000],
                    }
                )
    return rows, issues


def db_native_row_ids() -> set[str]:
    """``table:id`` keys of rows created directly on the platform (DB-native).

    Read from the authoritative change log (corrections.db). Returns an empty
    set when the log does not exist yet; never fails the pipeline.
    """
    try:
        try:
            from workflows import corrections_db
        except ImportError:  # run as a script from the repo root
            import corrections_db  # type: ignore[no-redef]
        path = corrections_db.default_path()
        if not path.exists():
            return set()
        conn = corrections_db.connect(path)
        try:
            return corrections_db.created_row_ids(conn)
        finally:
            conn.close()
    except Exception:
        return set()


def db_type_matches(entry: dict[str, Any], db_row: dict[str, Any]) -> bool:
    labels = component_label_guesses(entry) or {str(entry.get("event_label_guess") or "")}
    if db_row["db_table"] == "contract":
        return "new_contract" in labels
    if db_row["db_table"] != "sub_contract":
        return False
    sub_type = normalize_match_text(db_row.get("sub_type"))
    if not sub_type:
        return False
    for label in labels:
        allowed = DB_EVENT_TYPE_MAP.get(str(label), set())
        if any(value in sub_type for value in allowed):
            return True
    return False


def primary_db_type_matches(entry: dict[str, Any], db_row: dict[str, Any]) -> bool:
    label = entry.get("event_label_guess")
    if db_row["db_table"] == "contract":
        return label == "new_contract"
    if db_row["db_table"] != "sub_contract":
        return False
    sub_type = normalize_match_text(db_row.get("sub_type"))
    if not sub_type:
        return False
    allowed = DB_EVENT_TYPE_MAP.get(str(label), set())
    return any(value in sub_type for value in allowed)


def event_type_relation(entry: dict[str, Any], db_table: str, db_sub_type: Any) -> str:
    """How the Word event label relates to a DB row's table/type, per link.

    This is the single source of truth the review UI renders (it must not keep
    its own label→type map; see qa_packet_schema.md v4):

    - ``exact``        — the normalized label IS the DB category (termination→
                         termination, balance→balance, …, new_contract→contract).
    - ``interpretive`` — the label folds into the category only via the
                         permissive ``DB_EVENT_TYPE_MAP`` groupings (cessione→
                         variation, ratifica→variation, proroga→renewal, …).
                         These groupings are an editorial judgment marked
                         FT-review-pending; the UI shows a quiet note, not a
                         warning.
    - ``mismatch``     — the type is outside every component label's allowed
                         set; the UI warns.
    - ``unknown``      — no usable label or blank sub_type; the UI stays quiet.

    Component-aware: a combined ``[disdetta] + [nuova]`` entry is judged against
    each parsed component label, so its `contract` row is ``exact``, not a
    mismatch against "termination".
    """
    labels = {
        str(label)
        for label in (component_label_guesses(entry) or {str(entry.get("event_label_guess") or "")})
        if label
    }
    if db_table == "contract":
        if not labels:
            return "unknown"
        return "exact" if "new_contract" in labels else "mismatch"
    sub_type = normalize_match_text(db_sub_type)
    if not labels or not sub_type:
        return "unknown"
    # Canonical codings: the label IS the DB category, or is the category the
    # original input rules define for it (modifica → variation is the rules' own
    # category for modifications, the same standing as disdetta → termination).
    canonical = {
        "termination": "termination",
        "balance": "balance",
        "renewal": "renewal",
        "variation": "variation",
        "modification": "variation",
    }
    if any(canonical.get(label, "") and canonical[label] in sub_type for label in labels):
        return "exact"
    if any(
        value in sub_type
        for label in labels
        for value in DB_EVENT_TYPE_MAP.get(label, set())
    ):
        return "interpretive"
    return "mismatch"


def candidate_db_rows_for_entry(
    entry: dict[str, Any],
    db_rows_by_register: dict[str, list[dict[str, Any]]],
    db_contract_rows_by_id: dict[int, list[dict[str, Any]]],
    db_main_rows_by_id: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = db_rows_by_register.get(str(entry["register_id"]), [])
    event_number = integer_or_none(entry.get("event_number_raw"))
    referenced_number = integer_or_none(entry.get("referenced_event_number_raw"))
    component_contract_numbers = component_contract_ids(entry)
    component_sub_main_numbers = component_sub_contract_main_ids(entry)
    parsed_dates = entry_date_candidates(entry)
    stile_dates = entry_stile_fiorentino_dates(entry)
    candidate_ids: set[str] = set()
    candidates: list[dict[str, Any]] = []

    def add_if(row: dict[str, Any], condition: bool) -> None:
        if condition and row["db_row_id"] not in candidate_ids:
            candidate_ids.add(row["db_row_id"])
            candidates.append(row)

    if (
        entry.get("event_label_guess") in {"new_contract", *CONTRACT_ID_COMPATIBLE_LABELS}
        and event_number is not None
    ):
        for row in db_contract_rows_by_id.get(event_number, []):
            add_if(row, row["db_table"] == "contract")
    for component_contract_number in component_contract_numbers:
        for row in db_contract_rows_by_id.get(component_contract_number, []):
            add_if(row, row["db_table"] == "contract")
    if referenced_number is not None:
        for row in db_main_rows_by_id.get(referenced_number, []):
            add_if(row, row["db_table"] == "sub_contract")
    elif event_number is not None and entry.get("event_label_guess") != "new_contract":
        for row in db_main_rows_by_id.get(event_number, []):
            add_if(row, row["db_table"] == "sub_contract")
    for component_sub_main_number in component_sub_main_numbers:
        for row in db_main_rows_by_id.get(component_sub_main_number, []):
            add_if(row, row["db_table"] == "sub_contract")

    for row in rows:
        add_if(
            row,
            entry.get("event_label_guess") in {"new_contract", *CONTRACT_ID_COMPATIBLE_LABELS}
            and event_number is not None
            and row["db_table"] == "contract"
            and row["contract_id"] == event_number,
        )
        add_if(
            row,
            row["db_table"] == "contract"
            and row["contract_id"] in component_contract_numbers,
        )
        add_if(
            row,
            row["db_table"] == "sub_contract"
            and referenced_number is not None
            and row.get("main_contract_id") == referenced_number,
        )
        add_if(
            row,
            row["db_table"] == "sub_contract"
            and event_number is not None
            and row.get("main_contract_id") == event_number,
        )
        add_if(
            row,
            row["db_table"] == "sub_contract"
            and row.get("main_contract_id") in component_sub_main_numbers,
        )
        add_if(row, bool(parsed_dates) and row.get("registration_date") in parsed_dates)
        add_if(row, bool(stile_dates) and row.get("registration_date") in stile_dates)
        add_if(row, folio_relationship(entry, row) in {"exact", "within", "overlap"})
    return candidates


def score_match(entry: dict[str, Any], db_row: dict[str, Any], entry_text: str) -> dict[str, Any]:
    score = 0.0
    signals: list[str] = []
    conflicts: list[str] = []
    event_number = integer_or_none(entry.get("event_number_raw"))
    referenced_number = integer_or_none(entry.get("referenced_event_number_raw"))
    component_contract_numbers = component_contract_ids(entry)
    component_sub_main_numbers = component_sub_contract_main_ids(entry)
    parsed_dates = entry_date_candidates(entry)
    stile_fiorentino_dates = entry_stile_fiorentino_dates(entry)
    parsed_date = parse_italian_date(entry.get("registration_date_raw"))
    metrics = narrative_similarity_metrics(entry_text, db_row.get("_match_text") or "")
    field_hits = structured_field_overlap(entry_text, db_row)

    if db_row.get("register_id") is None:
        conflicts.append("db_register_missing")
    elif db_row.get("register_id") != entry.get("register_id"):
        conflicts.append("db_register_differs")

    if (
        entry.get("event_label_guess") == "new_contract"
        and event_number is not None
        and db_row["db_table"] == "contract"
        and db_row["contract_id"] == event_number
    ):
        score += 45
        signals.append("contract_id_exact")
    elif (
        entry.get("event_label_guess") in CONTRACT_ID_COMPATIBLE_LABELS
        and event_number is not None
        and db_row["db_table"] == "contract"
        and db_row["contract_id"] == event_number
    ):
        score += 35
        signals.append("contract_id_from_event_number")
    elif (
        db_row["db_table"] == "contract"
        and db_row["contract_id"] in component_contract_numbers
    ):
        score += 40
        signals.append("component_contract_id_exact")
    if db_row["db_table"] == "sub_contract":
        if referenced_number is not None and db_row.get("main_contract_id") == referenced_number:
            score += 35
            signals.append("main_contract_id_referenced")
        elif event_number is not None and db_row.get("main_contract_id") == event_number:
            score += 30
            signals.append("main_contract_id_from_event_number")
        elif db_row.get("main_contract_id") in component_sub_main_numbers:
            score += 32
            signals.append("component_main_contract_id")
    # The exact-date signal (and the date conflict) is judged against the act's
    # OWN dating from the entry head block, not against every date the narrative
    # mentions: 423 margin-note dates and ~52 genuinely different body dates used
    # to satisfy "registration_date_exact" and silently suppress real date
    # conflicts (LOG 2026-06-11). A body/margin date that matches the DB is still
    # recorded — as the weak ``date_in_narrative`` signal, which never hides a
    # head-date conflict. Year-only dating (early registers) earns a soft
    # ``registration_year_match`` and never raises a conflict.
    head_dates, head_stile_dates, head_years = entry_head_dates(entry)
    db_date = db_row.get("registration_date")
    if db_date and db_date in head_dates:
        score += 25
        signals.append("registration_date_exact")
    elif db_date and db_date in head_stile_dates:
        # DB stores the modern calendar; the Word date is one year behind because
        # the Florentine year began 25 March. Treat as a (slightly softer) match.
        score += 20
        signals.append("registration_date_stile_fiorentino")
    else:
        if db_date and head_dates:
            conflicts.append("registration_date_differs")
        if db_date and db_date not in head_dates:
            if not head_dates and head_years and str(db_date)[:4] in head_years:
                score += 8
                signals.append("registration_year_match")
            elif db_date in parsed_dates or db_date in stile_fiorentino_dates:
                score += 6
                signals.append("date_in_narrative")

    folio_relation = folio_relationship(entry, db_row)
    if folio_relation == "exact":
        score += 20
        signals.append("folio_exact")
    elif folio_relation == "within":
        # DB row sits inside the Word entry's folio span (or vice versa).
        score += 16
        signals.append("folio_within")
    elif folio_relation == "overlap":
        score += 13
        signals.append("folio_overlap")
    elif folio_relation == "off_by_one":
        # Adjacent folio numbers: weak positive; often original-vs-current numbering.
        score += 3
        signals.append("folio_adjacent")
    elif folio_relation == "different":
        conflicts.append("folio_differs")

    if db_type_matches(entry, db_row):
        score += 10
        signals.append(
            "event_type_compatible"
            if primary_db_type_matches(entry, db_row)
            else "component_event_type_compatible"
        )
    elif entry.get("event_label_guess") == "new_contract" and db_row["db_table"] != "contract":
        conflicts.append("event_type_table_differs")
    elif (
        entry.get("event_label_guess") != "new_contract"
        and db_row["db_table"] == "contract"
        and "contract_id_from_event_number" not in signals
        and "component_contract_id_exact" not in signals
    ):
        conflicts.append("event_type_table_differs")

    text_similarity = metrics["narrative_similarity_ratio"]
    text_containment = metrics["text_containment_ratio"]
    # Use the stronger of symmetric ratio and order-aware containment so that a
    # DB snippet contained in a longer Word segment is not mis-scored as dissimilar.
    text_strength = max(text_similarity, text_containment)
    if entry_text and db_row.get("_match_text"):
        score += round(text_strength * 20, 2)
        strong_directional = (
            metrics["db_token_coverage_in_word"] >= 0.70
            and metrics["longest_shared_phrase_words"] >= 8
        )
        if text_strength >= 0.55 or strong_directional:
            signals.append("text_similarity_good")
        elif text_strength < 0.20 and metrics["db_token_coverage_in_word"] < 0.50:
            conflicts.append("text_similarity_low")
        if metrics["word_token_coverage_in_db"] >= 0.55 and metrics["shared_distinctive_token_count"] >= 12:
            score += 6
            signals.append("token_coverage_good")
        if metrics["longest_shared_phrase_words"] >= 8:
            score += 5
            signals.append("shared_phrase_good")

    hit_fields = {hit["field"] for hit in field_hits}
    if {"person_name", "person_last_name"} & hit_fields:
        score += min(10, 3 * sum(1 for hit in field_hits if hit["field"] in {"person_name", "person_last_name"}))
        signals.append("person_name_overlap")
    if {"firm_name", "sub_firm_name", "partnership_name"} & hit_fields:
        score += 8
        signals.append("firm_name_overlap")
    if {"place_name", "address"} & hit_fields:
        score += 5
        signals.append("place_overlap")
    if {"total", "investment_cash"} & hit_fields:
        score += 6
        signals.append("amount_overlap")
    if {"registration_date", "start_date", "end_date"} & hit_fields:
        score += 4
        signals.append("date_field_overlap")
    return {
        "score": round(score, 2),
        "signals": signals,
        "conflicts": conflicts,
        "text_similarity": round(text_similarity, 4),
        "narrative_similarity_ratio": metrics["narrative_similarity_ratio"],
        "text_containment_ratio": metrics["text_containment_ratio"],
        "word_token_coverage_in_db": metrics["word_token_coverage_in_db"],
        "db_token_coverage_in_word": metrics["db_token_coverage_in_word"],
        "shared_distinctive_token_count": metrics["shared_distinctive_token_count"],
        "shared_phrase_count": metrics["shared_phrase_count"],
        "longest_shared_phrase_words": metrics["longest_shared_phrase_words"],
        "shared_phrases": metrics["shared_phrases"],
        "field_overlap_count": len(field_hits),
        "field_overlap": field_hits,
        "field_overlap_plain_language": field_overlap_summary(field_hits),
        "entry_registration_date_iso": parsed_date,
        "entry_registration_date_candidates": sorted(parsed_dates),
        "folio_relation": folio_relation,
    }


def classify_match(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "word_only"
    best = candidates[0]
    second_score = candidates[1]["score"] if len(candidates) > 1 else 0
    margin = best["score"] - second_score
    if has_matched_multiple(candidates):
        return "matched_multiple"
    if is_dual_contract_sub_combined_act(candidates):
        return "matched_high_confidence"
    if best["score"] >= 85 and margin >= 12 and not best["conflicts"]:
        return "matched_high_confidence"
    if best["score"] >= 65 and margin >= 8:
        return "matched_candidate"
    if best["score"] >= 45:
        return "ambiguous" if len(candidates) > 1 else "matched_candidate"
    return "word_only"


def has_matched_multiple(candidates: list[dict[str, Any]]) -> bool:
    if len(candidates) < 2:
        return False
    best = candidates[0]
    second = candidates[1]
    if best["score"] < 85 or second["score"] < 85:
        return False
    if best["conflicts"] or second["conflicts"]:
        return False
    if abs(best["score"] - second["score"]) > 3:
        return False
    same_main = (
        best.get("db_main_contract_id") is not None
        and best.get("db_main_contract_id") == second.get("db_main_contract_id")
    )
    same_date = best.get("db_registration_date") == second.get("db_registration_date")
    same_folio = (
        best.get("db_folio_start") == second.get("db_folio_start")
        and best.get("db_folio_end") == second.get("db_folio_end")
    )
    return same_main and same_date and same_folio


def is_dual_contract_sub_combined_act(candidates: list[dict[str, Any]]) -> bool:
    """True when top two candidates are a contract + sub_contract combined act.

    Word entries like [disdetta + nuova] often link one sub_contract (termination)
    and one contract (new) with close scores but different main_contract_ids, so
    has_matched_multiple() does not apply and the default margin rule marks them ambiguous.
    """
    if len(candidates) < 2:
        return False
    first = candidates[0]
    second = candidates[1]
    if {first.get("db_table"), second.get("db_table")} != {"contract", "sub_contract"}:
        return False
    for candidate in (first, second):
        if candidate["score"] < 100:
            return False
        if candidate.get("conflicts"):
            return False
        if not (ID_SIGNALS & set(candidate.get("signals") or [])):
            return False
        if candidate_text_strength(candidate) < 0.85:
            return False
    if first["score"] - second["score"] > 25:
        return False
    if len(candidates) > 2 and second["score"] - candidates[2]["score"] < 40:
        return False
    return True


def is_combined_word_label(entry_or_match: dict[str, Any]) -> bool:
    raw = str(entry_or_match.get("event_label_raw") or "").lower()
    return (
        entry_or_match.get("event_label_guess") == "combined_event"
        or "+" in raw
        or "/" in raw
        or "&" in raw
    )


def connected_contract_subcontract(candidates: list[dict[str, Any]]) -> bool:
    contract_ids = {
        candidate.get("db_contract_id")
        for candidate in candidates
        if candidate.get("db_table") == "contract"
    }
    main_ids = {
        candidate.get("db_main_contract_id")
        for candidate in candidates
        if candidate.get("db_main_contract_id") is not None
    }
    return bool(contract_ids & main_ids)


ID_SIGNALS = {
    "contract_id_exact",
    "contract_id_from_event_number",
    "component_contract_id_exact",
    "main_contract_id_referenced",
    "main_contract_id_from_event_number",
    "component_main_contract_id",
}
# Corroboration that a pinned link is the right row even when the DB document is
# terse and scores low on text: an exact (or stile-fiorentino) date AND a person/
# firm-name overlap. Boundary sampling showed weak-text-but-correct links (terse
# disdette) hinge on this, not on a text-similarity threshold.
DATE_CORROBORATE_SIGNALS = {"registration_date_exact", "registration_date_stile_fiorentino"}
NAME_CORROBORATE_SIGNALS = {"person_name_overlap", "firm_name_overlap"}
FOLIO_AGREEMENT_RELATIONS = {"exact", "within", "overlap"}


def candidate_text_strength(candidate: dict[str, Any]) -> float:
    return max(
        float(candidate.get("narrative_similarity_ratio") or 0),
        float(candidate.get("text_containment_ratio") or 0),
    )


def folio_conflict_corroborated(candidate: dict[str, Any]) -> bool:
    """Is a folio disagreement safe to link through anyway?

    A non-overlapping folio (``folio_differs``) is tolerated only when the event
    number/main-contract id AND the registration date AND the narrative all
    agree. That is the original-vs-current foliation signature; it still blocks
    genuine mismatches where the text is also weak.
    """
    signals = set(candidate.get("signals") or [])
    text_strong = candidate_text_strength(candidate) >= 0.55 or "text_similarity_good" in signals
    return bool(ID_SIGNALS & signals) and "registration_date_exact" in signals and text_strong


def linkable_candidate(candidate: dict[str, Any], allow_minor_date_conflict: bool = False) -> bool:
    conflicts = set(candidate.get("conflicts") or [])
    severe_conflicts = {"db_register_missing", "db_register_differs", "text_similarity_low"}
    if severe_conflicts & conflicts:
        return False
    tolerable_conflicts = {"registration_date_differs", "folio_differs"}
    if conflicts - tolerable_conflicts:
        return False
    if "registration_date_differs" in conflicts and not allow_minor_date_conflict:
        return False
    # Folio disagreement is no longer automatically fatal: a Word entry usually
    # spans several folia, and DB foliation may use original numbering. Only block
    # it when the rest of the evidence does not strongly corroborate the link.
    if "folio_differs" in conflicts and not folio_conflict_corroborated(candidate):
        return False
    return (
        candidate["score"] >= 85
        or (
            candidate["score"] >= 65
            and "text_similarity_good" in candidate.get("signals", [])
            and candidate.get("folio_relation") in FOLIO_AGREEMENT_RELATIONS
        )
    )


def strong_secondary_link_candidate(candidate: dict[str, Any]) -> bool:
    conflicts = set(candidate.get("conflicts") or [])
    if conflicts & {"db_register_missing", "db_register_differs", "folio_differs"}:
        return False
    if conflicts - {"text_similarity_low", "registration_date_differs"}:
        return False
    strong_text_or_fields = (
        max(
            float(candidate.get("narrative_similarity_ratio") or 0),
            float(candidate.get("text_containment_ratio") or 0),
        )
        >= 0.55
        or (
            float(candidate.get("word_token_coverage_in_db") or 0) >= 0.70
            and int(candidate.get("longest_shared_phrase_words") or 0) >= 8
        )
        or int(candidate.get("field_overlap_count") or 0) >= 5
    )
    if not strong_text_or_fields:
        return False
    if "text_similarity_low" in conflicts:
        return (
            float(candidate.get("word_token_coverage_in_db") or 0) >= 0.70
            and int(candidate.get("longest_shared_phrase_words") or 0) >= 8
            and int(candidate.get("field_overlap_count") or 0) >= 5
        )
    return float(candidate.get("score") or 0) >= 65


def has_multiple_event_components(entry: dict[str, Any]) -> bool:
    return len(component_label_guesses(entry)) > 1 or is_combined_word_label(entry)


# Minimum narrative-similarity gap for a sibling to win the "primary" link by
# text alone (when the Word event number does not single one out). Below this,
# the siblings stay co-equal and the entry is left for an ambiguous "pick one".
SIBLING_PRIMARY_MIN_GAP = 0.15


def entry_component_numbers(entry: dict[str, Any]) -> set[int]:
    """All event/reference numbers the Word entry names (e.g. `[Bilancio] di 3325`)."""
    numbers: set[int] = set()
    for component in event_components_for_entry(entry):
        for key in ("event_number", "referenced_event_number"):
            value = integer_or_none(component.get(key))
            if value is not None:
                numbers.add(value)
    for key in ("event_number_raw", "referenced_event_number_raw"):
        value = integer_or_none(entry.get(key))
        if value is not None:
            numbers.add(value)
    # Enumerated back-references ("[Disdetta] di 669 e 798") — every named act, so
    # a combined act that names several accomandite keeps all matching DB rows as
    # primary instead of demoting the co-acts to siblings.
    for value in entry.get("referenced_event_numbers_raw") or []:
        number = integer_or_none(value)
        if number is not None:
            numbers.add(number)
    return numbers


def is_sibling_link_set(selected: list[dict[str, Any]]) -> bool:
    """True when links are siblings of one accomandita, not a combined act.

    All `sub_contract` rows that share a `main_contract_id`, or share a single
    `sub_type` — the boilerplate-driven pile-up shape. A `contract`+`sub_contract`
    mix (a genuine combined act) is never a sibling set. See
    `docs/workflows/link_candidate_pruning.md`.
    """
    if len(selected) < 2:
        return False
    if any(candidate.get("db_table") != "sub_contract" for candidate in selected):
        return False
    mains = {candidate.get("db_main_contract_id") for candidate in selected}
    sub_types = {str(candidate.get("db_sub_type") or "").strip().lower() for candidate in selected}
    same_parent = len(mains) == 1 and None not in mains
    same_subtype = len(sub_types) == 1 and "" not in sub_types
    return same_parent or same_subtype


def choose_primary_sibling_index(entry: dict[str, Any], selected: list[dict[str, Any]]) -> int | None:
    """Index of the single sibling this Word act describes, or None if unresolved.

    1. **Word event number** — if `event_number`/`referenced_event_number` matches
       exactly one sibling's `contract_id`/`main_contract_id`, that is the primary.
    2. else **narrative similarity** — the highest `narrative_similarity_ratio`,
       but only when it clears the runner-up by `SIBLING_PRIMARY_MIN_GAP`.
       (Symmetric similarity separates siblings; containment is inflated by shared
       boilerplate and must not be used here.)
    """
    numbers = entry_component_numbers(entry)
    if numbers:
        id_hits = [
            index
            for index, candidate in enumerate(selected)
            if integer_or_none(candidate.get("db_contract_id")) in numbers
            or integer_or_none(candidate.get("db_main_contract_id")) in numbers
        ]
        if len(id_hits) == 1:
            return id_hits[0]
    similarity = lambda candidate: float(candidate.get("narrative_similarity_ratio") or 0)  # noqa: E731
    ranked = sorted(range(len(selected)), key=lambda index: similarity(selected[index]), reverse=True)
    best, runner_up = ranked[0], ranked[1]
    if similarity(selected[best]) - similarity(selected[runner_up]) >= SIBLING_PRIMARY_MIN_GAP:
        return best
    return None


def relationship_type_for_links(entry: dict[str, Any], links: list[dict[str, Any]]) -> str:
    if not links:
        return (
            "word_only_expected_non_accomandita"
            if entry.get("event_label_guess") == "without_accomandita"
            else "word_only_unresolved"
        )
    if len(links) == 1:
        return "simple_one_to_one"
    tables = {link["db_table"] for link in links}
    sub_types = {str(link.get("db_sub_type") or "").strip().lower() for link in links if link.get("db_sub_type")}
    if "contract" in tables and "sub_contract" in tables and connected_contract_subcontract(links):
        return "word_entry_to_contract_and_subcontract"
    if tables == {"sub_contract"} and len(sub_types) >= 2:
        return "word_entry_to_multiple_subcontracts"
    return "word_entry_to_multiple_db_rows"


def component_label_for_link(entry: dict[str, Any], candidate: dict[str, Any]) -> str:
    if candidate["db_table"] == "contract":
        return "new contract" if is_combined_word_label(entry) else "contract"
    if candidate.get("db_sub_type"):
        return str(candidate["db_sub_type"])
    return "sub-contract"


def link_reason_for_candidate(relationship_type: str, candidate: dict[str, Any]) -> str:
    reasons = []
    if relationship_type != "simple_one_to_one":
        reasons.append("Word entry may represent more than one structured DB row")
    if "contract_id_exact" in candidate.get("signals", []) or "contract_id_from_event_number" in candidate.get("signals", []):
        reasons.append("Word event number points to this contract row")
    if "main_contract_id_referenced" in candidate.get("signals", []) or "main_contract_id_from_event_number" in candidate.get("signals", []):
        reasons.append("Word event number points to this related sub-contract row")
    if "registration_date_exact" in candidate.get("signals", []):
        reasons.append("registration date matches")
    elif "registration_date_stile_fiorentino" in candidate.get("signals", []):
        reasons.append("registration date matches after Florentine-calendar (stile fiorentino) year shift")
    signal_set = set(candidate.get("signals", []))
    if "folio_exact" in signal_set:
        reasons.append("folio matches")
    elif "folio_within" in signal_set:
        reasons.append("DB folio falls inside the Word entry's folio span")
    elif "folio_overlap" in signal_set:
        reasons.append("Word and DB folio ranges overlap")
    elif "folio_adjacent" in signal_set:
        reasons.append("Word and DB folios are one folio apart (possible original/current numbering)")
    if "text_similarity_good" in candidate.get("signals", []):
        reasons.append(
            f"DB narrative similarity metric is {candidate.get('narrative_similarity_ratio', candidate.get('text_similarity'))}"
        )
    if "token_coverage_good" in candidate.get("signals", []):
        reasons.append(
            f"{candidate.get('word_token_coverage_in_db')} of distinctive Word tokens appear in the DB narrative"
        )
    if "shared_phrase_good" in candidate.get("signals", []):
        reasons.append(
            f"longest shared phrase has {candidate.get('longest_shared_phrase_words')} distinctive words"
        )
    if candidate.get("field_overlap_count"):
        reasons.append(candidate.get("field_overlap_plain_language"))
    if candidate.get("conflicts"):
        reasons.append("visible conflict remains for review")
    return "; ".join(reasons) or "Candidate retained for human review"


def source_entry_db_link_candidates(
    entry: dict[str, Any], candidates: list[dict[str, Any]], match_status: str
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    strong_links = [candidate for candidate in candidates if linkable_candidate(candidate)]
    reviewable_links = [
        candidate
        for candidate in candidates
        if linkable_candidate(candidate, allow_minor_date_conflict=True)
    ]
    selected: list[dict[str, Any]]
    if len(strong_links) >= 2:
        selected = strong_links
    elif is_combined_word_label(entry) and len(reviewable_links) >= 2:
        selected = reviewable_links
    elif match_status == "matched_high_confidence" and strong_links:
        selected = [strong_links[0]]
    elif match_status == "matched_candidate":
        # A candidate match is deliberately not final truth, but it should remain
        # in the review alignment layer so DB-only diagnostics do not treat it as absent.
        selected = [candidates[0]]
    elif match_status == "matched_multiple" and reviewable_links:
        selected = reviewable_links
    elif match_status == "ambiguous" and reviewable_links:
        selected = reviewable_links
    else:
        selected = []

    if has_multiple_event_components(entry):
        selected_ids = {candidate["db_row_id"] for candidate in selected}
        for candidate in candidates:
            if candidate["db_row_id"] in selected_ids:
                continue
            if strong_secondary_link_candidate(candidate):
                selected.append(candidate)
                selected_ids.add(candidate["db_row_id"])

    # An independent main `contract` (a distinct accomandita) must not be
    # co-linked to a Word entry on boilerplate text + folio overlap alone: two
    # separate contracts rarely share one narrative, and registers pack several
    # acts onto the same folio. Keep the primary link, but drop any *secondary*
    # contract link that lacks its own id evidence (its contract_id matching a
    # Word event number/component). Sub-contract links — the normal
    # "same act, several DB rows" pattern (a contract and its termination, a
    # termination recorded also as a variation) — are unaffected.
    if len(selected) >= 2:
        filtered = [selected[0]]
        for candidate in selected[1:]:
            if candidate.get("db_table") == "contract" and not (ID_SIGNALS & set(candidate.get("signals") or [])):
                continue
            filtered.append(candidate)
        selected = filtered

    # A single Word act linked to several siblings of one accomandita is a
    # boilerplate-driven pile-up, not a combined act: keep one primary link and
    # demote the rest to `alternative` (evidence retained, but not co-equal).
    roles = ["primary"] * len(selected)
    if not has_multiple_event_components(entry) and is_sibling_link_set(selected):
        primary_index = choose_primary_sibling_index(entry, selected)
        if primary_index is not None:
            primary = selected[primary_index]
            selected = [primary] + [c for i, c in enumerate(selected) if i != primary_index]
            roles = ["primary"] + ["alternative"] * (len(selected) - 1)

    primary_selected = [candidate for candidate, role in zip(selected, roles) if role == "primary"]
    relationship_type = relationship_type_for_links(entry, primary_selected)
    group_id = f"{entry['source_entry_id']}__{relationship_type}"
    rows: list[dict[str, Any]] = []
    for ordinal, (candidate, role) in enumerate(zip(selected, roles), start=1):
        rows.append(
            {
                "link_role": role,
                "match_group_id": group_id,
                "source_entry_id": entry["source_entry_id"],
                "source_entry_key": entry.get("source_entry_key"),
                "register_id": entry["register_id"],
                "entry_label_raw": entry.get("event_label_raw"),
                "entry_label_guess": entry.get("event_label_guess"),
                "entry_registration_date_raw": entry.get("registration_date_raw"),
                "entry_folio_start": entry.get("folio_start"),
                "entry_folio_end": entry.get("folio_end"),
                "relationship_type": relationship_type,
                "component_label": component_label_for_link(entry, candidate),
                "link_ordinal": ordinal,
                "db_row_id": candidate["db_row_id"],
                "db_table": candidate["db_table"],
                "db_contract_id": candidate["db_contract_id"],
                "db_main_contract_id": candidate.get("db_main_contract_id"),
                "db_sub_type": candidate.get("db_sub_type"),
                "db_firm_name": candidate.get("db_firm_name"),
                "db_sub_firm_name": candidate.get("db_sub_firm_name"),
                "db_registration_date": candidate.get("db_registration_date"),
                "db_folio_raw": candidate.get("db_folio_raw"),
                "event_type_relation": event_type_relation(
                    entry, candidate["db_table"], candidate.get("db_sub_type")
                ),
                "score": candidate["score"],
                "signals": candidate["signals"],
                "conflicts": candidate["conflicts"],
                "text_similarity": candidate["text_similarity"],
                "narrative_similarity_ratio": candidate["narrative_similarity_ratio"],
                "text_containment_ratio": candidate.get("text_containment_ratio"),
                "word_token_coverage_in_db": candidate["word_token_coverage_in_db"],
                "db_token_coverage_in_word": candidate["db_token_coverage_in_word"],
                "shared_distinctive_token_count": candidate["shared_distinctive_token_count"],
                "shared_phrase_count": candidate["shared_phrase_count"],
                "longest_shared_phrase_words": candidate["longest_shared_phrase_words"],
                "shared_phrases": candidate["shared_phrases"],
                "field_overlap_count": candidate["field_overlap_count"],
                "field_overlap": candidate["field_overlap"],
                "field_overlap_plain_language": candidate["field_overlap_plain_language"],
                "needs_review": role == "alternative"
                or relationship_type != "simple_one_to_one"
                or bool(candidate.get("conflicts"))
                or match_status != "matched_high_confidence",
                "link_reason": link_reason_for_candidate(relationship_type, candidate),
            }
        )
    return rows


def alignment_diagnostic_row(
    *,
    diagnostic_type: str,
    priority: str,
    source_entry_id: str | None,
    db_row_id: str | None,
    register_id: str | None,
    explanation: str,
    recommended_action: str,
    evidence: str,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "diagnostic_type": diagnostic_type,
        "priority": priority,
        "source_entry_id": source_entry_id,
        "db_row_id": db_row_id,
        "register_id": register_id,
        "explanation": explanation,
        "recommended_action": recommended_action,
        "evidence": evidence,
        "candidate_score": candidate.get("score") if candidate else None,
        "narrative_similarity_ratio": candidate.get("narrative_similarity_ratio") if candidate else None,
        "word_token_coverage_in_db": candidate.get("word_token_coverage_in_db") if candidate else None,
        "field_overlap_count": candidate.get("field_overlap_count") if candidate else None,
        "field_overlap_plain_language": candidate.get("field_overlap_plain_language") if candidate else "",
        "conflicts": candidate.get("conflicts") if candidate else [],
    }


def candidate_has_strong_unlinked_evidence(candidate: dict[str, Any]) -> bool:
    return (
        max(
            float(candidate.get("narrative_similarity_ratio") or 0),
            float(candidate.get("text_containment_ratio") or 0),
        )
        >= 0.55
        or float(candidate.get("word_token_coverage_in_db") or 0) >= 0.60
        or int(candidate.get("field_overlap_count") or 0) >= 5
        or float(candidate.get("score") or 0) >= 85
    )


def source_entry_has_marginal_opening(source_entry: dict[str, Any]) -> bool:
    text = str(source_entry.get("current_text") or "").strip()
    return text.lower().startswith("a margine") or "a margine:" in text[:220].lower()


def possible_original_folio_numbering(candidate: dict[str, Any], source_entry: dict[str, Any]) -> bool:
    db_folio = str(candidate.get("db_folio_raw") or "").lower()
    word_text = str(source_entry.get("current_text") or "").lower()
    signals = set(candidate.get("signals") or [])
    conflicts = set(candidate.get("conflicts") or [])
    text_ok = candidate_text_strength(candidate) >= 0.35
    mentions_orig = "orig" in db_folio or "numerazione originale" in word_text
    # Either an adjacent-folio signal (off-by-one numbering) or an explicit
    # original-numbering annotation on an otherwise text-aligned candidate.
    return text_ok and (
        "folio_adjacent" in signals
        or ("folio_differs" in conflicts and mentions_orig)
    )


def diagnostic_plain_language(diagnostics: list[dict[str, Any]]) -> str:
    if not diagnostics:
        return "No additional alignment diagnostic was recorded."
    parts = []
    for diagnostic in diagnostics:
        parts.append(
            f"{diagnostic['diagnostic_type']}: {diagnostic['explanation']} "
            f"Evidence: {diagnostic['evidence']} "
            f"Recommended action: {diagnostic['recommended_action']}"
        )
    return "\n".join(parts)


def diagnostic_types(diagnostics: list[dict[str, Any]]) -> str:
    return "; ".join(sorted({diagnostic["diagnostic_type"] for diagnostic in diagnostics}))


def build_alignment_diagnostics(
    entry_matches: list[dict[str, Any]],
    match_candidates: list[dict[str, Any]],
    link_candidates: list[dict[str, Any]],
    db_only_rows: list[dict[str, Any]],
    duplicate_link_candidates: list[dict[str, Any]],
    source_entries_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    candidates_by_db: dict[str, list[dict[str, Any]]] = {}
    candidates_by_source: dict[str, list[dict[str, Any]]] = {}
    for candidate in match_candidates:
        candidates_by_db.setdefault(candidate["db_row_id"], []).append(candidate)
        candidates_by_source.setdefault(candidate["source_entry_id"], []).append(candidate)
    for candidates in candidates_by_db.values():
        candidates.sort(key=lambda row: row["score"], reverse=True)
    for candidates in candidates_by_source.values():
        candidates.sort(key=lambda row: row["score"], reverse=True)

    for db_row in db_only_rows:
        db_row_id = db_row["db_row_id"]
        best_candidate = (candidates_by_db.get(db_row_id) or [None])[0]
        if best_candidate and candidate_has_strong_unlinked_evidence(best_candidate):
            diagnostics.append(
                alignment_diagnostic_row(
                    diagnostic_type="db_text_found_in_word_but_not_linked",
                    priority="High",
                    source_entry_id=best_candidate["source_entry_id"],
                    db_row_id=db_row_id,
                    register_id=db_row.get("register_id"),
                    explanation="This DB row is currently unlinked, but a Word entry has strong text or field evidence for it.",
                    recommended_action="Review the suggested Word entry before treating the DB row as genuinely DB-only.",
                    evidence=(
                        f"score {best_candidate.get('score')}; narrative similarity "
                        f"{best_candidate.get('narrative_similarity_ratio')}; field overlaps: "
                        f"{best_candidate.get('field_overlap_plain_language') or 'none recorded'}"
                    ),
                    candidate=best_candidate,
                )
            )

    for match in entry_matches:
        source_entry = source_entries_by_id.get(match["source_entry_id"], {})
        source_candidates = candidates_by_source.get(match["source_entry_id"], [])
        top_candidate = source_candidates[0] if source_candidates else None
        top_conflicts = set(match.get("top_conflicts") or [])
        if top_candidate and source_entry_has_marginal_opening(source_entry) and "registration_date_differs" in top_conflicts:
            diagnostics.append(
                alignment_diagnostic_row(
                    diagnostic_type="possible_marginal_date_confusion",
                    priority="High",
                    source_entry_id=match["source_entry_id"],
                    db_row_id=top_candidate["db_row_id"],
                    register_id=match["register_id"],
                    explanation="The Word entry opens with a marginal note and the top DB candidate has a date conflict.",
                    recommended_action="Check whether the marginal note date was confused with the main act date.",
                    evidence=f"Word date {match.get('entry_registration_date_raw')}; DB date {top_candidate.get('db_registration_date')}.",
                    candidate=top_candidate,
                )
            )
        if top_candidate and possible_original_folio_numbering(top_candidate, source_entry):
            diagnostics.append(
                alignment_diagnostic_row(
                    diagnostic_type="possible_original_folio_numbering_match",
                    priority="Medium",
                    source_entry_id=match["source_entry_id"],
                    db_row_id=top_candidate["db_row_id"],
                    register_id=match["register_id"],
                    explanation="Folio fields disagree, but text evidence is strong and original/current folio numbering is mentioned.",
                    recommended_action="Check current and original folio numbering before rejecting the candidate.",
                    evidence=f"Word folio {match.get('entry_folio_start')}-{match.get('entry_folio_end')}; DB folio {top_candidate.get('db_folio_raw')}.",
                    candidate=top_candidate,
                )
            )
        if top_candidate and "registration_date_stile_fiorentino" in set(top_candidate.get("signals") or []):
            diagnostics.append(
                alignment_diagnostic_row(
                    diagnostic_type="possible_stile_fiorentino_date_alignment",
                    priority="Medium",
                    source_entry_id=match["source_entry_id"],
                    db_row_id=top_candidate["db_row_id"],
                    register_id=match["register_id"],
                    explanation="The Word date matches the DB date only after a Florentine-calendar (stile fiorentino) one-year shift; the Florentine year began 25 March, so Jan-24 Mar dates lag the modern year by one.",
                    recommended_action="Confirm the calendar-style alignment before treating the date as a match.",
                    evidence=f"Word date {match.get('entry_registration_date_raw')}; DB date {top_candidate.get('db_registration_date')}.",
                    candidate=top_candidate,
                )
            )
        if int(match.get("suggested_primary_link_count") or match.get("suggested_link_count") or 0) > 1:
            diagnostics.append(
                alignment_diagnostic_row(
                    # Informational, not an alarm: one Word narrative legitimately
                    # approving several DB rows (a contract + its termination, etc.)
                    # is the expected combined-act pattern, not a defect. Low so it
                    # does not force the row into the High-priority queue. Keyed on
                    # *primary* links so a pruned sibling pile-up (one primary +
                    # demoted alternatives) is not mislabelled a combined act.
                    diagnostic_type="word_entry_combines_multiple_db_rows",
                    priority="Low",
                    source_entry_id=match["source_entry_id"],
                    db_row_id=None,
                    register_id=match["register_id"],
                    explanation="The Word source entry has multiple proposed DB links.",
                    recommended_action="Confirm this narrative approves these DB rows together (the normal combined-act pattern).",
                    evidence=f"Suggested DB rows: {'; '.join(match.get('suggested_db_row_ids') or [])}.",
                )
            )
        if match["match_status"] == "word_only" and top_candidate and candidate_has_strong_unlinked_evidence(top_candidate):
            diagnostics.append(
                alignment_diagnostic_row(
                    diagnostic_type="word_only_has_strong_rejected_candidate",
                    priority="High",
                    source_entry_id=match["source_entry_id"],
                    db_row_id=top_candidate["db_row_id"],
                    register_id=match["register_id"],
                    explanation="This Word entry is marked Word-only, but a rejected candidate has strong text or field evidence.",
                    recommended_action="Inspect conflicts to decide whether this is a parser issue, a DB modeling issue, or a true rejected match.",
                    evidence=(
                        f"score {top_candidate.get('score')}; conflicts {top_candidate.get('conflicts')}; "
                        f"field overlaps: {top_candidate.get('field_overlap_plain_language') or 'none recorded'}"
                    ),
                    candidate=top_candidate,
                )
            )

    for duplicate in duplicate_link_candidates:
        for source_entry_id in duplicate.get("source_entry_ids") or []:
            diagnostics.append(
                alignment_diagnostic_row(
                    # A DB row legitimately referenced by more than one Word entry
                    # (an act and a later entry that cites it) is common; only worth
                    # a glance, not a High-priority block. Medium keeps it out of the
                    # forced-High queue while staying filterable for audits.
                    diagnostic_type="db_row_linked_to_multiple_word_entries",
                    priority="Medium",
                    source_entry_id=source_entry_id,
                    db_row_id=duplicate["db_row_id"],
                    register_id=source_entries_by_id.get(source_entry_id, {}).get("register_id"),
                    explanation="The same DB row is proposed for more than one Word entry.",
                    recommended_action="Decide whether the Word entries are duplicates, later references, or separate acts needing separate DB rows.",
                    evidence=f"All source entries sharing this DB row: {'; '.join(duplicate.get('source_entry_ids') or [])}.",
                )
            )

    seen: set[tuple[str, str | None, str | None]] = set()
    unique: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        key = (diagnostic["diagnostic_type"], diagnostic.get("source_entry_id"), diagnostic.get("db_row_id"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(diagnostic)
    return unique


def run_match_db(args: argparse.Namespace) -> int:
    config = load_config(args)
    segmentation_dir = config["output_root"] / "04_source_entries"
    match_dir = config["output_root"] / "05_db_candidate_matches"
    ensure_dir(match_dir)

    source_entries = [
        entry
        for entry in read_jsonl(segmentation_dir / "source_entries.jsonl")
        if entry.get("parser_status") in MATCH_OUTPUT_ALLOWED_STATUSES
    ]
    source_entries_by_id = {entry["source_entry_id"]: entry for entry in source_entries}
    db_rows, issues = load_db_match_rows(config["db_path"])
    db_rows_by_register: dict[str, list[dict[str, Any]]] = {}
    db_contract_rows_by_id: dict[int, list[dict[str, Any]]] = {}
    db_main_rows_by_id: dict[int, list[dict[str, Any]]] = {}
    for row in db_rows:
        if row.get("register_id") is not None:
            db_rows_by_register.setdefault(row["register_id"], []).append(row)
        db_contract_rows_by_id.setdefault(int(row["contract_id"]), []).append(row)
        if row.get("main_contract_id") is not None:
            db_main_rows_by_id.setdefault(int(row["main_contract_id"]), []).append(row)

    entry_matches: list[dict[str, Any]] = []
    match_candidates: list[dict[str, Any]] = []
    link_candidates: list[dict[str, Any]] = []

    for index, entry in enumerate(source_entries, start=1):
        if index == 1 or index % 500 == 0 or index == len(source_entries):
            print(f"[{index}/{len(source_entries)}] matching {entry['source_entry_id']}", flush=True)
        entry_body_text = entry_text_for_similarity(entry)
        scored: list[dict[str, Any]] = []
        for db_row in candidate_db_rows_for_entry(
            entry,
            db_rows_by_register,
            db_contract_rows_by_id,
            db_main_rows_by_id,
        ):
            details = score_match(entry, db_row, entry_body_text)
            scored.append(
                {
                    "source_entry_id": entry["source_entry_id"],
                    "source_entry_key": entry.get("source_entry_key"),
                    "register_id": entry["register_id"],
                    "entry_ordinal": entry["entry_ordinal"],
                    "event_label_raw": entry["event_label_raw"],
                    "event_label_guess": entry["event_label_guess"],
                    "event_component_labels": sorted(component_label_guesses(entry)),
                    "event_component_contract_ids": sorted(component_contract_ids(entry)),
                    "event_component_sub_contract_main_ids": sorted(component_sub_contract_main_ids(entry)),
                    "event_number_raw": entry.get("event_number_raw"),
                    "referenced_event_number_raw": entry.get("referenced_event_number_raw"),
                    "entry_registration_date_raw": entry.get("registration_date_raw"),
                    "entry_registration_date_iso": details["entry_registration_date_iso"],
                    "entry_registration_date_candidates": details["entry_registration_date_candidates"],
                    "entry_folio_start": entry.get("folio_start"),
                    "entry_folio_end": entry.get("folio_end"),
                    "db_row_id": db_row["db_row_id"],
                    "db_table": db_row["db_table"],
                    "db_contract_id": db_row["contract_id"],
                    "db_main_contract_id": db_row.get("main_contract_id"),
                    "db_sub_type": db_row.get("sub_type"),
                    "db_firm_name": db_row.get("firm_name"),
                    "db_sub_firm_name": db_row.get("sub_firm_name"),
                    "db_registration_date": db_row.get("registration_date"),
                    "db_folio_raw": db_row.get("folio_raw"),
                    "db_folio_start": db_row.get("folio_start"),
                    "db_folio_end": db_row.get("folio_end"),
                    "score": details["score"],
                    "signals": details["signals"],
                    "conflicts": details["conflicts"],
                    "text_similarity": details["text_similarity"],
                    "narrative_similarity_ratio": details["narrative_similarity_ratio"],
                    "text_containment_ratio": details["text_containment_ratio"],
                    "word_token_coverage_in_db": details["word_token_coverage_in_db"],
                    "db_token_coverage_in_word": details["db_token_coverage_in_word"],
                    "shared_distinctive_token_count": details["shared_distinctive_token_count"],
                    "shared_phrase_count": details["shared_phrase_count"],
                    "longest_shared_phrase_words": details["longest_shared_phrase_words"],
                    "shared_phrases": details["shared_phrases"],
                    "field_overlap_count": details["field_overlap_count"],
                    "field_overlap": details["field_overlap"],
                    "field_overlap_plain_language": details["field_overlap_plain_language"],
                    "folio_relation": details["folio_relation"],
                }
            )
        scored.sort(key=lambda row: row["score"], reverse=True)
        top_candidates = scored[:5]
        match_candidates.extend(top_candidates)
        match_status = classify_match(top_candidates)
        top = top_candidates[0] if top_candidates else None
        entry_link_candidates = source_entry_db_link_candidates(entry, top_candidates, match_status)
        link_candidates.extend(entry_link_candidates)
        if match_status == "word_only":
            issues.append(
                {
                    "severity": "warning",
                    "code": "word_entry_without_db_candidate",
                    "message": "No plausible DB candidate found for source entry",
                    "source_entry_id": entry["source_entry_id"],
                    "db_row_id": None,
                    "register_id": entry["register_id"],
                }
            )
        elif match_status == "ambiguous":
            issues.append(
                {
                    "severity": "warning",
                    "code": "word_entry_ambiguous_db_match",
                    "message": "Multiple plausible DB candidates or weak score margin",
                    "source_entry_id": entry["source_entry_id"],
                    "db_row_id": top["db_row_id"] if top else None,
                    "register_id": entry["register_id"],
                }
            )
        elif match_status == "matched_multiple":
            issues.append(
                {
                    "severity": "info",
                    "code": "word_entry_matches_multiple_db_rows",
                    "message": "Source entry appears to correspond to multiple DB rows with equivalent evidence",
                    "source_entry_id": entry["source_entry_id"],
                    "db_row_id": top["db_row_id"] if top else None,
                    "register_id": entry["register_id"],
                }
            )
        entry_matches.append(
            {
                "source_entry_id": entry["source_entry_id"],
                "source_entry_key": entry.get("source_entry_key"),
                "register_id": entry["register_id"],
                "entry_ordinal": entry["entry_ordinal"],
                "event_label_raw": entry["event_label_raw"],
                "event_label_guess": entry["event_label_guess"],
                "event_number_raw": entry.get("event_number_raw"),
                "referenced_event_number_raw": entry.get("referenced_event_number_raw"),
                "entry_registration_date_raw": entry.get("registration_date_raw"),
                "entry_registration_date_precision": entry.get("registration_date_precision"),
                "entry_registration_date_source": entry.get("date_candidates_source"),
                "entry_registration_date_iso": top["entry_registration_date_iso"] if top else parse_italian_date(entry.get("registration_date_raw")),
                "entry_folio_start": entry.get("folio_start"),
                "entry_folio_end": entry.get("folio_end"),
                "match_status": match_status,
                "top_db_row_id": top["db_row_id"] if top else None,
                "top_db_table": top["db_table"] if top else None,
                "top_db_contract_id": top["db_contract_id"] if top else None,
                "top_db_main_contract_id": top["db_main_contract_id"] if top else None,
                "top_db_sub_type": top["db_sub_type"] if top else None,
                "top_score": top["score"] if top else 0,
                "top_signals": top["signals"] if top else [],
                "top_conflicts": top["conflicts"] if top else [],
                "top_narrative_similarity_ratio": top["narrative_similarity_ratio"] if top else 0,
                "top_text_containment_ratio": top.get("text_containment_ratio") if top else 0,
                "top_word_token_coverage_in_db": top["word_token_coverage_in_db"] if top else 0,
                "top_db_token_coverage_in_word": top["db_token_coverage_in_word"] if top else 0,
                "top_shared_phrase_count": top["shared_phrase_count"] if top else 0,
                "top_longest_shared_phrase_words": top["longest_shared_phrase_words"] if top else 0,
                "top_field_overlap_count": top["field_overlap_count"] if top else 0,
                "top_field_overlap_plain_language": top["field_overlap_plain_language"] if top else "",
                "candidate_count": len(scored),
                "suggested_link_count": len(entry_link_candidates),
                "suggested_primary_link_count": sum(
                    1 for row in entry_link_candidates if row.get("link_role", "primary") == "primary"
                ),
                "suggested_relationship_type": (
                    entry_link_candidates[0]["relationship_type"]
                    if entry_link_candidates
                    else relationship_type_for_links(entry, [])
                ),
                "suggested_db_row_ids": [row["db_row_id"] for row in entry_link_candidates],
            }
        )

    accepted_link_rows = [row for row in link_candidates if row.get("db_row_id")]
    matched_db_rows = {row["db_row_id"] for row in accepted_link_rows}
    link_counts_by_db_row = Counter(row["db_row_id"] for row in accepted_link_rows)
    duplicate_link_candidates: list[dict[str, Any]] = []
    for db_row_id, count in sorted(link_counts_by_db_row.items()):
        if count <= 1:
            continue
        linked_rows = [row for row in accepted_link_rows if row["db_row_id"] == db_row_id]
        duplicate_link_candidates.append(
            {
                "db_row_id": db_row_id,
                "proposed_link_count": count,
                "source_entry_ids": [row["source_entry_id"] for row in linked_rows],
                "relationship_types": [row["relationship_type"] for row in linked_rows],
                "event_label_raws": [row["entry_label_raw"] for row in linked_rows],
                "scores": [row["score"] for row in linked_rows],
            }
        )
        issues.append(
            {
                "severity": "warning",
                "code": "db_row_reused_as_link_candidate",
                "message": "DB row is proposed as a link candidate for multiple Word entries",
                "source_entry_id": None,
                "db_row_id": db_row_id,
                "register_id": linked_rows[0]["register_id"],
            }
        )

    # DB-native rows — added directly to the database after the Word-corpus
    # freeze (an applied `create` op in corrections.db) — have no Word summary
    # BY DESIGN and must never be reported as unlinked DB rows. Without this,
    # every future addition would pollute the "DB row needs a Word link" queue
    # forever on the next match-db run.
    db_native_ids = db_native_row_ids()
    db_only_rows = [
        {
            key: value
            for key, value in row.items()
            if key != "_match_text"
        }
        for row in db_rows
        if row["db_row_id"] not in matched_db_rows and row["db_row_id"] not in db_native_ids
    ]
    db_native_count = sum(
        1 for row in db_rows if row["db_row_id"] in db_native_ids and row["db_row_id"] not in matched_db_rows
    )
    for row in db_only_rows:
        issues.append(
            {
                "severity": "warning",
                "code": "db_row_without_matched_word_entry",
                "message": "DB row was not proposed as a link candidate for a Word entry",
                "source_entry_id": None,
                "db_row_id": row["db_row_id"],
                "register_id": row["register_id"],
            }
        )

    alignment_diagnostics = build_alignment_diagnostics(
        entry_matches,
        match_candidates,
        link_candidates,
        db_only_rows,
        duplicate_link_candidates,
        source_entries_by_id,
    )
    for diagnostic in alignment_diagnostics:
        issues.append(
            {
                "severity": "warning" if diagnostic["priority"] == "High" else "info",
                "code": diagnostic["diagnostic_type"],
                "message": diagnostic["explanation"],
                "source_entry_id": diagnostic.get("source_entry_id"),
                "db_row_id": diagnostic.get("db_row_id"),
                "register_id": diagnostic.get("register_id"),
            }
        )

    word_register_ids = {row["register_id"] for row in entry_matches}
    matched_main_contract_ids = {
        row["db_main_contract_id"]
        for row in accepted_link_rows
        if row.get("db_table") == "sub_contract" and row.get("db_main_contract_id") is not None
    }
    word_only_rows = [row for row in entry_matches if row["match_status"] == "word_only"]
    review_buckets = [
        {
            "bucket": "word_only_without_accomandita",
            "count": sum(1 for row in word_only_rows if row["event_label_guess"] == "without_accomandita"),
            "interpretation": "Usually expected: Word has a source act not intended as an accomandita DB row.",
        },
        {
            "bucket": "word_only_zero_candidates",
            "count": sum(1 for row in word_only_rows if row["event_label_guess"] != "without_accomandita" and row["candidate_count"] == 0),
            "interpretation": "No DB row shared usable ID/date/folio signals; inspect first.",
        },
        {
            "bucket": "word_only_weak_or_conflicting_candidates",
            "count": sum(1 for row in word_only_rows if row["event_label_guess"] != "without_accomandita" and row["candidate_count"] > 0),
            "interpretation": "Candidates exist but signals conflict or score too low.",
        },
        {
            "bucket": "db_only_unknown_register",
            "count": sum(1 for row in db_only_rows if row.get("register_id") is None),
            "interpretation": "DB folder/register metadata is missing or malformed.",
        },
        {
            "bucket": "db_only_outside_word_registers",
            "count": sum(1 for row in db_only_rows if row.get("register_id") is not None and row.get("register_id") not in word_register_ids),
            "interpretation": "DB row belongs to a register not represented in the current Word corpus.",
        },
        {
            "bucket": "db_only_main_contract_for_matched_subcontract",
            "count": sum(
                1
                for row in db_only_rows
                if row["db_table"] == "contract" and row["contract_id"] in matched_main_contract_ids
            ),
            "interpretation": "Main contract row was not itself matched, but related sub_contract rows were matched.",
        },
        {
            "bucket": "db_row_reused_as_link_candidate",
            "count": len(duplicate_link_candidates),
            "interpretation": "One DB row is proposed for multiple Word entries; often duplicate/repeated Word evidence.",
        },
        {
            "bucket": "matched_multiple_word_entries",
            "count": sum(1 for row in entry_matches if row["match_status"] == "matched_multiple"),
            "interpretation": "One Word entry plausibly corresponds to multiple DB rows with equivalent evidence.",
        },
        {
            "bucket": "word_entry_to_multiple_db_rows",
            "count": sum(1 for row in entry_matches if row["suggested_link_count"] > 1),
            "interpretation": "One Word narrative unit has multiple proposed DB-row links; review as a group.",
        },
        {
            "bucket": "alignment_diagnostics",
            "count": len(alignment_diagnostics),
            "interpretation": "Unresolved or suspicious Word-DB alignments where DB evidence helps explain what to review.",
        },
    ]

    status_counts = {status: count for status, count in sorted(Counter(row["match_status"] for row in entry_matches).items())}
    summary_rows: list[dict[str, Any]] = []
    for register_id in sorted(
        {row["register_id"] for row in entry_matches} | {row["register_id"] for row in db_rows},
        key=lambda value: str(value or ""),
    ):
        register_entry_matches = [row for row in entry_matches if row["register_id"] == register_id]
        register_db_rows = [row for row in db_rows if row["register_id"] == register_id]
        summary_rows.append(
            {
                "register_id": register_id or "_unknown_register",
                "word_entry_count": len(register_entry_matches),
                "db_row_count": len(register_db_rows),
                "matched_high_confidence": sum(1 for row in register_entry_matches if row["match_status"] == "matched_high_confidence"),
                "matched_candidate": sum(1 for row in register_entry_matches if row["match_status"] == "matched_candidate"),
                "matched_multiple": sum(1 for row in register_entry_matches if row["match_status"] == "matched_multiple"),
                "ambiguous": sum(1 for row in register_entry_matches if row["match_status"] == "ambiguous"),
                "word_only": sum(1 for row in register_entry_matches if row["match_status"] == "word_only"),
                "db_only": sum(1 for row in db_only_rows if row["register_id"] == register_id),
                "word_entries_with_multiple_link_candidates": sum(1 for row in register_entry_matches if row["suggested_link_count"] > 1),
            }
        )

    write_jsonl(match_dir / "entry_db_matches.jsonl", entry_matches)
    write_csv(match_dir / "entry_db_matches.csv", entry_matches)
    write_jsonl(match_dir / "match_candidates.jsonl", match_candidates)
    write_jsonl(match_dir / "source_entry_db_link_candidates.jsonl", link_candidates)
    write_csv(match_dir / "source_entry_db_link_candidates.csv", link_candidates)
    write_jsonl(match_dir / "db_only_rows.jsonl", db_only_rows)
    write_jsonl(match_dir / "duplicate_link_candidates.jsonl", duplicate_link_candidates)
    write_jsonl(match_dir / "alignment_diagnostics.jsonl", alignment_diagnostics)
    write_csv(match_dir / "alignment_diagnostics.csv", alignment_diagnostics)
    write_csv(match_dir / "review_buckets.csv", review_buckets)
    write_csv(match_dir / "register_match_summary.csv", summary_rows)
    write_jsonl(match_dir / "issues.jsonl", issues)

    summary_lines = [
        f"- Source-entry input: `{segmentation_dir.relative_to(project_root())}`",
        f"- SQLite input: `{config['db_path'].relative_to(project_root()) if config['db_path'].is_relative_to(project_root()) else config['db_path']}`",
        f"- Word source entries matched: {len(entry_matches)}",
        f"- DB rows considered: {len(db_rows)}",
        f"- Match statuses: {status_counts}",
        f"- Candidate rows written: {len(match_candidates)}",
        f"- Source-entry/DB link candidates written: {len(link_candidates)}",
        f"- Word entries with multiple proposed DB links: {sum(1 for row in entry_matches if row['suggested_link_count'] > 1)}",
        f"- DB-only rows: {len(db_only_rows)}",
        f"- DB-native rows (added after the corpus freeze; exempt from Word-link expectations): {db_native_count}",
        f"- Alignment diagnostics: {len(alignment_diagnostics)}",
        f"- DB rows proposed for multiple Word entries: {len(duplicate_link_candidates)}",
        f"- Matched-multiple Word entries: {sum(1 for row in entry_matches if row['match_status'] == 'matched_multiple')}",
        f"- Issues: {len(issues)}",
        "",
        "Review buckets:",
        *[f"- {row['bucket']}: {row['count']} ({row['interpretation']})" for row in review_buckets],
        "",
        "How to read this stage:",
        "- matches are ranked candidates, not accepted truth;",
        "- `source_entry_db_link_candidates` is the review alignment layer and allows one Word entry to map to several DB rows;",
        "- `alignment_diagnostics` explains unresolved or suspicious cases inside the same matching/review flow;",
        "- new contracts primarily use `contract.contract_id` when the Word event number is available;",
        "- non-new acts use `sub_contract.main_contract_id`, date, folio, type, and text signals;",
        "- `matched_multiple` means one Word entry plausibly maps to multiple DB rows with equivalent evidence;",
        "- conflicts remain visible and should be reviewed before field-level reconciliation.",
        "",
        "What this stage does not do:",
        "- it does not update SQLite;",
        "- it does not extract structured field corrections;",
        "- it does not force a match when signals disagree.",
    ]
    write_summary(match_dir / "match_summary.md", "DB Candidate Match Summary", summary_lines)

    print(f"Wrote DB candidate match outputs to {match_dir.relative_to(project_root())}")
    print(f"Word entries: {len(entry_matches)}; DB rows: {len(db_rows)}; statuses: {status_counts}")
    print(f"DB-only rows: {len(db_only_rows)}; issues: {len(issues)}")
    return 1 if any(issue["severity"] == "error" for issue in issues) else 0


def readable_list(values: list[str] | None, labels: dict[str, str]) -> str:
    if not values:
        return "None recorded."
    return "; ".join(labels.get(value, value.replace("_", " ")) for value in values)


def compact_text(text: str | None, limit: int = 2500) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 20].rstrip() + " ... [truncated]"


def load_db_documents(db_path: Path) -> dict[str, str]:
    documents: dict[str, str] = {}
    if not db_path.exists():
        return documents
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for table_name in ["contract", "sub_contract"]:
            for row in connection.execute(f"SELECT contract_id, document FROM {table_name}"):
                documents[f"{table_name}:{row['contract_id']}"] = row["document"] or ""
    return documents


def candidate_summary(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "No candidate rows were generated."
    parts: list[str] = []
    for index, candidate in enumerate(candidates[:5], start=1):
        signals = readable_list(candidate.get("signals"), SIGNAL_LABELS)
        conflicts = readable_list(candidate.get("conflicts"), CONFLICT_LABELS)
        parts.append(
            f"{index}. {candidate['db_row_id']} "
            f"(score {candidate['score']}; date {candidate.get('db_registration_date')}; "
            f"folio {candidate.get('db_folio_raw')}; type {candidate.get('db_sub_type') or candidate.get('db_table')}; "
            f"narrative similarity {candidate.get('narrative_similarity_ratio', candidate.get('text_similarity'))}; "
            f"Word-token coverage in DB {candidate.get('word_token_coverage_in_db')}; "
            f"field overlaps: {candidate.get('field_overlap_plain_language', 'None recorded.')}; "
            f"signals: {signals}; conflicts: {conflicts})"
        )
    return "\n".join(parts)


def db_link_candidate_summary(link_candidates: list[dict[str, Any]]) -> str:
    if not link_candidates:
        return "No DB link candidate was generated."
    parts: list[str] = []
    for index, link in enumerate(link_candidates, start=1):
        conflicts = readable_list(link.get("conflicts"), CONFLICT_LABELS)
        signals = readable_list(link.get("signals"), SIGNAL_LABELS)
        parts.append(
            f"{index}. {link['db_row_id']} ({link['component_label']}; {link['relationship_type']}; "
            f"score {link['score']}; date {link.get('db_registration_date')}; folio {link.get('db_folio_raw')}; "
            f"narrative similarity {link.get('narrative_similarity_ratio')}; "
            f"Word-token coverage in DB {link.get('word_token_coverage_in_db')}; "
            f"longest shared phrase {link.get('longest_shared_phrase_words')} words; "
            f"field overlaps: {link.get('field_overlap_plain_language')}; "
            f"signals: {signals}; conflicts: {conflicts}; reason: {link.get('link_reason')})"
        )
    return "\n".join(parts)


def db_link_candidate_ids(link_candidates: list[dict[str, Any]]) -> str:
    return "; ".join(link["db_row_id"] for link in link_candidates)


def db_link_candidate_documents(link_candidates: list[dict[str, Any]], db_documents: dict[str, str]) -> str:
    parts: list[str] = []
    for link in link_candidates[:5]:
        text = compact_text(db_documents.get(link["db_row_id"], ""), limit=1200)
        parts.append(f"{link['db_row_id']} ({link['component_label']}): {text or '[No DB narrative text]'}")
    return "\n\n".join(parts)


def image_candidate_summary(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "No image candidate was generated."
    parts: list[str] = []
    for index, candidate in enumerate(candidates[:6], start=1):
        review_note = candidate.get("review_reason") if candidate.get("needs_review") else "No special review flag."
        parts.append(
            f"{index}. {candidate['image_file']} "
            f"({candidate.get('page_position') or 'page unknown'} page, matched folio {candidate.get('matched_folio')}; "
            f"needs review: {candidate.get('needs_review')}; {review_note})"
        )
    return "\n".join(parts)


def image_candidate_paths(candidates: list[dict[str, Any]]) -> str:
    paths = []
    for candidate in candidates[:6]:
        path = candidate.get("image_path")
        if path and path not in paths:
            paths.append(path)
    return "; ".join(paths)


def image_candidates_need_review(candidates: list[dict[str, Any]]) -> bool:
    return any(bool(candidate.get("needs_review")) for candidate in candidates)


def clear_word_db_evidence(match: dict[str, Any]) -> bool:
    return (
        max(
            float(match.get("top_narrative_similarity_ratio") or 0),
            float(match.get("top_text_containment_ratio") or 0),
        )
        >= 0.55
        or (
            float(match.get("top_word_token_coverage_in_db") or 0) >= 0.55
            and int(match.get("top_longest_shared_phrase_words") or 0) >= 8
        )
        or int(match.get("top_field_overlap_count") or 0) >= 5
    )


def db_document_available_for_success(db_documents: dict[str, str], db_row_id: Any) -> bool:
    text = str(db_documents.get(str(db_row_id)) or "").strip()
    return bool(text) and "[No DB narrative text]" not in text


def source_entry_clean_for_success_control(source_entry: dict[str, Any]) -> bool:
    text = str(source_entry.get("current_text") or "").lower()
    return not re.search(
        r"\b(?:ghost|da rivedere|da inserire|da creare|non inserit[ao]|non si trova|a quale contratto)\b",
        text,
    )


# Queue order: decision-heavy work first, routine confirms and DB-side last.
# Mirrors the front-end filter ordering (apps/review/src/utils/reviewBuckets.ts).
REVIEW_BUCKET_ORDER = [
    "Choose the right row",
    "Verify a field",
    "Investigate — no clear DB match",
    "Confirm combined act",
    "Confirm the link",
    "DB row needs a Word link",
    "Non-accomandita (Word-only)",
]


def review_bucket_for_match(match: dict[str, Any]) -> tuple[str, str]:
    """Action-first review bucket for a Word-entry match. Returns (bucket, action).

    Organised by what the reviewer DOES, not by the matcher's internal status.
    The order asks, in turn: is this even an accomandita? can the matcher pin a
    row? is the pinned link basically right? — instead of letting any recorded
    conflict dominate (the old conflict-first order scattered correct-link-field-
    differs cases into a 'Conflicts' grab-bag). Priority is gone: the bucket is
    the routing. Clean single high-confidence matches stay auto-eligible and are
    not surfaced at all (see qa_rows_for_matches).
    """
    status = match["match_status"]

    # 1. Not an accomandita at all — a deliberate, label-detected historian category.
    if status == "word_only" and match.get("event_label_guess") == "without_accomandita":
        return (
            "Non-accomandita (Word-only)",
            "Confirm this is a non-accomandita act and should stay Word-only.",
        )

    # 2. Word entry the matcher could not place — investigate / forge a link.
    if status == "word_only":
        return (
            "Investigate — no clear DB match",
            "Decide: a DB mismatch, a true Word-only entry, a parser miss, or a row to create.",
        )

    # 3. Rows were found but the matcher cannot choose among them.
    primary_links = int(match.get("suggested_primary_link_count") or match.get("suggested_link_count") or 0)
    if status in ("ambiguous", "matched_multiple"):
        if primary_links > 1:
            return (
                "Confirm combined act",
                "One narrative covers several acts — confirm each suggested database row.",
            )
        return (
            "Choose the right row",
            "Several sibling rows are plausible — mark only the one(s) the narrative supports.",
        )

    # 4. A single act is pinned (high-confidence or candidate). Is the link right?
    signals = set(match.get("top_signals") or [])
    has_id = bool(ID_SIGNALS & signals)
    date_and_name = bool(DATE_CORROBORATE_SIGNALS & signals) and bool(NAME_CORROBORATE_SIGNALS & signals)
    strength = max(
        float(match.get("top_narrative_similarity_ratio") or 0),
        float(match.get("top_text_containment_ratio") or 0),
    )
    link_is_right = has_id or date_and_name or strength >= 0.6

    if match.get("top_conflicts"):
        if link_is_right:
            return (
                "Verify a field",
                "The link is right; a field disagrees (date, folio, register, or type) — confirm the link, then fix the field in Corrections.",
            )
        return (
            "Choose the right row",
            "Weak evidence and a conflict — check whether this is even the right row before approving.",
        )

    # 5. Clean pinned link, no conflict.
    if primary_links > 1:
        return (
            "Confirm combined act",
            "One narrative approves these database rows together (a combined act).",
        )
    return (
        "Confirm the link",
        "Spot-check this single match before it is used for field-level reconciliation.",
    )


def entry_revision_fields(
    source_entry: dict[str, Any],
    comments_by_key: dict[tuple[str, str], dict[str, Any]],
    notes_by_key: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Editorial-history fields for the tracked-changes Word panel.

    Carries the raw `revision_aware_text` marker string plus the resolved comment
    and footnote/endnote bodies referenced by the entry, so the review server can
    parse them into a render-ready token stream (see
    docs/workflows/tracked_changes_word_panel.md). The CSV serialization omits the
    nested objects; the JSONL keeps them.
    """
    revision_text = str(source_entry.get("revision_aware_text") or "")
    register_id = str(source_entry.get("register_id") or "")
    comments: list[dict[str, Any]] = []
    for comment_id in source_entry.get("comment_ids") or []:
        body = comments_by_key.get((register_id, str(comment_id)))
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
    notes: list[dict[str, Any]] = []
    for note_kind, id_field in (("footnote", "footnote_ids"), ("endnote", "endnote_ids")):
        for note_id in source_entry.get(id_field) or []:
            body = notes_by_key.get((register_id, str(note_id)))
            if body:
                notes.append({"id": str(note_id), "kind": note_kind, "text": body.get("text")})
    return {
        "word_entry_revision_text": revision_text,
        "word_entry_has_revisions": bool(source_entry.get("has_revisions")),
        "word_entry_revision_summary": {
            "insertions": revision_text.count("<INS "),
            "deletions": revision_text.count("<DEL "),
            "moves": revision_text.count("<MOVETO "),
            "comments": len(comments),
            "notes": len(notes),
        },
        "word_entry_comments": comments,
        "word_entry_notes": notes,
    }


def qa_rows_for_matches(
    entry_matches: list[dict[str, Any]],
    source_entries_by_id: dict[str, dict[str, Any]],
    candidates_by_entry: dict[str, list[dict[str, Any]]],
    link_candidates_by_entry: dict[str, list[dict[str, Any]]],
    diagnostics_by_entry: dict[str, list[dict[str, Any]]],
    image_candidates_by_entry: dict[str, list[dict[str, Any]]],
    db_documents: dict[str, str],
    comments_by_key: dict[tuple[str, str], dict[str, Any]],
    notes_by_key: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in entry_matches:
        source_entry = source_entries_by_id.get(match["source_entry_id"], {})
        link_candidates = link_candidates_by_entry.get(match["source_entry_id"], [])
        image_candidates = image_candidates_by_entry.get(match["source_entry_id"], [])
        alignment_diagnostics = diagnostics_by_entry.get(match["source_entry_id"], [])

        # Surface only what genuinely needs a human:
        #   - unresolved conflicts or ambiguity,
        #   - Word entries with no / weak DB match,
        #   - clean multi-row links to confirm (the normal combined-act pattern),
        #   - candidate matches below the high-confidence bar.
        # Clean single high-confidence matches are auto-eligible and stay out of
        # the queue entirely (no control-sample rows). A genuine, non-structural
        # High alignment diagnostic still forces a row in as a safety net.
        status = match["match_status"]
        has_conflict = bool(match.get("top_conflicts"))
        # Count *primary* links, not total: a clean single-act match often retains
        # demoted `alternative` siblings (kept as evidence by the pruner), and
        # counting those would force the entry into the queue when it is really a
        # clean single match. Use the same primary-link signal
        # review_bucket_for_match uses for the combined-act bucket, so inclusion and
        # bucketing agree (genuine combined acts still surface; clean singles stay out).
        primary_link_count = int(
            match.get("suggested_primary_link_count") or match.get("suggested_link_count") or 0
        )
        include = False
        if status in {"ambiguous", "word_only"} or has_conflict:
            include = True
        elif primary_link_count > 1:
            include = True
        elif status == "matched_candidate" and float(match.get("top_score") or 0) < 70:
            include = True
        # A genuine, non-structural High alignment diagnostic forces a row in as a
        # safety net — EXCEPT `db_text_found_in_word_but_not_linked`, which is about
        # an *unlinked DB row* (already surfaced DB-side as "DB row needs a Word
        # link"); force-including the already-well-matched Word entry just duplicates
        # the same Word↔DB pair under a second bucket.
        if any(
            diagnostic.get("priority") == "High"
            and diagnostic.get("diagnostic_type") != "db_text_found_in_word_but_not_linked"
            for diagnostic in alignment_diagnostics
        ):
            include = True
        if not include:
            continue
        bucket, recommendation = review_bucket_for_match(match)
        top_db_row_id = match.get("top_db_row_id")
        relationship_type = match.get("suggested_relationship_type") or relationship_type_for_links(match, link_candidates)
        rows.append(
            {
                "packet_section": "Word entry review",
                "recommended_review_bucket": bucket,
                "recommended_reviewer_action": recommendation,
                "source_entry_id": match["source_entry_id"],
                "source_entry_key": match.get("source_entry_key"),
                "register_id": match["register_id"],
                "entry_label": match["event_label_raw"],
                "entry_type_interpretation": match["event_label_guess"],
                "entry_number": match.get("event_number_raw"),
                "referenced_entry_number": match.get("referenced_event_number_raw"),
                "word_registration_date": match.get("entry_registration_date_raw"),
                "word_registration_date_precision": match.get("entry_registration_date_precision"),
                "word_registration_date_source": match.get("entry_registration_date_source"),
                "word_folio_range": f"{match.get('entry_folio_start') or ''}-{match.get('entry_folio_end') or ''}".strip("-"),
                "match_status": MATCH_STATUS_LABELS.get(match["match_status"], match["match_status"]),
                "top_db_row_id": top_db_row_id,
                "top_db_table": match.get("top_db_table"),
                "top_db_contract_id": match.get("top_db_contract_id"),
                "top_db_main_contract_id": match.get("top_db_main_contract_id"),
                "top_db_type": match.get("top_db_sub_type") or match.get("top_db_table"),
                "top_match_score": match.get("top_score"),
                "top_match_signals_plain_language": readable_list(match.get("top_signals"), SIGNAL_LABELS),
                "top_match_conflicts_plain_language": readable_list(match.get("top_conflicts"), CONFLICT_LABELS),
                "narrative_similarity_ratio": match.get("top_narrative_similarity_ratio"),
                "text_containment_ratio": match.get("top_text_containment_ratio"),
                "word_token_coverage_in_db": match.get("top_word_token_coverage_in_db"),
                "db_token_coverage_in_word": match.get("top_db_token_coverage_in_word"),
                "shared_phrase_count": match.get("top_shared_phrase_count"),
                "longest_shared_phrase_words": match.get("top_longest_shared_phrase_words"),
                "field_overlap_count": match.get("top_field_overlap_count"),
                "field_overlap_plain_language": match.get("top_field_overlap_plain_language"),
                "alignment_diagnostic_types": diagnostic_types(alignment_diagnostics),
                "alignment_diagnostics_plain_language": diagnostic_plain_language(alignment_diagnostics),
                "candidate_count": match.get("candidate_count"),
                "top_candidates_plain_language": candidate_summary(candidates_by_entry.get(match["source_entry_id"], [])),
                "suggested_relationship_type": relationship_type,
                "suggested_db_row_ids": db_link_candidate_ids(link_candidates),
                "suggested_db_rows_plain_language": db_link_candidate_summary(link_candidates),
                "suggested_db_documents_text": db_link_candidate_documents(link_candidates, db_documents),
                "suggested_link_count": len(link_candidates),
                "image_candidates_plain_language": image_candidate_summary(image_candidates),
                "image_candidate_paths": image_candidate_paths(image_candidates),
                "image_candidates_need_review": image_candidates_need_review(image_candidates),
                "word_entry_text": compact_text(source_entry.get("current_text")),
                "top_db_document_text": compact_text(db_documents.get(str(top_db_row_id), "")),
                **entry_revision_fields(source_entry, comments_by_key, notes_by_key),
            }
        )
    return rows


def qa_rows_for_db_only(
    db_only_rows: list[dict[str, Any]],
    source_entries_by_id: dict[str, dict[str, Any]],
    db_documents: dict[str, str],
    word_register_ids: set[str],
    matched_main_contract_ids: set[int],
    diagnostics_by_db: dict[str, list[dict[str, Any]]],
    comments_by_key: dict[tuple[str, str], dict[str, Any]],
    notes_by_key: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """DB rows with no Word link. Returns (review_rows, out_of_scope_count).

    Out-of-scope rows — a register with no Word file, or no register metadata at
    all (`_unknown_register`) — have no Word side to review, so they are NOT
    surfaced as queue cases; only their count is returned for the summary. The
    rest collapse into one honest bucket: an unlinked DB row whose Word
    counterpart should be found (or confirmed a duplicate).
    """
    rows: list[dict[str, Any]] = []
    out_of_scope = 0
    for db_row in db_only_rows:
        register_id = db_row.get("register_id")
        if register_id not in word_register_ids or register_id in (None, "", "_unknown_register"):
            out_of_scope += 1
            continue
        alignment_diagnostics = diagnostics_by_db.get(db_row["db_row_id"], [])
        diagnostic_source_entry_id = next(
            (diagnostic.get("source_entry_id") for diagnostic in alignment_diagnostics if diagnostic.get("source_entry_id")),
            None,
        )
        diagnostic_source_entry = source_entries_by_id.get(str(diagnostic_source_entry_id), {})
        primary_diagnostic = alignment_diagnostics[0] if alignment_diagnostics else {}
        bucket = "DB row needs a Word link"
        action = "An unlinked DB row — check whether a Word entry exists for it (or it is a duplicate)."
        rows.append(
            {
                "packet_section": "DB-only review",
                "recommended_review_bucket": bucket,
                "recommended_reviewer_action": action,
                "source_entry_id": diagnostic_source_entry_id,
                "source_entry_key": diagnostic_source_entry.get("source_entry_key"),
                "register_id": register_id or "_unknown_register",
                "entry_label": diagnostic_source_entry.get("event_label_raw"),
                "entry_type_interpretation": diagnostic_source_entry.get("event_label_guess"),
                "entry_number": diagnostic_source_entry.get("event_number_raw"),
                "referenced_entry_number": diagnostic_source_entry.get("referenced_event_number_raw"),
                "word_registration_date": diagnostic_source_entry.get("registration_date_raw"),
                "word_folio_range": f"{diagnostic_source_entry.get('folio_start') or ''}-{diagnostic_source_entry.get('folio_end') or ''}".strip("-"),
                "match_status": "DB row has no proposed Word link",
                "top_db_row_id": db_row["db_row_id"],
                "top_db_table": db_row["db_table"],
                "top_db_contract_id": db_row["contract_id"],
                "top_db_main_contract_id": db_row.get("main_contract_id"),
                "top_db_type": db_row.get("sub_type") or db_row["db_table"],
                "top_match_score": primary_diagnostic.get("candidate_score"),
                "top_match_signals_plain_language": "See alignment diagnostic.",
                "top_match_conflicts_plain_language": "This DB row has no proposed Word-entry link.",
                "narrative_similarity_ratio": primary_diagnostic.get("narrative_similarity_ratio"),
                "word_token_coverage_in_db": primary_diagnostic.get("word_token_coverage_in_db"),
                "db_token_coverage_in_word": None,
                "shared_phrase_count": None,
                "longest_shared_phrase_words": None,
                "field_overlap_count": primary_diagnostic.get("field_overlap_count"),
                "field_overlap_plain_language": primary_diagnostic.get("field_overlap_plain_language", ""),
                "alignment_diagnostic_types": diagnostic_types(alignment_diagnostics),
                "alignment_diagnostics_plain_language": diagnostic_plain_language(alignment_diagnostics),
                "candidate_count": None,
                "top_candidates_plain_language": "This is a DB-only diagnostic row.",
                "suggested_relationship_type": "db_only_unlinked",
                "suggested_db_row_ids": db_row["db_row_id"],
                "suggested_db_rows_plain_language": "This DB row was not proposed as a link candidate for a Word entry.",
                "suggested_db_documents_text": compact_text(db_documents.get(db_row["db_row_id"], "")),
                "suggested_link_count": 0,
                "image_candidates_plain_language": "No Word source entry is attached to this DB-only diagnostic row.",
                "image_candidate_paths": "",
                "image_candidates_need_review": False,
                "word_entry_text": compact_text(diagnostic_source_entry.get("current_text")),
                "top_db_document_text": compact_text(db_documents.get(db_row["db_row_id"], "")),
                **entry_revision_fields(diagnostic_source_entry, comments_by_key, notes_by_key),
            }
        )
    return rows, out_of_scope


def write_qa_packet_html(path: Path, rows: list[dict[str, Any]], summary_lines: list[str]) -> None:
    ensure_dir(path.parent)
    cards: list[str] = []
    for index, row in enumerate(rows, start=1):
        cards.append(
            f"""
            <section class="card">
              <h2>{index}. {html.escape(str(row['recommended_review_bucket']))}</h2>
              <p><strong>Section:</strong> {html.escape(str(row['packet_section']))}
                 · <strong>Register:</strong> {html.escape(str(row['register_id']))}</p>
              <p><strong>Recommended action:</strong> {html.escape(str(row['recommended_reviewer_action']))}</p>
              <dl>
                <dt>Word entry</dt><dd>{html.escape(str(row.get('source_entry_id') or 'No Word entry'))}</dd>
                <dt>Label / type</dt><dd>{html.escape(str(row.get('entry_label') or ''))} · {html.escape(str(row.get('entry_type_interpretation') or ''))}</dd>
                <dt>Date / folio</dt><dd>{html.escape(str(row.get('word_registration_date') or ''))} · {html.escape(str(row.get('word_folio_range') or ''))}</dd>
                <dt>Bucket</dt><dd>{html.escape(str(row.get('recommended_review_bucket') or ''))}</dd>
                <dt>Suggested DB rows</dt><dd>{html.escape(str(row.get('suggested_db_row_ids') or row.get('top_db_row_id') or 'No suggested DB row'))}</dd>
                <dt>Relationship type</dt><dd>{html.escape(str(row.get('suggested_relationship_type') or ''))}</dd>
                <dt>Match status</dt><dd>{html.escape(str(row.get('match_status') or ''))}</dd>
                <dt>Text metrics</dt><dd>{html.escape(f"Narrative similarity: {row.get('narrative_similarity_ratio')}; text containment: {row.get('text_containment_ratio')}; Word-token coverage in DB: {row.get('word_token_coverage_in_db')}; longest shared phrase: {row.get('longest_shared_phrase_words')} words")}</dd>
                <dt>Structured overlap</dt><dd>{html.escape(str(row.get('field_overlap_plain_language') or 'No structured DB field overlap was recorded.'))}</dd>
                <dt>Alignment diagnostics</dt><dd>{html.escape(str(row.get('alignment_diagnostics_plain_language') or 'No additional alignment diagnostic was recorded.'))}</dd>
                <dt>Signals</dt><dd>{html.escape(str(row.get('top_match_signals_plain_language') or ''))}</dd>
                <dt>Conflicts</dt><dd>{html.escape(str(row.get('top_match_conflicts_plain_language') or ''))}</dd>
                <dt>Image candidates</dt><dd>{html.escape(str(row.get('image_candidate_paths') or 'No image candidate path'))}</dd>
              </dl>
              <div class="side-by-side">
                <div>
                  <h3>Word source entry</h3>
                  <pre>{html.escape(str(row.get('word_entry_text') or ''))}</pre>
                </div>
                <div>
                  <h3>Top DB document</h3>
                  <pre>{html.escape(str(row.get('suggested_db_documents_text') or row.get('top_db_document_text') or ''))}</pre>
                </div>
              </div>
              <details>
                <summary>Suggested DB links and scoring details</summary>
                <pre>{html.escape(str(row.get('suggested_db_rows_plain_language') or row.get('top_candidates_plain_language') or ''))}</pre>
              </details>
              <details>
                <summary>Image candidates</summary>
                <pre>{html.escape(str(row.get('image_candidates_plain_language') or ''))}</pre>
              </details>
            </section>
            """
        )
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>FlorAcco Word-DB Match QA Packet</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; line-height: 1.45; }}
    .summary {{ background: #f5f5f5; border: 1px solid #ddd; padding: 1rem; margin-bottom: 1.5rem; }}
    .card {{ border: 1px solid #ccc; border-left: 8px solid #999; padding: 1rem; margin: 1rem 0; border-radius: 6px; }}
    dl {{ display: grid; grid-template-columns: 12rem 1fr; column-gap: 1rem; }}
    dt {{ font-weight: 700; }}
    dd {{ margin: 0 0 .35rem 0; }}
    .side-by-side {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
    pre {{ white-space: pre-wrap; background: #fafafa; border: 1px solid #e5e5e5; padding: .75rem; max-height: 28rem; overflow: auto; }}
    @media (max-width: 900px) {{ .side-by-side {{ grid-template-columns: 1fr; }} dl {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <h1>FlorAcco Word-DB Match QA Packet</h1>
  <div class="summary">
    {"".join(f"<p>{html.escape(line)}</p>" for line in summary_lines)}
  </div>
  {''.join(cards)}
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def run_qa_packet(args: argparse.Namespace) -> int:
    config = load_config(args)
    extraction_dir = config["output_root"] / "03_extracted_registers"
    segmentation_dir = config["output_root"] / "04_source_entries"
    match_dir = config["output_root"] / "05_db_candidate_matches"
    qa_dir = config["output_root"] / "06_qa_packet"
    ensure_dir(qa_dir)

    source_entries = read_jsonl(segmentation_dir / "source_entries.jsonl")
    entry_matches = read_jsonl(match_dir / "entry_db_matches.jsonl")
    match_candidates = read_jsonl(match_dir / "match_candidates.jsonl")
    link_candidates = read_jsonl(match_dir / "source_entry_db_link_candidates.jsonl")
    db_only_rows = read_jsonl(match_dir / "db_only_rows.jsonl")
    alignment_diagnostics = read_jsonl(match_dir / "alignment_diagnostics.jsonl")
    image_link_rows = read_jsonl(config["output_root"] / "07_image_links" / "source_entry_image_candidates.jsonl")
    if not source_entries or not entry_matches:
        issues = [
            {
                "severity": "error",
                "code": "qa_packet_inputs_missing",
                "message": "Missing source entries or match outputs. Run segment-entries and match-db first.",
            }
        ]
        write_jsonl(qa_dir / "issues.jsonl", issues)
        return 1

    source_entries_by_id = {row["source_entry_id"]: row for row in source_entries}
    candidates_by_entry: dict[str, list[dict[str, Any]]] = {}
    for candidate in match_candidates:
        candidates_by_entry.setdefault(candidate["source_entry_id"], []).append(candidate)
    link_candidates_by_entry: dict[str, list[dict[str, Any]]] = {}
    for link_candidate in link_candidates:
        link_candidates_by_entry.setdefault(link_candidate["source_entry_id"], []).append(link_candidate)
    diagnostics_by_entry: dict[str, list[dict[str, Any]]] = {}
    diagnostics_by_db: dict[str, list[dict[str, Any]]] = {}
    for diagnostic in alignment_diagnostics:
        if diagnostic.get("source_entry_id"):
            diagnostics_by_entry.setdefault(diagnostic["source_entry_id"], []).append(diagnostic)
        if diagnostic.get("db_row_id"):
            diagnostics_by_db.setdefault(diagnostic["db_row_id"], []).append(diagnostic)
    image_candidates_by_entry: dict[str, list[dict[str, Any]]] = {}
    for image_link in image_link_rows:
        image_candidates_by_entry.setdefault(image_link["source_entry_id"], []).append(image_link)
    db_documents = load_db_documents(config["db_path"])
    comments_by_key = {
        (str(row.get("register_id")), str(row.get("comment_id"))): row
        for row in read_jsonl(extraction_dir / "comments.jsonl")
    }
    notes_by_key = {
        (str(row.get("register_id")), str(row.get("note_id"))): row
        for row in read_jsonl(extraction_dir / "footnotes.jsonl")
    }
    word_register_ids = {row["register_id"] for row in entry_matches}
    matched_main_contract_ids = {
        int(row["db_main_contract_id"])
        for row in link_candidates
        if row.get("db_table") == "sub_contract" and row.get("db_main_contract_id") is not None
    }

    qa_rows = qa_rows_for_matches(
        entry_matches,
        source_entries_by_id,
        candidates_by_entry,
        link_candidates_by_entry,
        diagnostics_by_entry,
        image_candidates_by_entry,
        db_documents,
        comments_by_key,
        notes_by_key,
    )
    db_only_qa_rows, out_of_scope_count = qa_rows_for_db_only(
        db_only_rows,
        source_entries_by_id,
        db_documents,
        word_register_ids,
        matched_main_contract_ids,
        diagnostics_by_db,
        comments_by_key,
        notes_by_key,
    )
    qa_rows.extend(db_only_qa_rows)
    bucket_rank = {name: index for index, name in enumerate(REVIEW_BUCKET_ORDER)}
    qa_rows.sort(
        key=lambda row: (
            bucket_rank.get(str(row["recommended_review_bucket"]), len(REVIEW_BUCKET_ORDER)),
            str(row["register_id"]),
            str(row.get("source_entry_id") or row.get("top_db_row_id")),
        )
    )

    summary_lines = [
        f"Generated {len(qa_rows)} QA review rows from {len(entry_matches)} Word-entry match rows and {len(db_only_rows)} DB-only rows.",
        f"Dropped {out_of_scope_count} out-of-scope DB-only rows (no Word file / no register metadata) from the review queue.",
        f"Loaded {len(link_candidates)} source-entry/DB link candidate rows.",
        f"Loaded {len(alignment_diagnostics)} alignment diagnostic rows.",
        f"Loaded {len(image_link_rows)} source-entry image candidate rows.",
        "This packet is for human review only. It does not approve matches or update SQLite.",
        "Rows are grouped by recommended_review_bucket (action-first); priority has been removed.",
    ]
    csv_exclude = {"word_entry_revision_summary", "word_entry_comments", "word_entry_notes"}
    csv_fieldnames = sorted({key for row in qa_rows for key in row if key not in csv_exclude})
    write_csv(qa_dir / "word_db_match_qa_packet.csv", qa_rows, fieldnames=csv_fieldnames)
    write_jsonl(qa_dir / "word_db_match_qa_packet.jsonl", qa_rows)
    write_qa_packet_html(qa_dir / "word_db_match_qa_packet.html", qa_rows, summary_lines)
    write_summary(
        qa_dir / "qa_packet_summary.md",
        "Word-DB Match QA Packet Summary",
        [
            f"- Match input: `{match_dir.relative_to(project_root())}`",
            f"- QA rows: {len(qa_rows)}",
            f"- Out-of-scope DB-only rows dropped from queue: {out_of_scope_count}",
            *[
                f"- {bucket}: {sum(1 for row in qa_rows if row['recommended_review_bucket'] == bucket)}"
                for bucket in REVIEW_BUCKET_ORDER
            ],
            f"- QA rows with image candidates: {sum(1 for row in qa_rows if row.get('image_candidate_paths'))}",
            f"- QA rows with image candidates needing review: {sum(1 for row in qa_rows if row.get('image_candidates_need_review'))}",
            f"- QA rows with multiple suggested DB links: {sum(1 for row in qa_rows if int(row.get('suggested_link_count') or 0) > 1)}",
            f"- QA rows with structured DB field overlap: {sum(1 for row in qa_rows if int(row.get('field_overlap_count') or 0) > 0)}",
            f"- QA rows with alignment diagnostics: {sum(1 for row in qa_rows if row.get('alignment_diagnostic_types'))}",
            "",
            "Outputs:",
            "- `word_db_match_qa_packet.csv` for filtering and sorting;",
            "- `word_db_match_qa_packet.html` for side-by-side reading;",
            "- `word_db_match_qa_packet.jsonl` for later scripted review workflows.",
        ],
    )
    print(f"Wrote QA packet outputs to {qa_dir.relative_to(project_root())}")
    print(f"QA rows: {len(qa_rows)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Word corpus inventory and normalization pipeline.")
    parser.add_argument("--data-root", help="Corpus root containing word/ and img/. Defaults to FLORACCO_DATA_ROOT.")
    parser.add_argument("--images-root", help="Image root. Defaults to FLORACCO_IMAGES_ROOT.")
    parser.add_argument("--db-path", help="SQLite DB path. Defaults to FLORACCO_DB_PATH.")
    parser.add_argument("--output-root", help=f"Derived output root. Defaults to {DEFAULT_OUTPUT_ROOT}.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("inventory", help="Write register/file coverage inventory outputs.")

    normalize = subparsers.add_parser("normalize", help="Create normalized DOCX processing copies.")
    normalize.add_argument("--soffice", help="Explicit path to LibreOffice soffice executable.")
    normalize.add_argument("--force", action="store_true", help="Overwrite existing normalized DOCX outputs.")
    normalize.add_argument(
        "--timeout-seconds",
        type=int,
        default=90,
        help="Per-file LibreOffice conversion timeout for legacy .doc files.",
    )

    subparsers.add_parser(
        "validate-normalized",
        help="Validate normalized DOCX files before extraction.",
    )
    subparsers.add_parser(
        "extract-registers",
        help="Extract register-level Word XML evidence from normalized DOCX files.",
    )
    subparsers.add_parser(
        "segment-entries",
        help="Segment extracted paragraphs into candidate Word source entries.",
    )
    subparsers.add_parser(
        "match-db",
        help="Rank candidate SQLite matches for segmented Word source entries.",
    )
    subparsers.add_parser(
        "qa-packet",
        help="Build a human-readable QA packet for Word-DB match review.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inventory":
        return run_inventory(args)
    if args.command == "normalize":
        return run_normalize(args)
    if args.command == "validate-normalized":
        return run_validate_normalized(args)
    if args.command == "extract-registers":
        return run_extract_registers(args)
    if args.command == "segment-entries":
        return run_segment_entries(args)
    if args.command == "match-db":
        return run_match_db(args)
    if args.command == "qa-packet":
        return run_qa_packet(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
