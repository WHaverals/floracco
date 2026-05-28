"""Image inventory and provisional folio-linking pipeline.

This script is deliberately conservative:

- It never edits original image files.
- It never writes to SQLite.
- It writes only regenerable outputs under ``data/derived/word-pipeline/07_image_links``.
- It treats image/folio links as review aids, not confirmed evidence.

Usage:
    uv run python workflows/image_pipeline.py inventory
    uv run python workflows/image_pipeline.py map-folios
    uv run python workflows/image_pipeline.py link-source-entries
    uv run python workflows/image_pipeline.py all
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_OUTPUT_ROOT = Path("data/derived/word-pipeline")
IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
FILENAME_TOKEN_RE = re.compile(r"_(?P<token>[^_]+?)\.jpe?g$", flags=re.IGNORECASE)
NUMBER_TOKEN_RE = re.compile(
    r"^(?P<number>\d+)(?P<suffix>bis|[a-z]+)?(?:\s*\((?P<note>[^)]*)\))?$",
    flags=re.IGNORECASE,
)


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
    output_root = Path(args.output_root or DEFAULT_OUTPUT_ROOT)
    return {
        "data_root": resolve_repo_path(data_root),
        "images_root": resolve_repo_path(images_root),
        "output_root": resolve_repo_path(output_root),
    }


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


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


def normalize_folder(folder: str | None) -> str | None:
    if folder is None:
        return None
    folder = str(folder).strip()
    if not folder:
        return None
    if folder.lower() == "1263bis":
        return "1263bis"
    return folder


def infer_folder(text: str) -> str | None:
    match = re.search(r"(?<!\d)(108\d{2}|1263bis|1262|1263)(?!\d)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return normalize_folder(match.group(1))


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


def sort_key_for_image(path: Path) -> tuple[str, int, str, str]:
    token = parse_filename_token(path.name)["filename_token"] or ""
    parsed_number = parse_filename_token(path.name)["parsed_number"]
    number_sort = int(parsed_number) if parsed_number is not None else -1
    return (path.parent.name, number_sort, token, path.name)


def parse_filename_token(file_name: str) -> dict[str, Any]:
    if file_name.lower() == "000000.jpg":
        return {
            "filename_token": "000000",
            "parsed_number": None,
            "parsed_suffix": None,
            "filename_note": None,
            "image_role": "photographer_card",
            "use_for_contract_linking": False,
            "needs_review": False,
            "review_reason": "Photographer card; not a manuscript folio image.",
        }

    token_match = FILENAME_TOKEN_RE.search(file_name)
    if not token_match:
        return {
            "filename_token": None,
            "parsed_number": None,
            "parsed_suffix": None,
            "filename_note": None,
            "image_role": "unparsed_needs_review",
            "use_for_contract_linking": False,
            "needs_review": True,
            "review_reason": "Could not parse an image filename token.",
        }

    token = token_match.group("token")
    number_match = NUMBER_TOKEN_RE.match(token)
    if not number_match:
        return {
            "filename_token": token,
            "parsed_number": None,
            "parsed_suffix": None,
            "filename_note": None,
            "image_role": "unparsed_needs_review",
            "use_for_contract_linking": False,
            "needs_review": True,
            "review_reason": "Filename token is not a recognized folio or front-matter token.",
        }

    parsed_number = number_match.group("number")
    parsed_suffix = (number_match.group("suffix") or "").lower() or None
    filename_note = number_match.group("note")
    if parsed_number == "000":
        return {
            "filename_token": token,
            "parsed_number": parsed_number,
            "parsed_suffix": parsed_suffix,
            "filename_note": filename_note,
            "image_role": "front_matter_or_index",
            "use_for_contract_linking": False,
            "needs_review": False,
            "review_reason": "Front matter or alphabetical index image; not used for contract folio linking.",
        }
    if filename_note:
        return {
            "filename_token": token,
            "parsed_number": parsed_number,
            "parsed_suffix": parsed_suffix,
            "filename_note": filename_note,
            "image_role": "folio_opening_numbering_jump",
            "use_for_contract_linking": True,
            "needs_review": True,
            "review_reason": f"Filename note requires review: {filename_note}",
        }
    if parsed_suffix == "bis":
        return {
            "filename_token": token,
            "parsed_number": parsed_number,
            "parsed_suffix": parsed_suffix,
            "filename_note": filename_note,
            "image_role": "folio_opening_bis",
            "use_for_contract_linking": True,
            "needs_review": True,
            "review_reason": "Bis opening; verify folio sides against neighboring images.",
        }
    if parsed_suffix:
        return {
            "filename_token": token,
            "parsed_number": parsed_number,
            "parsed_suffix": parsed_suffix,
            "filename_note": filename_note,
            "image_role": "folio_opening_lettered",
            "use_for_contract_linking": True,
            "needs_review": True,
            "review_reason": "Lettered folio opening; verify folio sides against neighboring images.",
        }
    return {
        "filename_token": token,
        "parsed_number": parsed_number,
        "parsed_suffix": parsed_suffix,
        "filename_note": filename_note,
        "image_role": "folio_opening_plain",
        "use_for_contract_linking": True,
        "needs_review": False,
        "review_reason": "",
    }


def image_inventory_rows(images_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    if not images_root.exists():
        return [], [
            {
                "severity": "error",
                "code": "images_root_missing",
                "message": f"Images root not found: {images_root}",
            }
        ]

    image_paths = sorted(
        (path for path in images_root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=sort_key_for_image,
    )
    for image_path in image_paths:
        folder = infer_folder(str(image_path.parent))
        series = series_for_folder(folder, str(image_path.parent))
        register_id = register_id_for(series, folder)
        parsed = parse_filename_token(image_path.name)
        if register_id is None:
            issues.append(
                {
                    "severity": "warning",
                    "code": "image_register_unrecognized",
                    "message": f"Could not infer register from image path: {image_path}",
                    "image_file": image_path.name,
                }
            )
        if parsed["image_role"] == "unparsed_needs_review":
            issues.append(
                {
                    "severity": "warning",
                    "code": "image_filename_unparsed",
                    "message": f"Could not parse image filename for folio mapping: {image_path.name}",
                    "register_id": register_id,
                    "image_file": image_path.name,
                }
            )
        rows.append(
            {
                "register_id": register_id,
                "archive": "ASF" if register_id else None,
                "series": series,
                "folder": folder,
                "image_folder": str(image_path.parent.relative_to(project_root())),
                "image_file": image_path.name,
                "image_path": str(image_path.relative_to(project_root())),
                "file_size_bytes": image_path.stat().st_size,
                **parsed,
            }
        )
    return rows, issues


def previous_folio_verso(number_text: str) -> str | None:
    number = int(number_text)
    if number <= 1:
        return None
    return f"{number - 1}v"


def plain_right_recto(number_text: str) -> str:
    return f"{int(number_text)}r"


def candidate_rows_for_image(row: dict[str, Any], previous_row: dict[str, Any] | None) -> list[dict[str, Any]]:
    base = {
        "register_id": row["register_id"],
        "series": row["series"],
        "folder": row["folder"],
        "image_file": row["image_file"],
        "image_path": row["image_path"],
        "filename_token": row["filename_token"],
        "image_role": row["image_role"],
        "use_for_contract_linking": row["use_for_contract_linking"],
    }
    if not row["use_for_contract_linking"]:
        return [
            {
                **base,
                "page_position": None,
                "folio_candidate": None,
                "needs_review": row["needs_review"],
                "review_reason": row["review_reason"],
            }
        ]

    number = row["parsed_number"]
    suffix = row["parsed_suffix"]
    role = row["image_role"]
    candidate_rows: list[dict[str, Any]] = []
    left_candidate = previous_folio_verso(number)
    left_needs_review = bool(row["needs_review"])
    left_reason = row["review_reason"]
    right_candidate = plain_right_recto(number)
    right_needs_review = bool(row["needs_review"])
    right_reason = row["review_reason"]

    if role == "folio_opening_bis":
        left_candidate = f"{int(number)}v"
        right_candidate = f"{int(number)}bisr"
        left_needs_review = True
        right_needs_review = True
        left_reason = "Bis opening; likely prior folio verso, but verify against neighboring images."
        right_reason = "Bis opening; likely inserted folio recto, but verify against neighboring images."
    elif role == "folio_opening_lettered":
        left_candidate = f"{int(number)}v"
        right_candidate = f"{int(number)}{suffix}r"
        left_needs_review = True
        right_needs_review = True
        left_reason = "Lettered folio opening; likely prior folio verso, but verify against neighboring images."
        right_reason = "Lettered folio opening; likely inserted/lettered folio recto, but verify."
    elif role == "folio_opening_numbering_jump":
        left_candidate = previous_folio_verso(number)
        right_candidate = plain_right_recto(number)
        left_needs_review = True
        right_needs_review = True
        left_reason = row["review_reason"]
        right_reason = row["review_reason"]
    elif (
        previous_row
        and previous_row.get("image_role") == "folio_opening_bis"
        and previous_row.get("parsed_number") is not None
        and int(previous_row["parsed_number"]) == int(number) - 1
    ):
        left_candidate = f"{int(number) - 1}bisv"
        left_needs_review = True
        left_reason = "Previous image is a bis opening; verify that this left page is the bis verso."
    elif (
        previous_row
        and previous_row.get("image_role") == "folio_opening_lettered"
        and previous_row.get("parsed_number") is not None
        and int(previous_row["parsed_number"]) == int(number) - 1
        and previous_row.get("parsed_suffix")
    ):
        left_candidate = f"{int(number) - 1}{previous_row['parsed_suffix']}v"
        left_needs_review = True
        left_reason = "Previous image is a lettered opening; verify that this left page is the lettered verso."

    if left_candidate:
        candidate_rows.append(
            {
                **base,
                "page_position": "left",
                "folio_candidate": left_candidate,
                "needs_review": left_needs_review,
                "review_reason": left_reason,
            }
        )
    else:
        candidate_rows.append(
            {
                **base,
                "page_position": "left",
                "folio_candidate": None,
                "needs_review": True,
                "review_reason": "First numeric opening or missing previous folio; no left-page folio inferred.",
            }
        )
    candidate_rows.append(
        {
            **base,
            "page_position": "right",
            "folio_candidate": right_candidate,
            "needs_review": right_needs_review,
            "review_reason": right_reason,
        }
    )
    return candidate_rows


def image_folio_map_rows(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous_by_register: dict[str, dict[str, Any]] = {}
    for row in inventory_rows:
        register_id = row["register_id"] or "_unknown_register"
        previous_row = previous_by_register.get(register_id)
        rows.extend(candidate_rows_for_image(row, previous_row))
        if row["use_for_contract_linking"]:
            previous_by_register[register_id] = row
    return rows


def normalize_folio(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return re.sub(r"\s+", "", text)


def source_entry_image_link_rows(
    source_entries: list[dict[str, Any]], image_folio_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    image_rows_by_register_and_folio: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in image_folio_rows:
        if not row["use_for_contract_linking"] or not row["folio_candidate"]:
            continue
        key = (row["register_id"], normalize_folio(row["folio_candidate"]) or "")
        image_rows_by_register_and_folio.setdefault(key, []).append(row)

    link_rows: list[dict[str, Any]] = []
    for entry in source_entries:
        register_id = entry.get("register_id")
        folio_points = [
            ("start", normalize_folio(entry.get("folio_start"))),
            ("end", normalize_folio(entry.get("folio_end"))),
        ]
        seen_links: set[tuple[str, str, str]] = set()
        for folio_role, folio in folio_points:
            if not register_id or not folio:
                continue
            for image_row in image_rows_by_register_and_folio.get((register_id, folio), []):
                link_key = (entry["source_entry_id"], image_row["image_path"], image_row["folio_candidate"])
                if link_key in seen_links:
                    continue
                seen_links.add(link_key)
                link_rows.append(
                    {
                        "source_entry_id": entry["source_entry_id"],
                        "register_id": register_id,
                        "entry_folio_role": folio_role,
                        "entry_folio_start": entry.get("folio_start"),
                        "entry_folio_end": entry.get("folio_end"),
                        "matched_folio": image_row["folio_candidate"],
                        "image_file": image_row["image_file"],
                        "image_path": image_row["image_path"],
                        "page_position": image_row["page_position"],
                        "image_role": image_row["image_role"],
                        "needs_review": image_row["needs_review"],
                        "review_reason": image_row["review_reason"],
                    }
                )
    return link_rows


def image_output_dir(config: dict[str, Path]) -> Path:
    return config["output_root"] / "07_image_links"


def write_inventory_outputs(output_dir: Path, rows: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    fieldnames = [
        "register_id",
        "archive",
        "series",
        "folder",
        "image_folder",
        "image_file",
        "image_path",
        "file_size_bytes",
        "filename_token",
        "image_role",
        "use_for_contract_linking",
        "parsed_number",
        "parsed_suffix",
        "filename_note",
        "needs_review",
        "review_reason",
    ]
    write_csv(output_dir / "image_inventory.csv", rows, fieldnames=fieldnames)
    write_jsonl(output_dir / "image_inventory.jsonl", rows)
    write_jsonl(output_dir / "issues.jsonl", issues)
    role_counts = Counter(row["image_role"] for row in rows)
    write_summary(
        output_dir / "image_inventory_summary.md",
        "Image Inventory Summary",
        [
            f"- Images inventoried: {len(rows)}",
            f"- Registers represented: {len({row['register_id'] for row in rows if row['register_id']})}",
            f"- Images used for contract linking: {sum(1 for row in rows if row['use_for_contract_linking'])}",
            f"- Images excluded from contract linking: {sum(1 for row in rows if not row['use_for_contract_linking'])}",
            f"- Images needing review: {sum(1 for row in rows if row['needs_review'])}",
            "",
            "Image roles:",
            *[f"- {role}: {count}" for role, count in sorted(role_counts.items())],
        ],
    )


def write_map_outputs(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "register_id",
        "series",
        "folder",
        "image_file",
        "image_path",
        "filename_token",
        "image_role",
        "use_for_contract_linking",
        "page_position",
        "folio_candidate",
        "needs_review",
        "review_reason",
    ]
    write_csv(output_dir / "image_folio_map.csv", rows, fieldnames=fieldnames)
    write_jsonl(output_dir / "image_folio_map.jsonl", rows)
    write_summary(
        output_dir / "image_folio_map_summary.md",
        "Image Folio Map Summary",
        [
            f"- Image-page mapping rows: {len(rows)}",
            f"- Linkable mapping rows: {sum(1 for row in rows if row['use_for_contract_linking'] and row['folio_candidate'])}",
            f"- Mapping rows needing review: {sum(1 for row in rows if row['needs_review'])}",
            "",
            "Interpretation:",
            "- plain numeric images map to an inferred two-page opening;",
            "- photographer cards and front-matter/index images are excluded from contract linking;",
            "- bis, lettered, skipped-numbering, and first-opening cases are marked `needs_review`.",
        ],
    )


def write_source_link_outputs(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "source_entry_id",
        "register_id",
        "entry_folio_role",
        "entry_folio_start",
        "entry_folio_end",
        "matched_folio",
        "image_file",
        "image_path",
        "page_position",
        "image_role",
        "needs_review",
        "review_reason",
    ]
    write_csv(output_dir / "source_entry_image_candidates.csv", rows, fieldnames=fieldnames)
    write_jsonl(output_dir / "source_entry_image_candidates.jsonl", rows)
    write_summary(
        output_dir / "source_entry_image_candidates_summary.md",
        "Source Entry Image Candidates Summary",
        [
            f"- Source-entry/image candidate rows: {len(rows)}",
            f"- Source entries with at least one image candidate: {len({row['source_entry_id'] for row in rows})}",
            f"- Candidate rows needing review: {sum(1 for row in rows if row['needs_review'])}",
            "",
            "Interpretation:",
            "- these are candidate image links for review, not confirmed manuscript citations;",
            "- source entries are matched to image-page candidates by register and folio endpoint;",
            "- special filename cases remain visible through `needs_review` and `review_reason`.",
        ],
    )


def build_inventory(args: argparse.Namespace) -> tuple[dict[str, Path], list[dict[str, Any]], list[dict[str, Any]]]:
    config = load_config(args)
    rows, issues = image_inventory_rows(config["images_root"])
    write_inventory_outputs(image_output_dir(config), rows, issues)
    return config, rows, issues


def run_inventory(args: argparse.Namespace) -> int:
    config, rows, issues = build_inventory(args)
    output_dir = image_output_dir(config)
    print(f"Wrote image inventory outputs to {output_dir.relative_to(project_root())}")
    print(f"Images: {len(rows)}; issues: {len(issues)}")
    return 1 if any(issue["severity"] == "error" for issue in issues) else 0


def build_folio_map(args: argparse.Namespace) -> tuple[dict[str, Path], list[dict[str, Any]], list[dict[str, Any]]]:
    config, inventory_rows, issues = build_inventory(args)
    map_rows = image_folio_map_rows(inventory_rows)
    write_map_outputs(image_output_dir(config), map_rows)
    return config, map_rows, issues


def run_map_folios(args: argparse.Namespace) -> int:
    config, map_rows, issues = build_folio_map(args)
    output_dir = image_output_dir(config)
    print(f"Wrote image folio map outputs to {output_dir.relative_to(project_root())}")
    print(f"Image-page rows: {len(map_rows)}")
    return 1 if any(issue["severity"] == "error" for issue in issues) else 0


def run_link_source_entries(args: argparse.Namespace) -> int:
    config, map_rows, issues = build_folio_map(args)
    source_entries_path = config["output_root"] / "04_source_entries" / "source_entries.jsonl"
    source_entries = read_jsonl(source_entries_path)
    output_dir = image_output_dir(config)
    if not source_entries:
        issues.append(
            {
                "severity": "error",
                "code": "source_entries_missing",
                "message": f"Source entries not found or empty: {source_entries_path}",
            }
        )
        write_jsonl(output_dir / "issues.jsonl", issues)
        return 1
    link_rows = source_entry_image_link_rows(source_entries, map_rows)
    write_source_link_outputs(output_dir, link_rows)
    print(f"Wrote source-entry image candidate outputs to {output_dir.relative_to(project_root())}")
    print(f"Candidate rows: {len(link_rows)}; source entries linked: {len({row['source_entry_id'] for row in link_rows})}")
    return 1 if any(issue["severity"] == "error" for issue in issues) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Image inventory and provisional folio-linking pipeline.")
    parser.add_argument("--data-root", help="Corpus root containing word/ and img/. Defaults to FLORACCO_DATA_ROOT.")
    parser.add_argument("--images-root", help="Image root. Defaults to FLORACCO_IMAGES_ROOT.")
    parser.add_argument("--output-root", help=f"Derived output root. Defaults to {DEFAULT_OUTPUT_ROOT}.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inventory", help="Inventory image files and classify filename roles.")
    subparsers.add_parser("map-folios", help="Create provisional image-page to folio-candidate mappings.")
    subparsers.add_parser("link-source-entries", help="Link Word source-entry folios to image candidates.")
    subparsers.add_parser("all", help="Run inventory, folio mapping, and source-entry image linking.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inventory":
        return run_inventory(args)
    if args.command == "map-folios":
        return run_map_folios(args)
    if args.command in {"link-source-entries", "all"}:
        return run_link_source_entries(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
