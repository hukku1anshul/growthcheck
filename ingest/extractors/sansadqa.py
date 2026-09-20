"""Lok Sabha questions - what each member actually raised, and with which ministry.

Source: "Indian parliament proceedings raw dataset", Zenodo record 18146342,
CC-BY-4.0, scraped from sansad.in and published 2026-01-04.

WHAT THIS ADDS
--------------
PRS already tells us a member asked 85 questions. A count is a productivity
metric; it says nothing about what the member cares about or who they hold to
account. This dataset carries, for all 37,271 questions of the 18th Lok Sabha:

    subject     "Toll Plazas in Dharmapuri, Tamil Nadu"
    ministry    ROAD TRANSPORT AND HIGHWAYS
    type        STARRED (oral answer, supplementaries allowed) / UNSTARRED
    date        the sitting it was asked on
    member      attribution, 465 distinct names

That turns "asked 85 questions" into "asked about toll plazas, rail
connectivity, farmer skill development and tourism, and questioned Health,
Railways and Agriculture most often" - which is what a constituent actually
wants to know.

WHAT IT DOES NOT CONTAIN - CHECKED, NOT ASSUMED
-----------------------------------------------
The file has `questionText` and `answerText` columns and BOTH ARE EMPTY in all
37,271 rows. An earlier plan for this project claimed the dataset carried "the
government's answer text"; that was wrong, and it was wrong because the column
existed and nobody checked whether it was populated.

The actual text of each question and the ministry's reply lives in a per-question
PDF on sansad.in, linked from `questionsFilePath` (37,266 links). Retrieving
those is a 37,000-document scrape of a government site and a separate project.
Until someone does it, this extractor claims subjects and ministries only, and
the UI must not imply otherwise.

STARRED vs UNSTARRED matters and is preserved: a starred question is answered
orally on the floor and the member can ask supplementaries; an unstarred one gets
a written reply. 2,473 of 37,271 are starred. Treating them as equivalent would
overstate how much floor time a member actually commanded.
"""

from __future__ import annotations

import ast
import io
import re
from collections import Counter
from typing import Iterator

from ..base import Claim, Extractor

ZENODO_RECORD = "https://zenodo.org/api/records/18146342"
FILE_NAME = "Loksabha_questions.xlsx"
CITATION = (
    "Indian parliament proceedings raw dataset (Zenodo 18146342, CC-BY-4.0), "
    "scraped from sansad.in"
)

# "18.12.2025" -> 2025-12-18
DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def _date(raw) -> str | None:
    m = DATE.match(str(raw or "").strip())
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def _members(raw) -> list[str]:
    """The member column is a stringified Python list: "['Shri Mani A']".

    A question can be tabled by several members jointly, and each of them asked
    it - so each gets the claim. Splitting on commas would break names that
    contain them.
    """
    s = str(raw or "").strip()
    if not s or s == "None":
        return []
    if s.startswith("["):
        try:
            v = ast.literal_eval(s)
            if isinstance(v, (list, tuple)):
                return [str(x).strip() for x in v if str(x).strip()]
        except (ValueError, SyntaxError):
            pass
    return [s]


class SansadQA(Extractor):
    name = "sansadqa"
    version = "0.1"
    country = "IND"
    publisher = "Zenodo 18146342 (CC-BY-4.0), sourced from sansad.in"
    licence = "CC-BY-4.0"
    robots_checked = True  # a licensed bulk dataset, not a crawl

    jurisdiction = "IN/LokSabha2024"

    def __init__(self, con, max_per_member: int = 40, **kw):
        super().__init__(con, **kw)
        # One claim per question would be 37,271 rows for 465 people, and the
        # web bundle has to stay loadable. The full set stays in claims.db; the
        # export samples. `limit` still caps total rows for a quick run.
        self.max_per_member = max_per_member
        self._people: dict[str, int] = {}

    # ------------------------------------------------------------------ fetch
    def _rows(self) -> list[dict]:
        import openpyxl

        sid, body = self.archive.get(ZENODO_RECORD)
        self.stats.documents += 1
        import json

        files = {f["key"]: f["links"]["self"] for f in json.loads(body)["files"]}
        if FILE_NAME not in files:
            self.stats.errors.append(f"{FILE_NAME} not in the Zenodo record")
            return []

        fsid, xlsx = self.archive.get(files[FILE_NAME])
        self.stats.documents += 1
        self._source_id = fsid

        ws = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True).active
        it = ws.iter_rows(values_only=True)
        hdr = list(next(it))
        out = []
        for row in it:
            out.append(dict(zip(hdr, row)))
        print(f"  {len(out):,} questions loaded", flush=True)
        return out

    # -------------------------------------------------------------- harvesting
    def harvest(self) -> Iterator[Claim]:
        rows = self._rows()
        if not rows:
            return
        sid = self._source_id

        per_member: Counter = Counter()
        ministries: dict[str, Counter] = {}
        starred: Counter = Counter()
        totals: Counter = Counter()

        emitted = 0
        for r in rows:
            subject = re.sub(r"\s+", " ", str(r.get("subjects") or "")).strip()
            ministry = str(r.get("ministry") or "").strip()
            qtype = str(r.get("type") or "").strip().upper()
            when = _date(r.get("date"))
            if not subject or not when:
                continue

            for member in _members(r.get("member")):
                totals[member] += 1
                ministries.setdefault(member, Counter())[ministry] += 1
                if qtype == "STARRED":
                    starred[member] += 1
                if per_member[member] >= self.max_per_member:
                    continue
                per_member[member] += 1
                emitted += 1
                yield Claim(
                    predicate="parliamentary_question",
                    source_id=sid,
                    person_name=member,
                    context="Lok Sabha",
                    value_text=subject[:300],
                    as_of=when,
                    note=(
                        f"{qtype.title()} question to the Ministry of "
                        f"{ministry.title()}. Question no {r.get('quesNo')}, "
                        f"session {r.get('sessionNo')}. Full text and the "
                        f"ministry's reply are in the linked PDF, not in this "
                        f"dataset: {r.get('questionsFilePath')}"
                    ),
                )
                if self.limit and emitted >= self.limit:
                    break
            if self.limit and emitted >= self.limit:
                break

        # A per-member summary, so the UI can show a profile without loading
        # every question.
        for member, n in totals.items():
            top = ministries[member].most_common(5)
            yield Claim(
                predicate="questions_topics",
                source_id=sid,
                person_name=member,
                context="Lok Sabha",
                value_text="; ".join(f"{m.title()} ({c})" for m, c in top),
                value_num=float(n),
                unit="questions",
                as_of="2024-01-01",
                note=(
                    f"{n} questions in the 18th Lok Sabha carry this member's name, "
                    f"of which {starred[member]} are starred (answered orally on the "
                    f"floor). Ministries most often questioned, with counts. "
                    f"COUNTING NOTE: questions are frequently tabled JOINTLY - "
                    f"37,271 questions carry 156,760 names, a mean of 4.2 members "
                    f"each - and every signatory is credited here. This is "
                    f"therefore NOT the same measure as the PRS 'questions asked' "
                    f"figure shown elsewhere on this page, and the two must not be "
                    f"compared. Source: {CITATION}."
                ),
            )
        print(f"  {emitted:,} question claims + {len(totals)} member profiles", flush=True)

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        """Bulk spreadsheet: the change checker compares the file hash, not rows."""
        return []
