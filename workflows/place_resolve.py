"""Place pipeline batch: resolve → group (dedup) → geocode.

A one-time, cached, resumable job that resolves each verbatim place string to a
modern reference (via an LLM through OpenRouter), groups equal resolutions into
same-as suggestions, and geocodes the resolved names against the bundled GeoNames
dumps. Everything it writes is machine output (clearly labelled, regenerable,
inert) — a human confirms suggestions in the Reference worklist and the verbatim
term is never touched. See docs/reference/place_pipeline.md.

The LLM is called via OpenRouter's OpenAI-compatible endpoint (no provider SDK),
so any OpenRouter model works; default ``anthropic/claude-opus-4.8``.

Usage:
    python -m scripts.fetch_geonames                 # once: get the gazetteer
    export OPENROUTER_API_KEY=sk-or-...              # your key (never committed)
    uv run python -m workflows.place_resolve         # resolve + group + geocode
    uv run python -m workflows.place_resolve --mock  # no API key: deterministic stub
    uv run python -m workflows.place_resolve --model anthropic/claude-opus-4.8
    uv run python -m workflows.place_resolve --only-geocode
    uv run python -m workflows.place_resolve --limit 25 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflows import place_cache
from workflows.review_server import db_path  # reuse the resolved main.db path

CONFIDENT_THRESHOLD = 0.8  # ≥ → "Likely the same"; below → "Needs your eye"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-opus-4.8"

# Sub-national realms/regions whose name isn't a GeoNames point: place them at a
# representative capital rather than the (much cruder) country centroid. Whole
# countries are deliberately absent — their country-centroid is already correct.
REPRESENTATIVE_CITY = {
    "kingdom of naples": "Naples", "kingdom of sicily": "Palermo",
    "kingdom of etruria": "Florence", "grand duchy of tuscany": "Florence",
    "principality of lucca and piombino": "Lucca",
    "papal states": "Rome", "patrimonio di san pietro": "Viterbo",
    "crown of castile": "Toledo", "provence": "Aix-en-Provence",
    "saxony": "Dresden", "brabant": "Brussels", "duchy of brabant": "Brussels",
    "montagna pistoiese": "Pistoia", "barbary coast": "Tunis",
}


# Vague historic regions the LLM resolves without a country (so the gazetteer's
# hard country filter can't protect them) — pin them to an approximate point.
REGION_POINT = {
    "levant": (34.0, 36.5),          # eastern Mediterranean coast
    "barbary coast": (34.0, 9.0),    # north-west Africa
}


def _parent_city_candidates(modern_name: str, admin_region: str) -> list[str]:
    """City names to try for a parish that GeoNames can't place directly. The
    LLM names parishes like 'San Lorenzo, Florence' — the parent city is usually
    the last comma/paren-separated token; Florence/Firenze is the common case."""
    import re
    parts = [p.strip() for p in re.split(r"[,()]", modern_name) if p.strip()]
    cands = list(reversed(parts))             # city is usually last
    if admin_region:
        cands.append(admin_region)
    if re.search(r"\b(firenze|florence)\b", modern_name, re.IGNORECASE):
        cands.insert(0, "Florence")
    # de-dup, preserve order
    seen, out = set(), []
    for c in cands:
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out

SYSTEM_PROMPT = """\
You are curating the place vocabulary of FlorAcco, a database of Florentine \
accomandita (limited-partnership) contracts, 1445-1808. Each string was \
transcribed verbatim from the manuscripts as a partner's place of residence or \
origin, a contract location, or a trade-fair site, in early-modern Italian.

Expect: Tuscan cities, towns, and tiny hamlets (Firenze, Empoli, Brucianesi, \
Monterappoli, Diacceto); other Italian cities (Napoli, Venezia, Messina); \
European trade cities under Italian exonyms (Lisbona=Lisbon, Anversa=Antwerp, \
Lione=Lyon, Bruggia=Bruges, Saragozza=Zaragoza, Norimberga=Nuremberg); regions \
and realms (Regno di Napoli, Fiandre, Toscana, Levante, Barberia); administrative \
descriptors (contado di Firenze, distretto di Firenze, dominio fiorentino, \
terra/castello di Empoli); Florentine parishes (Firenze, popolo di San Lorenzo); \
and trade fairs (fiere di Piacenza, fiere di Besancon). Spelling/archaisms are \
common: Reame=Regno, Aquila=L'Aquila, Fiandra=Fiandre, Montelione=Monteleone, \
Bisanzone=Besancon.

