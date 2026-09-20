"""UK Parliament - members and the Register of Members' Financial Interests.

This extractor exists to test whether the engine actually travels. It is as
different from MyNeta as a second source reasonably could be:

    MyNeta                          UK Parliament
    ------                          -------------
    HTML pages, scraped             JSON REST API, documented and keyless
    one row per candidate           two APIs joined on member id
    declared asset TOTAL (a stock)  itemised outside PAYMENTS (dated flows)
    affidavit, filed at election    rolling register, updated continuously

Nothing in `base.py`, `archive.py`, `resolve.py` or `schema.py` needed to change to
add it - only this file and two new predicates. That is the portability claim,
tested rather than asserted.

On the stock/flow distinction: the UK register records payments received, not net
worth, so these are emitted as `outside_earnings`, never as `declared_assets`.
Showing a UK MP's £18k speaking fee on the same axis as an Indian MP's ₹8cr asset
declaration would be a category error the reader could not detect.

Both APIs are published by Parliament for public reuse and require no key.
members-api.parliament.uk and interests-api.parliament.uk serve no robots.txt;
the rate limit here is set conservatively regardless.
"""

from __future__ import annotations

import json
import re
from typing import Iterator

from ..base import Claim, Extractor, Office

MEMBERS = "https://members-api.parliament.uk/api/Members/Search"
INTERESTS = "https://interests-api.parliament.uk/api/v1/Interests"

# "Payment received on 12 March 2025 - £18,450.00"  ->  18450.00
STERLING = re.compile(r"£\s*([\d,]+(?:\.\d{1,2})?)")

HOUSE_COMMONS = 1
PAGE = 20        # the members API caps `take` at 20
INTERESTS_PAGE = 50  # interests API page size; paginated below


def sterling(text: str) -> float | None:
    m = STERLING.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


class UKParliament(Extractor):
    name = "ukparliament"
    version = "0.1"
    country = "GBR"
    publisher = "UK Parliament (members-api / interests-api)"
    licence = "Open Parliament Licence"
    robots_checked = True  # both API hosts serve no robots.txt; checked 2026-09-20

    jurisdiction = "GB/Commons"

    def __init__(self, con, **kw):
        super().__init__(con, **kw)

    # ------------------------------------------------------------------ index
    def members(self) -> list[dict]:
        """Every current Commons member, paged 20 at a time."""
        out: list[dict] = []
        skip, total = 0, None
        while total is None or skip < total:
            url = (
                f"{MEMBERS}?House={HOUSE_COMMONS}&IsCurrentMember=true"
                f"&skip={skip}&take={PAGE}"
            )
            try:
                _, body = self.archive.text(url)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"members skip={skip}: {exc}")
                break
            self.stats.documents += 1
            data = json.loads(body)
            total = data.get("totalResults", 0)
            items = data.get("items", [])
            if not items:
                break
            out.extend(items)
            skip += PAGE
            if self.limit and len(out) >= self.limit:
                break
        print(f"  {len(out)} current Commons members indexed (of {total})", flush=True)
        return out[: self.limit] if self.limit else out

    # -------------------------------------------------------------- harvesting
    def harvest(self) -> Iterator[Claim | Office]:
        members = self.members()
        for n, item in enumerate(members, 1):
            if n % 50 == 0:
                print(f"  ...{n}/{len(members)}", flush=True)
            v = item.get("value") or {}
            mid = v.get("id")
            name = v.get("nameDisplayAs") or v.get("nameListAs")
            if not mid or not name:
                continue

            seat = (v.get("latestHouseMembership") or {})
            party = (v.get("latestParty") or {}).get("name")
            constituency = seat.get("membershipFrom")
            ctx = constituency or "Commons"

            # The member record itself came from a members-API page we archived;
            # find that page's source id by re-requesting it (served from archive).
            sid, _ = self.archive.text(
                f"{MEMBERS}?House={HOUSE_COMMONS}&IsCurrentMember=true"
                f"&skip={(n - 1) // PAGE * PAGE}&take={PAGE}"
            )

            yield Office(
                person_name=name,
                jurisdiction=self.jurisdiction,
                source_id=sid,
                title="MP",
                constituency=constituency,
                region=None,
                party=party,
                won=True,
            )
            if party:
                yield Claim(
                    predicate="party_affiliation",
                    source_id=sid,
                    person_name=name,
                    context=ctx,
                    value_text=party,
                    as_of=(seat.get("membershipStartDate") or "")[:10] or None,
                )

            yield from self._interests(mid, name, ctx)

    def _interests(self, member_id: int, name: str, ctx: str) -> Iterator[Claim]:
        """Every registered interest for a member, following pagination.

        The API defaults to PublishingDateDescending, so a single Take=50 request
        silently drops the oldest entries for anyone with a longer register - four
        current members have 53, 70, 72 and 88. Truncating a financial-interests
        register at an arbitrary cut-off, with no error, is exactly the kind of
        quiet data loss this project exists to avoid.
        """
        skip, total = 0, None
        while total is None or skip < total:
            url = f"{INTERESTS}?MemberId={member_id}&Take={INTERESTS_PAGE}&Skip={skip}"
            try:
                sid, body = self.archive.text(url)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"interests {member_id} skip={skip}: {exc}")
                return
            self.stats.documents += 1
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                self.stats.errors.append(f"interests {member_id}: bad json: {exc}")
                return
            total = payload.get("totalResults", 0)
            items = payload.get("items", [])
            if not items:
                return
            yield from self._claims_from(items, sid, name, ctx)
            skip += INTERESTS_PAGE

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        """Re-derive claims from an archived interests payload, no network I/O."""
        if "interests-api" not in url:
            return []          # members-search pages carry no claims of their own
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return []
        return list(
            self._claims_from(payload.get("items", []), source_id, "", "")
        )

    def _claims_from(self, items, sid: int, name: str, ctx: str) -> Iterator[Claim]:
        for it in items:
            summary = (it.get("summary") or "").strip()
            if not summary:
                continue
            category = (it.get("category") or {}).get("name") or "uncategorised"
            as_of = (it.get("registrationDate") or it.get("publishedDate") or "")[:10]
            amount = sterling(summary)

            if amount is not None:
                yield Claim(
                    predicate="outside_earnings",
                    source_id=sid,
                    person_name=name,
                    context=ctx,
                    value_num=amount,
                    currency="GBP",
                    as_of=as_of or None,
                    # The figure is parsed out of a free-text summary rather than a
                    # populated Value field, so it is not a certainty.
                    confidence=0.9,
                    note=f"{category}: {summary[:220]}",
                )
            else:
                yield Claim(
                    predicate="registered_interest",
                    source_id=sid,
                    person_name=name,
                    context=ctx,
                    value_text=summary[:400],
                    as_of=as_of or None,
                    note=category,
                )
