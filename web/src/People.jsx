import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { fmt } from './Chart.jsx'

const BASE = `${import.meta.env.BASE_URL}data/people`

/**
 * The politician side of the project, and the point where it meets the country
 * side: a person's declared figures are shown against the national series for
 * the same years, so "8 crore" becomes a number a reader can actually judge.
 */
export default function People({ dark }) {
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [q, setQ] = useState('')
  const [country, setCountry] = useState('all')
  const [sort, setSort] = useState('relative')
  const [selected, setSelected] = useState(null)
  const [person, setPerson] = useState(null)

  useEffect(() => {
    fetch(`${BASE}/index.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`index.json: ${r.status}`)
        return r.json()
      })
      .then(setMeta)
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    if (selected == null) return setPerson(null)
    fetch(`${BASE}/${selected}.json`)
      .then((r) => r.json())
      .then(setPerson)
      .catch(() => setPerson(null))
  }, [selected])

  const rows = useMemo(() => {
    if (!meta) return []
    const needle = q.trim().toLowerCase()
    let list = meta.people.filter(
      (p) =>
        (country === 'all' || p.country === country) &&
        (!needle ||
          p.name.toLowerCase().includes(needle) ||
          (p.party || '').toLowerCase().includes(needle) ||
          (p.constituency || '').toLowerCase().includes(needle))
    )
    const cmp = {
      relative: (a, b) => ratio(b) - ratio(a),
      declared: (a, b) => (b.latest_assets || 0) - (a.latest_assets || 0),
      name: (a, b) => a.name.localeCompare(b.name),
      claims: (a, b) => b.n_claims - a.n_claims,
    }[sort]
    return [...list].sort(cmp).slice(0, 400)
  }, [meta, q, country, sort])

  if (error)
    return (
      <div className="main">
        <div className="panel">
          <h3>No people data yet</h3>
          <p className="note">
            Run <code>python -m ingest.run myneta</code> then{' '}
            <code>python -m etl.export_people</code> to populate this view.
          </p>
          <p className="note">{error}</p>
        </div>
      </div>
    )
  if (!meta) return <div className="loading">Loading people…</div>

  return (
    <div className="body">
      <aside className="side">
        <section>
          <h2>Filter</h2>
          <input
            className="search"
            placeholder="Name, party or seat…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <label className="row">
            <span>Country</span>
            <select
              className="mini"
              value={country}
              onChange={(e) => setCountry(e.target.value)}
            >
              <option value="all">All</option>
              {meta.countries.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label className="row">
            <span>Sort by</span>
            <select className="mini" value={sort} onChange={(e) => setSort(e.target.value)}>
              <option value="relative">Growth vs national</option>
              <option value="declared">Declared total</option>
              <option value="claims">Most claims</option>
              <option value="name">Name</option>
            </select>
          </label>
          <p className="note">
            {rows.length} shown of {meta.people.length}
          </p>
        </section>

        <section>
          <h2>Read this first</h2>
          <p className="note">{meta.caveats.not_a_score}</p>
          <p className="note">{meta.caveats.stock_vs_flow}</p>
        </section>
      </aside>

      <main className="main">
        {person ? (
          <Person p={person} meta={meta} dark={dark} onBack={() => setSelected(null)} />
        ) : (
          <>
            <div className="charthead">
              <div>
                <h2>People</h2>
                <p className="unit">
                  Declared figures from official filings, each traceable to an archived
                  document.
                </p>
              </div>
            </div>
            <div className="ptable">
              <div className="ptr phead">
                <span>Name</span>
                <span>Party</span>
                <span>Seat</span>
                <span className="num">Declared</span>
                <span className="num">vs national</span>
              </div>
              {rows.map((p) => (
                <button key={p.id} className="ptr" onClick={() => setSelected(p.id)}>
                  <span className="pname">{p.name}</span>
                  <span className="dim">{p.party || '—'}</span>
                  <span className="dim">{p.constituency || '—'}</span>
                  <span className="num">
                    {p.latest_assets != null
                      ? `${sym(p.currency)}${fmt(p.latest_assets)}`
                      : p.flow_total != null
                        ? `${sym(p.currency)}${fmt(p.flow_total)} (payments)`
                        : '—'}
                  </span>
                  <span className="num">
                    {p.declared_multiple ? (
                      <Ratio p={p} />
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  )
}

/* ------------------------------------------------------------------ person */

function Person({ p, meta, dark, onBack }) {
  const ctx = p.context
  return (
    <>
      <div className="charthead">
        <div>
          <button className="ghost sm" onClick={onBack}>
            ← all people
          </button>
          <h2 style={{ marginTop: 8 }}>{p.name}</h2>
          {(p.offices || []).map((o, i) => (
            <p className="unit" key={i}>
              {[o.title, o.party, o.constituency, o.region].filter(Boolean).join(' · ')}
              {p.offices.length > 1 && i === 0 && (
                <span className="badge" style={{ marginLeft: 8 }}>
                  {p.offices.length} seats contested
                </span>
              )}
            </p>
          ))}
        </div>
      </div>

      {ctx && (
        <div className="context-box">
          <div className="ctx-nums">
            <div>
              <span className="ctx-label">Declared assets</span>
              <span className="ctx-big">×{ctx.declared_multiple.toLocaleString()}</span>
              <span className="ctx-sub">
                {sym(p.currency)}
                {fmt(ctx.from_value)} → {sym(p.currency)}
                {fmt(ctx.to_value)}
              </span>
            </div>
            <div className="ctx-vs">vs</div>
            <div>
              <span className="ctx-label">National GDP per capita</span>
              <span className="ctx-big ctx-muted">×{ctx.national_multiple}</span>
              <span className="ctx-sub">
                same country, same {ctx.years} years ({ctx.from_year}–{ctx.to_year})
              </span>
            </div>
          </div>
          <p className="ctx-caveat">{meta.caveats.not_a_score}</p>
        </div>
      )}

      {p.conflicts?.length > 0 && (
        <div className="panel conflict-panel" style={{ marginTop: 12 }}>
          <h3>Sources disagree on {p.conflicts.length} fact
            {p.conflicts.length > 1 ? 's' : ''}</h3>
          <p className="note" style={{ marginTop: 0 }}>
            Two or more official documents state different things. Both are shown;
            neither is picked as correct. A candidate standing in two seats files two
            affidavits, and they do not always match.
          </p>
          {p.conflicts.map((c, i) => (
            <div className="conflict" key={i}>
              <span className="cl-pred">
                {c.predicate.replace(/_/g, ' ')}
                {c.as_of ? ` · ${c.as_of}` : ''}
              </span>
              <ul>
                {c.values.map((v, j) => (
                  <li key={j}>{v}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {p.asset_points.length > 1 && (
        <AssetChart points={p.asset_points} currency={p.currency} dark={dark} />
      )}

      <div className="panel" style={{ marginTop: 12 }}>
        <h3>Every claim, with its receipt</h3>
        <div className="claims">
          {p.claims.map((c, i) => (
            <div key={i} className="claim">
              <div className="cl-head">
                <span className="cl-pred">{c.predicate.replace(/_/g, ' ')}</span>
                <span className="cl-date">{c.as_of || '—'}</span>
              </div>
              <div className="cl-val">
                {c.num != null
                  ? // Only money claims carry a currency. Falling back to the
                    // person's currency turns "0 declared pending cases" into
                    // "0 rupees declared pending cases".
                    `${c.currency ? sym(c.currency) : ''}${c.num.toLocaleString()}${
                      c.unit ? ` ${c.unit}` : ''
                    }`
                  : c.text}
              </div>
              {c.note && <div className="cl-note">{c.note}</div>}
              <div className="cl-src">
                <a href={c.src_url} target="_blank" rel="noopener noreferrer">
                  live source
                </a>
                {' · '}archived {c.src_fetched?.slice(0, 10)} · sha256 {c.src_sha}…
                {c.confidence < 1 && ` · confidence ${c.confidence}`}
              </div>
            </div>
          ))}
        </div>
        <p className="note caveat">{meta.caveats.cases}</p>
      </div>
    </>
  )
}

function AssetChart({ points, currency, dark }) {
  const ref = useRef(null)
  useEffect(() => {
    const c = echarts.init(ref.current, dark ? 'dark' : null)
    const text = dark ? '#cbd5e1' : '#475569'
    const line = dark ? '#334155' : '#e2e8f0'
    c.setOption({
      backgroundColor: 'transparent',
      grid: { left: 62, right: 20, top: 22, bottom: 30 },
      tooltip: {
        trigger: 'axis',
        formatter: (ps) =>
          `${ps[0].axisValue}<br/><b>${sym(currency)}${ps[0].value[1].toLocaleString()}</b>`,
      },
      xAxis: {
        type: 'value',
        // Years are values, not magnitudes: without an explicit domain ECharts
        // starts the axis at 0 and squeezes two decades into the right-hand edge.
        min: (v) => Math.floor(v.min - 1),
        max: (v) => Math.ceil(v.max + 1),
        minInterval: 1,
        axisLabel: { color: text, formatter: (v) => String(Math.round(v)) },
        axisLine: { lineStyle: { color: line } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: text, formatter: (v) => fmt(v) },
        splitLine: { lineStyle: { color: line, type: 'dashed' } },
      },
      series: [
        {
          type: 'line',
          data: points,
          symbolSize: 8,
          lineStyle: { width: 2.4 },
          itemStyle: { color: '#3b82f6' },
        },
      ],
    })
    const r = () => c.resize()
    window.addEventListener('resize', r)
    return () => {
      window.removeEventListener('resize', r)
      c.dispose()
    }
  }, [points, currency, dark])
  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <h3>Declared assets at each election</h3>
      <div ref={ref} style={{ height: 230 }} />
    </div>
  )
}

/* ------------------------------------------------------------------- bits */

function Ratio({ p }) {
  const r = ratio(p)
  const hot = r >= 3
  return (
    <span title={`declared ×${p.declared_multiple} vs national ×${p.national_multiple} (${p.span})`}>
      <b className={hot ? 'hot' : ''}>×{p.declared_multiple.toLocaleString()}</b>
      <span className="dim"> / ×{p.national_multiple}</span>
    </span>
  )
}

const ratio = (p) =>
  p.declared_multiple && p.national_multiple
    ? p.declared_multiple / Math.max(p.national_multiple, 0.01)
    : 0

const sym = (cur) => ({ INR: '₹', GBP: '£', USD: '$' }[cur] || '')
