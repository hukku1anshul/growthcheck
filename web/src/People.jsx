import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { fmt } from './Chart.jsx'
import { readUrlState, writeUrlState } from './data.js'
import CheckClaim from './CheckClaim.jsx'

const BASE = `${import.meta.env.BASE_URL}data/people`

// Rows per page. Small enough that the table stays quick to scan and cheap to
// render, large enough that paging through a country is not a chore.
const PAGE_SIZE = 100

/**
 * The politician side of the project, and the point where it meets the country
 * side: a person's declared figures are shown against the national series for
 * the same years, so "8 crore" becomes a number a reader can actually judge.
 */
export default function People({ dark, filtersOpen = false, onCloseFilters }) {
  const onOpen = (id) => {
    setSelected(id)
    onCloseFilters?.()
  }
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [q, setQ] = useState('')
  const [country, setCountry] = useState('all')
  const [sort, setSort] = useState('relative')
  const [selected, setSelected] = useState(readUrlState().person ?? null)
  const [person, setPerson] = useState(null)
  const [page, setPage] = useState(0)

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
    writeUrlState({ p: selected ?? undefined })
  }, [selected])

  useEffect(() => {
    if (selected == null) return setPerson(null)
    fetch(`${BASE}/${selected}.json`)
      .then((r) => r.json())
      .then(setPerson)
      .catch(() => setPerson(null))
  }, [selected])

  // Everything matching the filters, in order. This used to be truncated to the
  // first 400 with no way to see the rest: fine at 1,284 people, but the store
  // now holds 2,196 and most of them were unreachable unless you already knew a
  // name to search for. The cap is now a PAGE, not a ceiling.
  const matches = useMemo(() => {
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
      utilisation: (a, b) => (b.utilisation ?? -1) - (a.utilisation ?? -1),
      allocated: (a, b) => (b.allocated ?? 0) - (a.allocated ?? 0),
    }[sort]
    return [...list].sort(cmp)
  }, [meta, q, country, sort])

  const pageCount = Math.max(1, Math.ceil(matches.length / PAGE_SIZE))
  // Narrowing the filters can strand you past the last page, so clamp rather
  // than showing an empty table for a search that did match something.
  const current = Math.min(page, pageCount - 1)
  const rows = useMemo(
    () => matches.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE),
    [matches, current]
  )

  // Any change to the filters or the ordering makes the old page number
  // meaningless - page 7 of a new result set is not where the reader was.
  useEffect(() => setPage(0), [q, country, sort])

  // Turning a page from the controls at the BOTTOM would otherwise leave the
  // reader looking at the end of the new page.
  const listTop = useRef(null)
  const goto = (n) => {
    setPage(n)
    listTop.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }

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
      <aside className={`side ${filtersOpen ? 'side-open' : ''}`}>
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
              <option value="utilisation">Public funds used</option>
              <option value="allocated">Public funds allocated</option>
              <option value="declared">Declared total</option>
              <option value="claims">Most claims</option>
              <option value="name">Name</option>
            </select>
          </label>
          <p className="note">
            {matches.length === meta.people.length
              ? `${meta.people.length} people`
              : `${matches.length} of ${meta.people.length} match`}
            {pageCount > 1 && ` · page ${current + 1} of ${pageCount}`}
          </p>
        </section>

        <section>
          <h2>Read this first</h2>
          <p className="note">{meta.caveats.not_a_score}</p>
          <p className="note">{meta.caveats.stock_vs_flow}</p>
        </section>

        {meta.review_queue?.length > 0 && (
          <section>
            <h2>
              Needs a human <span className="hint">{meta.review_queue.length}</span>
            </h2>
            <p className="note">{meta.caveats.review}</p>
            {meta.review_queue.map((r, i) => (
              <div className="review-pair" key={i}>
                <button className="rp-name" onClick={() => onOpen(r.a.id)}>
                  {r.a.name}
                </button>
                <span className="rp-score">
                  same person? score {r.score}
                </span>
                <button className="rp-name" onClick={() => onOpen(r.b.id)}>
                  {r.b.name}
                </button>
              </div>
            ))}
          </section>
        )}
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

            <CheckClaim people={meta.people} />
            <div className="ptable" ref={listTop}>
              <div className="ptr phead">
                <span>Name</span>
                <span>Party</span>
                <span>Seat</span>
                <span className="num">Declared (own)</span>
                <span className="num">Public funds used</span>
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
                    {p.utilisation != null ? (
                      <span title={`₹${fmt(p.spent)} of ₹${fmt(p.allocated)} allocated`}>
                        <b>{p.utilisation}%</b>
                        <span className="dim"> of ₹{fmt(p.allocated)}</span>
                      </span>
                    ) : p.declared_multiple ? (
                      <Ratio p={p} />
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </span>
                </button>
              ))}
            </div>
            {pageCount > 1 && (
              <nav className="pager" aria-label="People list pages">
                <button
                  className="pg"
                  onClick={() => goto(current - 1)}
                  disabled={current === 0}
                >
                  ← Previous
                </button>
                <span className="pg-at">
                  {current * PAGE_SIZE + 1}–
                  {Math.min((current + 1) * PAGE_SIZE, matches.length)} of{' '}
                  {matches.length}
                </span>
                <button
                  className="pg"
                  onClick={() => goto(current + 1)}
                  disabled={current >= pageCount - 1}
                >
                  Next →
                </button>
              </nav>
            )}
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

      {p.public_money && (
        <div className="panel public-money" style={{ marginTop: 12 }}>
          <h3>Public money directed to this constituency (MPLADS)</h3>
          <div className="pm-row">
            <div>
              <span className="ctx-label">Allocated to them</span>
              <span className="ctx-big">
                {p.public_money.allocated != null
                  ? `₹${fmt(p.public_money.allocated)}`
                  : '—'}
              </span>
              <span className="ctx-sub">entitlement for the tenure</span>
            </div>
            <div>
              <span className="ctx-label">Completed works</span>
              <span className="ctx-big">
                {p.public_money.spent != null ? `₹${fmt(p.public_money.spent)}` : '—'}
              </span>
              <span className="ctx-sub">
                {p.public_money.works
                  ? `${p.public_money.works.toLocaleString()} works`
                  : 'none recorded'}
              </span>
            </div>
            <div>
              <span className="ctx-label">Utilisation</span>
              <span className="ctx-big">
                {p.public_money.utilisation != null
                  ? `${p.public_money.utilisation}%`
                  : '—'}
              </span>
              <span className="ctx-sub">completed ÷ allocated</span>
            </div>
          </div>
          <p className="ctx-caveat">{meta.caveats.public_money}</p>
          {p.public_money.largest_works?.length > 0 && (
            <>
              <h3 style={{ marginTop: 12 }}>Largest completed works</h3>
              <div className="works">
                {p.public_money.largest_works.map((w, i) => (
                  <div className="work" key={i}>
                    <span className="w-amt">₹{fmt(w.amount)}</span>
                    <span className="w-date">{w.as_of || 'undated'}</span>
                    <span className="w-desc">{w.detail}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {p.asked && (
        <div className="panel asked" style={{ marginTop: 12 }}>
          <h3>What they asked Parliament about</h3>
          <div className="pm-row">
            <div>
              <span className="ctx-label">Questions bearing their name</span>
              <span className="ctx-big">{p.asked.total?.toLocaleString()}</span>
              <span className="ctx-sub">18th Lok Sabha</span>
            </div>
            <div style={{ flex: 1, minWidth: 220 }}>
              <span className="ctx-label">Ministries most often questioned</span>
              <span className="ctx-sub" style={{ fontSize: 12.5, color: 'var(--text)' }}>
                {p.asked.ministries}
              </span>
            </div>
          </div>
          <p className="ctx-caveat">{meta.caveats.questions}</p>
          {p.asked.recent?.length > 0 && (
            <>
              <h3 style={{ marginTop: 12 }}>Most recent questions</h3>
              <div className="works">
                {p.asked.recent.map((q, i) => (
                  <div className="work" key={i}>
                    <span className="w-date">{q.as_of}</span>
                    <span className="w-desc" style={{ color: 'var(--text)' }}>
                      {q.subject}
                    </span>
                    <span className="w-desc">{q.detail}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {p.filings?.length > 0 && (
        <div className="panel" style={{ marginTop: 12 }}>
          <h3>Financial disclosures filed</h3>
          <p className="note" style={{ marginTop: -4 }}>{meta.caveats.filings}</p>
          <div className="works">
            {p.filings.map((f, i) => (
              <div className="work" key={i}>
                <span className="w-date">{f.as_of}</span>
                <span className="w-desc" style={{ color: 'var(--text)' }}>{f.kind}</span>
                <span className="w-desc">{f.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {p.cost_outliers?.length > 0 && (
        <div className="panel" style={{ marginTop: 12 }}>
          <h3>Works that cost more than others of the same kind</h3>
          {/* The heading describes the arithmetic and stops. "Suspicious",
              "irregular" or "flagged" would carry a verdict this comparison
              cannot support - and the member does not set the price anyway. */}
          <p className="warn" style={{ marginTop: -4 }}>{meta.caveats.cost_outliers}</p>
          <div className="works">
            {p.cost_outliers.map((w, i) => (
              <div className="work" key={i}>
                <span className="w-amt">₹{fmt(w.amount)}</span>
                <span className="w-date">{w.as_of}</span>
                <span className="w-desc" style={{ color: 'var(--text)' }}>
                  <b>{w.ratio}×</b> the median for “{w.category}”
                  <span className="dim">
                    {' '}— median ₹{fmt(w.median)} across{' '}
                    {w.n.toLocaleString()} works
                  </span>
                </span>
                <span className="w-desc">{w.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {p.factchecks?.items?.length > 0 && (
        <div className="panel" style={{ marginTop: 12 }}>
          <h3>Fact-checks published about them, by others</h3>
          {/* The name-matching limit is a warning, not a footnote. A review
              about a different person of the same name, shown here as though
              it were about this one, is the worst thing this page could do. */}
          <p className="warn" style={{ marginTop: -4 }}>{meta.caveats.factchecks}</p>
          <div className="works">
            {p.factchecks.items.map((f, i) => (
              <div className="work" key={i}>
                <span className="w-date">{f.as_of}</span>
                <span className="w-desc" style={{ color: 'var(--text)' }}>{f.rating}</span>
                <span className="w-desc">{f.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {p.activity && (
        <div className="panel" style={{ marginTop: 12 }}>
          <h3>Parliamentary activity (PRS)</h3>
          <div className="pm-row">
            {[
              ['attendance_pct', 'Attendance', '%'],
              ['debates_participated', 'Debates', ''],
              ['questions_asked', 'Questions', ''],
              ['private_member_bills', "Private member's bills", ''],
            ].map(([k, label, suffix]) =>
              p.activity[k] ? (
                <div key={k}>
                  <span className="ctx-label">{label}</span>
                  <span className="ctx-big">
                    {p.activity[k].value}
                    {suffix}
                  </span>
                  <span className="ctx-sub">
                    {p.activity[k].benchmark || 'no benchmark published'}
                  </span>
                </div>
              ) : null
            )}
          </div>
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

      {p.asset_points.length < 2 && p.flow_by_year?.length > 0 && (
        <FlowChart points={p.flow_by_year} currency={p.currency} dark={dark} />
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
        {/* Only where there are criminal-case claims to caveat. It used to
            print unconditionally, so a UK member with no such claims - a
            country that does not publish them at all - carried a notice about
            self-declared pending cases on an affidavit they never filed. A
            caveat for absent data implies the data is there. */}
        {p.claims?.some((c) => c.predicate === 'criminal_cases_declared') && (
          <p className="note caveat">{meta.caveats.cases}</p>
        )}
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

/**
 * Registered payments per year. Bars, not a line: these are separate dated
 * receipts, not a quantity that moved continuously between them. Drawing a line
 * through them would imply a trajectory that the underlying data does not assert,
 * and would invite comparison with the asset-total chart above - a different kind
 * of number entirely.
 */
function FlowChart({ points, currency, dark }) {
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
          `Registered in ${ps[0].axisValue}<br/><b>${sym(currency)}${ps[0].value.toLocaleString()}</b>`,
      },
      xAxis: {
        type: 'category',
        data: points.map(([y]) => y),
        axisLabel: { color: text },
        axisLine: { lineStyle: { color: line } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: text, formatter: (v) => fmt(v) },
        splitLine: { lineStyle: { color: line, type: 'dashed' } },
      },
      series: [
        {
          type: 'bar',
          data: points.map(([, v]) => v),
          barMaxWidth: 46,
          itemStyle: { color: '#0891b2', borderRadius: [3, 3, 0, 0] },
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
      <h3>Registered payments, by year of registration</h3>
      <p className="note" style={{ marginTop: -4 }}>
        Individual declared payments, not a measure of total wealth. Grouped by the
        date the interest was <em>registered</em>, which is not always the date the
        payment was received. Not comparable with the declared-asset totals shown
        for Indian members.
      </p>
      <div ref={ref} style={{ height: 210 }} />
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
