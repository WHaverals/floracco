"""Offline geocoder over bundled GeoNames dumps.

Resolves an LLM-normalised ``(modern_name, country)`` to coordinates against the
GeoNames `cities500` (global, pop≥500) + `IT` (all Italian features) dumps under
``data/geonames/`` — free, reproducible, no rate limit. Used by the place
pipeline to attach lat/lon to every place_id; never authoritative over the
verbatim term. Download the dumps with ``scripts/fetch_geonames.py``.

GeoNames TSV columns: 0 id · 1 name · 2 asciiname · 3 alternatenames ·
4 lat · 5 lon · 6 feature_class · 7 feature_code · 8 country · 10 admin1 ·
14 population.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def _data_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    env = os.getenv("FLORACCO_DATA_DIR")
    base = Path(env).expanduser().resolve() if env else root / "data"
    return base / "geonames"


DEFAULT_DUMPS = ("cities500.txt", "IT.txt")

# LLM returns country *names*; map the ones this corpus actually uses to ISO codes.
COUNTRY_ISO = {
    "italy": "IT", "italia": "IT", "france": "FR", "francia": "FR",
    "spain": "ES", "spagna": "ES", "portugal": "PT", "portogallo": "PT",
    "netherlands": "NL", "belgium": "BE", "germany": "DE", "germania": "DE",
    "united kingdom": "GB", "england": "GB", "switzerland": "CH", "austria": "AT",
    "poland": "PL", "turkey": "TR", "turchia": "TR", "greece": "GR", "grecia": "GR",
    "egypt": "EG", "tunisia": "TN", "syria": "SY", "croatia": "HR",
    "czech republic": "CZ", "czechia": "CZ", "hungary": "HU",
    "morocco": "MA", "cyprus": "CY", "india": "IN", "sweden": "SE", "malta": "MT",
}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


# Approximate country centroids — a transparent fallback for `is_area` places
# (regions/realms/countries) whose name isn't a populated-place in the dumps, so
# they still get an approximate point for a map. Covers this corpus's geography.
COUNTRY_CENTROID: dict[str, tuple[float, float]] = {
    "italy": (42.8, 12.8), "france": (46.6, 2.4), "spain": (40.2, -3.7),
    "portugal": (39.5, -8.0), "netherlands": (52.2, 5.3), "belgium": (50.6, 4.5),
    "germany": (51.2, 10.4), "united kingdom": (54.0, -2.0), "england": (52.5, -1.5),
    "switzerland": (46.8, 8.2), "austria": (47.6, 14.1), "poland": (52.1, 19.4),
    "turkey": (39.0, 35.2), "greece": (39.0, 22.0), "egypt": (26.8, 30.8),
    "tunisia": (34.0, 9.0), "syria": (35.0, 38.0), "croatia": (45.1, 15.2),
    "czechia": (49.8, 15.5), "czech republic": (49.8, 15.5), "hungary": (47.2, 19.5),
    "morocco": (31.8, -7.0), "cyprus": (35.1, 33.4), "sweden": (62.0, 15.0), "malta": (35.9, 14.4),
}


def country_point(country: str | None) -> tuple[float, float] | None:
    return COUNTRY_CENTROID.get(_norm(country or "")) if country else None


@dataclass
class GeoEntry:
    name: str
    lat: float
    lon: float
    feature_class: str
    feature_code: str
    country: str
    admin1: str
    population: int
    geonameid: str


class Gazetteer:
    """In-memory name → entries index over the GeoNames dumps."""

    def __init__(self) -> None:
        self.by_name: dict[str, list[GeoEntry]] = {}
        self.admin1: dict[str, str] = {}   # "IT.16" -> "Tuscany"

    @classmethod
    def load(cls, dumps: Iterable[str] = DEFAULT_DUMPS) -> "Gazetteer":
        gz = cls()
        base = _data_dir()
        admin_path = base / "admin1CodesASCII.txt"
        if admin_path.exists():
            with admin_path.open(encoding="utf-8") as fh:
                for line in fh:
                    cols = line.rstrip("\n").split("\t")
                    if len(cols) >= 2:
                        gz.admin1[cols[0]] = cols[1]   # "IT.16" -> "Tuscany"
        for fname in dumps:
            path = base / fname
            if not path.exists():
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    cols = line.rstrip("\n").split("\t")
                    if len(cols) < 15:
                        continue
                    fclass = cols[6]
                    if fclass not in ("P", "A"):   # populated places + admin areas only
                        continue
                    try:
                        entry = GeoEntry(
                            name=cols[1], lat=float(cols[4]), lon=float(cols[5]),
                            feature_class=fclass, feature_code=cols[7], country=cols[8],
                            admin1=cols[10], population=int(cols[14] or 0), geonameid=cols[0],
                        )
                    except (ValueError, IndexError):
                        continue
                    keys = {_norm(cols[1]), _norm(cols[2])}
                    if cols[3]:
                        keys.update(_norm(a) for a in cols[3].split(",") if a)
                    for k in keys:
                        if k:
                            gz.by_name.setdefault(k, []).append(entry)
        return gz

    def geocode(
        self, name: str, country: str | None = None, admin_region: str | None = None
    ) -> GeoEntry | None:
        """Best match for a (modern) name.

        The LLM's ``country`` is a **hard** filter when its ISO is known: only
        in-country candidates are considered, and if none exist we return None
        (the caller then uses a country-centroid/capital fallback). This stops a
        European region matching a same-named US town — England→England,Arkansas,
        Flanders→Flanders,NY — the bug that soft filtering allowed. Among
        in-country candidates, a matching ``admin_region`` (e.g. Calabria) wins,
        then populated place over admin area, then population."""
        cands = self.by_name.get(_norm(name))
        if not cands:
            return None
        iso = COUNTRY_ISO.get(_norm(country or "")) if country else None
        if iso:
            in_country = [e for e in cands if e.country == iso]
            if not in_country:
                return None
            cands = in_country
        reg = _norm(admin_region or "")

        def rank(e: GeoEntry) -> tuple:
            admin_name = _norm(self.admin1.get(f"{e.country}.{e.admin1}", ""))
            region_match = 1 if (reg and admin_name and (reg in admin_name or admin_name in reg)) else 0
            return (region_match, 1 if e.feature_class == "P" else 0, e.population)

        return max(cands, key=rank)


_SHARED: Gazetteer | None = None


def shared() -> Gazetteer:
    global _SHARED
    if _SHARED is None:
        _SHARED = Gazetteer.load()
    return _SHARED
