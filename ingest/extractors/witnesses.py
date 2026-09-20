"""Extra witnesses: OpenSanctions and Wikidata.

Until now most facts in the store had one witness, and a few had two. These two
sources add a third and fourth for people we ALREADY hold - which is the point.

    OpenSanctions in_sansad   18,559 Lok and Rajya Sabha members as structured
                              entities, with aliases and birth dates. Free bulk
                              CSV, refreshed weekly.
    Wikidata                  birth date, education, party, Wikipedia link, for
                              most sitting members. Free SPARQL, no key.

WHY THESE ENRICH AND NEVER EXPAND
---------------------------------
Both extractors resolve against EXISTING people and skip anything they cannot
match. They never call find_or_create, so they cannot invent a politician.

That restraint is deliberate. OpenSanctions carries 18,559 members across
decades of both houses; importing all of them would triple the person table with
people we hold no other fact about, and every one of those rows would then be a
new opportunity for the resolver to mis-merge somebody. A witness that doubles
the size of the thing it is meant to corroborate is not a witness.

WIKIDATA RATE LIMITS ARE REAL
-----------------------------
Three of five exploratory queries in development returned HTTP 429 or timed out.
The public SPARQL endpoint is shared infrastructure with a hard query budget, so
this batches names into a single VALUES clause, sleeps between batches, backs off
on 429, and gives up rather than hammering. It must never be called from a web
request - it is a build-step enrichment and nothing else.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from typing import Iterator

from ..base import Claim, Extractor
from ..resolve import AUTO_MERGE, initials_key, normalise_name, score

OPENSANCTIONS = (
    "https://data.opensanctions.org/datasets/latest/in_sansad/targets.simple.csv"
)
WDQS = "https://query.wikidata.org/sparql"

WD_BATCH = 60          # names per SPARQL query
WD_PAUSE = 4.0         # seconds between queries
WD_MAX_RETRY = 3


class Witnesses(Extractor):
    """Base behaviour shared by both witness sources."""

    country = "IND"
    robots_checked = True

    def _existing(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT id, full_name, norm_name FROM persons WHERE country = ?",
            (self.country,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _match(norm: str, people: list[dict]) -> int | None:
        """Resolve to an existing person, or None. Never creates."""
        key = initials_key(norm)
        toks = set(norm.split())
        best, best_s = None, 0.0
        for c in people:
            if initials_key(c["norm_name"]) != key and not (
                toks & {t for t in c["norm_name"].split() if len(t) >= 3}
            ):
                continue
            s = score(norm, c["norm_name"])
            if s > best_s:
                best, best_s = c, s
        # Only an unambiguous match counts. A witness that guesses is worse than
        # no witness, because it launders a guess into apparent corroboration.
        return best["id"] if best and best_s >= AUTO_MERGE else None


class OpenSanctionsWitness(Witnesses):
    name = "opensanctions"
    version = "0.1"
    publisher = "OpenSanctions - India Lok and Rajya Sabha Members (in_sansad)"
    licence = "CC-BY-NC 4.0 (OpenSanctions); non-commercial use"

    def harvest(self) -> Iterator[Claim]:
        sid, body = self.archive.get(OPENSANCTIONS)
        self.stats.documents += 1
        people = self._existing()
        print(f"  {len(people)} existing Indian people to corroborate", flush=True)

        text = body.decode("utf-8", errors="replace")
        matched = aliases = dates = 0
        seen: set[int] = set()
        for row in csv.DictReader(io.StringIO(text)):
            nm = (row.get("name") or "").strip()
            if not nm:
                continue
            pid = self._match(normalise_name(nm), people)
            if pid is None:
                continue
            matched += 1

            # every spelling this publisher uses, recorded as an alias
            alts = [a.strip() for a in (row.get("aliases") or "").split(";") if a.strip()]
            for alt in [nm] + alts:
                try:
                    cur = self.con.execute(
                        "INSERT OR IGNORE INTO person_aliases "
                        "(person_id, alias, norm_alias, source_id) VALUES (?,?,?,?)",
                        (pid, alt, normalise_name(alt), sid),
                    )
                    aliases += cur.rowcount
                except Exception:  # noqa: BLE001
                    pass

            dob = (row.get("birth_date") or "").strip()
            if re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", dob):
                dates += 1
                if pid not in seen:
                    seen.add(pid)
                yield Claim(
                    predicate="age",
                    source_id=sid,
                    person_id=pid,
                    value_num=float(2026 - int(dob[:4])),
                    unit="years",
                    as_of="2026-01-01",
                    confidence=0.9,
                    note=f"Derived from birth date {dob} published by OpenSanctions "
                         f"(in_sansad). A third witness for age.",
                )
        self.con.commit()
        print(f"  matched {matched} rows to existing people; "
              f"{aliases} new aliases; {dates} birth dates", flush=True)

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        return []


class WikidataWitness(Witnesses):
    name = "wikidata"
    version = "0.1"
    publisher = "Wikidata (query.wikidata.org SPARQL)"
    licence = "CC0"

    def harvest(self) -> Iterator[Claim]:
        import requests

        people = self._existing()
        if self.limit:
            people = people[: self.limit]
        print(f"  querying Wikidata for {len(people)} people "
              f"in batches of {WD_BATCH}", flush=True)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "politicalfindings/0.1 (open-source public-data research)",
            "Accept": "application/sparql-results+json",
        })

        found = 0
        for i in range(0, len(people), WD_BATCH):
            batch = people[i:i + WD_BATCH]
            values = " ".join(f'"{p["full_name"]}"@en' for p in batch
                              if '"' not in p["full_name"])
            if not values:
                continue
            q = f"""
