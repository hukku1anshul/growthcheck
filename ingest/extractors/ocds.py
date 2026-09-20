"""Open Contracting Data Standard - public procurement, from any OCDS publisher.

OCDS is the one genuinely international schema in this whole project. Dozens of
governments publish awards in the same shape, which means this extractor is
deliberately NOT written for one country: point it at a different OCDS endpoint
and it works, because the field names are the standard's rather than a
publisher's.

    python -m ingest.run ocds --limit 500                 # UK Contracts Finder
    OCDS_URL=https://.../api/releases python -m ingest.run ocds

It emits one `contract_awarded` claim per award, carrying the buyer, the
supplier, the amount, the date and the ocid - the standard's globally unique
contracting process identifier, which is what makes a claim here traceable back
to the publisher's own record.

WHY THIS SITS IN THE SAME CLAIM STORE
-------------------------------------
Procurement is about organisations, not people, so these claims use
subject='contract' and carry no person_id. Keeping them beside the politician
data is still the point: the interesting question is whether an organisation that
appears on one side of politics appears on the other. In India, several of the
largest electoral bond purchasers are infrastructure contractors, and the claim
store is the thing that would let someone check that against procurement records
rather than assert it.

This extractor does not make that link, and no code here should. Matching company
names across registers is its own hard problem - the same trap as matching
politicians' names, with the same defamation risk attached if done carelessly.
"""

from __future__ import annotations

import json
import os
from typing import Iterator

from ..base import Claim, Extractor

# UK Contracts Finder publishes OCDS 1.1 with cursor pagination and no key.
# A single award above this is treated as a probable publisher error rather than
# a real contract. Deliberately generous: real megaprojects exist, and the point
# is to catch misplaced zeros, not to second-guess procurement.
IMPLAUSIBLE_AWARD = 5e10

DEFAULT_URL = (
    "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
)


def _money(obj) -> tuple[float | None, str | None]:
    if not isinstance(obj, dict):
        return None, None
    amt = obj.get("amount")
    try:
        return (float(amt) if amt is not None else None), obj.get("currency")
    except (TypeError, ValueError):
        return None, obj.get("currency")


class OCDS(Extractor):
    name = "ocds"
    version = "0.1"
    country = "GBR"          # of the default publisher; override per endpoint
    publisher = "UK Contracts Finder (Open Contracting Data Standard 1.1)"
    licence = "Open Government Licence"
    robots_checked = True    # documented public API, no key required

    def __init__(self, con, url: str | None = None, max_pages: int = 20, **kw):
        super().__init__(con, **kw)
        self.url = url or os.environ.get("OCDS_URL") or DEFAULT_URL
        self.max_pages = max_pages

    def harvest(self) -> Iterator[Claim]:
        url = self.url
        seen_awards = 0
        for page in range(1, self.max_pages + 1):
            try:
                sid, body = self.archive.text(url)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"page {page}: {exc}")
                return
            self.stats.documents += 1
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                self.stats.errors.append(f"page {page}: bad json: {exc}")
                return

            releases = payload.get("releases") or []
            if not releases:
                break
            for claim in self._from_releases(releases, sid):
                seen_awards += 1
                yield claim

            if self.limit and seen_awards >= self.limit:
                break
            nxt = (payload.get("links") or {}).get("next")
            if not nxt or nxt == url:
                break
            url = nxt
            print(f"  page {page}: {seen_awards} awards so far", flush=True)

    def _from_releases(self, releases, sid: int) -> Iterator[Claim]:
        for r in releases:
            ocid = r.get("ocid")
            buyer = (r.get("buyer") or {}).get("name") or "buyer not named"
            tender = r.get("tender") or {}
            title = (tender.get("title") or "").strip() or "untitled procurement"

            for award in (r.get("awards") or []):
                amount, currency = _money(award.get("value"))
                if amount is None:
                    # An award with no published value is still a fact, but it is
                    # not a money fact, and inventing 0 would corrupt every total.
                    continue
                suppliers = [
                    s.get("name") for s in (award.get("suppliers") or []) if s.get("name")
                ] or ["supplier not named"]
                date = (award.get("date") or r.get("date") or "")[:10] or None

                # Publishers make data-entry errors, and a single one can swamp
                # every total computed downstream. The first 456 awards pulled
                # from Contracts Finder summed to GBP 200.9bn, of which GBP 200bn
                # was ONE framework call-off for a hospital - implausible against
                # total UK health spending of roughly GBP 180bn a year, and
                # almost certainly a misplaced set of zeros.
                #
                # We do not correct it: the publisher said it, and silently
                # rewriting a source is exactly what this project refuses to do.
                # We record it faithfully, mark it implausible, and lower the
                # confidence so it can be excluded from aggregates by anyone who
                # wants a sane total.
                implausible = amount >= IMPLAUSIBLE_AWARD
                note = (
                    f"{title[:150]} | buyer: {buyer[:80]} | "
                    f"supplier: {', '.join(suppliers)[:90]} | ocid: {ocid}"
                )
                if implausible:
                    note = (
                        "IMPLAUSIBLE VALUE - recorded as published, not corrected. "
                        f"{currency} {amount:,.0f} for a single award exceeds the "
                        f"sanity threshold; treat as a probable publisher error "
                        f"and verify against the source before citing. " + note
                    )

                yield Claim(
                    predicate="contract_awarded",
                    source_id=sid,
                    subject="contract",
                    subject_id=ocid,
                    value_num=amount,
                    currency=currency,
                    as_of=date,
                    confidence=0.2 if implausible else 1.0,
                    note=note,
                )

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return []
        return list(self._from_releases(payload.get("releases") or [], source_id))
