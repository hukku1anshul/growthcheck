"""MyNeta (Association for Democratic Reforms) - Indian candidate affidavits.

MyNeta republishes the sworn affidavits that every candidate for Indian office must
file with the Election Commission. ADR states that it adds and subtracts nothing:
the underlying documents are the candidates' own declarations, already public.

What makes this source unusually valuable is the "Other Elections" block on a
candidate page, which lists the same person's declared assets at each election they
have contested. That turns a snapshot into a time series - the single most
useful fact for the question "how is this politician doing, and with whose money?"

IMPORTANT - what this extractor does NOT do:
  * It does not score, rank or flag anyone.
  * A declared asset rise is not evidence of wrongdoing. Assets rise with property
    prices, inheritance, business income and inflation. The app's job is to show
    the declared numbers next to their dates and sources, and let the reader think.
  * Criminal cases here are *declared pending cases*, not convictions. Conflating
    the two is defamatory and factually wrong.

robots.txt (checked 2026-09-20) disallows only /*print=true and /*printer=true,
neither of which this extractor touches.
"""

from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..base import Claim, Extractor, Office

BASE = "https://www.myneta.info/"

# "Rs 8,05,85,824" / "Rs&nbsp;8,05,85,824" -> 80585824
MONEY = re.compile(r"Rs[\s ]*([\d,]+)")
# "Age: 52"
AGE = re.compile(r"Age\s*:?\s*(\d{1,3})")


