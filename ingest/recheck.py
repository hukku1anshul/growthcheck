"""Re-fetch archived documents and report what actually changed.

    python -m ingest.recheck --sample 25
    python -m ingest.recheck --all --extractor myneta
    python -m ingest.recheck --sample 10 --wayback

WHY A CLAIM-LEVEL DIFF, NOT A BYTE DIFF
---------------------------------------
Re-fetching a page and comparing hashes tells you almost nothing useful. Session
tokens, CSRF fields, "generated at" timestamps, rotating ad slots and A/B markers
mean a government page can change its bytes on every single request while saying
exactly the same thing. A byte-level monitor on such a source cries wolf until
someone turns it off, which is worse than having no monitor.

So this compares the FACTS. A page is re-fetched, re-parsed, and the claims it
yields now are compared against the claims we stored from it before:

    identical   same bytes - nothing to do
    cosmetic    bytes changed, every claim identical - the page was restyled
    CHANGED     a claim's value differs - this is news, and is reported loudly
    ADDED/GONE  a fact appeared or disappeared
    MISSING     the URL no longer resolves or returns an error

Only the last two categories are worth waking anyone for. `cosmetic` is expected
and is the reason this tool exists in this shape.

Because the original bytes are archived, a CHANGED result is provable: we hold
both versions and can show exactly what the publisher used to say.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from .archive import ARCHIVE, Archive
from .extractors.myneta import MyNeta
from .extractors.ukparliament import UKParliament
from .schema import connect

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "claims.db"

EXTRACTORS = {"myneta": MyNeta, "ukparliament": UKParliament}

WAYBACK = "https://archive.org/wayback/available"


def key(c) -> tuple:
    """Identity of a claim, independent of its value."""
    return (c.predicate, c.as_of)


def value(c):
    return c.value_num if c.value_num is not None else c.value_text


def stored_claims(con: sqlite3.Connection, source_id: int) -> dict[tuple, object]:
    rows = con.execute(
        "SELECT predicate, as_of, value_num, value_text FROM claims WHERE source_id = ?",
        (source_id,),
    ).fetchall()
    return {
        (r["predicate"], r["as_of"]): (
            r["value_num"] if r["value_num"] is not None else r["value_text"]
        )
        for r in rows
    }


def wayback_witness(url: str, session: requests.Session) -> str | None:
    """Ask the Internet Archive whether an independent snapshot exists.

    This is corroboration, not proof. It establishes that a third party also saw
    this URL at some point, so our archive is not the only witness to what the
    publisher said. Read-only: this never asks the Archive to save anything.
    """
    try:
        r = session.get(WAYBACK, params={"url": url}, timeout=30)
        snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        if snap.get("available"):
            ts = snap.get("timestamp", "")
            pretty = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else ts
            return f"{pretty} {snap.get('url', '')}"
    except Exception:
        return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=20, help="how many URLs to recheck")
    ap.add_argument("--all", action="store_true", help="recheck every archived URL")
    ap.add_argument("--extractor", choices=sorted(EXTRACTORS), default=None)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--wayback", action="store_true",
                    help="also look for an independent Internet Archive snapshot")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    con = connect(args.db)

    where = "WHERE extractor = ?" if args.extractor else ""
    params = (args.extractor,) if args.extractor else ()
    rows = con.execute(
        f"""SELECT id, url, sha256, archive_path, extractor, fetched_at
            FROM sources {where} ORDER BY id""", params
    ).fetchall()
    if not args.all:
        random.seed(0)  # reproducible sample
        rows = random.sample(rows, min(args.sample, len(rows)))

    print(f"\nrechecking {len(rows)} archived documents "
          f"({args.delay}s between requests)\n")

    tally: dict[str, int] = defaultdict(int)
    news: list[str] = []
    session = requests.Session()

    # one Archive per extractor so re-fetches are attributed correctly
    archives = {}
    parsers = {}
    for name, cls in EXTRACTORS.items():
        inst = cls(con, delay=args.delay)
        parsers[name] = inst
        archives[name] = inst.archive

    for i, r in enumerate(rows, 1):
        name = r["extractor"]
        arc: Archive = archives.get(name) or archives["myneta"]
        try:
            new_sid, body = arc.get(r["url"], reuse=False)
        except Exception as exc:  # noqa: BLE001
            tally["missing"] += 1
            news.append(f"MISSING  {r['url']}\n         {type(exc).__name__}: {exc}")
            continue

        if new_sid == r["id"]:
            tally["identical"] += 1
            status = "identical"
        else:
            # bytes differ - do the facts?
            before = stored_claims(con, r["id"])
            parser = parsers.get(name)
            after: dict[tuple, object] = {}
            reparsed = False
            if parser is not None:
                try:
                    for item in parser.reparse(body, new_sid, r["url"]):
                        after[key(item)] = value(item)
                    reparsed = True
                except NotImplementedError as exc:
                    news.append(f"NO-REPARSE {name}: {exc}")
                except Exception as exc:  # noqa: BLE001
                    news.append(f"PARSE-FAIL {r['url']}: {exc}")

            if not reparsed:
                # Without a successful re-parse we cannot tell a real change from
                # a parser gap, and reporting every stored fact as deleted would
                # be worse than saying nothing. Count it and move on.
                tally["unverifiable"] += 1
                continue

            changed = {k: (before[k], after[k]) for k in before.keys() & after.keys()
                       if before[k] != after[k]}
            gone = before.keys() - after.keys()
            added = after.keys() - before.keys()

            if changed or gone or added:
                tally["changed"] += 1
                status = "CHANGED"
                lines = [f"CHANGED  {r['url']}",
                         f"         archived {r['fetched_at']}  sha {r['sha256'][:12]}"]
                for k, (b, a) in list(changed.items())[:6]:
                    lines.append(f"         {k[0]} @ {k[1]}: {b!r} -> {a!r}")
                for k in list(gone)[:4]:
                    lines.append(f"         GONE  {k[0]} @ {k[1]} (was {before[k]!r})")
                for k in list(added)[:4]:
                    lines.append(f"         NEW   {k[0]} @ {k[1]} = {after[k]!r}")
                news.append("\n".join(lines))
            else:
                tally["cosmetic"] += 1
                status = "cosmetic"

        if args.wayback:
            w = wayback_witness(r["url"], session)
            tally["wayback_seen" if w else "wayback_absent"] += 1
            if w and status in ("CHANGED",):
                news.append(f"         independent snapshot: {w}")

        if i % 10 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)}  " + "  ".join(
                f"{k}={v}" for k, v in sorted(tally.items())), flush=True)

    con.commit()
    con.close()

    print("\n== result ==")
    for k in ("identical", "cosmetic", "changed", "missing",
              "wayback_seen", "wayback_absent"):
        if tally.get(k):
            print(f"  {k:16} {tally[k]}")
    if news:
        print("\n== worth a human look ==")
        for n in news[:30]:
            print("  " + n.replace("\n", "\n  "))
    else:
        print("\n  nothing changed that would alter a published claim.")
    print(f"\n  checked at {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
