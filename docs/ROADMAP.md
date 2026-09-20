# What to add next, and what "fake news" this can actually check

Written 2026-09-20 after probing every candidate source directly. Where a source
is described as reachable, keyless or licensed, that was tested, not assumed.

## Where the site stands

Six extractors, 51,931 sourced claims, 1,284 politicians, 217 countries. The
things it does that nothing else in the market does, confirmed by looking at the
alternatives:

| Capability | Us | MyNeta | NetaWorth | PRS | OWID |
|---|---|---|---|---|---|
| Political decisions marked on economic charts, with the dispute attached | yes | – | – | – | no decision layer |
| Declared wealth beside **public money directed** (MPLADS) | yes | – | – | – | – |
| Cross-publisher corroboration with a stated agreement rate | yes (97.5%) | – | – | – | – |
| Every figure traceable to an archived, hashed document | yes | source link | source link | – | citation |
| Disagreements between official sources shown, not resolved | yes | – | – | – | – |
| Refuses to merge ambiguous names; queues them for a human | yes | – | – | – | – |

Where the market beats us: **breadth**. NetaWorth has 4,092 sitting MLAs, 229
Rajya Sabha members and 181,307 historical candidate records. We have 543 Lok
Sabha members and one election. That gap is closable from sources we already use.

---

## Tier 1 — unique, feasible now, sources verified

### 1. What each MP actually asked Parliament  — BUILT, with a correction