def rupees(text: str) -> float | None:
    """Parse an Indian-format rupee amount. Returns None for 'Nil' / unreadable."""
    if not text:
        return None
    m = MONEY.search(text.replace(" ", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _dedupe(it) -> list[int]:
    """Order-preserving dedupe."""
    seen, out = set(), []
    for x in it:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


class MyNeta(Extractor):
    name = "myneta"
    version = "0.2"
    country = "IND"
    publisher = "Association for Democratic Reforms (myneta.info)"
    licence = "Public affidavit data sourced from the Election Commission of India"
    robots_checked = True  # verified 2026-09-20; see module docstring

    def __init__(self, con, election: str = "LokSabha2024", mode: str = "full", **kw):
        super().__init__(con, **kw)
        self.election = election
        self.jurisdiction = f"IN/{election}"
        self.mode = mode  # 'full' = every seat via constituency pages; 'summary' = demo

    def _url(self, query: str) -> str:
        return urljoin(BASE, f"{self.election}/{query}")

    # ------------------------------------------------------------------ index
    def candidate_ids(self) -> list[int]:
        ids = (
            self._ids_from_summary() if self.mode == "summary"
            else self._ids_from_constituencies()
        )
        return ids[: self.limit] if self.limit else ids

    def _ids_from_summary(self) -> list[int]:
        """The analysed-winners summary page. Fast, but only lists ~18 members."""
        sid, html = self.archive.text(
            self._url("index.php?action=summary&subAction=winner_analyzed&sort=candidate")
        )
        self.stats.documents += 1
        return _dedupe(int(i) for i in re.findall(r"candidate_id=(\d+)", html))

    def _ids_from_constituencies(self) -> list[int]:
        """Every seat's winner: landing page -> constituency page -> winning candidate.

        MyNeta has no single page listing all 543 winners, so this is the only
        complete route. The election landing page links every seat directly, which
        is why we do NOT walk state pages: `show_constituencies&state_id=N` is a
        paginated candidate list with no winner marker, and following it costs 36
        extra requests to arrive at less information.
        """
        _, landing = self.archive.text(self._url(""))
        self.stats.documents += 1
        seats = _dedupe(
            int(m) for m in re.findall(r"constituency_id=(\d+)", landing)
        )
        print(f"  {len(seats)} seats linked from the election landing page", flush=True)

        winners: list[int] = []
        for n, seat in enumerate(seats, 1):
            if n % 100 == 0:
                print(f"  seats indexed {n}/{len(seats)}", flush=True)
            try:
                _, html = self.archive.text(
                    self._url(f"index.php?action=show_candidates&constituency_id={seat}")
                )
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"seat {seat}: {exc}")
                continue
            self.stats.documents += 1
            cid = self._winner_of(html)
            if cid:
                winners.append(cid)
        return _dedupe(winners)

    @staticmethod
    def _winner_of(html: str) -> int | None:
        """The candidate_id in the row flagged Winner on a constituency page."""
        soup = BeautifulSoup(html, "lxml")
        for tr in soup.find_all("tr"):
            if not re.search(r"\bwinner\b", tr.get_text(" "), re.I):
                continue
            a = tr.find("a", href=re.compile(r"candidate_id=(\d+)"))
            if a:
                return int(re.search(r"candidate_id=(\d+)", a["href"]).group(1))
        return None

    # -------------------------------------------------------------- harvesting
    def harvest(self) -> Iterator[Claim | Office]:
        ids = self.candidate_ids()
        print(f"  index complete: {len(ids)} members to fetch", flush=True)
        for n, cid in enumerate(ids, 1):
            if n % 50 == 0:
                print(f"  ...{n}/{len(ids)}", flush=True)
            url = self._url(f"candidate.php?candidate_id={cid}")
            try:
                sid, html = self.archive.text(url)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"fetch {cid}: {exc}")
                continue
            self.stats.documents += 1
            yield from self._parse(html, sid, url)

    def _parse(self, html: str, sid: int, url: str) -> Iterator[Claim | Office]:
        soup = BeautifulSoup(html, "lxml")
        text = clean(soup.get_text(" "))

        name = self._name(soup)
        if not name:
            self.stats.errors.append(f"no name found at {url}")
            return

        party = self._after(soup, r"Party\s*:\s*(.+?)(?:S/o|D/o|W/o|Age|$)")
        constituency, region = self._seat(soup)
        won = "(Winner)" in soup.get_text(" ")
        ctx = f"{constituency or '?'} {self.election}"

        yield Office(
            person_name=name,
            jurisdiction=self.jurisdiction,
            source_id=sid,
            title="MP" if won else "candidate",
            constituency=constituency,
            region=region,
            party=party,
            won=won,
        )

        def claim(pred, **kw):
            return Claim(
                predicate=pred, source_id=sid, person_name=name, context=ctx, **kw
            )

        # --- headline declarations for THIS election -------------------------
        assets = self._labelled(text, r"Assets\s*:\s*(Rs[\s ]*[\d,]+)")
        liabs = self._labelled(text, r"Liabilities\s*:\s*(Rs[\s ]*[\d,]+)")
        year = self._year()

        if assets is not None:
            yield claim("declared_assets", value_num=assets, currency="INR", as_of=year)
        if liabs is not None:
            yield claim("declared_liabilities", value_num=liabs, currency="INR", as_of=year)

        if party:
            yield claim("party_affiliation", value_text=party, as_of=year)

        m = AGE.search(text)
        if m:
            yield claim("age", value_num=float(m.group(1)), unit="years", as_of=year)

        edu = self._after(soup, r"Category\s*:\s*([A-Za-z0-9 .\-]+)")
        if edu:
            yield claim("education_level", value_text=edu, as_of=year)

        prof = self._after(soup, r"Self Profession\s*:\s*(.+?)(?:Spouse|$)")
        if prof:
            yield claim("self_profession", value_text=prof, as_of=year)

        cases = self._cases(text)
        if cases is not None:
            yield claim(
                "criminal_cases_declared",
                value_num=float(cases),
                unit="declared pending cases",
                as_of=year,
                note="Self-declared pending cases from the ECI affidavit. NOT convictions.",
            )

        # --- the time series: declarations at previous elections -------------
        yield from self._other_elections(soup, name, sid, ctx)

    # ------------------------------------------------------------------ bits
    def _name(self, soup: BeautifulSoup) -> str | None:
        """Candidate name.

        The <title> is the reliable source: 'Name(Party):Constituency- SEAT(STATE)'.
        The first <h2> on the page is the election name, not the candidate - reading
        it instead collapses every candidate into a single person, which is the
        worst failure this system can have.
        """
        t = soup.find("title")
        if t:
            head = clean(t.get_text()).split("(")[0]
            head = head.split(":")[0]
            if clean(head):
                return clean(head)

        # Fallback: the h2 carrying the (Winner)/(Loser) marker.
        for h in soup.find_all(["h2", "h1"]):
            txt = clean(h.get_text(" "))
            if re.search(r"\((Winner|Loser)\)", txt, re.I):
                return clean(re.sub(r"\((Winner|Loser)\)", "", txt, flags=re.I)) or None
        return None

    def _seat(self, soup: BeautifulSoup) -> tuple[str | None, str | None]:
        """Constituency and state, from the breadcrumb: Home -> LS 2024 -> STATE -> SEAT."""
        crumbs = [
            clean(a.get_text())
            for a in soup.find_all("a")
            if a.get("href", "").startswith(("constituencies.php", "index.php?action=show_constituencies", "state.php"))
        ]
        title = soup.find("title")
        if title:
            m = re.search(r"Constituency-\s*([A-Z .\-]+)\(([A-Z &.\-]+)\)", clean(title.get_text()))
            if m:
                return clean(m.group(1)), clean(m.group(2))
        if len(crumbs) >= 2:
            return crumbs[-1], crumbs[-2]
        return (crumbs[-1] if crumbs else None), None

    def _labelled(self, text: str, pattern: str) -> float | None:
        m = re.search(pattern, text, re.I)
        return rupees(m.group(1)) if m else None

    def _after(self, soup: BeautifulSoup, pattern: str) -> str | None:
        m = re.search(pattern, clean(soup.get_text(" ")), re.I)
        if not m:
            return None
        v = clean(m.group(1))
        return v[:180] or None

    def _cases(self, text: str) -> int | None:
        if re.search(r"No criminal cases", text, re.I):
            return 0
        m = re.search(r"(\d+)\s*(?:criminal\s*)?cases?\s*(?:pending|declared)", text, re.I)
        return int(m.group(1)) if m else None

    def _year(self) -> str:
        m = re.search(r"(\d{4})", self.election)
        return f"{m.group(1)}-01-01" if m else ""

    def _other_elections(self, soup, name, sid, ctx) -> Iterator[Claim]:
        """Parse the 'Other Elections' table - the same person's past declarations."""
        # Anchor on the column header rather than the section title: the section
        # title sits in its own single-cell row, and find_next('table') from it
        # lands on the Assets/Liabilities summary instead.
        table = None
        for tbl in soup.find_all("table"):
            if any(
                "declaration in" in clean(c.get_text()).lower()
                for row in tbl.find_all("tr")[:3]
                for c in row.find_all(["td", "th"])
            ):
                table = tbl
                break
        if table is None:
            return
        for tr in table.find_all("tr"):
            cells = [clean(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            label = cells[0]
            ym = re.search(r"(19|20)\d{2}", label)
            if not ym:
                continue
            amount = rupees(cells[1])
            if amount is None:
                continue
            yield Claim(
                predicate="declared_assets",
                source_id=sid,
                person_name=name,
                context=ctx,
                value_num=amount,
                currency="INR",
                as_of=f"{ym.group(0)}-01-01",
                confidence=0.9,   # parsed from a summary table, not the affidavit itself
                note=f"Declared at: {label}",
            )
            if len(cells) >= 3 and cells[2].isdigit():
                yield Claim(
                    predicate="criminal_cases_declared",
                    source_id=sid,
                    person_name=name,
                    context=ctx,
                    value_num=float(cells[2]),
                    unit="declared pending cases",
                    as_of=f"{ym.group(0)}-01-01",
                    confidence=0.9,
                    note=f"Declared at: {label}. Pending cases, NOT convictions.",
                )
