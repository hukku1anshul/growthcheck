"""PRS Legislative Research - MP performance, and a second opinion on MyNeta.

PRS tracks what MPs actually do in Parliament: attendance, debates, questions
asked, private member's bills. That is the "how is he doing?" half of the
question, separate from the "where did the money go?" half.

Its second value is corroboration. Until now every fact in this store had exactly
one witness. PRS independently publishes four fields MyNeta also publishes -
party, constituency, age and education - for the same 543 people. Where they
agree, confidence in both rises. Where they disagree, the `conflicts` view
surfaces it and neither is quietly chosen. That is the first real test of the
claim store's central premise: that recording disagreement beats resolving it.

On politeness: prsindia.org sets `Crawl-delay: 10` for all agents, and their
listing is fixed at 9 MPs per page, so a full pass is ~604 requests and about
100 minutes. The delay default here is 10 and should not be lowered. Nothing in
robots.txt disallows /mptrack; the disallowed paths are Drupal internals.
"""

from __future__ import annotations

import re
from typing import Iterator

from bs4 import BeautifulSoup

from ..base import Claim, Extractor, Office

BASE = "https://prsindia.org"
TRACK = f"{BASE}/mptrack"

# "Attendance Selected MP 87 % National Average 85 %"
STAT = r"{label}\s*Selected MP\s*([\d.]+)"

# PRS renders the profile as one run of "Label : value" pairs with no separators,
# so a field ends only where the NEXT known label begins. A generic
# "[A-Z][a-z]+ :" lookahead is not enough: "Nature of membership :" and
# "No. of Term :" do not match it, so Party and Education silently captured
# nothing at all. Terminating on an explicit label list is uglier and correct.
LABELS = (
    "State", "Constituency", "Party", "Nature of membership", "Start of Term",
    "End of Term", "No. of Term", "Personal Profile", "Age", "Gender",
    "Education", "Parliamentary Activity", "Detailed Information",
)
_NEXT = "|".join(re.escape(x) for x in LABELS)
# Section headers carry no colon, so they need their own terminator rule. Without
# this, "Education : Graduate Parliamentary Activity ..." never terminated and
# education captured nothing. They cannot simply be added to the colon-terminated
# list either: "Party" appears inside "Bharatiya Janata Party", so a colon-free
# terminator on that label would truncate the value mid-name.
SECTIONS = ("Parliamentary Activity", "Personal Profile", "Detailed Information")
_SECT = "|".join(re.escape(x) for x in SECTIONS)
FIELD = r"\b{label}\s*:\s*(.{{1,90}}?)\s*(?:" + _NEXT + r")\s*[:\b]|\b{label}\s*:\s*(.{{1,90}}?)$"


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


