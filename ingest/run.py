"""Run an extractor.

    python -m ingest.run myneta --limit 15
    python -m ingest.run --report
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .extractors.myneta import MyNeta
from .extractors.ukparliament import UKParliament
from .schema import connect

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "claims.db"

EXTRACTORS = {"myneta": MyNeta, "ukparliament": UKParliament}


def report(con) -> None:
    q = con.execute
    print("\n== claim store ==")
    for label, sql in [
        ("sources archived", "SELECT COUNT(*) FROM sources"),
        ("people", "SELECT COUNT(*) FROM persons"),
        ("offices", "SELECT COUNT(*) FROM offices"),
        ("claims", "SELECT COUNT(*) FROM claims"),
        ("archived bytes", "SELECT COALESCE(SUM(bytes),0) FROM sources"),
    ]:
        print(f"  {label:18} {q(sql).fetchone()[0]:,}")

    print("\n  claims by predicate:")
    for r in q(
        "SELECT predicate, COUNT(*) n, COUNT(DISTINCT person_id) p "
        "FROM claims GROUP BY predicate ORDER BY n DESC"
    ):
        print(f"    {r['predicate']:28} {r['n']:>5}  ({r['p']} people)")

    orphans = q("SELECT COUNT(*) FROM claims WHERE source_id IS NULL").fetchone()[0]
    print(f"\n  claims with no source document: {orphans}   <- must be 0")

    dupes = q("SELECT COUNT(*) FROM conflicts").fetchone()[0]
    print(f"  facts where sources disagree:   {dupes}")

    print("\n  people with a declared-asset time series (2+ dated points):")
    rows = q(
        """SELECT p.full_name, COUNT(DISTINCT c.as_of) pts,
                  MIN(c.as_of) lo, MAX(c.as_of) hi,
                  MIN(c.value_num) vlo, MAX(c.value_num) vhi
           FROM claims c JOIN persons p ON p.id = c.person_id
           WHERE c.predicate = 'declared_assets' AND c.as_of != ''
           GROUP BY c.person_id HAVING pts >= 2
           ORDER BY pts DESC, vhi DESC LIMIT 8"""
    ).fetchall()
    if not rows:
        print("    (none yet)")
    for r in rows:
        print(
            f"    {r['full_name'][:34]:36} {r['pts']} points "
            f"{r['lo'][:4]}->{r['hi'][:4]}  "
            f"Rs {r['vlo']/1e7:,.2f}cr -> Rs {r['vhi']/1e7:,.2f}cr"
        )
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("extractor", nargs="?", choices=sorted(EXTRACTORS))
    ap.add_argument("--limit", type=int, default=None, help="max records to fetch")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--db", default=str(DB), help="claim store path")
    args = ap.parse_args()

    con = connect(args.db)

    if args.extractor:
        cls = EXTRACTORS[args.extractor]
        ex = cls(con, delay=args.delay, limit=args.limit)
        print(f"\nrunning {ex.name} v{ex.version} against {ex.publisher}")
        print(f"  rate limit: {args.delay}s between requests")
        stats = ex.run()
        print(f"  {stats}")
        for e in stats.errors[:6]:
            print(f"    ! {e}")

    report(con)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
