"""US Congress - the people spine, and pointers to their financial disclosures.

Two sources, both free and keyless:

  unitedstates/congress-legislators   539 sitting members, with cross-dataset
                                      identifiers: bioguide, FEC, OpenSecrets,
                                      GovTrack, Wikidata, Ballotpedia
  House Clerk financial disclosures   the annual filing INDEX - who filed, what
                                      type, when, and the document id

WHY THE UNITED STATES, AND WHY THIS SHAPE
-----------------------------------------
This project's per-politician depth was almost entirely Indian: 92% of claims
from one country, because MPLADS alone contributed 36,608 of them. That is an
accident of where the good data was, not a design decision, and it made the
engine's portability a claim rather than a demonstration.

The US is the highest-leverage second country because `congress-legislators` is
not just a list of names - it is a set of FOREIGN KEYS. A member here carries
their FEC candidate id, their OpenSecrets id and their Wikidata QID, so adding
the people automatically makes every US money dataset joinable later without
another round of name matching. No other country's open data hands you that.

WHAT THIS DOES NOT CLAIM - CHECKED, NOT ASSUMED
------------------------------------------------
The House disclosure ZIP contains an index of 2,935 filings and NOTHING ELSE.
The asset ranges, income and transactions are inside per-filing PDFs. So this
extractor emits `disclosure_filed` - a dated pointer to a primary document that
exists - and never a dollar figure. That is deliberately weaker than the Indian
`declared_assets` claims, and the UI must not present them as equivalent: India
publishes the numbers, the US publishes the paperwork.

The PDF URL pattern was verified to return 200 application/pdf, so the pointer
is real and a reader can open the filing themselves.

Senate disclosures are behind a search form with a terms-of-use gate and are
deliberately not fetched.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Iterator

from ..base import Claim, Extractor, Office

LEGISLATORS = (
    "https://unitedstates.github.io/congress-legislators/legislators-current.json"
)
HOUSE_FD = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP"
HOUSE_PDF = (
    "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}/{doc}.pdf"
)

# The Clerk's filing-type codes.
FILING_TYPE = {
    "O": "original annual report",
    "A": "amendment",
    "C": "candidate report",
    "D": "new filer or termination report",
    "P": "periodic transaction report",
    "X": "filing extension",
}

CHAMBER = {"sen": "Senator", "rep": "Representative"}


class USCongress(Extractor):
    name = "uscongress"
    version = "0.1"
    country = "USA"
    publisher = "unitedstates/congress-legislators + US House Clerk"
    licence = "congress-legislators is public domain (CC0); House filings are public record"
    robots_checked = True  # static bulk files published for reuse

    jurisdiction = "US/Congress"

    def __init__(self, con, years: tuple[int, ...] = (2025, 2024), **kw):
        super().__init__(con, **kw)
        self.years = years

    # -------------------------------------------------------------- harvesting
    def harvest(self) -> Iterator[Claim | Office]:
        yield from self._legislators()
        yield from self._disclosures()

    def _legislators(self) -> Iterator[Claim | Office]:
        sid, body = self.archive.get(LEGISLATORS)
        self.stats.documents += 1
        people = json.loads(body)
        if self.limit:
            people = people[: self.limit]
        print(f"  {len(people)} sitting members of Congress", flush=True)

        for p in people:
            name = (p.get("name") or {}).get("official_full") or " ".join(
                x for x in ((p.get("name") or {}).get("first"),
                            (p.get("name") or {}).get("last")) if x
            )
            if not name:
                continue
            terms = p.get("terms") or []
            if not terms:
                continue
            t = terms[-1]
            chamber = CHAMBER.get(t.get("type"), t.get("type"))
            state = t.get("state")
            district = t.get("district")
            seat = f"{state}-{district:02d}" if district is not None else state
            ids = p.get("id") or {}

            yield Office(
                person_name=name,
                jurisdiction=f"{self.jurisdiction}/{'Senate' if t.get('type')=='sen' else 'House'}",
                source_id=sid,
                title=chamber,
                constituency=seat,
                region=state,
                party=t.get("party"),
                won=True,
            )
            if t.get("party"):
                yield Claim(
                    predicate="party_affiliation",
                    source_id=sid,
                    person_name=name,
                    context=seat,
                    value_text=t["party"],
                    as_of=(t.get("start") or "")[:10] or None,
                )
            # The cross-dataset identifiers are the reason this source is worth
            # more than a list of names, so they are stored as a fact rather
            # than discarded.
            keys = {k: v for k, v in ids.items()
                    if k in ("bioguide", "fec", "opensecrets", "govtrack", "wikidata")}
            if keys:
                yield Claim(
                    predicate="external_identifier",
                    source_id=sid,
                    person_name=name,
                    context=seat,
                    value_text="; ".join(
                        f"{k}={v if not isinstance(v, list) else ','.join(v)}"
                        for k, v in keys.items()
                    ),
                    as_of=(t.get("start") or "")[:10] or None,
                    note="Identifiers that make this person joinable to FEC, "
                         "OpenSecrets, GovTrack and Wikidata without name matching.",
                )

    def _disclosures(self) -> Iterator[Claim]:
        """Filings by people we already hold. Never creates a person.

        The House index covers everyone who filed - candidates who lost, former
        members, spouses' trustees - not just sitting members. Emitting a claim
        for each would have created 2,349 people on top of the 539 legislators,
        every one of them a person we know nothing else about, and each a fresh
        opportunity for the resolver to mis-match somebody. 128 name pairs went
        to the review queue in the first run for exactly this reason.

        So filings are attached only where the filer resolves to a sitting
        member, and the rest are counted and reported rather than imported. A
        filing by a non-member is a real fact about a person this project does
        not otherwise cover, and inventing a thin record for them is worse than
        omitting them.
        """
        from ..resolve import AUTO_MERGE, initials_key, normalise_name, score

        sitting = [
            dict(r) for r in self.con.execute(
                "SELECT id, full_name, norm_name FROM persons WHERE country = ?",
                (self.country,),
            )
        ]

        def match(name: str) -> int | None:
            norm = normalise_name(name)
            key, toks = initials_key(norm), set(norm.split())
            best, best_s = None, 0.0
            for c in sitting:
                if initials_key(c["norm_name"]) != key and not (
                    toks & {t for t in c["norm_name"].split() if len(t) >= 3}
                ):
                    continue
                s = score(norm, c["norm_name"])
                if s > best_s:
                    best, best_s = c, s
            return best["id"] if best and best_s >= AUTO_MERGE else None

        for year in self.years:
            try:
                sid, body = self.archive.get(HOUSE_FD.format(year=year))
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"house FD {year}: {exc}")
                continue
            self.stats.documents += 1
            try:
                z = zipfile.ZipFile(io.BytesIO(body))
                txt = next(n for n in z.namelist() if n.lower().endswith(".txt"))
                lines = z.read(txt).decode("utf-8", errors="replace").splitlines()
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"house FD {year}: unreadable zip: {exc}")
                continue

            hdr = lines[0].split("\t")
            n = skipped = 0
            for line in lines[1:]:
                cells = line.split("\t")
                if len(cells) < len(hdr):
                    continue
                d = dict(zip(hdr, cells))
                first, last = d.get("First", "").strip(), d.get("Last", "").strip()
                if not first or not last:
                    continue
                name = f"{first} {last}".strip()
                doc = d.get("DocID", "").strip()
                pid = match(name)
                if pid is None:
                    skipped += 1
                    continue
                ftype = FILING_TYPE.get(d.get("FilingType", "").strip(),
                                        d.get("FilingType", "").strip())
                filed = d.get("FilingDate", "").strip()
                iso = None
                if "/" in filed:
                    try:
                        m, dd, yy = filed.split("/")
                        iso = f"{yy}-{int(m):02d}-{int(dd):02d}"
                    except ValueError:
                        iso = None
                yield Claim(
                    predicate="disclosure_filed",
                    source_id=sid,
                    person_id=pid,
                    context=d.get("StateDst", "").strip() or None,
                    value_text=ftype,
                    as_of=iso,
                    note=(
                        f"US House financial disclosure, {d.get('StateDst','')}, "
                        f"filing year {d.get('Year','')}. This records that a filing "
                        f"EXISTS and where to read it - the asset and income figures "
                        f"are inside the PDF and are not extracted: "
                        f"{HOUSE_PDF.format(year=year, doc=doc)}"
                    ),
                )
                n += 1
                if self.limit and n >= self.limit:
                    break
            print(f"  {year}: {n} filings attached to sitting members; "
                  f"{skipped} filers not in Congress, skipped", flush=True)

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        """Only the legislators file is re-parsed; the FD zip is a bulk archive."""
        if "legislators-current" not in url:
            return []
        out: list[Claim] = []
        for p in json.loads(body.decode("utf-8", errors="replace")):
            name = (p.get("name") or {}).get("official_full")
            terms = p.get("terms") or []
            if name and terms and terms[-1].get("party"):
                out.append(Claim(
                    predicate="party_affiliation", source_id=source_id,
                    person_name=name, value_text=terms[-1]["party"],
                    as_of=(terms[-1].get("start") or "")[:10] or None,
                ))
        return out
