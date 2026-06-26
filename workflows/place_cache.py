"""Derived cache for the place pipeline (resolutions · coordinates · suggestions).

Machine output only — regenerable, clearly labelled, and **never authoritative**
over the verbatim term. Lives in its own ``place_cache.db`` under the data dir
(gitignored), separate from the audited ``corrections.db``. The review server
imports only the read side here (no LLM/network dependency); the batch that fills
it lives in ``place_resolve.py``. See docs/reference/place_pipeline.md.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

PROMPT_VERSION = "place-resolve/v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS place_resolution (
  place_id INTEGER PRIMARY KEY, verbatim TEXT, modern_name TEXT, country TEXT,
  admin_region TEXT, feature_type TEXT, is_area INTEGER, confidence REAL,
  note TEXT, model TEXT, prompt_version TEXT, run_at TEXT
);
CREATE TABLE IF NOT EXISTS place_geo (
  place_id INTEGER PRIMARY KEY, lat REAL, lon REAL, is_area INTEGER,
  gazetteer TEXT, gazetteer_id TEXT, resolved_name TEXT, confidence REAL,
  source TEXT, run_at TEXT
);
CREATE TABLE IF NOT EXISTS place_match_suggestion (
  suggestion_id TEXT PRIMARY KEY, group_key TEXT, place_id INTEGER,
  confidence REAL, rationale TEXT, model TEXT, prompt_version TEXT, run_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_pms_group ON place_match_suggestion(group_key);
"""


def default_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    env = os.getenv("FLORACCO_DATA_DIR")
    base = Path(env).expanduser().resolve() if env else root / "data"
    return base / "sqlite" / "place_cache.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def exists() -> bool:
    return default_path().exists()


# --- read side (server) -----------------------------------------------------

def resolved_ids(conn: sqlite3.Connection) -> set[int]:
    return {int(r[0]) for r in conn.execute("SELECT place_id FROM place_resolution")}


def suggestion_families(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """LLM same-as suggestions, one dict per group: ``{group_key, place_ids,
    confidence, rationale}``. The server turns these into worklist families."""
    groups: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT group_key, place_id, confidence, rationale FROM place_match_suggestion"
    ):
        g = groups.setdefault(
            r["group_key"],
            {"group_key": r["group_key"], "place_ids": [], "confidence": r["confidence"],
             "rationale": r["rationale"]},
        )
        g["place_ids"].append(int(r["place_id"]))
        g["confidence"] = min(g["confidence"], r["confidence"])
    return [g for g in groups.values() if len(g["place_ids"]) > 1]


def resolution_for(conn: sqlite3.Connection, place_id: int) -> dict[str, Any] | None:
    """The machine resolution + coordinate for one place (for the term detail)."""
    r = conn.execute(
        "SELECT * FROM place_resolution WHERE place_id = ?", (place_id,)
    ).fetchone()
    if not r:
        return None
    out = dict(r)
    g = conn.execute("SELECT lat, lon, source FROM place_geo WHERE place_id = ?", (place_id,)).fetchone()
    if g:
        out["lat"], out["lon"], out["geo_source"] = g["lat"], g["lon"], g["source"]
    return out


def all_geo(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every geocoded place with lat/lon + feature_type + an approximate flag,
    for the map. Approximate = anything not a direct GeoNames hit."""
    rows = conn.execute(
        "SELECT g.place_id, g.lat, g.lon, g.source, r.feature_type, r.modern_name "
        "FROM place_geo g LEFT JOIN place_resolution r ON r.place_id = g.place_id "
        "WHERE g.lat IS NOT NULL"
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "place_id": int(r["place_id"]),
            "lat": r["lat"], "lon": r["lon"],
            "type": r["feature_type"] or "unknown",
            "modern_name": r["modern_name"] or "",
            "approx": (r["source"] or "") != "resolved+geonames",
        })
    return out


def geo_for(conn: sqlite3.Connection, place_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not place_ids:
        return {}
    marks = ",".join("?" * len(place_ids))
    return {
        int(r["place_id"]): dict(r)
        for r in conn.execute(
            f"SELECT * FROM place_geo WHERE place_id IN ({marks})", tuple(place_ids)
        )
    }