For each verbatim string return STRICT JSON (no prose) with keys:
  modern_name   - the current standard name (its country's form, or English);
                  for an administrative area, the area's modern name.
  country       - modern country name (English).
  admin_region  - region/province if known, else "".
  feature_type  - one of: city, town, village, region, realm, parish, fair, area, unknown.
  is_area       - true for regions/realms/contado/distretto/area descriptors.
  confidence    - 0.0-1.0.
  note          - one short clause of justification.

IDENTITY RULES (critical):
- Administrative scope is part of identity: the CITY of Firenze, the CONTADO di \
Firenze, and the DISTRETTO di Firenze are DIFFERENT places. Give each its own \
feature_type/is_area; never collapse a contado/distretto/terra/castello to the \
bare city. Set modern_name to the parent place but keep the distinct feature_type.
- "di Sotto"/"di Sopra" (Inferiore/Superiore) are DIFFERENT towns.
- A fair (fiere di X) is distinct from the town X (feature_type "fair").
- Prefer LOWER confidence over a guess; use feature_type "unknown" if unsure. \
Never invent a place."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _progress(i: int, total: int, label: str, start: float) -> None:
    """In-place progress bar with ETA (stderr, dependency-free)."""
    elapsed = time.monotonic() - start
    rate = i / elapsed if elapsed > 0 else 0
    eta = int((total - i) / rate) if rate > 0 else 0
    width = 24
    filled = int(width * i / total) if total else width
    bar = "█" * filled + "·" * (width - filled)
    line = f"\r  [{bar}] {i}/{total} {i / total * 100 if total else 100:3.0f}%  ETA {eta:4d}s  {label[:26]:26}"
    sys.stderr.write(line)
    sys.stderr.flush()


# --- resolvers --------------------------------------------------------------

def resolve_llm(client: Any, model: str, verbatim: str) -> dict[str, Any]:
    """One structured resolution via OpenRouter (OpenAI-compatible chat API).
    ``client`` is an httpx.Client carrying the auth header. Returns the parsed
    JSON dict."""
    resp = client.post(
        OPENROUTER_URL,
        json={
            "model": model,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f'Resolve this place string: "{verbatim}"'},
            ],
        },
        timeout=90,
    )
    resp.raise_for_status()
    text = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    # tolerate a stray prose line around the JSON object
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def resolve_mock(verbatim: str) -> dict[str, Any]:
    """Deterministic stub so the whole pipeline + UI can be tested without a key.
    Encodes a few known equivalences from the data; everything else → unknown."""
    v = verbatim.lower()
    table = {
        "regno di napoli": ("Kingdom of Naples", "Italy", "Campania", "realm", True, 0.95),
        "reame di napoli": ("Kingdom of Naples", "Italy", "Campania", "realm", True, 0.9),
        "fiandre": ("Flanders", "Belgium", "", "region", True, 0.9),
        "fiandra": ("Flanders", "Belgium", "", "region", True, 0.85),
        "l'aquila": ("L'Aquila", "Italy", "Abruzzo", "city", False, 0.95),
        "aquila": ("L'Aquila", "Italy", "Abruzzo", "city", False, 0.7),
        "monteleone di calabria": ("Vibo Valentia", "Italy", "Calabria", "town", False, 0.9),
        "montelione di calabria": ("Vibo Valentia", "Italy", "Calabria", "town", False, 0.65),
        "fiere di besancon": ("Besançon", "France", "Bourgogne-Franche-Comté", "fair", False, 0.85),
        "fiere di bisanzone": ("Besançon", "France", "Bourgogne-Franche-Comté", "fair", False, 0.6),
    }
    if v in table:
        n, c, a, ft, area, conf = table[v]
        return {"modern_name": n, "country": c, "admin_region": a, "feature_type": ft,
                "is_area": area, "confidence": conf, "note": "mock"}
    return {"modern_name": verbatim, "country": "Italy", "admin_region": "", "feature_type": "unknown",
            "is_area": False, "confidence": 0.3, "note": "mock/unknown"}


# --- main steps -------------------------------------------------------------

