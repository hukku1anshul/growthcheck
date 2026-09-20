import { useCallback, useEffect, useMemo, useState } from 'react'
import Chart, { fmt, isDerived } from './Chart.jsx'
import People from './People.jsx'
import {
  loadMeta,
  loadSeries,
  loadEvents,
  rebase,
  COLOURS,
  eventColour,
  eventLabel,
} from './data.js'

const MAX_COUNTRIES = 6
const DEFAULT_COUNTRIES = ['IND', 'CHN']
const DEFAULT_INDICATOR = 'gdp_pc'

export default function App() {
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)

  const [selected, setSelected] = useState(DEFAULT_COUNTRIES)
  const [focus, setFocus] = useState(DEFAULT_COUNTRIES[0])
  const [indicatorId, setIndicatorId] = useState(DEFAULT_INDICATOR)
  const [range, setRange] = useState([1960, 2025])
  const [rebased, setRebased] = useState(false)
  const [showSpans, setShowSpans] = useState(true)
  const [kinds, setKinds] = useState(null) // null = not yet initialised
  const [search, setSearch] = useState('')
  const [seriesByIso, setSeriesByIso] = useState({})
  const [eventData, setEventData] = useState({ events: [], spans: [] })
  const [picked, setPicked] = useState(null)
  const [mode, setMode] = useState('countries')
  const [dark, setDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  )

  // ---------------------------------------------------------------- bootstrap
  useEffect(() => {
    loadMeta().then(setMeta).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    if (!meta || kinds) return
    // Start with the curated decisions and ruptures on, routine elections off -
    // showing every election in a 65-year window buries the signal immediately.
    const on = new Set(
      Object.keys(meta.event_kinds).filter((k) => k !== 'election' && k !== 'leader_change')
    )
    setKinds(on)
  }, [meta, kinds])

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  }, [dark])

  // ---------------------------------------------------------------- data load
  useEffect(() => {
    let live = true
    loadSeries(indicatorId)
      .then((d) => live && setSeriesByIso(d))
      .catch((e) => live && setError(String(e)))
    return () => {
      live = false
    }
  }, [indicatorId])

  useEffect(() => {
    let live = true
    loadEvents(focus).then((d) => live && setEventData(d))
    return () => {
      live = false
    }
  }, [focus])

  // Keep focus valid when the selection changes.
  useEffect(() => {
    if (selected.length && !selected.includes(focus)) setFocus(selected[0])
  }, [selected, focus])

  // ---------------------------------------------------------------- derived
  const indicator = useMemo(
    () => meta?.indicators.find((i) => i.id === indicatorId) || null,
    [meta, indicatorId]
  )

  const countryName = useCallback(
    (iso3) => meta?.countries.find((c) => c.iso3 === iso3)?.name || iso3,
    [meta]
  )

  const chartCountries = useMemo(
    () => selected.map((iso3) => ({ iso3, name: countryName(iso3) })),
    [selected, countryName]
  )

  const chartSeries = useMemo(() => {
    const out = {}
    for (const iso3 of selected) {
      const raw = seriesByIso[iso3] || []
      out[iso3] = rebased
        ? rebase(raw, range)
        : raw.filter(([y]) => y >= range[0] && y <= range[1])
    }
    return out
  }, [selected, seriesByIso, rebased, range])

  const visibleEvents = useMemo(() => {
    if (!kinds) return []
    return eventData.events.filter(
      (e) => kinds.has(e.kind) && e.year >= range[0] && e.year <= range[1]
    )
  }, [eventData, kinds, range])

  const visibleSpans = useMemo(
    () => eventData.spans.filter((s) => s.kind === 'leader'),
    [eventData]
  )

  const countryList = useMemo(() => {
    if (!meta) return []
    const q = search.trim().toLowerCase()
    const list = q
      ? meta.countries.filter((c) => c.name.toLowerCase().includes(q))
      : meta.countries
    return list.slice(0, 400)
  }, [meta, search])

  const byCategory = useMemo(() => {
    if (!meta) return []
    return Object.entries(meta.categories).map(([key, cat]) => ({
      key,
      ...cat,
      items: meta.indicators.filter((i) => i.category === key),
    }))
  }, [meta])

  // ---------------------------------------------------------------- handlers
  const toggleCountry = (iso3) =>
    setSelected((s) =>
      s.includes(iso3)
        ? s.filter((x) => x !== iso3)
        : s.length >= MAX_COUNTRIES
          ? s
          : [...s, iso3]
    )

  const toggleKind = (k) =>
    setKinds((s) => {
      const n = new Set(s)
      n.has(k) ? n.delete(k) : n.add(k)
      return n
    })

  if (error) return <div className="fatal">Could not load data: {error}</div>
  if (!meta || !kinds) return <div className="loading">Loading…</div>

  const coverage = indicator?.coverage

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>Political Findings</h1>
          <p>
            {meta.indicators.length} indicators · {meta.countries.length} countries ·
            {' '}{Object.keys(meta.event_kinds).length} kinds of event, drawn on the chart
          </p>
        </div>
        <div className="modeswitch">
          <button
            className={mode === 'countries' ? 'on' : ''}
            onClick={() => setMode('countries')}
          >
            Countries
          </button>
          <button
            className={mode === 'people' ? 'on' : ''}
            onClick={() => setMode('people')}
          >
            People
          </button>
        </div>
        <button className="ghost" onClick={() => setDark((d) => !d)} title="Toggle theme">
          {dark ? '☀' : '☾'}
        </button>
      </header>

      {mode === 'people' ? <People dark={dark} /> : (
      <div className="body">
        {/* ------------------------------------------------ filters */}
        <aside className="side">
          <section>
            <h2>Countries <span className="hint">{selected.length}/{MAX_COUNTRIES}</span></h2>
            <input
              className="search"
              placeholder="Search countries…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="chips">
              {selected.map((iso3, i) => (
                <button
                  key={iso3}
                  className={`chip ${iso3 === focus ? 'chip-focus' : ''}`}
                  style={{ '--c': COLOURS[i % COLOURS.length] }}
                  onClick={() => setFocus(iso3)}
                  title="Click to show this country's events on the chart"
                >
                  {countryName(iso3)}
                  <span
                    className="x"
                    onClick={(e) => {
                      e.stopPropagation()
                      toggleCountry(iso3)
                    }}
                  >
                    ×
                  </span>
                </button>
              ))}
            </div>
            <p className="note">
              Events are drawn for the <b>highlighted</b> country only. Click a chip to
              switch.
            </p>
            <div className="scroller">
              {countryList.map((c) => (
                <label key={c.iso3} className="row">
                  <input
                    type="checkbox"
                    checked={selected.includes(c.iso3)}
                    disabled={!selected.includes(c.iso3) && selected.length >= MAX_COUNTRIES}
                    onChange={() => toggleCountry(c.iso3)}
                  />
                  <span>{c.name}</span>
                </label>
              ))}
            </div>
          </section>

          <section>
            <h2>Indicator</h2>
            <div className="scroller tall">
              {byCategory.map((cat) => (
                <div key={cat.key} className="catgroup">
                  <div className="cathead" title={cat.blurb}>{cat.label}</div>
                  {cat.items.map((i) => (
                    <label key={i.id} className="row">
                      <input
                        type="radio"
                        name="indicator"
                        checked={indicatorId === i.id}
                        onChange={() => setIndicatorId(i.id)}
                      />
                      <span>{i.name}</span>
                    </label>
                  ))}
                </div>
              ))}
            </div>
          </section>

          <section>
            <h2>Events on chart</h2>
            <div className="kinds">
              {Object.entries(meta.event_kinds).map(([k, v]) => {
                const n = eventData.events.filter(
                  (e) => e.kind === k && e.year >= range[0] && e.year <= range[1]
                ).length
                return (
                  <label key={k} className={`row ${n === 0 ? 'muted' : ''}`}>
                    <input type="checkbox" checked={kinds.has(k)} onChange={() => toggleKind(k)} />
                    <span className="swatch" style={{ background: eventColour(meta, k) }} />
                    <span>{v.label}</span>
                    <span className="count">{n}</span>
                  </label>
                )
              })}
            </div>
            <label className="row toggle">
              <input
                type="checkbox"
                checked={showSpans}
                onChange={(e) => setShowSpans(e.target.checked)}
              />
              <span>Shade who was in power</span>
            </label>
          </section>
        </aside>

        {/* ------------------------------------------------ chart */}
        <main className="main">
          <div className="charthead">
            <div>
              <h2>{indicator?.name}</h2>
              <p className="unit">{indicator?.unit}</p>
            </div>
            <div className="controls">
              <label className="toggle inline">
                <input
                  type="checkbox"
                  checked={rebased}
                  onChange={(e) => setRebased(e.target.checked)}
                />
                <span>Index to 100</span>
              </label>
              <div className="years">
                <input
                  type="number"
                  value={range[0]}
                  min={1789}
                  max={range[1] - 1}
                  onChange={(e) => setRange([+e.target.value || 1960, range[1]])}
                />
                <span>→</span>
                <input
                  type="number"
                  value={range[1]}
                  min={range[0] + 1}
                  max={2026}
                  onChange={(e) => setRange([range[0], +e.target.value || 2025])}
                />
              </div>
              {[[1975, 2025, '50y'], [1925, 2025, '100y'], [1960, 2025, 'max WDI']].map(
                ([a, b, label]) => (
                  <button key={label} className="ghost sm" onClick={() => setRange([a, b])}>
                    {label}
                  </button>
                )
              )}
            </div>
          </div>

          {coverage && (
            <p className="coverage">
              This series covers <b>{coverage.min_year}–{coverage.max_year}</b> across{' '}
              {coverage.countries} countries ({coverage.observations.toLocaleString()}{' '}
              observations). Gaps in a line mean the country did not report that year —
              they are not zeros.
            </p>
          )}

          <Chart
            meta={meta}
            indicator={indicator}
            countries={chartCountries}
            seriesByIso={chartSeries}
            focus={focus}
            events={visibleEvents}
            spans={visibleSpans}
            range={range}
            rebased={rebased}
            showSpans={showSpans}
            onPickEvent={setPicked}
            dark={dark}
          />

          <div className="panels">
            <Explainer indicator={indicator} />
            <EventPanel
              meta={meta}
              picked={picked}
              events={visibleEvents}
              focusName={countryName(focus)}
              onPick={setPicked}
              caveats={meta.caveats}
            />
          </div>
        </main>
      </div>
      )}

      <footer className="foot">
        <span>
          Built {meta.built_at.slice(0, 10)} from World Bank WDI, Our World in Data
          (V-Dem, Maddison, Regimes of the World) and REIGN, plus a hand-curated
          decisions file. Every source file is hashed and cached at build time.
        </span>
      </footer>
    </div>
  )
}

