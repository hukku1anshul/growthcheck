"""The claim store.

The design rule for the whole of Product B is one sentence:

    Nothing enters this database without a receipt.

Concretely, every fact is a row in `claims`, and every row in `claims` has a
non-null `source_id` pointing at a row in `sources`, which records the URL, the
fetch time, the sha256 and the local path of the archived document the fact was
read out of. There is no code path that writes a claim without one. That is what
makes the output defensible when someone - very reasonably - asks "says who?"

Why a claim table rather than a wide `politicians` table:

  * Sources disagree. Two portals will report different asset totals for the same
    person on the same date. A wide table forces you to silently pick a winner; a
    claim table records both and shows the conflict.
  * Facts are dated. "Assets" is meaningless without "as declared on". The same
    person has a different value at every election.
  * Corrections happen. A government portal can silently restate a figure. Keeping
    the old claim plus its archived document is the only way to notice.

Nothing here computes a score, a ranking or a judgement of a politician. That is
deliberate - see docs/ETHICS.md.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
PRAGMA journal_mode = WAL;

-- Every document we ever read, archived and hashed.
CREATE TABLE IF NOT EXISTS sources (
    id                INTEGER PRIMARY KEY,
    url               TEXT    NOT NULL,
    fetched_at        TEXT    NOT NULL,          -- ISO8601 UTC
    http_status       INTEGER,
    content_type      TEXT,
    bytes             INTEGER,
    sha256            TEXT    NOT NULL,
    archive_path      TEXT    NOT NULL,          -- relative to data/archive/
    extractor         TEXT    NOT NULL,
    extractor_version TEXT    NOT NULL,
    UNIQUE (url, sha256)
);

-- A human. Deliberately thin: almost everything about a person is a dated claim.
CREATE TABLE IF NOT EXISTS persons (
    id          INTEGER PRIMARY KEY,
    country     TEXT NOT NULL,                   -- ISO3
    full_name   TEXT NOT NULL,
    norm_name   TEXT NOT NULL,                   -- see resolve.normalise_name
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_persons_norm ON persons(country, norm_name);

-- Every spelling we have seen, with the document it appeared in. Entity
-- resolution is the hardest part of this system, so its working is kept, not
-- thrown away.
CREATE TABLE IF NOT EXISTS person_aliases (
    id         INTEGER PRIMARY KEY,
    person_id  INTEGER NOT NULL REFERENCES persons(id),
    alias      TEXT    NOT NULL,
    norm_alias TEXT    NOT NULL,
    source_id  INTEGER NOT NULL REFERENCES sources(id),
    UNIQUE (person_id, alias)
);

-- Candidacy or office held.
CREATE TABLE IF NOT EXISTS offices (
    id           INTEGER PRIMARY KEY,
    person_id    INTEGER NOT NULL REFERENCES persons(id),
    jurisdiction TEXT    NOT NULL,               -- e.g. 'IN/LokSabha/2024'
    title        TEXT,                           -- 'MP', 'MLA', 'candidate'
    constituency TEXT,
    region       TEXT,
    party        TEXT,
    won          INTEGER,                        -- 1 / 0 / NULL if unknown
    term_start   TEXT,
    term_end     TEXT,
    source_id    INTEGER NOT NULL REFERENCES sources(id),
    UNIQUE (person_id, jurisdiction, constituency)
);

-- The heart of the system. One dated, sourced assertion.
CREATE TABLE IF NOT EXISTS claims (
    id         INTEGER PRIMARY KEY,
    person_id  INTEGER REFERENCES persons(id),
    subject    TEXT    NOT NULL,                 -- 'person' | 'scheme' | 'contract'
    subject_id TEXT,                             -- external id when not a person
    predicate  TEXT    NOT NULL,                 -- see PREDICATES
    value_num  REAL,
    value_text TEXT,
    unit       TEXT,
    currency   TEXT,
    as_of      TEXT,                             -- the date the fact refers to
    source_id  INTEGER NOT NULL REFERENCES sources(id),
    extractor  TEXT    NOT NULL,
    confidence REAL    NOT NULL DEFAULT 1.0,     -- <1 when parsed heuristically
    note       TEXT
);

-- Uniqueness deliberately includes `note`, because a person can legitimately file
-- two declarations dated to the same year - a by-election, or two seats. Keying
-- only on (person, predicate, as_of, source) silently dropped the second: 31
-- declarations across 26 Indian members vanished that way. COALESCE is required
-- because SQLite treats NULLs as distinct in a UNIQUE index, which would disable
-- de-duplication entirely for the many claims that carry no note.
CREATE UNIQUE INDEX IF NOT EXISTS ux_claims
    ON claims(person_id, predicate, as_of, source_id, COALESCE(note, ''));
CREATE INDEX IF NOT EXISTS ix_claims_person ON claims(person_id, predicate, as_of);
CREATE INDEX IF NOT EXISTS ix_claims_pred   ON claims(predicate);

-- Two extractors disagreeing about the same fact. Populated by a reconciliation
-- pass; surfaced in the UI rather than resolved silently.
CREATE VIEW IF NOT EXISTS conflicts AS
SELECT person_id, predicate, as_of,
       COUNT(DISTINCT COALESCE(value_num, value_text)) AS distinct_values,
       COUNT(*) AS n_claims,
       GROUP_CONCAT(DISTINCT extractor) AS extractors
FROM claims
WHERE person_id IS NOT NULL
GROUP BY person_id, predicate, as_of
HAVING distinct_values > 1;
"""

# Controlled vocabulary. An extractor emitting anything else is a bug, because an
# uncontrolled predicate string means the fact can never be compared across
# countries - which is the entire point of a portable engine.
PREDICATES = {
    "declared_assets",        # value_num, currency - total assets on an affidavit
    "declared_liabilities",
    "declared_movable_assets",
    "declared_immovable_assets",
    "criminal_cases_declared",  # value_num - count
    "serious_criminal_cases",
    "education_level",        # value_text
    "self_profession",        # value_text
    "age",                    # value_num
    "party_affiliation",      # value_text
    "election_contested",     # value_text - jurisdiction label
    # declared outside interests. Distinct from declared_assets: an asset total is
    # a stock at one date, an outside payment is a dated flow with a named payer.
    # Collapsing them would make India and the UK look comparable when they are not.
    "outside_earnings",       # value_num + currency, one registered payment
    "registered_interest",    # value_text, a declared interest with no cash figure
    # money-out side, for procurement/budget extractors
    "budget_allocated",
    "budget_spent",
    "contract_awarded",
    # internal bookkeeping, written by resolve.py rather than by an extractor
    "possible_duplicate_of",
}


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row

    # `PRAGMA foreign_keys` is per-connection AND is a silent no-op inside a
    # transaction. sqlite3.executescript() issues a COMMIT before running, so a
    # pragma placed in the DDL string appears to work and does nothing at all.
    # The claim->source foreign key is the mechanism that enforces this project's
    # one hard rule, so it is set explicitly here and then verified.
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(DDL)
    con.execute("PRAGMA foreign_keys = ON")

    if not con.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError(
            "SQLite refused to enable foreign keys; refusing to open a claim store "
            "that cannot guarantee every claim has a source document."
        )
    return con