SELECT ?name ?dob ?partyLabel ?article WHERE {{
  VALUES ?name {{ {values} }}
  ?p rdfs:label ?name .
  ?p wdt:P31 wd:Q5 .
  OPTIONAL {{ ?p wdt:P569 ?dob }}
  OPTIONAL {{ ?p wdt:P102 ?party }}
  OPTIONAL {{ ?article schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}}"""
            data = None
            for attempt in range(1, WD_MAX_RETRY + 1):
                try:
                    r = session.get(WDQS, params={"query": q, "format": "json"}, timeout=120)
                    if r.status_code == 429:
                        wait = int(r.headers.get("Retry-After") or 60)
                        print(f"    429 - backing off {wait}s", flush=True)
                        time.sleep(wait)
                        continue
                    r.raise_for_status()
                    data = r.json()
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == WD_MAX_RETRY:
                        self.stats.errors.append(f"wikidata batch {i}: {exc}")
                    else:
                        time.sleep(WD_PAUSE * attempt)
            if data is None:
                continue

            # Archive the response so the claim has a receipt like any other.
            sid = self._archive_response(q, data)

            by_name = {p["full_name"]: p["id"] for p in batch}
            for b in data.get("results", {}).get("bindings", []):
                nm = b.get("name", {}).get("value")
                pid = by_name.get(nm)
                if pid is None:
                    continue
                dob = (b.get("dob") or {}).get("value", "")[:10]
                party = (b.get("partyLabel") or {}).get("value")
                article = (b.get("article") or {}).get("value")
                if re.match(r"^\d{4}-\d{2}-\d{2}$", dob):
                    found += 1
                    yield Claim(
                        predicate="age", source_id=sid, person_id=pid,
                        value_num=float(2026 - int(dob[:4])), unit="years",
                        as_of="2026-01-01", confidence=0.85,
                        note=f"Derived from birth date {dob} on Wikidata. A fourth "
                             f"witness for age. Wikidata is community-edited, which "
                             f"is why this carries lower confidence than an "
                             f"affidavit or a parliamentary register."
                             + (f" Wikipedia: {article}" if article else ""),
                    )
                if party:
                    yield Claim(
                        predicate="party_affiliation", source_id=sid, person_id=pid,
                        value_text=party, as_of="2026-01-01", confidence=0.85,
                        note="Wikidata (community-edited).",
                    )
            print(f"    batch {i//WD_BATCH + 1}: {found} facts so far", flush=True)
            time.sleep(WD_PAUSE)

    def _archive_response(self, query: str, data: dict) -> int:
        """Store the SPARQL response as an archived document with a receipt."""
        import hashlib
        from datetime import datetime, timezone
        from pathlib import Path

        from ..archive import ARCHIVE

        body = json.dumps(data, sort_keys=True).encode()
        digest = hashlib.sha256(body).hexdigest()
        rel = Path("query.wikidata.org") / digest[:2] / f"{digest}.bin"
        dest = ARCHIVE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(body)
        meta = {
            "archive_path": str(rel).replace("\\", "/"),
            "sha256": digest,
            "bytes": len(body),
            "content_type": "application/sparql-results+json",
            "http_status": 200,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        url = f"{WDQS}#{digest[:16]}"
        self.archive._remember(url, meta)
        self.stats.documents += 1
        return self.archive._register(url, body, meta)

    def reparse(self, body: bytes, source_id: int, url: str) -> list[Claim]:
        return []
