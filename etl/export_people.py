"""Export the claim store into web bundles, and join it to the country data.

    python -m etl.export_people

This is where the two halves of the project meet. On its own, "this MP declared
assets of 8 crore" means very little to a reader with no sense of scale. Placed
next to the national series for the same years, it becomes answerable: did this
person's declared wealth grow faster or slower than the economy they helped govern?

That comparison is computed here and shipped with each person, as a ratio with both
components shown. It is explicitly NOT a score and NOT an accusation - the UI shows
the multiple alongside the national multiple and says plainly what it does and does
not mean.

Stock vs flow is preserved. India's affidavits give an asset TOTAL at a date (a
stock, comparable to GDP per capita levels). The UK register gives individual
PAYMENTS (flows, not comparable to a stock). Only stock-type claims get a growth
comparison; flows are listed and summed per year, never trended against GDP.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_DB = ROOT / "data" / "processed" / "claims.db"
PF_DB = ROOT / "data" / "processed" / "pf.db"
OUT = ROOT / "web" / "public" / "data" / "people"

# Currency per country, for display only. No FX conversion is ever done: converting
# a 2004 rupee asset declaration to dollars would invent precision that isn't there.
CURRENCY = {"IND": "INR", "GBR": "GBP"}

STOCK_PREDICATES = {"declared_assets", "declared_liabilities"}
FLOW_PREDICATES = {"outside_earnings"}


def national_series(indicator: str = "gdp_pc") -> dict[str, dict[int, float]]:
    """{iso3: {year: value}} for the national context comparison."""
    con = sqlite3.connect(f"file:{PF_DB}?mode=ro", uri=True)
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for iso3, year, value in con.execute(
        "SELECT iso3, year, value FROM observations WHERE indicator = ?", (indicator,)
    ):
        out[iso3][int(year)] = float(value)
    con.close()
    return out


def growth_context(
    points: list[tuple[int, float]], gdp: dict[int, float]
) -> dict | None:
    """Compare a person's declared-asset growth with national GDP per capita growth.

    Returns None unless there are at least two dated points AND national data for
    both endpoints - a comparison with a missing denominator is worse than none.
    """
    pts = sorted((y, v) for y, v in points if v and v > 0)
    if len(pts) < 2:
        return None
    (y0, v0), (y1, v1) = pts[0], pts[-1]
    if y1 <= y0:
        return None
    g0, g1 = gdp.get(y0), gdp.get(y1)
    if not g0 or not g1 or g0 <= 0:
        return None

    return {
        "from_year": y0,
        "to_year": y1,
        "years": y1 - y0,
        "declared_multiple": round(v1 / v0, 2),
        "national_multiple": round(g1 / g0, 2),
        "from_value": v0,
        "to_value": v1,
    }


def _conflicts(claims: list[dict]) -> list[dict]:
    """Facts asserted differently by different documents.

    Reported, never resolved. Two affidavits from the same person in the same year
    can legitimately differ; deciding which is true is not this tool's job.
    """
    seen: dict[tuple, set] = defaultdict(set)
    for c in claims:
        if c["predicate"] == "possible_duplicate_of":
            continue
        val = c["num"] if c["num"] is not None else c["text"]
        seen[(c["predicate"], c["as_of"])].add(val)
    return [
        {"predicate": k[0], "as_of": k[1], "values": sorted(map(str, v))}
        for k, v in seen.items()
        if len(v) > 1
    ]


def build() -> dict:
    if not CLAIMS_DB.exists():
        raise SystemExit(f"no claim store at {CLAIMS_DB} - run `python -m ingest.run` first")

    con = sqlite3.connect(f"file:{CLAIMS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    gdp_by_country = national_series()

    people = {
        r["id"]: {
            "id": r["id"],
            "name": r["full_name"],
            "country": r["country"],
            "currency": CURRENCY.get(r["country"]),
        }
        for r in con.execute("SELECT id, full_name, country FROM persons")
    }

    # A person can hold or contest more than one office. In India a candidate may
    # legally stand in two constituencies at once - Rahul Gandhi did in 2024, and
    # filed two affidavits that disagree with each other. Flattening offices onto
    # the person record would silently drop one seat and pick a winner between two
    # genuine source documents, which is exactly what the claim store exists to
    # avoid. So offices are kept as a list.
    for r in con.execute(
        "SELECT person_id, jurisdiction, title, constituency, region, party, won"
        " FROM offices ORDER BY id"
    ):
        p = people.get(r["person_id"])
        if p is None:
            continue
        p.setdefault("offices", []).append(
            {
                "jurisdiction": r["jurisdiction"],
                "title": r["title"],
                "constituency": r["constituency"],
                "region": r["region"],
                "party": r["party"],
                "won": bool(r["won"]) if r["won"] is not None else None,
            }
        )

    for p in people.values():
        offices = p.get("offices") or []
        first = offices[0] if offices else {}
        # Convenience fields for the list view; `offices` remains authoritative.
        p.update(
            jurisdiction=first.get("jurisdiction"),
            title=first.get("title"),
            constituency=first.get("constituency"),
            region=first.get("region"),
            party=first.get("party"),
            won=first.get("won"),
            n_offices=len(offices),
        )

    claims_by_person: dict[int, list[dict]] = defaultdict(list)
    for r in con.execute(
        """SELECT c.person_id, c.predicate, c.value_num, c.value_text, c.unit,
                  c.currency, c.as_of, c.confidence, c.note, c.extractor,
                  s.url, s.sha256, s.fetched_at, s.archive_path
           FROM claims c JOIN sources s ON s.id = c.source_id
           WHERE c.person_id IS NOT NULL
           ORDER BY c.as_of"""
    ):
        claims_by_person[r["person_id"]].append(
            {
                "predicate": r["predicate"],
                "num": r["value_num"],
                "text": r["value_text"],
                "unit": r["unit"],
                "currency": r["currency"],
                "as_of": r["as_of"],
                "confidence": r["confidence"],
                "note": r["note"],
                "extractor": r["extractor"],
                # provenance, shipped with every single fact
                "src_url": r["url"],
                "src_sha": r["sha256"][:12],
                "src_fetched": r["fetched_at"],
                "src_archive": r["archive_path"],
            }
        )
    con.close()

    OUT.mkdir(parents=True, exist_ok=True)
    index = []

    for pid, person in people.items():
        claims = claims_by_person.get(pid, [])
        if not claims:
            continue

        assets = [
            (int(c["as_of"][:4]), c["num"])
            for c in claims
            if c["predicate"] == "declared_assets" and c["num"] and c["as_of"]
        ]
        assets = sorted(dict(assets).items())

        flows = defaultdict(float)
        for c in claims:
            if c["predicate"] in FLOW_PREDICATES and c["num"] and c["as_of"]:
                flows[int(c["as_of"][:4])] += c["num"]

        ctx = growth_context(assets, gdp_by_country.get(person["country"], {}))

        record = {
            **person,
            "claims": claims,
            "asset_points": assets,
            "flow_by_year": sorted(flows.items()),
            "context": ctx,
            "n_claims": len(claims),
            "sources": sorted({c["src_url"] for c in claims}),
            # facts where two source documents disagree, surfaced not resolved
            "conflicts": _conflicts(claims),
        }
        (OUT / f"{pid}.json").write_text(
            json.dumps(record, separators=(",", ":")), encoding="utf-8"
        )

        cases = next(
            (c["num"] for c in claims if c["predicate"] == "criminal_cases_declared"),
            None,
        )
        index.append(
            {
                "id": pid,
                "name": person["name"],
                "country": person["country"],
                "party": person.get("party"),
                "constituency": person.get("constituency"),
                "region": person.get("region"),
                "currency": person.get("currency"),
                "latest_assets": assets[-1][1] if assets else None,
                "asset_points": len(assets),
                "flow_total": round(sum(flows.values()), 2) if flows else None,
                "cases": cases,
                "declared_multiple": ctx["declared_multiple"] if ctx else None,
                "national_multiple": ctx["national_multiple"] if ctx else None,
                "span": f"{ctx['from_year']}-{ctx['to_year']}" if ctx else None,
                "n_claims": len(claims),
                "n_offices": person.get("n_offices", 0),
                "n_conflicts": len(_conflicts(claims)),
            }
        )

    # --- names the resolver refused to merge on its own ---------------------
    # These are pairs that scored between the review and auto-merge thresholds.
    # The resolver deliberately created TWO person records rather than risk
    # attributing one person's assets or criminal cases to another. A human has
    # to decide, so the pairs are shipped to the UI rather than buried in a table.
    con2 = sqlite3.connect(f"file:{CLAIMS_DB}?mode=ro", uri=True)
    con2.row_factory = sqlite3.Row
    review = []
    for r in con2.execute(
        """SELECT c.person_id, c.value_text AS other_id, c.confidence, c.note,
                  p.full_name, p.country
           FROM claims c JOIN persons p ON p.id = c.person_id
           WHERE c.predicate = 'possible_duplicate_of'"""
    ):
        try:
            other = con2.execute(
                "SELECT id, full_name FROM persons WHERE id = ?", (int(r["other_id"]),)
            ).fetchone()
        except (TypeError, ValueError):
            continue
        if not other:
            continue
        review.append({
            "a": {"id": r["person_id"], "name": r["full_name"]},
            "b": {"id": other["id"], "name": other["full_name"]},
            "country": r["country"],
            "score": r["confidence"],
            "note": r["note"],
        })
    con2.close()

    index.sort(key=lambda r: (r["country"], r["name"]))
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "people": index,
        "countries": sorted({r["country"] for r in index}),
        "review_queue": review,
        "caveats": {
            "not_a_score": (
                "These are declared figures from official filings, shown with their "
                "sources. Declared wealth rises with property prices, inheritance, "
                "business income and inflation. A high multiple is a reason to ask a "
                "question, not an accusation."
            ),
            "cases": (
                "Criminal cases shown are self-declared PENDING cases from the "
                "candidate's own affidavit. They are not convictions."
            ),
            "review": (
                "These name pairs scored close enough to be the same person, but "
                "not close enough for a machine to merge them safely. They are "
                "shown as separate people until a human decides. Merging two "
                "different politicians would attribute one person's assets and "
                "pending cases to another, which is why the default is to split."
            ),
            "stock_vs_flow": (
                "India's figures are total declared assets at a date. The UK's are "
                "individual registered payments. They are different kinds of number "
                "and are never plotted on the same axis."
            ),
        },
    }
    (OUT / "index.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def main() -> int:
    meta = build()
    idx = meta["people"]
    print(f"\n  people exported     {len(idx):,}")
    print(f"  countries           {', '.join(meta['countries'])}")
    print(f"  review queue        {len(meta['review_queue'])}")
    trended = [r for r in idx if r["declared_multiple"]]
    print(f"  with asset trend    {len(trended):,}")
    if trended:
        trended.sort(key=lambda r: r["declared_multiple"] / max(r["national_multiple"], 0.01),
                     reverse=True)
        print("\n  largest declared-growth multiples relative to national GDP/capita:")
        print("  (context for a question, NOT a finding of wrongdoing)")
        for r in trended[:8]:
            print(
                f"    {r['name'][:30]:32} {r['span']}  "
                f"declared x{r['declared_multiple']:>7,.1f}  "
                f"national x{r['national_multiple']:.2f}"
            )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
