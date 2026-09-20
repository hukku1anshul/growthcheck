import { useMemo } from 'react'
import { fmt } from './Chart.jsx'

/**
 * The query surface: what is currently being asked, as something you can edit.
 *
 * The controls for this chart were spread across a sidebar of radio buttons and
 * two year fields, which is fine for picking ONE thing and useless for seeing
 * what you have picked. A reader who lands on a shared link needs to know the
 * question before they can judge the answer, so every active condition is shown
 * as a chip and every chip can be removed.
 *
 * "Free-flow" here means composable, not natural language. A text box that
 * accepts "gdp under modi" has to guess when it is wrong, and this project's
 * whole argument is that a tool which guesses cannot be checked. Chips compose
 * in any order, state exactly what they filter, and each one is reversible.
 */

/** Leader and regime spells for the focus country, newest first. */
export function periodOptions(spans, countryName) {
  return (spans || [])
    .filter((s) => s.start)
    .map((s) => {
      const from = Number(String(s.start).slice(0, 4))
      const to = s.end ? Number(String(s.end).slice(0, 4)) : new Date().getFullYear()
      return {
        id: `${s.kind}:${s.label}:${from}`,
        kind: s.kind,
        label: s.label,
        from,
        to,
        // A caretaker who lasted five days is a real tenure but useless as a
        // chart range, so the length is shown rather than hidden.
        years: Math.max(0, to - from),
        title: `${s.label} · ${from}–${s.end ? to : 'present'} (${countryName})`,
      }
    })
    .sort((a, b) => b.from - a.from)
}

/**
 * First and last observed value in the visible range, the change between them,
 * and the compound annual rate.
 *
 * Deliberately NOT a verdict, and deliberately not computed across a gap it
 * cannot see: the endpoints are the first and last years that actually carry a
 * number, and both are named, so "+412%" can never be read without knowing it
 * means 1991→2025 rather than the range the reader selected.
 */
export function seriesStats(pairs) {
  const present = (pairs || []).filter(([, v]) => v !== null && v !== undefined)
  if (present.length < 1) return null
  const [y0, v0] = present[0]
  const [y1, v1] = present[present.length - 1]
  const span = y1 - y0
  const changePct = v0 ? ((v1 - v0) / Math.abs(v0)) * 100 : null
  // CAGR is meaningless across a sign change or from zero, and printing one
  // anyway is how a chart starts lying politely.
  const cagr =
    span > 0 && v0 > 0 && v1 > 0 ? (Math.pow(v1 / v0, 1 / span) - 1) * 100 : null
  return { y0, v0, y1, v1, span, changePct, cagr, n: present.length }
}

export default function QueryBar({
  meta,
  indicator,
  indicator2,
  onIndicator2,
  periods,
  periodId,
  onPeriod,
  range,
  countries,
  focus,
  onRemoveCountry,
  kinds,
  allKinds,
  onResetKinds,
  rebased,
  onExportPNG,
  onExportCSV,
}) {
  const period = periods.find((p) => p.id === periodId) || null
  const kindsFiltered = kinds && allKinds && kinds.size < allKinds.length

  const grouped = useMemo(() => {
    const out = {}
    for (const i of meta?.indicators || []) (out[i.category] ||= []).push(i)
    return out
  }, [meta])

  return (
    <div className="qbar">
      <div className="qchips">
        <span className="qlabel">Showing</span>

        {countries.map((c) => (
          <button
            key={c.iso3}
            className={`qchip ${c.iso3 === focus ? 'qchip-focus' : ''}`}
            title={c.iso3 === focus ? 'Events are drawn for this country' : 'Remove'}
            onClick={() => onRemoveCountry(c.iso3)}
          >
            {c.name}
            <span className="qx">×</span>
          </button>
        ))}

        <span className="qchip qchip-static" title={indicator?.unit || ''}>
          {indicator?.name}
        </span>

        {indicator2 && (
          <button
            className="qchip qchip-second"
            title={`Second axis · ${indicator2.unit || ''}`}
            onClick={() => onIndicator2(null)}
          >
            + {indicator2.name}
            <span className="qx">×</span>
          </button>
        )}

        {period ? (
          <button className="qchip qchip-period" onClick={() => onPeriod(null)}
                  title="Clear this period">
            {period.label} {period.from}–{period.to}
            <span className="qx">×</span>
          </button>
        ) : (
          <span className="qchip qchip-static">
            {range[0]}–{range[1]}
          </span>
        )}

        {rebased && <span className="qchip qchip-static">indexed to 100</span>}

        {kindsFiltered && (
          <button className="qchip" onClick={onResetKinds} title="Show all event kinds">
            {kinds.size} of {allKinds.length} event kinds
            <span className="qx">×</span>
          </button>
        )}
      </div>

      <div className="qcontrols">
        <label className="qfield">
          <span>Compare with</span>
          <select
            className="mini"
            value={indicator2?.id || ''}
            onChange={(e) => onIndicator2(e.target.value || null)}
          >
            <option value="">— none —</option>
            {Object.entries(grouped).map(([cat, items]) => (
              <optgroup key={cat} label={meta?.categories?.[cat]?.label || cat}>
                {items
                  .filter((i) => i.id !== indicator?.id)
                  .map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.name} ({i.unit})
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
        </label>

        <label className="qfield">
          <span>Period</span>
          <select
            className="mini"
            value={periodId || ''}
            onChange={(e) => onPeriod(e.target.value || null)}
            disabled={!periods.length}
          >
            <option value="">— whole range —</option>
            {periods.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label} · {p.from}–{p.to === new Date().getFullYear() && !p.years ? 'present' : p.to}
                {p.years < 1 ? ' (under a year)' : ''}
              </option>
            ))}
          </select>
        </label>

        <div className="qexport">
          <button className="ghost sm" onClick={onExportPNG}>Save image</button>
          <button className="ghost sm" onClick={onExportCSV}>Download data</button>
        </div>
      </div>
    </div>
  )
}

/** Per-country summary of what the visible lines actually do. */
export function StatsStrip({ countries, seriesByIso, indicator, series2ByIso, indicator2 }) {
  const rows = countries.flatMap((c) => {
    const out = [{ c, ind: indicator, st: seriesStats(seriesByIso[c.iso3]) }]
    if (indicator2) out.push({ c, ind: indicator2, st: seriesStats((series2ByIso || {})[c.iso3]) })
    return out
  }).filter((r) => r.st)

  if (!rows.length) return null

  return (
    <div className="panel stats-strip">
      <h3>What the visible lines do</h3>
      <p className="note" style={{ marginTop: -4 }}>
        First and last years that actually carry a number, which is not always the
        range you picked — a gap is missing data, never a zero. A rate is shown
        only where it means something: never across a sign change, and never from
        zero.
      </p>
      <div className="stats-rows">
        {rows.map(({ c, ind, st }, i) => (
          <div className="stats-row" key={`${c.iso3}-${ind.id}-${i}`}>
            <span className="s-name">
              {c.name}
              <span className="dim"> · {ind.name}</span>
            </span>
            <span className="s-span">{st.y0} → {st.y1}</span>
            <span className="s-val">{fmt(st.v0)} → {fmt(st.v1)}</span>
            <span className={`s-chg ${st.changePct > 0 ? 'up' : st.changePct < 0 ? 'down' : ''}`}>
              {st.changePct == null ? '—'
                : `${st.changePct > 0 ? '+' : ''}${st.changePct.toFixed(1)}%`}
            </span>
            <span className="s-cagr">
              {st.cagr == null ? '' : `${st.cagr > 0 ? '+' : ''}${st.cagr.toFixed(2)}%/yr`}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
