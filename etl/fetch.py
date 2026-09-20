"""Cached HTTP fetching.

Every raw file we download is kept on disk under data/raw/ and never deleted by the
pipeline. That is deliberate: a claim in this app is only as good as the document it
came from, and source URLs rot. The cached copy is the evidence.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
MANIFEST = RAW / "_manifest.json"

UA = "politicalfindings/0.1 (open-source public-data research tool)"


def _manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def _write_manifest(m: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")


def cached(url: str, name: str, *, max_age_days: int = 7, binary: bool = False):
    """Download `url` to data/raw/<name>, reusing the cache if it is fresh.

    Records url, fetch time, byte size and sha256 in the manifest so any number in the
    app can be traced back to an exact file.
    """
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / name
    man = _manifest()
    entry = man.get(name)

    fresh = (
        path.exists()
        and entry
        and (time.time() - entry.get("fetched_epoch", 0)) < max_age_days * 86400
    )
    if fresh:
        return path

    resp = requests.get(url, timeout=180, headers={"User-Agent": UA})
    resp.raise_for_status()
    path.write_bytes(resp.content)

    man[name] = {
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fetched_epoch": time.time(),
        "bytes": len(resp.content),
        "sha256": hashlib.sha256(resp.content).hexdigest(),
    }
    _write_manifest(man)
    return path


def source_manifest() -> dict:
    """The provenance record, for embedding in the shipped bundle."""
    m = _manifest()
    return {
        k: {kk: vv for kk, vv in v.items() if kk != "fetched_epoch"}
        for k, v in m.items()
    }