**Source:** [Indian parliament proceedings dataset on Zenodo](https://zenodo.org/records/18146342),
CC-BY-4.0, scraped from sansad.in. Built as `ingest/extractors/sansadqa.py`.

**CORRECTION.** An earlier version of this document said the dataset carried
"37,271 questions **with the government's answer text**." That was wrong. The
file has `questionText` and `answerText` columns and **both are empty in all
37,271 rows** — I saw the column names and did not check they were populated.

What it actually contains, verified column by column (100% populated):

| field | example |
|---|---|
| `subjects` | "Toll Plazas in Dharmapuri, Tamil Nadu" |
| `ministry` | ROAD TRANSPORT AND HIGHWAYS (56 distinct) |
| `type` | STARRED (2,473) / UNSTARRED (34,798) |
| `date`, `sessionNo`, `member` | 465 distinct members |
| `questionsFilePath` | link to the per-question PDF on sansad.in |

The question text and the ministry's reply live in **37,266 separate PDFs**.
Retrieving them is a 37,000-document scrape of a government site — a separate
project, not a parsing job.

**So the honest value is still real, but different:** not "the government's
words", but *what topics your MP raised and which ministry they held to
account*. PRS gives a count — "asked 85 questions". This gives the substance:
"asked about toll plazas, rail connectivity, farmer skill development and
tourism; questioned Health, Railways and Agriculture most often."

The extractor stores a sample of questions per member plus a `questions_topics`
profile (total, how many starred, top five ministries), and its note on every
claim states that the full text is in the linked PDF and not in this dataset.

### 2. A claim checker grounded in the claim store — the fake-news answer

Prototyped: `tools/checkclaim.py`. Deterministic, no language model, no verdict.
Seven test claims:

```
"Jyotiraditya Scindia's assets grew 100 times since 2004"
   record: x118.6 (2004 -> 2024)   CONSISTENT (within 25%)

"Rahul Gandhi has 20 criminal cases against him"
   record: 18 declared PENDING cases (NOT convictions)   NOT CONSISTENT

"Priya Saroj has spent none of her MPLADS money"
   record: 23.8% utilised   NOT CONSISTENT
   caveat: the member recommends; district authorities implement and pay

"India's GDP grew 10% in 2023"
   record: 7.21 (World Bank)   NOT CONSISTENT

"Amit Shah owns assets worth Rs 500 crore"
   record (2024): Rs 65.67 crore   NOT CONSISTENT

"Narendra Modi flew to the moon in 2019"
   CANNOT CHECK: could not tell which kind of fact the claim is about.
```

Every answer carries the source URL and the archive hash. The last case is the
important one: it refuses. A tool like this is only trustworthy if it says
"cannot check" far more often than it guesses, which is why it is rule-based —
a language model would produce a fluent answer for the moon claim.

**Why this is the gap:** [Boom](https://www.boomlive.in/), [Factly](https://factly.in/),
[Newschecker](https://newschecker.in/) and the other IFCN signatories check
*virality* — doctored images, misattributed quotes, out-of-context video. None
automatically checks a **numeric claim** against the politician's own affidavit
or the scheme portal. That is a different problem and it is the one this data
solves.

**To ship it:** a text box on the People page, the same rules, and a hard
"cannot check" path. Extend predicate detection as phrasings arrive. Never add a
"true/false" label; the ethics doc forbids it and the moon example shows why.

### 3. Existing fact-checks, surfaced on the politician's page

**Source:** [Google Fact Check Tools API](https://developers.google.com/fact-check/tools/api),
`claims:search` — aggregates ClaimReview markup from IFCN signatories worldwide,
including the Indian ones above and [Full Fact](https://fullfact.org/) in the UK.
Filters: `query`, `languageCode`, `reviewPublisherSiteFilter`, `maxAgeDays`.
Returns claim text, claimant, and each review's publisher, URL, date and
`textualRating`.

**Verified:** the endpoint returns `403 ... Please use API Key` without a key —
so it works with a key, and a key is free from Google Cloud Console. The
integration is a build-time step: for each of 1,284 names, fetch reviews, store
them as claims with `subject='factcheck'`, sourced to the review URL.

**Why it's worth it:** a reader on Rahul Gandhi's page sees "Boom rated this
viral claim about him False on 12 March" beside his affidavit figures. We add
nothing of our own — we relay a named fact-checker's published rating with a
link. That is the correct division of labour: they check virality, we check
numbers.

**What it does not do:** PIB Fact Check, the government's own unit, is a
login-walled submission portal with no readable feed. Out.

### 4. Third and fourth witnesses: OpenSanctions and Wikidata

Every fact currently has at most two publishers. Two more are free.

**[OpenSanctions `in_sansad`](https://www.opensanctions.org/datasets/in_sansad/)** —
18,559 Lok and Rajya Sabha members as structured entities, bulk CSV, updated
weekly. Fields: name, aliases, birth_date, country, sanctions, program_ids.
Verified reachable and parseable. Two uses: (a) another name/alias source to
strengthen entity resolution — it carries "Shri Sambhaji Rao Shinde" *and*
"Sambhaji Rao Shinde"; (b) the `sanctions` column, which is empty for members
now but is the join point to 479 enforcement datasets.

**Wikidata** — birth date, education, party, positions, Wikipedia link, for most
sitting members (Rahul Gandhi returned DOB, four institutions, Wikipedia). Free
SPARQL, no key. **Caveat, verified the hard way:** it rate-limits aggressively —
three of five queries in this session got 429 or timed out. Viable as a nightly
batch with backoff and caching, not as a live lookup. Never put it in the
request path.

**Payoff:** age and education agreement can be measured across four publishers
instead of two, and a disagreement between the affidavit and everyone else
becomes far more informative than one between the affidavit and PRS.

### 5. Breadth: state MLAs, Rajya Sabha, and the last four Lok Sabhas

**Source:** MyNeta, which we already harvest. `myneta.info/LokSabha2019/`,
`/LokSabha2014/`, `/LokSabha2009/` and state assembly folders such as
`/uttarpradesh2022/` all returned 200 with the same page structure.

**Why:** this is the breadth NetaWorth has and we don't, and it is the same
extractor with a different `election` argument — the parameter already exists.
Historical Lok Sabhas also mean the asset trajectories no longer depend solely
on the "Other Elections" table on a 2024 page; each past declaration gets its
own archived source document.

**Cost:** MyNeta has ~8,000 candidates per general election and ~4,000 sitting
MLAs. At the polite rate this is days of harvesting, not hours. Winners first.

---

## Tier 2 — valuable, more work or a key

**6. Election results per constituency** — margin, turnout, who they beat, over
time. `results.eci.gov.in` is reachable (a redirect page; the data sits behind
it). MyNeta constituency pages already list every candidate with votes. This
turns "won Guna" into "won Guna by 2.1% against X, on 68% turnout, having lost
it in 2019."

**7. UK Companies House officers** — every UK MP's current and past
directorships, from the official register, cross-checked against their own
Register of Interests. Free API key. Verified: the endpoint returns 401 without
one. This is the UK version of the question MPLADS answers for India: what else
is this person attached to?

**8. Donors ↔ contractors** — the join between 1,233 electoral bond purchasers
and procurement suppliers. Several of the largest bond buyers are infrastructure
firms. The claim store now holds both sides. **Deliberately not built:** matching
company names across registers is the same problem as matching politicians'
names, with the same defamation risk. It needs the resolver's conservative
treatment — a review band, no auto-merge — not a quick fuzzy join.

**9. Promises versus delivery** — the [Manifesto Project](https://manifesto-project.wzb.eu/)
codes party manifestos by policy position for most democracies. Registration
required; the API root returned 404 unauthenticated. Would let the country
explorer show what a governing party *promised* beside what the indicators did.

---

## What not to add, and why

| Idea | Verdict | Reason |
|---|---|---|
| **How each Indian MP voted** | not feasible | Fewer than 50 recorded divisions per Lok Sabha; individual names are often not published. The UK equivalent ([Public Whip](https://www.publicwhip.org.uk/)) is feasible and a good Tier 2 item — but India's is not, and pretending otherwise would mean inventing data. |
| An "integrity score" or ranking | never | Publishing an opinion about a named living person. `verify.py` fails if a scoring predicate appears. |
| Media sentiment, coverage counts | no | Measures attention, not conduct; and it is not public-record data. |
| AI-written politician summaries | no | Fluent, unverifiable, and exactly the failure mode a transparency tool must avoid. |
| Image/video forensics | no | Real problem, wrong tool. IFCN signatories do it well; we relay their results (item 3). |

---

## "Fake news": what this site can honestly check

Four kinds of claim, with what checks them:

| Claim type | Example | Checked against | Tool |
|---|---|---|---|
| **Numeric, about a politician** | "X's wealth grew 100x" / "X has 20 cases" / "X spent nothing from MPLADS" | the affidavit, the MPLADS portal | `checkclaim.py` — **unique** |
| **Numeric, about a country** | "GDP grew 10% in 2023" | World Bank / V-Dem series already in the build | `checkclaim.py` |
| **Viral / visual** | doctored image, fake quote | IFCN fact-checkers' published reviews | Google Fact Check API relay (item 3) |
| **"The site used to say..."** | a figure quietly restated | our archive + Wayback | `ingest/recheck.py`, already built |

And what it cannot check, stated plainly so nobody is misled:

- **Anything not on a public record.** "X took a bribe" has no dataset. The tool
  must say "cannot check," and does.
- **Intent, character, or "corruption."** Declared wealth rising 118x is a
  sourced fact; whether it is corrupt is a judgement the tool never makes.
- **Whether the record itself is true.** An affidavit is sworn, not audited. The
  site verifies *what the document says*, never *whether the politician lied to
  the document*. That distinction is in the README and must stay there.

---

## Authenticity upgrades that fall out of the above

- **Four-witness corroboration** (item 4) turns "two sources agree" into a
  materially stronger claim.
- **ClaimReview relay** (item 3) means a reader who distrusts us can see what an
  independent, IFCN-audited organisation said.
- **Parliamentary answers** (item 1) are government statements in the
  government's own words, archived — the least contestable text on the site.
- The archive, hash, `recheck` and `verify.py` layers already exist and apply to
  every new source unchanged. That is the point of the engine.

## Suggested order

~~1. Zenodo questions~~ — **built** (`sansadqa`), with the answer-text claim corrected above.
~~2. Claim checker in the UI~~ — **built** (`web/src/CheckClaim.jsx`).
~~3. Google Fact Check relay~~ — **built** (`factcheck`); needs a free key in `GOOGLE_FACTCHECK_KEY`.
~~4. OpenSanctions + Wikidata witnesses~~ — **built** (`opensanctions`, `wikidata`).
~~5. MyNeta breadth: 2019 Lok Sabha~~ — **built**; `--election LokSabha2019`. State assemblies next.
6. Next: sitting MLAs (`--election uttarpradesh2022` and siblings), and the US
   money layer beyond the disclosure index.
