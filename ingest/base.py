"""The portable extractor contract.

Rosie - Operação Serenata de Amor's anomaly-detection robot - found thousands of
suspicious reimbursements in Brazil, and then never left Brazil. It was built
against one expense stream on one portal. Every other country that wanted the same
thing had to start from scratch.

The point of this class is that the third country should cost a week, not two
years. An extractor's only job is to turn one publisher's documents into
`Claim` objects against the shared vocabulary in schema.PREDICATES. Fetching,
archiving, hashing, rate limiting, entity resolution and persistence are all
handled here and are not an extractor's business.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator

from .archive import Archive
from .resolve import find_or_create
from .schema import PREDICATES


@dataclass
class Claim:
    """One dated, sourced assertion about a person."""

    predicate: str
    source_id: int
    person_name: str | None = None
    person_id: int | None = None
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    currency: str | None = None
    as_of: str | None = None
    confidence: float = 1.0
    note: str | None = None
    context: str | None = None          # helps entity resolution, not stored as fact

    def __post_init__(self):
        if self.predicate not in PREDICATES:
            raise ValueError(
                f"predicate {self.predicate!r} is not in the shared vocabulary. "
                f"Add it to schema.PREDICATES deliberately, or map to an existing one - "
                f"free-text predicates make cross-country comparison impossible."
            )
        if self.value_num is None and self.value_text is None:
            raise ValueError(f"claim {self.predicate!r} has no value")


@dataclass
class Office:
    person_name: str
    jurisdiction: str
    source_id: int
    title: str | None = None
    constituency: str | None = None
    region: str | None = None
    party: str | None = None
    won: bool | None = None


@dataclass
class Stats:
    documents: int = 0
    claims: int = 0
    offices: int = 0
    people_created: int = 0
    people_matched: int = 0
    people_review: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{self.documents} documents, {self.claims} claims, {self.offices} offices | "
            f"people: {self.people_created} new, {self.people_matched} matched, "
            f"{self.people_review} queued for review"
            + (f" | {len(self.errors)} errors" if self.errors else "")
        )


class Extractor(ABC):
    """Base class. Subclass per publisher, implement `harvest`."""

    name: str = "unnamed"
    version: str = "0.1"
    country: str = "XXX"           # ISO3
    publisher: str = ""
    licence: str = ""
    # Set by the subclass after reading the publisher's robots.txt and terms.
    robots_checked: bool = False

    def __init__(self, con: sqlite3.Connection, delay: float = 1.0, limit: int | None = None):
        self.con = con
        self.limit = limit
        self.archive = Archive(con, self.name, self.version, delay=delay)
        self.stats = Stats()

    # ---------------------------------------------------------------- contract
    @abstractmethod
    def harvest(self) -> Iterator[Claim | Office]:
        """Yield Claims and Offices. Use `self.archive.text(url)` to fetch."""

    # ------------------------------------------------------------------- run
    def run(self) -> Stats:
        if not self.robots_checked:
            raise RuntimeError(
                f"{self.name}: set robots_checked = True only after actually reading "
                f"{self.publisher}'s robots.txt and terms of use."
            )
        for item in self.harvest():
            try:
                if isinstance(item, Claim):
                    self._write_claim(item)
                elif isinstance(item, Office):
                    self._write_office(item)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"{type(exc).__name__}: {exc}")
        self.con.commit()
        return self.stats

    # ------------------------------------------------------------------ write
    def _person(self, name: str, source_id: int, context: str | None) -> int:
        pid, action = find_or_create(
            self.con, self.country, name, source_id, context=context
        )
        setattr(
            self.stats,
            {"created": "people_created", "matched": "people_matched",
             "review": "people_review"}[action],
            getattr(self.stats, {"created": "people_created", "matched": "people_matched",
                                 "review": "people_review"}[action]) + 1,
        )
        return pid

    def _write_claim(self, c: Claim) -> None:
        pid = c.person_id
        if pid is None and c.person_name:
            pid = self._person(c.person_name, c.source_id, c.context)
        cur = self.con.execute(
            """INSERT OR IGNORE INTO claims
               (person_id, subject, predicate, value_num, value_text, unit, currency,
                as_of, source_id, extractor, confidence, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, "person", c.predicate, c.value_num, c.value_text, c.unit,
             c.currency, c.as_of, c.source_id, self.name, c.confidence, c.note),
        )
        if cur.rowcount:
            self.stats.claims += 1

    def _write_office(self, o: Office) -> None:
        pid = self._person(o.person_name, o.source_id, o.constituency)
        cur = self.con.execute(
            """INSERT OR IGNORE INTO offices
               (person_id, jurisdiction, title, constituency, region, party, won,
                source_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, o.jurisdiction, o.title, o.constituency, o.region, o.party,
             None if o.won is None else int(o.won), o.source_id),
        )
        if cur.rowcount:
            self.stats.offices += 1
