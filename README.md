# Political Findings

Two tools, one principle: **people should be able to check the claim themselves.**

**Product A — country growth explorer.** 50–100+ years of economic and political
indicators for ~200 countries, every series filterable and explained in plain
language, countries compared side by side, and the major decisions, crises, regime
changes and elections marked directly on the chart.

**Product B — the claim store.** A portable engine for reading public government
and civic data about individual politicians, where no fact can be stored without an
archived, hashed copy of the document it came from.

Read [docs/ETHICS.md](docs/ETHICS.md) before changing anything. It is short, and it
explains which lines are load-bearing.

---

## Quick start

```bash
pip install -r requirements.txt
python -m etl.build                 # ~5 min first run, then cached
cd web && npm install && npm run dev
```

Open http://localhost:5174.

To rebuild only a subset while iterating:

```bash
python -m etl.build --quick
```

To run the politician extractor:

```bash
python -m ingest.run myneta --limit 25
python -m ingest.run --report
```

---

## Product A: what's in it

**273,102 observations · 23 indicators · 217 countries · 5,972 events · 3,110 spans**

| Layer | Source | Coverage |
|---|---|---|
| Economy, livelihoods, state finances, openness, human development | World Bank WDI | 1960–2025 |
| Long-run GDP per capita | Maddison Project (via Our World in Data) | year 1–2022 |
| Democracy, liberal democracy, political corruption, regime type | V-Dem / Regimes of the World (via Our World in Data) | 1789–2025 |
| Leaders, elections, regime spells | REIGN | 1921–2021 |
| Landmark decisions with disputed interpretations | hand-curated, `data/curated/decisions.yaml` | 1965–2016, 24 countries |

Every indicator carries four explainers — what it is in plain language, what it
literally measures, what it does **not** capture, and how governments flatter it.
`etl/build.py` will not ship a series that is missing them.

### Known limits, stated up front

- **REIGN's public release ends August 2021.** Automatic political events stop
  there. The app says so under every event list.
- **Curated decisions cover 24 countries, not 200.** An empty stretch of chart
  means nobody has written that entry yet.
- **Pre-1950 Maddison figures are scholarly reconstructions**, not measurements.
- **Historical states** (Czechoslovakia, East Germany, South Yemen, both Vietnams)
  are mapped to successor ISO3 codes and flagged `historical` — see
  `etl/countries.py`.

---

## Product B: the claim store

The interesting prior art here is Brazil's *Operação Serenata de Amor*, whose robot
"Rosie" found 8,000+ suspicious congressional reimbursements — and then never left
Brazil, because it was built against one expense stream on one portal.

This is the same idea built to travel. An extractor's only job is to turn one
publisher's documents into `Claim` objects against a shared vocabulary. Fetching,
archiving, hashing, rate limiting, entity resolution and persistence belong to the
framework.

```
ingest/
  schema.py      claim store DDL; refuses to open without FK enforcement
  archive.py     provenance-preserving fetch: archive bytes, hash, rate-limit
  resolve.py     entity resolution; conservative, auditable, refuses to guess
  base.py        Extractor ABC + Claim/Office dataclasses
  extractors/
    myneta.py    India — candidate affidavits (ADR / Election Commission data)
```

Adding a country means writing one `Extractor` subclass.

### What it produces

Declared-asset trajectories per politician, across every election they contested,
each point traceable to an archived document:

```
Adv K Francis George
  2004  Rs     1.14 cr    Lok Sabha 2004
  2009  Rs     0.73 cr    Lok Sabha 2009
  2016  Rs     4.79 cr    Kerala 2016
  2021  Rs     6.22 cr    Kerala 2021
  2024  Rs     9.52 cr    Lok Sabha 2024
```

**This is a starting point for a question, not an answer.** Declared wealth rises
with property prices, inheritance, business income and inflation. Declared criminal
cases are *pending cases, not convictions*.

---

## Landscape: what already exists

Worth knowing before building more.

**Country data:** Our World in Data is the best presentation layer in the world for
this, but its charts carry no political-decision layer. World Bank, IMF, Maddison,
Penn World Table supply the economics; V-Dem, Polity5, DPI, Archigos and REIGN
supply the politics. Nobody joins them for a general reader.

**Machine-readable decisions:** the IMF Structural Reform Database (90 countries,
1973–2014) and the Laeven–Valencia Systemic Banking Crises Database (1970–2025) are
dated, coded landmark-policy datasets. Neither is in a consumer product. Both are
strong candidates for replacing hand-curation at scale.

**Politician accountability:** MyNeta and PRS (India), OpenSecrets, USAspending and
GovTrack (US), TheyWorkForYou (UK), Mzalendo (Kenya), Serenata de Amor (Brazil).
All excellent, all single-country, and none joins money-in to money-out to outcome.

**A caution.** `openbudgetsindia.org`, a well-known Indian civic budget portal
cited in academic work, has lapsed and now serves an online casino affiliate site.
Its data-processing pipelines survive at `github.com/cbgaindia`. This is why
Product B archives bytes rather than citing live URLs.

---

## Licence and attribution

Code is this project's. Data belongs to its publishers and each carries its own
terms — World Bank (CC BY 4.0), Our World in Data (CC BY), V-Dem, Maddison, REIGN,
and ADR/MyNeta (public ECI affidavit data). `data/raw/_manifest.json` records the
URL, fetch time, size and sha256 of every source file used in a build.