def load_places() -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    counts: dict[int, int] = defaultdict(int)
    for q in (
        "SELECT place_id k,COUNT(*) n FROM contract_place WHERE is_deleted=0 GROUP BY place_id",
        "SELECT place_of_residence k,COUNT(*) n FROM investor WHERE is_deleted=0 GROUP BY place_of_residence",
        "SELECT place_of_origin k,COUNT(*) n FROM investor WHERE is_deleted=0 GROUP BY place_of_origin",
    ):
        for r in conn.execute(q):
            if r["k"] not in (None, 0, ""):
                counts[int(r["k"])] += r["n"]
    places = [
        {"place_id": int(r["place_id"]), "value": r["place_name"], "count": counts.get(int(r["place_id"]), 0)}
        for r in conn.execute("SELECT place_id, place_name FROM place")
        if (r["place_name"] or "").strip()
    ]
    conn.close()
    return places


def step_resolve(cache: sqlite3.Connection, places: list[dict], model: str, mock: bool,
                 limit: int | None, dry: bool) -> int:
    done = place_cache.resolved_ids(cache)
    todo = [p for p in places if p["place_id"] not in done]
    if limit:
        todo = todo[:limit]
    client = None
    if not todo:
        return 0  # everything already resolved — re-group/re-geocode needs no key
    if not mock:
        import httpx
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # OPENROUTER_API_KEY from .env
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise SystemExit(
                "OPENROUTER_API_KEY is not set. Put it in .env or `export` it, and run with the "
                "project venv: `uv run python -m workflows.place_resolve` (or `--mock` to test "
                "without a key)."
            )
        client = httpx.Client(headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "FlorAcco place resolution",
        })
    try:
        n = _resolve_loop(cache, todo, client, model, mock, dry)
    finally:
        if client is not None:
            client.close()
    return n


def _resolve_with_retry(client, model: str, verbatim: str, attempts: int = 3) -> dict[str, Any]:
    """Resolve one place, retrying transient errors (network / bad JSON) with a
    short backoff before giving up on that item."""
    for a in range(attempts):
        try:
            return resolve_llm(client, model, verbatim)
        except Exception:
            if a == attempts - 1:
                raise
            time.sleep(1.5 * (a + 1))
    raise RuntimeError("unreachable")


def _resolve_loop(cache, todo, client, model, mock, dry) -> int:
    total = len(todo)
    n = 0
    failed = 0
    start = time.monotonic()
    for i, p in enumerate(todo, 1):
        try:
            res = resolve_mock(p["value"]) if mock else _resolve_with_retry(client, model, p["value"])
        except Exception as e:
            # one bad place must never kill the batch — log and continue; a later
            # re-run retries it (nothing is recorded for it, so it stays "todo").
            failed += 1
            sys.stderr.write(f"\n  ! skip {p['value']!r}: {type(e).__name__}: {str(e)[:80]}\n")
            continue
        if dry:
            print(f"  {p['value'][:40]:40} -> {res.get('modern_name')} [{res.get('feature_type')}] {res.get('confidence')}")
            continue
        cache.execute(
            "INSERT OR REPLACE INTO place_resolution VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["place_id"], p["value"], res.get("modern_name", ""), res.get("country", ""),
             res.get("admin_region", ""), res.get("feature_type", "unknown"),
             1 if res.get("is_area") else 0, float(res.get("confidence", 0)), res.get("note", ""),
             "mock" if mock else model, place_cache.PROMPT_VERSION, _now()),
        )
        cache.commit()
        n += 1
        _progress(i, total, p["value"], start)
    if not dry and total:
        sys.stderr.write("\n")
    if failed:
        print(f"  ({failed} skipped after retries — re-run to retry them)")
    return n