class PRS(Extractor):
    name = "prs"
    version = "0.1"
    country = "IND"
    publisher = "PRS Legislative Research (prsindia.org)"
    licence = "Publicly published parliamentary activity data"
    robots_checked = True  # Crawl-delay: 10 honoured; /mptrack not disallowed

    jurisdiction = "IN/LokSabha2024"
    sabha = "18th-lok-sabha"

    def __init__(self, con, delay: float = 10.0, **kw):
        # The publisher asks for 10 seconds. Default to it rather than to the
        # framework's 1s, so forgetting the flag cannot make us rude.
        super().__init__(con, delay=max(delay, 10.0), **kw)

    # ------------------------------------------------------------------ index
    def mp_slugs(self) -> list[str]:
        """Walk the paginated listing. 9 per page is fixed by the site."""
        slugs: list[str] = []
        # PRS's pager is 1-indexed: ?page=1 is the same page as the bare URL, not
        # the second. Starting at 0 and incrementing meant the second request
        # returned the first page again, the loop saw no new slugs and stopped
        # after 9 MPs - a silent 98% shortfall that looked like a clean run.
        page = 1
        seen_pages = 0
        while True:
            url = TRACK if page == 1 else f"{TRACK}?page={page}"
            try:
                _, html = self.archive.text(url)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"index page {page}: {exc}")
                break
            self.stats.documents += 1
            found = re.findall(rf"/mptrack/{self.sabha}/([a-z0-9\-]+)", html)
            new = [s for s in dict.fromkeys(found) if s not in slugs]
            if not new:
                break
            slugs.extend(new)
            seen_pages += 1
            page += 1
            if self.limit and len(slugs) >= self.limit:
                break
            if seen_pages > 120:      # guard against an infinite pager
                break
        print(f"  {len(slugs)} MPs indexed from {seen_pages} listing pages", flush=True)
        return slugs[: self.limit] if self.limit else slugs

    # -------------------------------------------------------------- harvesting
    def harvest(self) -> Iterator[Claim | Office]:
        slugs = self.mp_slugs()
        for n, slug in enumerate(slugs, 1):
            if n % 25 == 0:
                print(f"  ...{n}/{len(slugs)}", flush=True)
            url = f"{TRACK}/{self.sabha}/{slug}"
            try:
                sid, html = self.archive.text(url)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"{slug}: {exc}")
                continue
            self.stats.documents += 1
            yield from self._parse(html, sid, url)

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        html = body.decode("utf-8", errors="replace")
        return [i for i in self._parse(html, source_id, url) if isinstance(i, Claim)]

    # ------------------------------------------------------------------ parse
    def _parse(self, html: str, sid: int, url: str) -> Iterator[Claim | Office]:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = clean(soup.get_text(" "))

        name = self._name(soup)
        if not name:
            self.stats.errors.append(f"no name at {url}")
            return

        state = self._field(text, "State")
        constituency = self._field(text, "Constituency")
        party = self._field(text, "Party")
        ctx = f"{constituency or '?'} PRS"

        yield Office(
            person_name=name,
            jurisdiction=self.jurisdiction,
            source_id=sid,
            title="MP",
            constituency=constituency,
            region=state,
            party=party,
            won=True,
        )

        def claim(pred, **kw):
            return Claim(predicate=pred, source_id=sid, person_name=name,
                         context=ctx, **kw)

        year = "2024-01-01"

        # --- fields that MyNeta also publishes: the corroboration set ---------
        if party:
            yield claim("party_affiliation", value_text=party, as_of=year)
        age = self._field(text, "Age")
        if age and age.isdigit():
            yield claim("age", value_num=float(age), unit="years", as_of=year)
        edu = self._field(text, "Education")
        if edu:
            yield claim("education_level", value_text=edu, as_of=year)

        # --- what PRS uniquely measures: parliamentary activity ---------------
        for label, predicate, unit in (
            ("Attendance", "attendance_pct", "% of sittings"),
            ("No. of Debates", "debates_participated", "debates"),
            ("No. of Questions", "questions_asked", "questions"),
            ("Private Member's Bills", "private_member_bills", "bills"),
        ):
            v = self._stat(text, label)
            if v is not None:
                yield claim(predicate, value_num=v, unit=unit, as_of=year,
                            note=self._benchmark(text, label))

    # ------------------------------------------------------------------- bits
    def _name(self, soup: BeautifulSoup) -> str | None:
        t = soup.find("title")
        if t:
            n = clean(t.get_text()).split("|")[0]
            if n:
                return n
        h = soup.find(["h1", "h2"])
        return clean(h.get_text()) if h else None

    def _field(self, text: str, label: str) -> str | None:
        esc = re.escape(label)
        # value runs up to the next known label, or to end of string
        m = re.search(rf"\b{esc}\s*:\s*(.{{1,90}}?)\s*(?:{_NEXT})\s*:", text)
        if not m:
            # Section headers carry no colon, so a colon-terminated search cannot
            # end a field that runs into one. "Education : Graduate Parliamentary
            # Activity ..." captured nothing until this branch existed.
            m = re.search(rf"\b{esc}\s*:\s*(.{{1,90}}?)\s*(?:{_SECT})\b", text)
        if not m:
            m = re.search(rf"\b{esc}\s*:\s*(.{{1,90}}?)$", text)
        if not m:
            return None
        v = clean(m.group(1))
        # "West Bengal ( 41 more MPs )" -> "West Bengal"
        v = re.sub(r"\(\s*[\d,]+\s*more MPs?\s*\)", "", v).strip()
        return v.strip(" ,;") or None

    def _stat(self, text: str, label: str) -> float | None:
        m = re.search(STAT.format(label=re.escape(label)), text)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    def _benchmark(self, text: str, label: str) -> str | None:
        """PRS prints national and state averages beside each figure.

        Worth storing: "6 debates" means nothing without knowing the national
        average is 21.5. A reader who sees only the raw count will misjudge it,
        and a reader shown a rank will over-trust it - the averages let them
        judge for themselves.
        """
        m = re.search(
            re.escape(label)
            + r"\s*Selected MP\s*[\d.]+\s*%?\s*National Average\s*([\d.]+)\s*%?"
              r"\s*State Average\s*([\d.]+)",
            text,
        )
        if not m:
            return None
        return f"National average {m.group(1)}, state average {m.group(2)}"
