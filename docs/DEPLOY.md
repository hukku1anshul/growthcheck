# Deploying

Short version: **yes, this works on Render, as a static site, on the free tier.**

## Why it deploys so easily

The app has no backend and no database at runtime. `python -m etl.build` and
`python -m etl.export_people` pre-compute every number into JSON files, and the
browser only ever fetches files. That is a deliberate property:

- nothing to keep running, nothing to pay for, nothing to get breached
- what a reader sees is a fixed artefact that can be diffed between builds
- **no visitor request ever reaches a government portal.** Publishing this cannot
  turn your readers into unwitting load on MyNeta, MPLADS or PRS — which matters,
  because those are small civic and public bodies, not CDNs.

Render never needs Python, network access to the data sources, or the archive.

## Steps

**1. Push to GitHub.**

```bash
git remote add origin https://github.com/<you>/politicalfindings.git
git push -u origin main
```

The repo is ~30MB, almost all of it the committed JSON bundles. `.gitignore`
already excludes the things that must not ship: `data/archive/` (75MB+ of
archived source documents), `data/raw/`, `data/processed/` (the SQLite stores)
and `web/node_modules/`.

**2. Create the Render service.** `render.yaml` is a Blueprint, so in Render:
New → Blueprint → pick the repo. It reads:

| setting | value |
|---|---|
| type | static site |
| build | `cd web && npm ci && npm run build` |
| publish | `web/dist` |

Or configure the same three by hand with New → Static Site.

**3. That's it.** No environment variables, no secrets, no database.

## Will everything work?

| Feature | On Render | Why |
|---|---|---|
| Country explorer, all 23 indicators | yes | static JSON |
| 7,843 events, 56 countries of decisions | yes | static JSON |
| People view, 1,269 politicians | yes | static JSON |
| MPLADS public-money panels | yes | static JSON |
| Shareable URLs | yes | the rewrite rule sends every path to `index.html` |
| Mobile | yes | filter drawer, tested at 375px |
| Light/dark | yes | CSS only |
| **Running an extractor** | **no** | by design — see below |
| **Re-fetching live data** | **no** | by design |

Data refresh is a **local** operation, not something the deployed site does:

```bash
python -m ingest.run myneta        # and mplads / prs / ukparliament / ...
python -m etl.build
python -m etl.export_people
git commit -am "data refresh" && git push       # Render redeploys
```

This is the right split. Scraping from a web host would be slower, more fragile,
and would point a datacentre IP at civic portals on every deploy.

## Sizes

```
web/dist            29 MB   total
  assets             1.2 MB  (390KB gzipped — mostly ECharts)
  data              27 MB   all bundles, fetched on demand
```

A visitor's **first load is about 500KB gzipped**: the JS bundle, `meta.json`,
one indicator series and one country's events. Nobody downloads 27MB — the
People index (684KB, ~80KB gzipped) only loads if they open the People tab.

## Things to know before you publish

- **`data/archive/` is not deployed and should not be.** It is 75MB+ of archived
  source documents, and it is the evidence layer — keep it, back it up, but it
  belongs in local storage or object storage, not in a static site.
- **The site says "Built &lt;date&gt;" in the footer.** Keep that honest; a
  transparency tool showing stale figures without saying so is worse than none.
- **PRS asks for a 10-second crawl delay** and the extractor honours it. Do not
  lower it to make a refresh faster.
- **`indiaelections.org` is off-limits for bulk harvesting** — their
  `agent-permissions.json` states `"scraping_bulk": "not-permitted"`. Read and
  cite only.
- Before pushing anything public, run `python verify.py` and
  `python tests/test_parsers.py`. Both must be green.

## Alternatives

Any static host works identically — Netlify, Cloudflare Pages, GitHub Pages,
Vercel. Only two things matter: serve `web/dist`, and rewrite unknown paths to
`index.html` so shared URLs resolve. For GitHub Pages set Vite's `base` to the
repo name, since it serves from a subpath.
