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
import re
import sqlite3
from collections import defaultdict

from ingest.resolve import _near

# The UI's conflict panel and the corroboration report must agree about what a
# disagreement IS. Sharing this code is the point: comparing raw values flagged
# 497 of 1,284 people as having conflicting sources, when almost all of those
# were "BJP" vs "Bharatiya Janata Party", an affidavit age against a current
# age, or two education taxonomies. Publishing that to readers would be the
# 9.4%-agreement mistake, shown one politician at a time.
from etl.corroborate import (
    AGE_TOLERANCE_YEARS,
    education_span,
    learn_party_aliases,
    party_key,
)
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
# Public money is a different category from personal wealth and must never be
# mixed into the same figure. An MPLADS allocation is the public's money that a
# member may direct; declared assets are the member's own. Showing them in one
# number would be the single most misleading thing this app could do.
PUBLIC_MONEY = {"budget_allocated", "budget_spent", "contract_awarded"}
ACTIVITY = {"attendance_pct", "debates_participated", "questions_asked",
            "private_member_bills"}
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


def _seat_key(name: str | None) -> str:
    """Normalise a constituency name to bare letters.

    Reservation markers carry no identifying information - "JAMUI" and
    "JAMUI(SC)" are one seat - so they are stripped. Three traps here, all of
    which this function previously fell into:

      * a lazy, all-optional paren pattern can match the EMPTY string, so re.sub
        inserts its replacement at every position and shreds the name.

      * stripping a bare "sc"/"st" anywhere in the name is not safe, and the
        word-boundary version is not safe either. It was written once as
        `\\b(sc|st)\\b` and reached this file with the escapes eaten, as two
        literal backspace bytes, which quietly matched nothing at all. Repairing
        it would have been worse than the bug: every Indian reservation marker
        in this data is PARENTHESISED - 126 of them, none bare - so the rule
        earns nothing here, while a working version would rename the UK's
        "St Albans" to "Albans" and reduce South Carolina to a single seat. The
        rule is gone rather than fixed.

      * the final filter must keep digits. "SC-02" and "SC-06" are two seats.
    """
    import re as _re
    s = (name or "").lower()
    s = _re.sub(r"\([^)]*\)?", " ", s)          # drop "(sc)" and unclosed "(sc"
    # Digits are part of the name, not punctuation. Dropping them turned every
    # South Carolina district - SC-02, SC-04, SC-06 - into the single key "sc",
    # so a member redistricted from one seat to another would have shown one
    # seat instead of two.
    return _re.sub(r"[^a-z0-9]", "", s)


def _same_seat(a: str, b: str) -> bool:
    """Do two constituency spellings denote the same seat?

    Publishers disagree on spelling and truncate long names:

        MyNeta  BARAMULLA                    MPLADS  BARAMULLAH
        MyNeta  NAINITAL-UDHAM SINGH NAGAR   MPLADS  NAINITAL UDHAM SINGH NAG.

    Exact comparison made one seat look like several and badged members as
    having contested seats they never stood in. Rahul Gandhi's WAYANAD and
    RAE BARELI are genuinely two seats and must stay two, so this stays strict
    about anything that is not a spelling variant or a truncation.
    """
    if not a or not b:
        return a == b
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 6 and long_.startswith(short):
        return True                              # truncated ("...nag" / "...nagar")
    return _near(a, b)                           # one-character spelling variant


# A conflict only makes sense for a fact that should have ONE value per person
# per date. A politician has one age and one declared asset total on a given
# date, so two different values are a disagreement worth showing.
#
# These predicates are the opposite: many distinct instances legitimately share a
# date. Four MPLADS works completed on the same day are four works, not four
# rival claims about one work, and a UK member can register several payments on
# one date. Grouping them by (predicate, date) made the live site announce
# "sources disagree on 20 facts" for a member whose sources agreed completely -
# a false alarm in exactly the place the app asks readers to trust it most.
#
# This set has to grow whenever a new multi-instance predicate is added, and it
# was missed twice. `parliamentary_question` and `disclosure_filed` arrived with
# the Zenodo and US House extractors and were not declared here, so an MP who
# tabled three questions on one sitting day, or a Representative who filed an
# original report and an amendment on one date, was announced as having sources
# that disagree. That accounted for 1,772 of 1,935 flagged facts - 90% of the
# warning was noise. Check [9] in verify.py now fails if one predicate dominates
# the conflict count like that again.
MULTI_INSTANCE = {
    "contract_awarded",
    "outside_earnings",
    "registered_interest",
    "electoral_bonds_received",
    "electoral_bonds_purchased",
    "possible_duplicate_of",
    # Several questions can be tabled on one sitting day, and each is its own
    # question rather than a rival account of one question.
    "parliamentary_question",
    # A US filer can lodge an original report and an amendment the same day.
    "disclosure_filed",
}


