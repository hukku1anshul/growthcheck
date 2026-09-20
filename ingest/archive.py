"""Provenance-preserving fetch.

Government and civic portals rot. In the course of building this project,
openbudgetsindia.org - a well-known civic budget portal cited in academic work and
news coverage - had lapsed and was serving an online casino affiliate site from
the same URL. Any claim we had sourced to that URL last year would today be
unverifiable, and any link we showed a citizen would send them to a gambling page.

So: we never cite a live URL as evidence. We archive the bytes, hash them, record
when we got them, and cite the archived copy. The live URL is shown as a
convenience and clearly marked as "may have changed since".
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

from .dns_fallback import install as install_dns_fallback

# Government portals are exactly where local resolvers fail. Tried only after the
# system resolver gives up; TLS verification is untouched. See dns_fallback.py.
install_dns_fallback()

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "archive"
# url -> archive metadata, independent of any one claim database
INDEX = ARCHIVE / "_index.json"

UA = (
    "politicalfindings/0.1 (open-source public-data transparency research; "
    "respects robots.txt; contact via project repository)"
)


class Archive:
    """Fetches, archives and registers documents. One instance per run."""

    def __init__(self, con: sqlite3.Connection, extractor: str, version: str,
                 delay: float = 1.0):
        self.con = con
        self.extractor = extractor
        self.version = version
        self.delay = delay          # seconds between requests to one host
        self._last: dict[str, float] = {}
        self._index: dict | None = None
        self._primed: dict[str, bool] = {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    # ------------------------------------------------------------------ polite
    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.delay:
                time.sleep(self.delay - gap)
        self._last[host] = time.monotonic()

    # ------------------------------------------------------------------ fetch
    def get(self, url: str, *, reuse: bool = True) -> tuple[int, bytes]:
        """Fetch `url`, archive it, register a source row. Returns (source_id, body).

        With reuse=True an already-archived copy of the same URL is returned
        without a network call, which keeps re-runs cheap and keeps us off the
        publisher's server.
        """
        if reuse:
            row = self.con.execute(
                "SELECT id, archive_path FROM sources WHERE url = ? "
                "ORDER BY fetched_at DESC LIMIT 1",
                (url,),
            ).fetchone()
            if row:
                path = ARCHIVE / row["archive_path"]
                if path.exists():
                    return row["id"], path.read_bytes()

            # The sources table is per-database, but the archive on disk outlives
            # any one database. Without this second check, rebuilding the claim
            # store from scratch re-downloads every page we already hold - which
            # is rude to the publisher and slow for no reason.
            hit = self._disk_index().get(url)
            if hit:
                path = ARCHIVE / hit["archive_path"]
                if path.exists():
                    body = path.read_bytes()
                    return self._register(url, body, hit, from_disk=True), body

        self._wait(url)
        resp = self.session.get(url, timeout=90)
        body = resp.content
        digest = hashlib.sha256(body).hexdigest()

        rel = Path(urlparse(url).netloc) / digest[:2] / f"{digest}.bin"
        dest = ARCHIVE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(body)

        meta = {
            "archive_path": str(rel).replace("\\", "/"),
            "sha256": digest,
            "bytes": len(body),
            "content_type": resp.headers.get("Content-Type"),
            "http_status": resp.status_code,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._remember(url, meta)
        return self._register(url, body, meta), body

    # --------------------------------------------------------------- registry
    def _register(self, url: str, body: bytes, meta: dict, *, from_disk: bool = False) -> int:
        """Insert (or find) the sources row for an archived document."""
        cur = self.con.execute(
            """INSERT OR IGNORE INTO sources
               (url, fetched_at, http_status, content_type, bytes, sha256,
                archive_path, extractor, extractor_version)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                url,
                meta["fetched_at"],
                meta.get("http_status"),
                meta.get("content_type"),
                meta.get("bytes", len(body)),
                meta["sha256"],
                meta["archive_path"],
                self.extractor,
                self.version,
            ),
        )
        self.con.commit()
        # `lastrowid` is NOT cleared when INSERT OR IGNORE ignores a row - it keeps
        # the rowid of the last insert that actually happened on this connection.
        # Trusting it here returned an unrelated id for a document we already held,
        # which made the re-fetch checker report unchanged pages as CHANGED.
        # `rowcount` is 1 on insert and 0 on ignore, which is the real signal.
        if cur.rowcount:
            return cur.lastrowid
        row = self.con.execute(
            "SELECT id FROM sources WHERE url = ? AND sha256 = ?",
            (url, meta["sha256"]),
        ).fetchone()
        return row["id"]

    # ------------------------------------------------------------ disk index
    def _disk_index(self) -> dict:
        if self._index is None:
            if INDEX.exists():
                try:
                    self._index = json.loads(INDEX.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    self._index = {}
            else:
                self._index = {}
        return self._index

    def _remember(self, url: str, meta: dict) -> None:
        idx = self._disk_index()
        idx[url] = meta
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx), encoding="utf-8")
        tmp.replace(INDEX)

    def text(self, url: str, encoding: str = "utf-8", **kw) -> tuple[int, str]:
        sid, body = self.get(url, **kw)
        return sid, body.decode(encoding, errors="replace")

    # -------------------------------------------------------------- POST APIs
    def post_json(
        self, url: str, payload: dict, *, reuse: bool = True, prime: str | None = None
    ) -> tuple[int, bytes]:
        """Archive the response to a JSON POST.

        Some portals expose their public data only through POST endpoints, so
        provenance has to cover the request as well as the URL. The archived
        identity is `url?<urlencoded payload>`: a faithful, readable description
        of exactly what was asked for, and distinct per query, so two different
        questions to the same endpoint are two different documents rather than
        one silently overwriting the other.

        `prime` is a page to GET first when the endpoint requires a session
        cookie - MPLADS returns an empty array to a cold client.
        """
        identity = f"{url}?{urlencode(sorted(payload.items()))}"

        if reuse:
            row = self.con.execute(
                "SELECT id, archive_path FROM sources WHERE url = ? "
                "ORDER BY fetched_at DESC LIMIT 1", (identity,),
            ).fetchone()
            if row:
                path = ARCHIVE / row["archive_path"]
                if path.exists():
                    return row["id"], path.read_bytes()
            hit = self._disk_index().get(identity)
            if hit:
                path = ARCHIVE / hit["archive_path"]
                if path.exists():
                    body = path.read_bytes()
                    return self._register(identity, body, hit, from_disk=True), body

        if prime and not self._primed.get(urlparse(url).netloc):
            self._wait(prime)
            try:
                self.session.get(prime, timeout=90)
                self._primed[urlparse(url).netloc] = True
            except Exception:  # noqa: BLE001 - priming is best effort
                pass

        # These payloads run to tens of megabytes uncompressed and the connection
        # is dropped mid-stream often enough to matter - the same request that
        # fails at 56MB of 64MB will succeed on a retry. A truncated JSON body is
        # unparseable, and silently keeping a partial array would undercount
        # per-person totals in alphabetical order, so a failed read must retry
        # rather than degrade.
        last_error: Exception | None = None
        for attempt in range(1, 4):
            self._wait(url)
            try:
                resp = self.session.post(url, json=payload, timeout=900)
                body = resp.content
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(f"    retry {attempt}/3 after {type(exc).__name__}", flush=True)
        else:
            raise RuntimeError(f"POST {url} failed after 3 attempts: {last_error}")
        digest = hashlib.sha256(body).hexdigest()

        rel = Path(urlparse(url).netloc) / digest[:2] / f"{digest}.bin"
        dest = ARCHIVE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(body)

        meta = {
            "archive_path": str(rel).replace("\\", "/"),
            "sha256": digest,
            "bytes": len(body),
            "content_type": resp.headers.get("Content-Type"),
            "http_status": resp.status_code,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._remember(identity, meta)
        return self._register(identity, body, meta), body