/* ------------------------------------------------------------------ panels */

function Explainer({ indicator }) {
  if (!indicator) return null
  return (
    <div className="panel">
      <h3>What this number actually is</h3>
      <p className="lead">{indicator.plain}</p>
      <dl>
        <dt>Measures</dt>
        <dd>{indicator.measures}</dd>
        <dt className="warn">Does not capture</dt>
        <dd className="warn">{indicator.blindspots}</dd>
        {indicator.gamed && (
          <>
            <dt className="warn">How it gets flattered</dt>
            <dd className="warn">{indicator.gamed}</dd>
          </>
        )}
      </dl>
    </div>
  )
}

function EventPanel({ meta, picked, events, focusName, onPick, caveats }) {
  return (
    <div className="panel">
      <h3>
        {picked ? 'Event' : `Events shown for ${focusName}`}
        {picked && (
          <button className="ghost sm right" onClick={() => onPick(null)}>
            back to list
          </button>
        )}
      </h3>

      {picked ? (
        <div className="event-detail">
          <div className="etag" style={{ background: eventColour(meta, picked.kind) }}>
            {eventLabel(meta, picked.kind)} · {picked.date}
          </div>
          {isDerived(picked) && (
            <p className="derived-warn">
              This marker was <b>computed from a data series</b>, not read from a
              document. It says something discontinuous happened here — not that a
              government did any particular thing.
            </p>
          )}
          <h4>{picked.title}</h4>
          <p>{picked.detail}</p>
          {picked.contested && (
            <div className="contested">
              <strong>What is disputed</strong>
              <p>{picked.contested}</p>
            </div>
          )}
          {picked.refs && (
            <ul className="refs">
              {JSON.parse(picked.refs).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
          <p className="src">Source: {picked.source}</p>
        </div>
      ) : (
        <>
          <div className="scroller">
            {events.length === 0 && (
              <p className="note">
                No events of the selected kinds in this window. Turn more on in the
                sidebar.
              </p>
            )}
            {events.map((e, i) => (
              <button key={i} className="event-row" onClick={() => onPick(e)}>
                <span
                  className={`swatch ${isDerived(e) ? 'swatch-derived' : ''}`}
                  style={{ background: eventColour(meta, e.kind) }}
                />
                <span className="ey">{e.year}</span>
                <span className="et">{e.title}</span>
                {isDerived(e) && <span className="badge">derived</span>}
              </button>
            ))}
          </div>
          <p className="note caveat">{caveats.note}</p>
        </>
      )}
    </div>
  )
}