def step_group(cache: sqlite3.Connection, places: list[dict]) -> int:
    """Group equal resolutions into same-as suggestions. Confidence = min member
    confidence.

    For most types the key is the resolved (modern_name, feature_type, is_area,
    region) — that captures the exonym/spelling/archaic wins. But **parish and
    area** types are grouped only by their *exact normalised verbatim* string:
    the LLM resolves all Florentine parishes to "Florence" and all the territorial
    descriptors (contado/distretto/dominio di Firenze) to "Florence — area", which
    would falsely merge legally-distinct units. Keying those on the verbatim means
    only true spelling-variants of the same string merge there."""
    from workflows.reference_match import place_signature
    cache.execute("DELETE FROM place_match_suggestion")
    by_key: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for r in cache.execute("SELECT * FROM place_resolution WHERE feature_type != 'unknown'"):
        if r["feature_type"] in ("parish", "area"):
            key = ("verbatim", place_signature(r["verbatim"]))
        else:
            key = (r["modern_name"].strip().lower(), r["feature_type"], r["is_area"],
                   (r["admin_region"] or "").strip().lower())
        by_key[key].append(r)
    n = 0
    for key, rows in by_key.items():
        if len(rows) < 2:
            continue
        group_key = "|".join(str(k) for k in key)
        conf = min(r["confidence"] for r in rows)
        rationale = (f"All resolve to {rows[0]['modern_name']}"
                     f"{' (' + rows[0]['admin_region'] + ')' if rows[0]['admin_region'] else ''}"
                     f" — {rows[0]['feature_type']}.")
        for r in rows:
            cache.execute(
                "INSERT INTO place_match_suggestion VALUES (?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), group_key, r["place_id"], conf, rationale,
                 r["model"], place_cache.PROMPT_VERSION, _now()),
            )
        n += 1
    cache.commit()
    return n


def step_geocode(cache: sqlite3.Connection) -> int:
    from workflows.geonames import country_point, shared
    gz = shared()
    cache.execute("DELETE FROM place_geo")  # clean rebuild from current resolutions
    n = 0
    for r in cache.execute("SELECT * FROM place_resolution"):
        lat = lon = None
        gid = ""
        resolved = r["modern_name"]
        source = "resolved+geonames"
        name, country, ftype = r["modern_name"], r["country"], r["feature_type"]

        rp = REGION_POINT.get((name or "").strip().lower())
        if rp:  # vague region — always use the curated point over any gazetteer guess
            cache.execute(
                "INSERT OR REPLACE INTO place_geo VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r["place_id"], rp[0], rp[1], r["is_area"], "geonames", "",
                 name, r["confidence"], "region (approx)", _now()),
            )
            n += 1
            continue

        entry = gz.geocode(name, country, r["admin_region"]) if name else None
        if not entry and r["is_area"] and r["admin_region"]:
            entry = gz.geocode(r["admin_region"], country)  # region → its admin point
        if entry:
            lat, lon, gid, resolved = entry.lat, entry.lon, entry.geonameid, entry.name
        else:
            # fix #3 — a sub-national realm/region → its capital (better than country centroid)
            cap = REPRESENTATIVE_CITY.get((name or "").strip().lower())
            cap_entry = gz.geocode(cap, country) if cap else None
            # fix #1 — a parish GeoNames can't place → its parent city (approximate)
            parent_entry = None
            if not cap_entry and ftype == "parish":
                for cand in _parent_city_candidates(name, r["admin_region"] or ""):
                    parent_entry = gz.geocode(cand, country)
                    if parent_entry:
                        break
            if cap_entry:
                lat, lon, gid, resolved, source = (
                    cap_entry.lat, cap_entry.lon, cap_entry.geonameid, cap_entry.name, "capital (approx)")
            elif parent_entry:
                lat, lon, gid, resolved, source = (
                    parent_entry.lat, parent_entry.lon, parent_entry.geonameid, parent_entry.name, "parent-city (approx)")
            elif r["is_area"]:
                pt = country_point(country)  # realm/region/country → approx centroid
                if pt:
                    lat, lon, resolved, source = pt[0], pt[1], country, "country-centroid"
        if lat is None:
            continue
        cache.execute(
            "INSERT OR REPLACE INTO place_geo VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["place_id"], lat, lon, r["is_area"], "geonames", gid,
             resolved, r["confidence"], source, _now()),
        )
        n += 1
    cache.commit()
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve → group → geocode the place vocabulary.")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="any OpenRouter model id")
    ap.add_argument("--mock", action="store_true", help="deterministic stub (no API key)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-geocode", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    places = load_places()
    cache = place_cache.connect()
    print(f"places: {len(places)}  ({sum(1 for p in places if p['count'] > 0)} used)")

    if not args.only_geocode:
        n = step_resolve(cache, places, args.model, args.mock, args.limit, args.dry_run)
        print(f"resolved: {n}")
        if not args.dry_run:
            g = step_group(cache, places)
            print(f"same-as suggestion groups: {g}")
    if not args.dry_run:
        gc = step_geocode(cache)
        print(f"geocoded: {gc}")
    cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
