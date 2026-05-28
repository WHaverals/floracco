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
}
DB_EVENT_TYPE_MAP = {
    "new_contract": {"contract"},
    "termination": {"termination"},
    "rescinded_termination": {"termination"},
    "assignment": {"cession", "assignment", "variation"},
    "modification": {"variation", "modification"},
    "balance": {"balance"},
    "renewal": {"renewal"},
    "ratification": {"ratification"},
    "extension": {"renewal", "extension", "variation"},
    "declaration": {"declaration", "variation"},
    "variation": {"variation"},
    "dissolution": {"termination", "dissolution"},
    "confirmation": {"confirmation", "variation"},
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
    "main_contract_id_referenced": "Word reference number matches DB sub_contract.main_contract_id",
    "main_contract_id_from_event_number": "Word event number matches DB sub_contract.main_contract_id",
    "registration_date_exact": "Registration dates match exactly",
    "folio_exact": "Folio range matches exactly",
    "folio_partial": "At least one folio endpoint matches",
    "event_type_compatible": "Word event type is compatible with DB table/type",
    "text_similarity_good": "Word and DB narratives are textually similar",
}
CONFLICT_LABELS = {
    "db_register_missing": "DB row has missing or malformed register metadata",
    "db_register_differs": "DB register differs from the Word register",
    "registration_date_differs": "Registration dates differ",
    "folio_differs": "Folio ranges differ",
    "event_type_table_differs": "Word event type points to a different DB table/type",
    "text_similarity_low": "Word and DB narratives have low text similarity",
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


def event_label_guesses(raw_label: str) -> list[str]:
    label = re.sub(r"\s+", " ", raw_label.strip().strip("[]").strip()).lower()
    if label.startswith(
        (
            "manca ",
            "nuova formula",
            "senza data",
            "senza testimoni",
            "non ci sono testimoni",
        )
    ):
        return []
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


def count_event_labels(text: str) -> int:
    bracket_count = sum(max(1, len(event_label_guesses(label))) for label in BRACKET_LABEL_RE.findall(text) if event_label_guesses(label))
    plus_count = len(PLUS_EVENT_RE.findall(BRACKET_LABEL_RE.sub("", text)))
    return bracket_count + plus_count


def parse_entry_label(text: str) -> dict[str, Any] | None:
    stripped_text = text.strip()
    if re.match(r"^Senza accomandita\b", stripped_text, flags=re.IGNORECASE):
        return {
            "event_label_raw": "Senza accomandita",
            "event_label_guess": "without_accomandita",
            "event_number_raw": None,
            "referenced_event_number_raw": None,
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
    referenced = REFERENCED_EVENT_RE.search(trailing)
    if referenced:
        referenced_event_number = referenced.group("number")
    number = EVENT_NUMBER_RE.search(trailing)
    if number:
        event_number = number.group("number")
    return {
        "event_label_raw": raw_label,
        "event_label_guess": label_guess,
        "event_number_raw": event_number,
        "referenced_event_number_raw": referenced_event_number,
        "event_label_trailing_text": trailing,
        "event_label_count": count_event_labels(stripped_text),
    }


def is_date_context_paragraph(paragraph: dict[str, Any]) -> bool:
    text = str(paragraph.get("current_text") or "").strip()
    if not text:
        return False
    if re.match(r"^\[manca la data\b", text, flags=re.IGNORECASE) and paragraph.get("date_candidates"):
        return True
    if paragraph.get("date_candidates") and len(text) <= 80 and not paragraph.get("bracket_labels"):
        return True
    return False


def group_rows_by_register(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["register_id"]), []).append(row)
    for group in grouped.values():
        group.sort(key=lambda row: int(row["paragraph_index"]))
    return grouped


def make_source_entry_id(register_id: str, ordinal: int) -> str:
    return f"{register_id}_entry_{ordinal:05d}"


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

    date_candidates = [
        candidate
        for paragraph in paragraphs[: max(1, paragraphs.index(label_paragraph) + 1)]
        for candidate in paragraph.get("date_candidates", [])
    ]
    if not date_candidates:
        date_candidates = [
            candidate for paragraph in paragraphs for candidate in paragraph.get("date_candidates", [])
        ]

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
    entry = {
        "source_entry_id": source_entry_id,
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
        "registration_date_raw": date_candidates[0] if date_candidates else None,
        "date_candidates": date_candidates,
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
        block = paragraphs[active_start:end_exclusive]
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


def parse_db_folio(folio: str | None) -> tuple[str | None, str | None]:
    if folio is None:
        return None, None
    cleaned = re.sub(r"\s+", "", str(folio).strip())
    if not cleaned:
        return None, None
    parsed = parse_folio_heading(f"c. {cleaned}")
    if parsed:
        return parsed["start"], parsed["end"]
    if re.fullmatch(rf"{FOLIO_NUMBER_PATTERN}[rv]?", cleaned, flags=re.IGNORECASE):
        token = re.match(rf"(?P<number>{FOLIO_NUMBER_PATTERN})(?P<side>[rv])?", cleaned, flags=re.IGNORECASE)
        if token:
            normalized = normalize_folio_token(token.group("number"), token.group("side"))
            return normalized, normalized
    return cleaned, cleaned


def folio_relationship(entry: dict[str, Any], db_row: dict[str, Any]) -> str:
    entry_start = entry.get("folio_start")
    entry_end = entry.get("folio_end")
    db_start = db_row.get("folio_start")
    db_end = db_row.get("folio_end")
    if not entry_start or not db_start:
        return "missing"
    if entry_start == db_start and entry_end == db_end:
        return "exact"
    if entry_start == db_start or entry_end == db_end:
        return "partial"
    return "different"


def integer_or_none(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return int(match.group(0))


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
                   document, NULL AS sub_type, NULL AS main_contract_id
            FROM contract
            """,
        ),
        (
            "sub_contract",
            """
            SELECT contract_id, archive, series, folder, folio, registration_date,
                   document, sub_type, main_contract_id
            FROM sub_contract
            """,
        ),
    ]
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
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
                        "sub_type": row["sub_type"],
                        "document_characters": len(document),
                        "document_text_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                        "_match_text": normalize_match_text(document)[:4000],
                    }
                )
    return rows, issues


def db_type_matches(entry: dict[str, Any], db_row: dict[str, Any]) -> bool:
    label = entry.get("event_label_guess")
    if label == "new_contract":
        return db_row["db_table"] == "contract"
    if db_row["db_table"] != "sub_contract":
        return False
    sub_type = normalize_match_text(db_row.get("sub_type"))
    if not sub_type:
        return False
    allowed = DB_EVENT_TYPE_MAP.get(str(label), set())
    return any(value in sub_type for value in allowed)


def candidate_db_rows_for_entry(
    entry: dict[str, Any],
    db_rows_by_register: dict[str, list[dict[str, Any]]],
    db_contract_rows_by_id: dict[int, list[dict[str, Any]]],
    db_main_rows_by_id: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = db_rows_by_register.get(str(entry["register_id"]), [])
    event_number = integer_or_none(entry.get("event_number_raw"))
    referenced_number = integer_or_none(entry.get("referenced_event_number_raw"))
    parsed_date = parse_italian_date(entry.get("registration_date_raw"))
    candidate_ids: set[str] = set()
    candidates: list[dict[str, Any]] = []

    def add_if(row: dict[str, Any], condition: bool) -> None:
        if condition and row["db_row_id"] not in candidate_ids:
            candidate_ids.add(row["db_row_id"])
            candidates.append(row)

    if entry.get("event_label_guess") == "new_contract" and event_number is not None:
        for row in db_contract_rows_by_id.get(event_number, []):
            add_if(row, row["db_table"] == "contract")
    if referenced_number is not None:
        for row in db_main_rows_by_id.get(referenced_number, []):
            add_if(row, row["db_table"] == "sub_contract")
    elif event_number is not None and entry.get("event_label_guess") != "new_contract":
        for row in db_main_rows_by_id.get(event_number, []):
            add_if(row, row["db_table"] == "sub_contract")

    for row in rows:
        add_if(
            row,
            entry.get("event_label_guess") == "new_contract"
            and event_number is not None
            and row["db_table"] == "contract"
            and row["contract_id"] == event_number,
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
        add_if(row, parsed_date is not None and row.get("registration_date") == parsed_date)
        add_if(row, folio_relationship(entry, row) in {"exact", "partial"})
    return candidates


def score_match(entry: dict[str, Any], db_row: dict[str, Any], entry_match_text: str) -> dict[str, Any]:
    score = 0.0
    signals: list[str] = []
    conflicts: list[str] = []
    event_number = integer_or_none(entry.get("event_number_raw"))
    referenced_number = integer_or_none(entry.get("referenced_event_number_raw"))
    parsed_date = parse_italian_date(entry.get("registration_date_raw"))

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
    if db_row["db_table"] == "sub_contract":
        if referenced_number is not None and db_row.get("main_contract_id") == referenced_number:
            score += 35
            signals.append("main_contract_id_referenced")
        elif event_number is not None and db_row.get("main_contract_id") == event_number:
            score += 30
            signals.append("main_contract_id_from_event_number")
    if parsed_date and db_row.get("registration_date") == parsed_date:
        score += 25
        signals.append("registration_date_exact")
    elif parsed_date and db_row.get("registration_date"):
        conflicts.append("registration_date_differs")

    folio_relation = folio_relationship(entry, db_row)
    if folio_relation == "exact":
        score += 20
        signals.append("folio_exact")
    elif folio_relation == "partial":
        score += 12
        signals.append("folio_partial")
    elif folio_relation == "different":
        conflicts.append("folio_differs")

    if db_type_matches(entry, db_row):
        score += 10
        signals.append("event_type_compatible")
    elif entry.get("event_label_guess") == "new_contract" and db_row["db_table"] != "contract":
        conflicts.append("event_type_table_differs")
    elif (
        entry.get("event_label_guess") != "new_contract"
        and db_row["db_table"] == "contract"
        and "contract_id_from_event_number" not in signals
    ):
        conflicts.append("event_type_table_differs")

    text_similarity = 0.0
    db_text = db_row.get("_match_text") or ""
    if entry_match_text and db_text:
        text_similarity = difflib.SequenceMatcher(None, entry_match_text[:4000], db_text).ratio()
        score += round(text_similarity * 20, 2)
        if text_similarity >= 0.55:
            signals.append("text_similarity_good")
        elif text_similarity < 0.20:
            conflicts.append("text_similarity_low")
    return {
        "score": round(score, 2),
        "signals": signals,
        "conflicts": conflicts,
        "text_similarity": round(text_similarity, 4),
        "entry_registration_date_iso": parsed_date,
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


def accepted_db_row_ids_for_match(match_status: str, candidates: list[dict[str, Any]]) -> set[str]:
    if not candidates:
        return set()
    if match_status == "matched_multiple":
        best = candidates[0]
        accepted: set[str] = set()
        for candidate in candidates:
            if (
                candidate["score"] >= 85
                and not candidate["conflicts"]
                and abs(candidate["score"] - best["score"]) <= 3
                and candidate.get("db_main_contract_id") == best.get("db_main_contract_id")
                and candidate.get("db_registration_date") == best.get("db_registration_date")
                and candidate.get("db_folio_start") == best.get("db_folio_start")
                and candidate.get("db_folio_end") == best.get("db_folio_end")
            ):
                accepted.add(candidate["db_row_id"])
        return accepted
    if match_status in {"matched_high_confidence", "matched_candidate"}:
        return {candidates[0]["db_row_id"]}
    return set()


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
    matched_db_rows: set[str] = set()

    for index, entry in enumerate(source_entries, start=1):
        if index == 1 or index % 500 == 0 or index == len(source_entries):
            print(f"[{index}/{len(source_entries)}] matching {entry['source_entry_id']}", flush=True)
        entry_match_text = normalize_match_text(entry_text_for_similarity(entry))[:4000]
        scored: list[dict[str, Any]] = []
        for db_row in candidate_db_rows_for_entry(
            entry,
            db_rows_by_register,
            db_contract_rows_by_id,
            db_main_rows_by_id,
        ):
            details = score_match(entry, db_row, entry_match_text)
            scored.append(
                {
                    "source_entry_id": entry["source_entry_id"],
                    "register_id": entry["register_id"],
                    "entry_ordinal": entry["entry_ordinal"],
                    "event_label_raw": entry["event_label_raw"],
                    "event_label_guess": entry["event_label_guess"],
                    "event_number_raw": entry.get("event_number_raw"),
                    "referenced_event_number_raw": entry.get("referenced_event_number_raw"),
                    "entry_registration_date_raw": entry.get("registration_date_raw"),
                    "entry_registration_date_iso": details["entry_registration_date_iso"],
                    "entry_folio_start": entry.get("folio_start"),
                    "entry_folio_end": entry.get("folio_end"),
                    "db_row_id": db_row["db_row_id"],
                    "db_table": db_row["db_table"],
                    "db_contract_id": db_row["contract_id"],
                    "db_main_contract_id": db_row.get("main_contract_id"),
                    "db_sub_type": db_row.get("sub_type"),
                    "db_registration_date": db_row.get("registration_date"),
                    "db_folio_raw": db_row.get("folio_raw"),
                    "db_folio_start": db_row.get("folio_start"),
                    "db_folio_end": db_row.get("folio_end"),
                    "score": details["score"],
                    "signals": details["signals"],
                    "conflicts": details["conflicts"],
                    "text_similarity": details["text_similarity"],
                    "folio_relation": details["folio_relation"],
                }
            )
        scored.sort(key=lambda row: row["score"], reverse=True)
        top_candidates = scored[:5]
        match_candidates.extend(top_candidates)
        match_status = classify_match(top_candidates)
        top = top_candidates[0] if top_candidates else None
        matched_db_rows.update(accepted_db_row_ids_for_match(match_status, top_candidates))
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
                "register_id": entry["register_id"],
                "entry_ordinal": entry["entry_ordinal"],
                "event_label_raw": entry["event_label_raw"],
                "event_label_guess": entry["event_label_guess"],
                "event_number_raw": entry.get("event_number_raw"),
                "referenced_event_number_raw": entry.get("referenced_event_number_raw"),
                "entry_registration_date_raw": entry.get("registration_date_raw"),
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
                "candidate_count": len(scored),
            }
        )

    accepted_top_matches = [
        row
        for row in entry_matches
        if row["match_status"] in {"matched_high_confidence", "matched_candidate", "matched_multiple"}
        and row.get("top_db_row_id")
    ]
    top_match_counts = Counter(row["top_db_row_id"] for row in accepted_top_matches)
    duplicate_top_matches: list[dict[str, Any]] = []
    for db_row_id, count in sorted(top_match_counts.items()):
        if count <= 1:
            continue
        linked_rows = [row for row in accepted_top_matches if row["top_db_row_id"] == db_row_id]
        duplicate_top_matches.append(
            {
                "db_row_id": db_row_id,
                "accepted_top_match_count": count,
                "source_entry_ids": [row["source_entry_id"] for row in linked_rows],
                "match_statuses": [row["match_status"] for row in linked_rows],
                "event_label_raws": [row["event_label_raw"] for row in linked_rows],
                "top_scores": [row["top_score"] for row in linked_rows],
            }
        )
        issues.append(
            {
                "severity": "warning",
                "code": "db_row_reused_as_top_match",
                "message": "DB row is the accepted top candidate for multiple Word entries",
                "source_entry_id": None,
                "db_row_id": db_row_id,
                "register_id": linked_rows[0]["register_id"],
            }
        )

    db_only_rows = [
        {
            key: value
            for key, value in row.items()
            if key != "_match_text"
        }
        for row in db_rows
        if row["db_row_id"] not in matched_db_rows
    ]
    for row in db_only_rows:
        issues.append(
            {
                "severity": "warning",
                "code": "db_row_without_matched_word_entry",
                "message": "DB row was not the accepted top candidate for a Word entry",
                "source_entry_id": None,
                "db_row_id": row["db_row_id"],
                "register_id": row["register_id"],
            }
        )

    word_register_ids = {row["register_id"] for row in entry_matches}
    matched_main_contract_ids = {
        row["top_db_main_contract_id"]
        for row in accepted_top_matches
        if row.get("top_db_table") == "sub_contract" and row.get("top_db_main_contract_id") is not None
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
            "bucket": "db_row_reused_as_top_match",
            "count": len(duplicate_top_matches),
            "interpretation": "One DB row is the accepted top match for multiple Word entries; often duplicate/repeated Word evidence.",
        },
        {
            "bucket": "matched_multiple_word_entries",
            "count": sum(1 for row in entry_matches if row["match_status"] == "matched_multiple"),
            "interpretation": "One Word entry plausibly corresponds to multiple DB rows with equivalent evidence.",
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
            }
        )

    write_jsonl(match_dir / "entry_db_matches.jsonl", entry_matches)
    write_csv(match_dir / "entry_db_matches.csv", entry_matches)
    write_jsonl(match_dir / "match_candidates.jsonl", match_candidates)
    write_jsonl(match_dir / "db_only_rows.jsonl", db_only_rows)
    write_jsonl(match_dir / "duplicate_top_matches.jsonl", duplicate_top_matches)
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
        f"- DB-only rows: {len(db_only_rows)}",
        f"- Duplicate accepted top DB rows: {len(duplicate_top_matches)}",
        f"- Matched-multiple Word entries: {sum(1 for row in entry_matches if row['match_status'] == 'matched_multiple')}",
        f"- Issues: {len(issues)}",
        "",
        "Review buckets:",
        *[f"- {row['bucket']}: {row['count']} ({row['interpretation']})" for row in review_buckets],
        "",
        "How to read this stage:",
        "- matches are ranked candidates, not accepted truth;",
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
            f"signals: {signals}; conflicts: {conflicts})"
        )
    return "\n".join(parts)


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


def review_bucket_for_match(match: dict[str, Any], duplicate_db_ids: set[str]) -> tuple[str, str, str]:
    status = match["match_status"]
    if match.get("top_db_row_id") in duplicate_db_ids:
        return (
            "DB row reused by multiple Word entries",
            "High",
            "Decide whether these Word entries are true duplicates, repeated source evidence, or separate acts that need separate DB rows.",
        )
    if status == "matched_multiple":
        return (
            "One Word entry maps to multiple DB rows",
            "High",
            "Review whether the Word entry should approve several DB rows together, usually for a combined act.",
        )
    if status == "ambiguous":
        return (
            "Ambiguous DB match",
            "High",
            "Choose the correct candidate, approve multiple candidates, or mark the entry as unresolved.",
        )
    if status == "word_only" and match["event_label_guess"] == "without_accomandita":
        return (
            "Expected Word-only non-accomandita act",
            "Low",
            "Usually mark as expected Word-only unless the text clearly belongs in SQLite.",
        )
    if status == "word_only" and int(match["candidate_count"]) == 0:
        return (
            "Word entry has no DB candidate",
            "High",
            "Check the Word source and DB coverage; no ID, date, or folio signal produced a plausible DB row.",
        )
    if status == "word_only":
        return (
            "Word entry has only weak or conflicting DB candidates",
            "High",
            "Inspect the top candidates and decide whether this is a DB mismatch, a Word-only entry, or a parser issue.",
        )
    if match.get("top_conflicts"):
        return (
            "Candidate match has visible conflicts",
            "Medium",
            "Review the conflicts before approving the match.",
        )
    if status == "matched_candidate":
        return (
            "Candidate match below high-confidence threshold",
            "Medium",
            "Spot-check before using this for field-level reconciliation.",
        )
    return (
        "High-confidence control sample",
        "Low",
        "Use as a control row; no action needed unless the side-by-side text looks wrong.",
    )


def qa_rows_for_matches(
    entry_matches: list[dict[str, Any]],
    source_entries_by_id: dict[str, dict[str, Any]],
    candidates_by_entry: dict[str, list[dict[str, Any]]],
    image_candidates_by_entry: dict[str, list[dict[str, Any]]],
    db_documents: dict[str, str],
    duplicate_db_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    high_confidence_controls_by_register: set[str] = set()
    candidate_controls_by_register: set[str] = set()
    for match in entry_matches:
        source_entry = source_entries_by_id.get(match["source_entry_id"], {})
        include = False
        if match["match_status"] in {"ambiguous", "matched_multiple", "word_only"}:
            include = True
        if match.get("top_db_row_id") in duplicate_db_ids:
            include = True
        serious_conflicts = {
            "db_register_missing",
            "db_register_differs",
            "event_type_table_differs",
        }
        if serious_conflicts.intersection(set(match.get("top_conflicts") or [])):
            include = True
        if match["match_status"] == "matched_candidate" and float(match.get("top_score") or 0) < 70:
            include = True
        if (
            match["match_status"] == "matched_candidate"
            and not match.get("top_conflicts")
            and match.get("top_db_row_id") not in duplicate_db_ids
            and match["register_id"] not in candidate_controls_by_register
        ):
            include = True
            candidate_controls_by_register.add(match["register_id"])
        if (
            match["match_status"] == "matched_high_confidence"
            and not match.get("top_conflicts")
            and match["register_id"] not in high_confidence_controls_by_register
        ):
            include = True
            high_confidence_controls_by_register.add(match["register_id"])
        if not include:
            continue
        bucket, priority, recommendation = review_bucket_for_match(match, duplicate_db_ids)
        top_db_row_id = match.get("top_db_row_id")
        image_candidates = image_candidates_by_entry.get(match["source_entry_id"], [])
        rows.append(
            {
                "packet_section": "Word entry review",
                "review_priority": priority,
                "recommended_review_bucket": bucket,
                "recommended_reviewer_action": recommendation,
                "source_entry_id": match["source_entry_id"],
                "register_id": match["register_id"],
                "entry_label": match["event_label_raw"],
                "entry_type_interpretation": match["event_label_guess"],
                "entry_number": match.get("event_number_raw"),
                "referenced_entry_number": match.get("referenced_event_number_raw"),
                "word_registration_date": match.get("entry_registration_date_raw"),
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
                "candidate_count": match.get("candidate_count"),
                "top_candidates_plain_language": candidate_summary(candidates_by_entry.get(match["source_entry_id"], [])),
                "image_candidates_plain_language": image_candidate_summary(image_candidates),
                "image_candidate_paths": image_candidate_paths(image_candidates),
                "image_candidates_need_review": image_candidates_need_review(image_candidates),
                "word_entry_text": compact_text(source_entry.get("current_text")),
                "top_db_document_text": compact_text(db_documents.get(str(top_db_row_id), "")),
            }
        )
    return rows


def qa_rows_for_db_only(
    db_only_rows: list[dict[str, Any]],
    db_documents: dict[str, str],
    word_register_ids: set[str],
    matched_main_contract_ids: set[int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for db_row in db_only_rows:
        register_id = db_row.get("register_id")
        if register_id is None:
            bucket = "DB-only row with missing register metadata"
            priority = "High"
            action = "Correct or investigate DB folder/register metadata before matching."
        elif register_id not in word_register_ids:
            bucket = "DB-only row outside current Word corpus"
            priority = "Low"
            action = "Usually out of scope for this Word matching pass."
        elif db_row["db_table"] == "contract" and db_row["contract_id"] in matched_main_contract_ids:
            bucket = "DB-only main contract with matched later acts"
            priority = "Medium"
            action = "Check whether the Word source has the original contract entry or only later acts referring to it."
        else:
            bucket = "DB-only row needing review"
            priority = "High"
            action = "Look for a missing Word entry, segmentation miss, or DB row that belongs outside the Word corpus."
        rows.append(
            {
                "packet_section": "DB-only review",
                "review_priority": priority,
                "recommended_review_bucket": bucket,
                "recommended_reviewer_action": action,
                "source_entry_id": None,
                "register_id": register_id or "_unknown_register",
                "entry_label": None,
                "entry_type_interpretation": None,
                "entry_number": None,
                "referenced_entry_number": None,
                "word_registration_date": None,
                "word_folio_range": None,
                "match_status": "DB row not accepted as top match",
                "top_db_row_id": db_row["db_row_id"],
                "top_db_table": db_row["db_table"],
                "top_db_contract_id": db_row["contract_id"],
                "top_db_main_contract_id": db_row.get("main_contract_id"),
                "top_db_type": db_row.get("sub_type") or db_row["db_table"],
                "top_match_score": None,
                "top_match_signals_plain_language": "None recorded.",
                "top_match_conflicts_plain_language": "No Word entry accepted this DB row as its top match.",
                "candidate_count": None,
                "top_candidates_plain_language": "This is a DB-only diagnostic row.",
                "image_candidates_plain_language": "No Word source entry is attached to this DB-only diagnostic row.",
                "image_candidate_paths": "",
                "image_candidates_need_review": False,
                "word_entry_text": "",
                "top_db_document_text": compact_text(db_documents.get(db_row["db_row_id"], "")),
            }
        )
    return rows


def write_qa_packet_html(path: Path, rows: list[dict[str, Any]], summary_lines: list[str]) -> None:
    ensure_dir(path.parent)
    cards: list[str] = []
    for index, row in enumerate(rows, start=1):
        priority_class = str(row["review_priority"]).lower()
        cards.append(
            f"""
            <section class="card {html.escape(priority_class)}">
              <h2>{index}. {html.escape(str(row['recommended_review_bucket']))}</h2>
              <p><strong>Priority:</strong> {html.escape(str(row['review_priority']))}
                 · <strong>Section:</strong> {html.escape(str(row['packet_section']))}
                 · <strong>Register:</strong> {html.escape(str(row['register_id']))}</p>
              <p><strong>Recommended action:</strong> {html.escape(str(row['recommended_reviewer_action']))}</p>
              <dl>
                <dt>Word entry</dt><dd>{html.escape(str(row.get('source_entry_id') or 'No Word entry'))}</dd>
                <dt>Label / type</dt><dd>{html.escape(str(row.get('entry_label') or ''))} · {html.escape(str(row.get('entry_type_interpretation') or ''))}</dd>
                <dt>Date / folio</dt><dd>{html.escape(str(row.get('word_registration_date') or ''))} · {html.escape(str(row.get('word_folio_range') or ''))}</dd>
                <dt>Top DB row</dt><dd>{html.escape(str(row.get('top_db_row_id') or 'No top DB row'))} · {html.escape(str(row.get('top_db_type') or ''))}</dd>
                <dt>Match status</dt><dd>{html.escape(str(row.get('match_status') or ''))}</dd>
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
                  <pre>{html.escape(str(row.get('top_db_document_text') or ''))}</pre>
                </div>
              </div>
              <details>
                <summary>Top candidates and scoring details</summary>
                <pre>{html.escape(str(row.get('top_candidates_plain_language') or ''))}</pre>
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
    .card.high {{ border-left-color: #b42318; }}
    .card.medium {{ border-left-color: #b54708; }}
    .card.low {{ border-left-color: #1f6feb; }}
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
    segmentation_dir = config["output_root"] / "04_source_entries"
    match_dir = config["output_root"] / "05_db_candidate_matches"
    qa_dir = config["output_root"] / "06_qa_packet"
    ensure_dir(qa_dir)

    source_entries = read_jsonl(segmentation_dir / "source_entries.jsonl")
    entry_matches = read_jsonl(match_dir / "entry_db_matches.jsonl")
    match_candidates = read_jsonl(match_dir / "match_candidates.jsonl")
    db_only_rows = read_jsonl(match_dir / "db_only_rows.jsonl")
    duplicate_top_matches = read_jsonl(match_dir / "duplicate_top_matches.jsonl")
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
    image_candidates_by_entry: dict[str, list[dict[str, Any]]] = {}
    for image_link in image_link_rows:
        image_candidates_by_entry.setdefault(image_link["source_entry_id"], []).append(image_link)
    duplicate_db_ids = {row["db_row_id"] for row in duplicate_top_matches}
    db_documents = load_db_documents(config["db_path"])
    word_register_ids = {row["register_id"] for row in entry_matches}
    matched_main_contract_ids = {
        int(row["top_db_main_contract_id"])
        for row in entry_matches
        if row.get("top_db_table") == "sub_contract" and row.get("top_db_main_contract_id") is not None
    }

    qa_rows = qa_rows_for_matches(
        entry_matches,
        source_entries_by_id,
        candidates_by_entry,
        image_candidates_by_entry,
        db_documents,
        duplicate_db_ids,
    )
    qa_rows.extend(qa_rows_for_db_only(db_only_rows, db_documents, word_register_ids, matched_main_contract_ids))
    priority_order = {"High": 0, "Medium": 1, "Low": 2}
    qa_rows.sort(
        key=lambda row: (
            priority_order.get(str(row["review_priority"]), 9),
            str(row["recommended_review_bucket"]),
            str(row["register_id"]),
            str(row.get("source_entry_id") or row.get("top_db_row_id")),
        )
    )

    summary_lines = [
        f"Generated {len(qa_rows)} QA rows from {len(entry_matches)} Word-entry match rows and {len(db_only_rows)} DB-only rows.",
        f"Loaded {len(image_link_rows)} source-entry image candidate rows.",
        "This packet is for human review only. It does not approve matches or update SQLite.",
        "Use the priority and recommended-review-bucket fields to work through the hardest rows first.",
    ]
    write_csv(qa_dir / "word_db_match_qa_packet.csv", qa_rows)
    write_jsonl(qa_dir / "word_db_match_qa_packet.jsonl", qa_rows)
    write_qa_packet_html(qa_dir / "word_db_match_qa_packet.html", qa_rows, summary_lines)
    write_summary(
        qa_dir / "qa_packet_summary.md",
        "Word-DB Match QA Packet Summary",
        [
            f"- Match input: `{match_dir.relative_to(project_root())}`",
            f"- QA rows: {len(qa_rows)}",
            f"- High priority rows: {sum(1 for row in qa_rows if row['review_priority'] == 'High')}",
            f"- Medium priority rows: {sum(1 for row in qa_rows if row['review_priority'] == 'Medium')}",
            f"- Low priority/control rows: {sum(1 for row in qa_rows if row['review_priority'] == 'Low')}",
            f"- QA rows with image candidates: {sum(1 for row in qa_rows if row.get('image_candidate_paths'))}",
            f"- QA rows with image candidates needing review: {sum(1 for row in qa_rows if row.get('image_candidates_need_review'))}",
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
