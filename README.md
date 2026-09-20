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

To harvest politicians and feed them into the web app:

```bash
python -m ingest.run myneta --delay 0.7
python -m ingest.run ukparliament --delay 0.4
python -m etl.export_people
```

Both are polite by default (rate-limited, archive-reusing) and safe to re-run -
already-archived pages are served from disk and never re-requested. Add
`--limit 20` for a quick sample.

To check that the project still keeps its own promises:

```bash
python verify.py
```

---

## Product A: what's in it

**273,102 observations · 23 indicators · 217 countries · 7,811 events · 3,110 spans**

| Layer | Source | Coverage |
|---|---|---|
| Economy, livelihoods, state finances, openness, human development | World Bank WDI | 1960-2025 |
| Long-run GDP per capita | Maddison Project (via Our World in Data) | year 1-2022 |
| Democracy, liberal democracy, political corruption, regime type | V-Dem / Regimes of the World (via Our World in Data) | 1789-2025 |
| Leaders, elections, regime spells | REIGN | 1921-2021 |
| Landmark decisions with disputed interpretations | hand-curated, `data/curated/decisions.yaml` | 1965-2016, 24 countries |
| Derived structural breaks | computed, `etl/sources/breaks.py` | 1789-2025, **199 countries** |

Every indicator carries four explainers - what it is in plain language, what it
literally measures, what it does **not** capture, and how governments flatter it.
`etl/build.py` will not ship a series that is missing them.

### Documented decisions vs derived breaks

Two different things sit on the same chart, and the difference matters:

- **Curated decisions** are read from documents. A named act of government, dated,
  with citations and a `contested` field. 32 of them, 24 countries.
- **Derived breaks** are computed from the series: regime transitions, sustained
  V-Dem shifts, output collapses, inflation onsets. 1,839 of them, 199 countries.
  They say *something discontinuous happened here* - never *a government did X*.

Derived breaks are drawn with dashed, fainter markers, badged `derived` in the
event list, and carry an explicit warning when opened. India 1975 is a good test
case: the curated Emergency sits alongside two independent derived signals that
flagged the same year from different data.

**Why derived, and not the IMF Structural Reform Database?** That was the plan. It
did not survive contact. `data.imf.org` sits behind Akamai and returns "Access
Denied" to any scripted client; its SDMX data endpoints return 503 even from inside
a browser session. And the SRD is not a list of decisions anyway - it is a set of
regulatory-stance indexes where, in the IMF's words, "an increase in the indexes
indicates a structural reform". Extracting events from it means detecting jumps in
a series, which is what `breaks.py` does with series we can actually fetch. The
result is wider (199 countries, 1789-2025) than the SRD (90 countries, 1973-2014),
at the cost of describing outcomes rather than legislation.

### Known limits, stated up front

- **REIGN's public release ends August 2021.** Automatic political events stop
  there. The app says so under every event list.
- **Curated decisions cover 24 countries, not 200.** An empty stretch of chart
  means nobody has written that entry yet.
- **Pre-1950 Maddison figures are scholarly reconstructions**, not measurements.
- **Historical states** (Czechoslovakia, East Germany, South Yemen, both Vietnams)
  are mapped to successor ISO3 codes and flagged `historical` - see
  `etl/countries.py`.

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
    myneta.py         India - candidate affidavits (ADR / Election Commission data)
    ukparliament.py   UK - members + Register of Members' Financial Interests
```

Adding a country means writing one `Extractor` subclass.

### What it produces

**544 Indian MPs · 649 UK MPs · 1,091+ archived documents · every claim sourced**

Declared-asset trajectories per politician, across every election they contested,
each point traceable to an archived document:

```
Jyotiraditya M. Scindia  (MP, BJP, Guna, Madhya Pradesh)
  declared assets   x118.55   over 2004-2024
  national GDP/cap  x2.65     same country, same 20 years
```

**This is a starting point for a question, not an answer.** Declared wealth rises
with property prices, inheritance, business income and inflation. Declared criminal
cases are *pending cases, not convictions*. The app shows both multiples side by
side and never computes a score or a ranking.

### Portability, tested rather than asserted

The UK extractor exists to prove the engine travels. It is as different from
MyNeta as a second source could be:

| MyNeta (India) | UK Parliament |
|---|---|
| HTML pages, scraped | JSON REST API, documented and keyless |
| one row per candidate | two APIs joined on member id |
| declared asset **total** (a stock) | itemised outside **payments** (dated flows) |
| affidavit, filed at election | rolling register, updated continuously |

Adding it required no change to `base.py`, `archive.py`, `resolve.py` or
`schema.py` - only the new extractor and two predicates. Stock and flow are kept
apart: a UK speaking fee is never plotted on the same axis as an Indian asset
declaration.

### Verifying the promises

```bash
python verify.py
```

`docs/ETHICS.md` lists seven commitments; `verify.py` checks each one against the
actual code and data and exits non-zero if any has drifted. It is how the
commitments stay real rather than decorative - it has already caught a name-scoring
bug that would have silently split "R. K. Sharma" from "Rajesh Kumar Sharma", and
a foreign-key pragma that was not actually being enforced.

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
