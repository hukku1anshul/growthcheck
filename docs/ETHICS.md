# Design commitments

These are not aspirations. Each one is enforced somewhere in the code, and the
enforcement point is named.

## 1. We mark events. We never assert causes.

"Which decisions caused growth" is the central unsettled question of political
economy. An app that draws a line from a marker to a trend and implies causation
will be correctly dismissed by economists and eagerly weaponised by partisans.

**Enforced by:** `data/curated/decisions.yaml` requires a `contested` field
describing what is genuinely disputed about each decision's effects. The UI renders
it in a highlighted box immediately below the decision, at equal visual weight
(`.contested` in `web/src/styles.css`). The chart draws vertical markers only —
there is no code path that computes or displays an effect size.

## 2. Every number carries a receipt.

A claim about a named living person is only as good as the document behind it.
Source URLs rot: while this project was being built, `openbudgetsindia.org` — a
civic budget portal cited in academic work and news coverage — had lapsed and was
serving an online casino affiliate site from the same address. Anything sourced to
that URL is now unverifiable, and any citizen sent there lands on a gambling page.

So we archive the bytes, hash them, record the fetch time, and cite the archived
copy. The live URL is a convenience, marked as "may have changed since".

**Enforced by:** `claims.source_id` is `NOT NULL REFERENCES sources(id)`, and
`ingest/schema.py:connect()` refuses to open the database at all if SQLite will not
enable foreign keys. There is no way to write a claim without a document.

## 3. We publish facts. We do not publish scores.

The moment you compute "effectiveness" or rank politicians, you are publishing an
opinion about a named living person. Mzalendo's parliamentary scorecards work
because they score disclosed, objective facts — attendance, bills — not character.

**Enforced by:** there is no scoring code, and `schema.PREDICATES` is a closed
vocabulary of observable, dated, attributable facts. A ranking, if one is ever
added, belongs in the client with user-chosen weights, never as a shipped default.

## 4. Declared assets are not evidence of corruption.

Declared wealth rises with property prices, inheritance, business income and plain
inflation. An asset trajectory is a starting point for a question, not an answer.

**Enforced by:** every `criminal_cases_declared` claim carries a `note` stating
these are *self-declared pending cases, not convictions*. `ingest/extractors/myneta.py`
says so in its module docstring and on every claim it emits.

## 5. A wrong merge is a defamation risk, not a data-quality annoyance.

Attributing one person's assets or pending cases to another person with a similar
name is the most damaging thing this system could do.

**Enforced by:** `ingest/resolve.py` auto-merges only above 0.93 similarity.
Between 0.78 and 0.93 it deliberately creates a *separate* person and writes a
`possible_duplicate_of` claim for human review. The default is to leave two records
rather than to merge and be wrong.

This is not theoretical. The first run of the MyNeta extractor read the page
heading instead of the candidate name and collapsed all twelve candidates into one
person. It was caught because the report prints people-created counts next to
claim counts. Keep that check.

## 6. Absence of a marker is not evidence that nothing happened.

The curated decisions file covers 24 countries. REIGN's automatic political events
stop in 2021. A reader must not infer a quiet period from an empty stretch of
chart.

**Enforced by:** `meta.caveats.note`, rendered under the event list on every view.

## 7. Indicators ship with their blind spots attached.

Presenting GDP per capita without saying it ignores distribution, or unemployment
without saying it excludes discouraged workers, teaches people to misread data
confidently. That is worse than not showing it.

**Enforced by:** `etl/build.py` validates that every indicator has `plain`,
`measures` and `blindspots`; the UI renders `blindspots` and `gamed` in a
highlighted warning block, not hidden behind a tooltip.

## Being a good citizen of other people's servers

- Requests are rate-limited per host (`ingest/archive.py`, default 1.0s).
- The User-Agent identifies the project and its purpose.
- Already-archived URLs are served from the archive, so re-runs cost the publisher
  nothing.
- `Extractor.run()` raises unless the subclass sets `robots_checked = True`, which
  is meant to be set only after a human has actually read that publisher's
  robots.txt and terms.
