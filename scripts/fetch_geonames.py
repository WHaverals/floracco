"""Download the GeoNames dumps used by the place geocoder.

Fetches `cities500` (global, pop≥500) + `IT` (all Italian features) into the
gitignored ``data/geonames/`` directory. Free, no API key. Run once:

    python -m scripts.fetch_geonames

Re-run to refresh. See docs/reference/place_pipeline.md.
"""

from __future__ import annotations

import io
import os
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE_URL = "https://download.geonames.org/export/dump/"
DUMPS = ("cities500.zip", "IT.zip")
PLAIN = ("admin1CodesASCII.txt",)   # not zipped; region-code → name (for disambiguation)


def _ssl_context() -> ssl.SSLContext:
    """Use certifi's CA bundle — the framework Python on macOS ships without root
    certificates, which otherwise fails SSL verification on download.geonames.org."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def target_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    env = os.getenv("FLORACCO_DATA_DIR")
    base = Path(env).expanduser().resolve() if env else root / "data"
    return base / "geonames"


def main() -> int:
    out = target_dir()
    out.mkdir(parents=True, exist_ok=True)
    ctx = _ssl_context()
    force = "--force" in sys.argv
    for name in DUMPS:
        txt = out / (name[:-4] + ".txt")
        if txt.exists() and not force:
            print(f"✓ {txt.name} already present — skipping (use --force to re-download)")
            continue
        url = BASE_URL + name
        print(f"↓ {url}")
        with urllib.request.urlopen(url, timeout=180, context=ctx) as resp:
            blob = resp.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            zf.extractall(out)
        (out / name).write_bytes(blob)
        print(f"  extracted to {out}")
    for name in PLAIN:
        dest = out / name
        if dest.exists() and not force:
            print(f"✓ {name} already present — skipping")
            continue
        print(f"↓ {BASE_URL + name}")
        with urllib.request.urlopen(BASE_URL + name, timeout=180, context=ctx) as resp:
            dest.write_bytes(resp.read())
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
