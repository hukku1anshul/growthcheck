"""Relay published fact-checks about the politicians we already hold.

    setx GOOGLE_FACTCHECK_KEY "..."      # Windows, then reopen the shell
    python -m ingest.run factcheck --delay 1

Source: Google Fact Check Tools API `claims:search`, which aggregates
ClaimReview markup published by IFCN-signatory fact-checkers worldwide -
including Boom, Factly, Newschecker and First Check in India, and Full Fact in
the UK.

THE DIVISION OF LABOUR
----------------------
Fact-checkers check virality: is this image doctored, was this quote real, did
this video happen where it claims. They are good at it and we should not
duplicate them.

What nobody does is check a NUMBER about a politician against that politician's
own sworn affidavit. That is what the rest of this project is for.

So this extractor does not check anything. It relays: it records that a named,
IFCN-audited organisation published a review of a claim about this person, with
their rating and a link to their article. We add no assessment of our own, and
we never restate their rating as our own finding.

WHY THE RATING IS STORED AS TEXT
--------------------------------
`textualRating` is a free-text field each publisher writes themselves - "False",
"Misleading", "Mostly False", "Half True", "Missing Context". Mapping those onto
a numeric truth scale would mean inventing an equivalence between publishers who
never agreed one, and would let this project imply a verdict it did not make.
The publisher's own words are stored verbatim.

THE MATCH IS BY NAME, AND THAT IS A REAL LIMIT
----------------------------------------------
`claims:search` takes a text query, so the only thing linking a review to a
politician here is their name. There is no identifier to join on - no bioguide,
no QID, nothing. A review returned for "Rajesh Verma" may be about any Rajesh
Verma.

This is the same failure that put a stranger's birth date on Tariq Anwar's page
when the Wikidata witness matched on label alone. It cannot be fixed the same
way, because these reviews carry no identifiers to filter on, and a
text-containment check would be worse than useless: the correct reviews for
Abhijit Gangopadhyay are written in Bengali and do not contain his name in
Latin script at all.

So the uncertainty is not hidden and not silently resolved. Every claim says it
was matched by name, and the UI must repeat that where a reader will see it.
Showing a fact-check about a different person as though it were about this one
is the most damaging mistake this feature could make.

A key is required. Google refuses unregistered callers with HTTP 403, which was
verified. Keys are free from the Google Cloud Console; this extractor will not
run without one and will say so rather than silently producing nothing.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from typing import Iterator

from ..base import Claim, Extractor

API = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
ENV_KEY = "GOOGLE_FACTCHECK_KEY"


class FactCheck(Extractor):
    name = "factcheck"
    version = "0.1"
    country = "IND"          # scope of the people queried; reviews may be global
    publisher = "Google Fact Check Tools API (aggregating IFCN signatories)"
    licence = "Review text and ratings belong to the publishing fact-checker"
    robots_checked = True    # documented public API

    def __init__(self, con, countries: tuple[str, ...] = ("IND", "GBR"),
                 language: str | None = None, max_age_days: int | None = None, **kw):
        super().__init__(con, **kw)
        self.countries = countries
        self.language = language
        self.max_age_days = max_age_days
        self.key = os.environ.get(ENV_KEY, "").strip()

    def harvest(self) -> Iterator[Claim]:
        if not self.key:
            raise RuntimeError(
                f"{ENV_KEY} is not set. The Google Fact Check Tools API refuses "
                f"unregistered callers (HTTP 403). Get a free key from the Google "
                f"Cloud Console, enable 'Fact Check Tools API', then set "
                f"{ENV_KEY} and re-run. Nothing is fetched without it."
            )

        rows = self.con.execute(
            "SELECT p.id, p.full_name, p.country FROM persons p "
            "WHERE p.country IN (%s) AND EXISTS "
            "(SELECT 1 FROM offices o WHERE o.person_id = p.id) "
            "ORDER BY p.full_name" % ",".join("?" * len(self.countries)),
            self.countries,
        ).fetchall()
        if self.limit:
            rows = rows[: self.limit]
        print(f"  querying published fact-checks for {len(rows)} politicians",
              flush=True)

        found = people_with = 0
        for n, r in enumerate(rows, 1):
            if n % 100 == 0:
                print(f"  ...{n}/{len(rows)}  {found} reviews so far", flush=True)
            params = {"query": r["full_name"], "key": self.key, "pageSize": 10}
            if self.language:
                params["languageCode"] = self.language
            if self.max_age_days:
                params["maxAgeDays"] = self.max_age_days
            url = f"{API}?{urllib.parse.urlencode(params)}"

            try:
                sid, body = self.archive.text(url)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"{r['full_name']}: {exc}")
                continue
            self.stats.documents += 1
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue

            got = False
            for c in data.get("claims", []):
                claim_text = (c.get("text") or "").strip()
                claimant = (c.get("claimant") or "").strip()
                claimed_on = (c.get("claimDate") or "")[:10] or None
                for rev in c.get("claimReview", []):
                    pub = (rev.get("publisher") or {}).get("name") or "unnamed publisher"
                    site = (rev.get("publisher") or {}).get("site") or ""
                    rating = (rev.get("textualRating") or "").strip() or "no rating given"
                    reviewed = (rev.get("reviewDate") or "")[:10] or None
                    link = rev.get("url") or ""
                    if not claim_text or not link:
                        continue
                    found += 1
                    got = True
                    yield Claim(
                        predicate="factcheck_published",
                        source_id=sid,
                        person_id=r["id"],
                        value_text=f"{pub} rated this “{rating}”",
                        as_of=reviewed or claimed_on,
                        confidence=1.0,
                        note=(
                            f"CLAIM REVIEWED (not ours, and not about our data): "
                            f"“{claim_text[:260]}”"
                            + (f" - attributed to {claimant}." if claimant else "")
                            + f" Reviewed by {pub}"
                            + (f" ({site})" if site else "")
                            + f". Their rating, in their words: “{rating}”. "
                            f"MATCHED BY NAME: this review was found by searching "
                            f"fact-checkers' databases for “{r['full_name']}”, not "
                            f"by any identifier. Names are shared, so it may "
                            f"concern a different person of the same name - read "
                            f"the article before relying on it. "
                            f"Read it: {link}"
                        ),
                    )
            if got:
                people_with += 1

        print(f"  {found} published reviews across {people_with} politicians",
              flush=True)

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        """Re-parse is not meaningful: the API result is a live search, not a
        document whose facts we derived. The change checker treats it as bytes."""
        return []
