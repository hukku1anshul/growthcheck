"""Electoral bonds - the money going IN to Indian politics.

Everything else in this project follows money outward or downward: what a
politician declares they own, what public funds their constituency spent. This
follows it inward. Between 2018 and 2024 anonymous corporate donations could be
routed to political parties through electoral bonds; the Supreme Court struck the
scheme down in February 2024 and ordered the data published, which is why it can
be read at all.

Two sides, both published:

    electoral_bonds_purchased   by donor - who bought bonds, and how many
    electoral_bonds_received    by party - who encashed them

WHAT THIS CANNOT TELL YOU
-------------------------
The published data does NOT link a specific donor to a specific party. Bonds
carried no public donor-party mapping; what exists is two lists that happen to
sum to the same total. Anyone claiming "company X funded party Y" from this data
alone is inferring, not reading, and this extractor deliberately produces no such
claim. The bond serial numbers that would allow matching were released
separately and matching them is a research project in its own right, not a
parsing job.

These claims attach to parties and donors, not to people. A reader reaches them
through an MP's party affiliation, and the UI has to be explicit that a party
receiving money is not the same as a member receiving money.
"""

from __future__ import annotations

import re
from typing import Iterator

from bs4 import BeautifulSoup

from ..base import Claim, Extractor

BASE = "https://www.myneta.info/electoral_bonds"
PARTY_PAGE = f"{BASE}/index.php?action=party_wise_bonds_encashed"
DONOR_PAGE = f"{BASE}/index.php?action=donor_wise_bond_details"

# "Rs. 60,60,51,11,000.00 ~ 6060 Crore+"
MONEY = re.compile(r"Rs\.?\s*([\d,]+(?:\.\d+)?)")

# The scheme ran from the 2018 notification to the Supreme Court judgment.
SCHEME_END = "2024-02-15"


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


def rupees(text: str) -> float | None:
    m = MONEY.search(clean(text))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


class ElectoralBonds(Extractor):
    name = "electoralbonds"
    version = "0.1"
    country = "IND"
    publisher = "Association for Democratic Reforms (myneta.info/electoral_bonds)"
    licence = "Published under Supreme Court order; republished by ADR"
    robots_checked = True  # same host as myneta; only /*print=true is disallowed

    def harvest(self) -> Iterator[Claim]:
        yield from self._table(
            PARTY_PAGE, "electoral_bonds_received", "party",
            note="Total face value of electoral bonds encashed by this party. "
                 "The published data does not say which donor's bonds these were.",
        )
        yield from self._table(
            DONOR_PAGE, "electoral_bonds_purchased", "donor",
            note="Total face value of electoral bonds purchased by this donor. "
                 "The published data does not say which party encashed them.",
        )

    def _table(self, url: str, predicate: str, subject: str, note: str) -> Iterator[Claim]:
        sid, html = self.archive.text(url)
        self.stats.documents += 1
        soup = BeautifulSoup(html, "lxml")

        best = None
        for tbl in soup.find_all("table"):
            rows = tbl.find_all("tr")
            if len(rows) > (len(best.find_all("tr")) if best else 2):
                best = tbl
        if best is None:
            self.stats.errors.append(f"no table at {url}")
            return

        emitted = 0
        for tr in best.find_all("tr"):
            cells = [clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            name, amount = cells[0], rupees(cells[1])
            if not name or amount is None or name.lower() in ("party", "donor name"):
                continue
            count = None
            if len(cells) >= 3:
                digits = re.sub(r"[^\d]", "", cells[2])
                count = int(digits) if digits else None

            yield Claim(
                predicate=predicate,
                source_id=sid,
                subject=subject,
                subject_id=name,
                value_num=amount,
                currency="INR",
                as_of=SCHEME_END,
                note=note + (f" Bonds: {count}." if count else ""),
            )
            emitted += 1
        print(f"  {subject}s: {emitted}", flush=True)

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        predicate, subject = (
            ("electoral_bonds_received", "party")
            if "party_wise" in url
            else ("electoral_bonds_purchased", "donor")
        )
        soup = BeautifulSoup(body.decode("utf-8", errors="replace"), "lxml")
        out: list[Claim] = []
        best = max(soup.find_all("table"),
                   key=lambda t: len(t.find_all("tr")), default=None)
        if best is None:
            return out
        for tr in best.find_all("tr"):
            cells = [clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            name, amount = cells[0], rupees(cells[1])
            if not name or amount is None or name.lower() in ("party", "donor name"):
                continue
            out.append(Claim(
                predicate=predicate, source_id=source_id, subject=subject,
                subject_id=name, value_num=amount, currency="INR", as_of=SCHEME_END,
            ))
        return out
