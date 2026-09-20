"""Full-dataset audit: check every invariant against every record.

    python tools/audit.py

WHY THIS EXISTS
---------------
Defects in this project kept surfacing one at a time, in whatever feature
happened to be under the cursor, and they were all the SAME defect: code that
picked an arbitrary element out of a list.

    used[used.length - 1]      cited the 2019 affidavit for a 2024 figure
    vals[0]                    compared a 2019 age against a 2024 one
    offices[0]                 showed a defector's old party as current
    c[c.length - 1]            called an unsorted array's last element "latest"
    one arbitrary SPARQL row   published a former party as the current one

Every one of them was correct while each person had exactly one claim per
predicate. Harvesting the 2019 affidavits and two more witnesses gave people a
second element, and they all broke at once - silently, because a plausible wrong
answer looks exactly like a right one.

Spot-checking a page cannot find that. `verify.py` checks that the ETHICS
commitments hold; `tests/test_parsers.py` checks parsers against hand-read
fixtures. Neither asks whether the 2,196 shipped bundles are internally
consistent with the claim store they came from. This does, for every record,
and it is meant to be run before every push.

A check here must be a statement that is true of ALL data, not a sample.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CLAIMS_DB = ROOT / "data" / "processed" / "claims.db"
PEOPLE = ROOT / "web" / "public" / "data" / "people"
WEBDATA = ROOT / "web" / "public" / "data"

findings: list[tuple[str, str, str]] = []   # (severity, area, detail)
checks_run = 0


def fail(area: str, detail: str) -> None:
    findings.append(("FAIL", area, detail))


def warn(area: str, detail: str) -> None:
    findings.append(("WARN", area, detail))


def ok(area: str, detail: str = "") -> None:
    global checks_run
    checks_run += 1
    print(f"  ok    {area}{f'  - {detail}' if detail else ''}")


def report(area: str, bad: list, detail_ok: str, limit: int = 3, severity=fail) -> None:
    """One invariant, over the whole dataset."""
    global checks_run
    checks_run += 1
    if bad:
        severity(area, f"{len(bad)} case(s), e.g. " + "; ".join(str(b) for b in bad[:limit]))
        print(f"  {'FAIL' if severity is fail else 'WARN'}  {area}  - {len(bad)} case(s)")
        for b in bad[:limit]:
            print(f"          {b}")
    else:
        print(f"  ok    {area}{f'  - {detail_ok}' if detail_ok else ''}")


# ---------------------------------------------------------------- claim store
def audit_claim_store():
    print("\n[A] Claim store: every claim, every source.")
    if not CLAIMS_DB.exists():
        warn("claim store", "no claims.db")
        return None
    con = sqlite3.connect(f"file:{CLAIMS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    from ingest.schema import PREDICATES

    rows = con.execute(
        """SELECT c.id, c.person_id, c.predicate, c.value_num, c.value_text,
                  c.as_of, c.unit, c.currency, c.extractor, c.note,
                  s.url, s.sha256, s.archive_path, p.country, p.full_name
           FROM claims c
           JOIN sources s ON s.id = c.source_id
           LEFT JOIN persons p ON p.id = c.person_id"""
    ).fetchall()
    print(f"  {len(rows):,} claims joined to their source document")

    today = date.today().isoformat()
    report("every predicate is in the closed vocabulary",
           sorted({r["predicate"] for r in rows} - PREDICATES),
           f"{len(PREDICATES)} predicates")
    report("no claim is dated in the future",
           [f"{r['predicate']} {r['as_of']} ({r['extractor']})"
            for r in rows if r["as_of"] and r["as_of"] > today],
           "vs " + today, severity=warn)
    report("no claim is dated absurdly early",
           [f"{r['predicate']} {r['as_of']}" for r in rows
            if r["as_of"] and r["as_of"] < "1900-01-01"], "")
    # `possible_duplicate_of` is the resolver's note to a human - "these two
    # records may be one person" - not a dated fact about the world, so it is
    # the one predicate that legitimately has no as_of.
    report("every factual claim carries a date",
           [f"{r['predicate']} ({r['extractor']})" for r in rows
            if not r["as_of"] and r["predicate"] != "possible_duplicate_of"],
           "possible_duplicate_of excluded: a review marker, not a dated fact")
    report("no monetary claim is negative",
           [f"{r['full_name']}: {r['predicate']}={r['value_num']}" for r in rows
            if r["value_num"] is not None and r["value_num"] < 0
            and r["predicate"] not in ("age",)], "")
    report("no numeric claim is NaN or infinite",
           [f"{r['predicate']} ({r['extractor']})" for r in rows
            if r["value_num"] is not None
            and (math.isnan(r["value_num"]) or math.isinf(r["value_num"]))], "")
    report("every claim has a non-empty source URL",
           [f"claim {r['id']} ({r['extractor']})" for r in rows if not r["url"]], "")
    report("no source URL still contains a credential",
           [r["url"][:70] for r in rows
            if r["url"] and ("key=AIza" in r["url"] or "api_key=" in r["url"])], "")

    # Ages must be plausible for a sitting legislator.
    ages = [r for r in rows if r["predicate"] == "age" and r["value_num"] is not None]
    report("declared ages are plausible (18-110)",
           [f"{r['full_name']}: {r['value_num']:g} ({r['extractor']})" for r in ages
            if not (18 <= r["value_num"] <= 110)], f"{len(ages):,} age claims")

    # Currency must match the country it describes.
    expect = {"IND": "INR", "GBR": "GBP"}
    report("currency matches the person's country",
           [f"{r['full_name']} ({r['country']}): {r['currency']}" for r in rows
            if r["currency"] and r["country"] in expect
            and r["currency"] != expect[r["country"]]], "")

    # The caveats that make a claim honest.
    report("every criminal-case claim says NOT convictions",
           [str(r["id"]) for r in rows if r["predicate"] == "criminal_cases_declared"
            and "NOT convictions" not in (r["note"] or "")], "")
    fc = [r for r in rows if r["predicate"] == "factcheck_published"]
    if fc:
        report("every relayed fact-check admits it was matched by name",
               [r["full_name"] for r in fc if "MATCHED BY NAME" not in (r["note"] or "")],
               f"{len(fc):,} reviews")
    return con


# ------------------------------------------------------- the shipped bundles
def audit_bundles(con):
    print("\n[B] Shipped bundles: 2,196 files against the store they came from.")
    index_file = PEOPLE / "index.json"
    if not index_file.exists():
        warn("bundles", "no export yet")
        return
    index = json.loads(index_file.read_text(encoding="utf-8"))
    listed = {p["id"]: p for p in index["people"]}
    on_disk = {int(f.stem) for f in PEOPLE.glob("*.json") if f.stem.isdigit()}
    print(f"  {len(listed):,} listed in index.json, {len(on_disk):,} person files on disk")

    report("every person in the index has a bundle file",
           sorted(set(listed) - on_disk)[:5], "")
    report("every bundle file is listed in the index",
           sorted(on_disk - set(listed))[:5], "")

    if con:
        db_ids = {r[0] for r in con.execute(
            "SELECT DISTINCT person_id FROM claims WHERE person_id IS NOT NULL")}
        report("every person with claims was exported",
               sorted(db_ids - set(listed))[:5], f"{len(db_ids):,} people hold claims")

    bad_id, bad_count, bad_src, bad_pts, bad_util, bad_conflict = [], [], [], [], [], []
    bad_currency, bad_party, bad_nan = [], [], []
    from etl.export_people import SINGLE_VALUED

    for pid, listing in listed.items():
        f = PEOPLE / f"{pid}.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("id") != pid:
            bad_id.append(f"{f.name} holds id {d.get('id')}")
        claims = d.get("claims") or []
        if d.get("n_claims") != len(claims) + (d.get("claims_omitted") or 0):
            bad_count.append(
                f"{d.get('name')}: n_claims={d.get('n_claims')} but "
                f"{len(claims)}+{d.get('claims_omitted') or 0} present")
        for c in claims:
            if not c.get("src_url"):
                bad_src.append(f"{d.get('name')}: {c.get('predicate')} has no src_url")
                break
            v = c.get("num")
            if v is not None and (math.isnan(v) or math.isinf(v)):
                bad_nan.append(f"{d.get('name')}: {c.get('predicate')}")
                break
        # asset points: sorted, positive, one per year
        pts = d.get("asset_points") or []
        years = [y for y, _ in pts]
        if years != sorted(years) or len(years) != len(set(years)):
            bad_pts.append(f"{d.get('name')}: years {years}")
        if any(v is None or v <= 0 for _, v in pts):
            bad_pts.append(f"{d.get('name')}: non-positive asset value")
        # MPLADS utilisation must equal spent/allocated
        pm = d.get("public_money") or {}
        if pm.get("allocated") and pm.get("utilisation") is not None:
            calc = 100 * (pm.get("spent") or 0) / pm["allocated"]
            if abs(calc - pm["utilisation"]) > 0.6:
                bad_util.append(
                    f"{d.get('name')}: shows {pm['utilisation']}% but "
                    f"{pm.get('spent')}/{pm['allocated']} = {calc:.1f}%")
        # a conflict must never be a predicate that legitimately repeats
        for k in d.get("conflicts") or []:
            if k.get("predicate") not in SINGLE_VALUED:
                bad_conflict.append(f"{d.get('name')}: {k.get('predicate')}")
                break
        # the headline party must match the most recent office
        offices = d.get("offices") or []
        if offices and d.get("party") and offices[0].get("party") != d.get("party"):
            bad_party.append(f"{d.get('name')}: shows {d.get('party')}, "
                             f"first office {offices[0].get('party')}")
        if d.get("country") == "IND" and d.get("currency") not in (None, "INR"):
            bad_currency.append(f"{d.get('name')}: {d.get('currency')}")

    report("every bundle's id matches its filename", bad_id, f"{len(listed):,} files")
    report("n_claims equals what the bundle actually carries", bad_count, "")
    report("every shipped claim carries its source URL", bad_src, "")
    report("no NaN or Infinity reached a bundle", bad_nan, "")
    report("asset points are year-sorted, unique and positive", bad_pts, "")
    report("MPLADS utilisation equals completed / allocated", bad_util, "")
    report("no conflict is a predicate that can repeat on one date",
           bad_conflict, f"{len(SINGLE_VALUED)} single-valued predicates")
    report("the headline party is the most recent term's party", bad_party, "")
    report("Indian bundles carry rupees", bad_currency, "")


# ------------------------------------------------------------- country data
def audit_country_data():
    print("\n[C] Country data and metadata.")
    meta_f = WEBDATA / "meta.json"
    if not meta_f.exists():
        warn("country data", "no meta.json")
        return
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    inds = meta.get("indicators", [])
    report("every indicator explains itself",
           [i["id"] for i in inds
            if not all(str(i.get(k, "")).strip() for k in ("plain", "measures", "blindspots"))],
           f"{len(inds)} indicators")

    # A series file is {iso3: [[year, value], ...]}, one file per indicator.
    # The first version of this check assumed a flat list, found nothing, and
    # reported all 23 files as "empty" - a false pass dressed as a warning,
    # which left every observation in the country half of the project
    # unvalidated. A check that cannot fail is worse than no check.
    series = WEBDATA / "series"
    files = sorted(series.glob("*.json")) if series.exists() else []
    bad_year, bad_val, bad_shape, empty, dupes, unsorted_ = [], [], [], [], [], []
    this_year = date.today().year
    countries_seen, observations = set(), 0

    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            bad_shape.append(f"{f.name}: not valid JSON ({exc})")
            continue
        if not isinstance(doc, dict) or not doc:
            bad_shape.append(f"{f.name}: expected a non-empty iso3 map")
            continue
        for iso3, points in doc.items():
            countries_seen.add(iso3)
            if not isinstance(points, list) or not points:
                empty.append(f"{f.name}:{iso3}")
                continue
            years = []
            for row in points:
                if not (isinstance(row, (list, tuple)) and len(row) >= 2):
                    bad_shape.append(f"{f.name}:{iso3} row {row!r}")
                    break
                y, v = row[0], row[1]
                observations += 1
                years.append(y)
                # The Maddison Project genuinely publishes estimates for year 1
                # CE, 730 and 1000, so the lower bound is 1, not a modern year.
                if not isinstance(y, (int, float)) or not (1 <= y <= this_year + 1):
                    bad_year.append(f"{f.name}:{iso3} year {y!r}")
                    break
                if v is None or (isinstance(v, float)
                                 and (math.isnan(v) or math.isinf(v))):
                    bad_val.append(f"{f.name}:{iso3} value {v!r} at {y}")
                    break
            if years != sorted(years):
                unsorted_.append(f"{f.name}:{iso3}")
            if len(years) != len(set(years)):
                dupes.append(f"{f.name}:{iso3}")

    print(f"  {len(files)} indicators x {len(countries_seen)} countries, "
          f"{observations:,} observations")
    report("every series file is an iso3 map of [year, value] rows", bad_shape, "")
    report("every series year is within range", bad_year, "")
    report("no series value is null, NaN or infinite", bad_val, "")
    report("every country's series is year-sorted", unsorted_, "")
    report("no country's series repeats a year", dupes, "")
    report("no country's series is empty", empty, "", severity=warn)


def main() -> int:
    print("=" * 72)
    print("FULL-DATASET AUDIT - every invariant against every record")
    print("=" * 72)
    con = audit_claim_store()
    audit_bundles(con)
    audit_country_data()
    if con:
        con.close()

    print("\n" + "=" * 72)
    fails = [f for f in findings if f[0] == "FAIL"]
    warns = [f for f in findings if f[0] == "WARN"]
    print(f"{checks_run} invariants checked  |  {len(fails)} failed  |  {len(warns)} warnings")
    for sev, area, detail in findings:
        print(f"  {sev}  {area}\n        {detail}")
    if not findings:
        print("\nEverything checked is internally consistent.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