def _term_year(jurisdiction: str | None) -> int:
    """The year in a jurisdiction like "IN/LokSabha2024", for ordering terms.

    Jurisdictions without a year (US/Congress, UK/Commons) sort as 0, which puts
    them after any dated term - they only ever appear alongside each other, so
    the relative order among them is unchanged.
    """
    m = re.search(r"(19|20)\d{2}", jurisdiction or "")
    return int(m.group(0)) if m else 0


def _same_value(predicate: str, values: list, aliases: dict) -> bool:
    """Are these reported values actually saying the same thing?"""
    if predicate == "party_affiliation":
        return len({party_key(str(v), aliases) for v in values}) == 1
    if predicate == "education_level":
        spans = [education_span(v) for v in values]
        if any(sp is None for sp in spans):
            return len({str(v).strip().lower() for v in values}) == 1
        return max(sp[0] for sp in spans) <= min(sp[1] for sp in spans)
    if predicate == "age":
        try:
            nums = [float(v) for v in values]
        except (TypeError, ValueError):
            return len(set(values)) == 1
        return max(nums) - min(nums) <= AGE_TOLERANCE_YEARS
    return len({str(v).strip().lower() for v in values}) == 1


def _conflicts(claims: list[dict], aliases: dict) -> list[dict]:
    """Facts asserted differently by different documents.

    Reported, never resolved. Two affidavits from the same person in the same year
    can legitimately differ; deciding which is true is not this tool's job.
    """
    seen: dict[tuple, set] = defaultdict(set)
    for c in claims:
        if c["predicate"] in MULTI_INSTANCE:
            continue
        val = c["num"] if c["num"] is not None else c["text"]
        seen[(c["predicate"], c["as_of"])].add(val)
    return [
        {"predicate": k[0], "as_of": k[1], "values": sorted(map(str, v))}
        for k, v in seen.items()
        if len(v) > 1 and not _same_value(k[0], list(v), aliases)
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
        # Three extractors reporting the same seat is not three seats. Dedupe on
        # (jurisdiction, constituency) so the "seats contested" badge means what
        # it says - Rahul Gandhi really did contest two, and that must stay
        # visible, but Arun Kumar Sagar's one seat was being shown as three.
        seen_seat: list[tuple] = []
        deduped = []
        # Most recent term first, then offices that name a seat: an extractor
        # that failed to parse the constituency should not manufacture an extra
        # "seat contested".
        #
        # The recency sort is not cosmetic. `offices[0]` becomes the party shown
        # in the list view, and it used to be whichever row was INSERTED first -
        # correct only because the 2024 affidavits happened to be harvested
        # before the 2019 ones. Rebuilding the store in a different order would
        # have relabelled every MP who has changed party with the party they
        # left.
        raw_offices = sorted(
            (p.get("offices") or []),
            key=lambda o: (
                -_term_year(o.get("jurisdiction")),
                0 if (o.get("constituency") or "").strip() else 1,
            ),
        )
        for o in raw_offices:
            juris, seat = o.get("jurisdiction"), _seat_key(o.get("constituency"))
            if not seat and any(j == juris for j, _ in seen_seat):
                continue
            if any(j == juris and _same_seat(seat, k) for j, k in seen_seat):
                continue
            seen_seat.append((juris, seat))
            deduped.append(o)
        p["offices"] = deduped
        offices = deduped
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

    # Learn party aliases once across the whole dataset, so "TDP" and "Telugu
    # Desam Party" are known to be the same before any person is compared. The
    # date travels with the value: two spellings only count as evidence of a
    # synonym when they describe the same person in the same year, or every MP
    # who has ever crossed the floor teaches the map that their old party and
    # their new one are the same thing.
    party_forms: dict[int, set] = defaultdict(set)
    for pid, cl in claims_by_person.items():
        for c in cl:
            if c["predicate"] == "party_affiliation" and c["text"]:
                party_forms[pid].add((c["text"], c["as_of"]))
    party_aliases = learn_party_aliases(party_forms)

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

        # --- public money directed by this member (MPLADS) -------------------
        def latest(pred):
            hits = [c for c in claims if c["predicate"] == pred and c["num"] is not None]
            return hits[-1]["num"] if hits else None

        allocated = latest("budget_allocated")
        spent = latest("budget_spent")
        works = [c for c in claims if c["predicate"] == "contract_awarded"]
        public = None
        if allocated or spent:
            public = {
                "allocated": allocated,
                "spent": spent,
                "utilisation": (round(100 * spent / allocated, 1)
                                if allocated and spent else None),
                "works": len(works),
                # a sample only: a member can have hundreds of works and the
                # bundle has to stay loadable
                "largest_works": [
                    {"amount": w["num"], "as_of": w["as_of"], "detail": w["note"]}
                    for w in sorted(works, key=lambda w: -(w["num"] or 0))[:12]
                ],
            }

        # --- what they asked Parliament about (Zenodo / sansad.in) -----------
        topic_claim = next(
            (c for c in claims if c["predicate"] == "questions_topics"), None
        )
        questions = [c for c in claims if c["predicate"] == "parliamentary_question"]
        asked = None
        if topic_claim:
            asked = {
                "total": topic_claim["num"],
                "ministries": topic_claim["text"],
                "note": topic_claim["note"],
                "recent": [
                    {"subject": c["text"], "as_of": c["as_of"], "detail": c["note"]}
                    for c in sorted(questions, key=lambda c: c["as_of"] or "",
                                    reverse=True)[:12]
                ],
            }

        # --- pointers to primary documents (US disclosures) ------------------
        filings = [
            {"kind": c["text"], "as_of": c["as_of"], "detail": c["note"]}
            for c in claims if c["predicate"] == "disclosure_filed"
        ]

        # --- parliamentary activity (PRS) ------------------------------------
        activity = {}
        for c in claims:
            if c["predicate"] in ACTIVITY and c["num"] is not None:
                activity[c["predicate"]] = {"value": c["num"], "benchmark": c["note"]}

        # Individual MPLADS works are summarised in `public_money` and sampled in
        # `largest_works`. Repeating all of them in `claims` too pushed one
        # person's bundle to 488KB and the whole export to 36MB, which is a slow
        # page load for data nobody scrolls through. The full set stays in
        # claims.db, which is what the analysis path uses.
        WORK_SAMPLE = 25
        works_in_claims = [c for c in claims if c["predicate"] == "contract_awarded"]
        shown_claims = [c for c in claims if c["predicate"] != "contract_awarded"]
        shown_claims += sorted(works_in_claims, key=lambda c: -(c["num"] or 0))[:WORK_SAMPLE]

        record = {
            **person,
            "claims": shown_claims,
            "claims_omitted": max(0, len(works_in_claims) - WORK_SAMPLE),
            "asset_points": assets,
            "flow_by_year": sorted(flows.items()),
            "context": ctx,
            "public_money": public,
            "activity": activity or None,
            "asked": asked,
            "filings": sorted(filings, key=lambda f: f["as_of"] or "", reverse=True)[:20] or None,
            "n_claims": len(claims),
            "sources": sorted({c["src_url"] for c in claims}),
            # facts where two source documents disagree, surfaced not resolved
            "conflicts": _conflicts(claims, party_aliases),
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
                "allocated": allocated,
                "spent": spent,
                "utilisation": public["utilisation"] if public else None,
                "works": len(works) if works else None,
                "attendance": activity.get("attendance_pct", {}).get("value"),
                "questions_named_on": topic_claim["num"] if topic_claim else None,
                "filings": len(filings) or None,
                "n_conflicts": len(_conflicts(claims, party_aliases)),
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
            "public_money": (
                "MPLADS figures are PUBLIC money a member may direct to works in "
                "their constituency - not the member's own money and not their "
                "income. The member recommends works; district authorities "
                "sanction, implement and pay. Low utilisation can reflect district "
                "capacity as much as the member."
            ),
            "questions": (
                "Question counts here are questions this member's NAME APPEARS "
                "ON. Questions are frequently tabled jointly - 37,271 questions "
                "carry 156,760 names, a mean of 4.2 members each - and every "
                "signatory is credited. This is a different measure from the PRS "
                "'questions asked' figure, and the two are not comparable. The "
                "text of each question and the ministry's reply are in a linked "
                "PDF and are not held here."
            ),
            "filings": (
                "A filing record says a financial disclosure EXISTS and links to "
                "it. No dollar figure is extracted: India publishes the numbers, "
                "the US publishes the paperwork."
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
