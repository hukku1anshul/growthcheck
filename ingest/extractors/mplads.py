"""MPLADS - the Members of Parliament Local Area Development Scheme.

This is the half of the original question that nothing else in this project
answered. MyNeta says what a politician *declares they own*. PRS says what they
*do in Parliament*. MPLADS says what happened to *public money*: every Indian MP
directs roughly Rs 5 crore a year of development funds to works in their
constituency, and the portal publishes, per member:

    budget_allocated   the entitlement for their tenure
    budget_spent       value of works actually completed
    contract_awarded   individual works - description, activity, amount,
                       completion date and implementing agency

HOW THE ENDPOINT WAS FOUND
--------------------------
The eSAKSHI portal ships obfuscated JavaScript that assembles its API calls at
runtime: string literals are stored reversed and rebuilt with
.split('').reverse().join(''). Guessing parameters from outside failed - every
endpoint returned an empty array. The real calls were observed instead by running
the live dashboard through `tools/govproxy.py` and reading what it sent.

The parameter is `combo` (`uname` on some endpoints): a four-part selector
"tenure,state,constituency,house" where 0 means "all" and house 2 is the Lok
Sabha. "0,0,0,2" is every Lok Sabha member of the current tenure.

WHAT THIS DOES NOT MEAN
-----------------------
  * An MP RECOMMENDS works. District authorities sanction, implement and pay.
    Money spent against a recommendation was never money the member handled, and
    low utilisation can reflect district capacity rather than the member.
  * Allocation is per tenure, not per year. Comparing members elected at
    different times without normalising is meaningless.
  * A completed work is not evidence of quality. The portal carries a rating
    field that is frequently empty.

These caveats travel with the claims, into the UI, rather than living only here.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Iterator

from ..base import Claim, Extractor, Office
from ..resolve import find_or_create

BASE = "https://mplads.mospi.gov.in"
API = f"{BASE}/rest/PreLoginDashboardData"
DASHBOARD = f"{BASE}/digigov/dashboard.html"

COMBO_ALL_LOK_SABHA = "0,0,0,2"

TILE_ALLOCATED = "Allocated Limit for Hon'ble MPs"
TILE_COMPLETED = "Works Completed"
TILE_SANCTIONED = "Works Sanctioned"


def _rows(body: bytes) -> list[dict]:
    """Unwrap {"<tile label>": "<json string of rows>"} into a list of rows."""
    try:
        outer = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    if isinstance(outer, list):
        return outer
    for value in outer.values():
        if isinstance(value, str):
            try:
                inner = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(inner, list):
                return inner
        elif isinstance(value, list):
            return value
    return []


def _date(raw) -> str | None:
    """'Jun 4, 2024 12:00:00 AM' -> '2024-06-04'."""
    if not raw:
        return None
    # The portal is not consistent: tenure dates arrive as
    # "Jun 4, 2024 12:00:00 AM" while work completion dates are "05-Sep-2024".
    for fmt in ("%b %d, %Y %I:%M:%S %p", "%d-%b-%Y", "%b %d, %Y",
                "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return _dt.datetime.strptime(str(raw).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class MPLADS(Extractor):
    name = "mplads"
    version = "0.2"
    country = "IND"
    publisher = "MoSPI - MPLADS eSAKSHI portal (mplads.mospi.gov.in)"
    licence = "Government of India public dashboard data"
    robots_checked = True  # no robots.txt served; the pre-login dashboard is public

    jurisdiction = "IN/LokSabha2024"

    def __init__(self, con, combo: str = COMBO_ALL_LOK_SABHA,
                 detail: bool = True, **kw):
        super().__init__(con, **kw)
        self.combo = combo
        self.detail = detail        # emit individual works, not just totals
        self._people: dict[str, int] = {}   # MP_NAME -> person_id
        # MP_NAME -> tenure start. The allocation rows carry it and the
        # completed-works rows do not, so it is remembered here and reused:
        # the spend total is the spend AGAINST that tenure's allocation, and
        # the two must share a date to be comparable at all.
        self._tenure_start: dict[str, str | None] = {}

    # ------------------------------------------------------------------ fetch
    def _tile(self, key: str) -> tuple[int, list[dict]]:
        sid, body = self.archive.post_json(
            f"{API}/getTilesReportData",
            {"combo": self.combo, "key": key},
            prime=DASHBOARD,
        )
        self.stats.documents += 1
        rows = _rows(body)
        print(f"  {key}: {len(rows):,} rows ({len(body):,}B)", flush=True)
        return sid, rows

    def _person(self, name: str, sid: int, ctx: str | None) -> int:
        """Resolve each member once.

        These tiles carry ~120,000 rows for 544 people. Letting the framework
        resolve a name per claim would run entity resolution a hundred thousand
        times to answer the same 544 questions.
        """
        pid = self._people.get(name)
        if pid is None:
            pid, action = find_or_create(self.con, self.country, name, sid, context=ctx)
            self._people[name] = pid
            key = {"created": "people_created", "matched": "people_matched",
                   "review": "people_review"}[action]
            setattr(self.stats, key, getattr(self.stats, key) + 1)
        return pid

    # -------------------------------------------------------------- harvesting
    def harvest(self) -> Iterator[Claim | Office]:
        yield from self._allocations()
        yield from self._works()

    def _allocations(self) -> Iterator[Claim | Office]:
        sid, rows = self._tile(TILE_ALLOCATED)
        for r in rows:
            name = (r.get("MP_NAME") or "").strip()
            amount = _num(r.get("ALLOCATED_AMT"))
            if not name or amount is None:
                continue
            constituency = (r.get("CONSTITUENCY") or "").strip() or None
            pid = self._person(name, sid, constituency)

            yield Office(
                person_name=name,
                jurisdiction=self.jurisdiction,
                source_id=sid,
                title="MP",
                constituency=constituency,
                region=(r.get("STATE_NAME") or "").strip() or None,
                party=None,                       # MPLADS does not publish party
                won=True,
            )
            yield Claim(
                predicate="budget_allocated",
                source_id=sid,
                person_id=pid,
                value_num=amount,
                currency="INR",
                as_of=self._tenure_start.setdefault(
                    name, _date(r.get("TENURE_START_DATE"))),
                note=(
                    f"MPLADS entitlement for {r.get('TENURE')}, {r.get('HOUSE_NAME')}. "
                    f"Per tenure, not per year."
                ),
            )

    def _works(self) -> Iterator[Claim]:
        sid, rows = self._tile(TILE_COMPLETED)

        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        seats: dict[str, str | None] = {}
        for r in rows:
            name = (r.get("MP_NAME") or "").strip()
            amt = _num(r.get("ACTUAL_AMOUNT"))
            if not name or amt is None:
                continue
            totals[name] = totals.get(name, 0.0) + amt
            counts[name] = counts.get(name, 0) + 1
            seats.setdefault(name, (r.get("CONSTITUENCY") or "").strip() or None)

        for name, total in totals.items():
            pid = self._person(name, sid, seats.get(name))
            yield Claim(
                predicate="budget_spent",
                source_id=sid,
                person_id=pid,
                value_num=round(total, 2),
                currency="INR",
                # Dated to the same tenure as the allocation it is spent
                # against. Left undated, this total could not be placed in time,
                # could not be compared with the entitlement, and two tenures
                # would collide as a single undated "conflict".
                as_of=self._tenure_start.get(name),
                note=(
                    f"Value of {counts[name]} completed works recommended by this "
                    f"member. The member recommends; district authorities sanction, "
                    f"implement and pay. This is not money the member handled."
                ),
            )

        if not self.detail:
            return

        for n, r in enumerate(rows, 1):
            name = (r.get("MP_NAME") or "").strip()
            amt = _num(r.get("ACTUAL_AMOUNT"))
            if not name or amt is None:
                continue
            if n % 10000 == 0:
                print(f"  ...work {n:,}/{len(rows):,}", flush=True)
            desc = (r.get("WORK_DESCRIPTION") or "").strip() or "no description given"
            activity = (r.get("ACTIVITY_NAME") or "").strip() or "unspecified"
            agency = (r.get("IDA_NAME") or "").strip()
            yield Claim(
                predicate="contract_awarded",
                source_id=sid,
                person_id=self._person(name, sid, seats.get(name)),
                value_num=amt,
                currency="INR",
                as_of=_date(r.get("ACTUAL_END_DATE")),
                note=(
                    f"{desc[:180]} | category: {activity}"
                    + (f" | agency: {agency}" if agency else "")
                    + f" | work id: {r.get('WORK_ID')}"
                ),
            )

    # ------------------------------------------------------------------ diff
    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        rows = _rows(body)
        if not rows:
            return []
        out: list[Claim] = []
        if "ALLOCATED_AMT" in rows[0]:
            for r in rows:
                name = (r.get("MP_NAME") or "").strip()
                amt = _num(r.get("ALLOCATED_AMT"))
                if name and amt is not None:
                    out.append(Claim(
                        predicate="budget_allocated", source_id=source_id,
                        person_name=name, value_num=amt, currency="INR",
                        as_of=_date(r.get("TENURE_START_DATE")),
                    ))
        elif "ACTUAL_AMOUNT" in rows[0]:
            totals: dict[str, float] = {}
            for r in rows:
                name = (r.get("MP_NAME") or "").strip()
                amt = _num(r.get("ACTUAL_AMOUNT"))
                if name and amt is not None:
                    totals[name] = totals.get(name, 0.0) + amt
            for name, t in totals.items():
                out.append(Claim(
                    predicate="budget_spent", source_id=source_id,
                    person_name=name, value_num=round(t, 2), currency="INR",
                ))
        return out
