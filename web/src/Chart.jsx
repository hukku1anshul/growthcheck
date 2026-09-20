import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { COLOURS, eventColour, eventLabel } from './data.js'

/**
 * One indicator, several countries, with the focus country's events drawn as
 * vertical markers and its leader/regime periods as shaded bands.
 *
 * Events are shown for ONE country at a time on purpose. Overlaying six countries'
 * coups and elections on a single axis produces a barcode that nobody can read, and
 * invites the reader to mentally connect country A's marker to country B's line.
 */
export default function Chart({
  meta,
  indicator,
  indicator2,    // optional second indicator, drawn on a right-hand axis
  countries,     // [{iso3, name}]
  seriesByIso,   // {iso3: [[year, value], ...]}
  series2ByIso,  // same shape, for indicator2
  focus,         // iso3 whose events are drawn
  events,        // focus country's events, already filtered
  spans,         // focus country's spans, already filtered
  range,
  rebased,
  showSpans,
  onPickEvent,
  onReady,       // hands the echarts instance up, for PNG export
  dark,
}) {
  const ref = useRef(null)
  const chart = useRef(null)

  useEffect(() => {
    chart.current = echarts.init(ref.current, dark ? 'dark' : null, { renderer: 'canvas' })
    // Deliberately exposed in production, not just in dev. The browser tests
    // assert things that are only visible in the chart OPTION - that a second
    // indicator really is bound to a second axis, and that the axis names the
    // unit it carries - and a canvas cannot be inspected for that. Guarding it
    // behind DEV would mean the axis tests silently passed against a build
    // where the hook did not exist, which is the same "green tick for
    // something never tested" failure as reusing a stale preview server.
    // It is read-only introspection of data the page already renders.
    window.__chart = chart.current
    const onResize = () => chart.current?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.current?.dispose()
    }
  }, [dark])

  useEffect(() => {
    if (!chart.current) return

    const axisColour = dark ? '#334155' : '#e2e8f0'
    const textColour = dark ? '#cbd5e1' : '#475569'
    const faint = dark ? 'rgba(148,163,184,.10)' : 'rgba(100,116,139,.08)'

    // With two indicators the country name alone is ambiguous, so the legend
    // and tooltip carry the indicator too.
    const label = (c, ind, second) =>
      indicator2 ? `${c.name} · ${ind?.name || ''}` : c.name

    const lines = countries.map((c, i) => ({
      name: label(c, indicator, false),
      type: 'line',
      showSymbol: false,
      symbol: 'circle',
      symbolSize: 6,
      connectNulls: false,
      lineStyle: { width: c.iso3 === focus ? 2.6 : 1.7 },
      emphasis: { focus: 'series' },
      itemStyle: { color: COLOURS[i % COLOURS.length] },
      data: seriesByIso[c.iso3] || [],
      yAxisIndex: 0,
      __unit: indicator?.unit,
      z: c.iso3 === focus ? 4 : 3,
    }))

    // The second indicator shares the colour of its country but is dashed and
    // thinner, so a reader can tell at a glance which axis a line belongs to
    // without hunting through the legend.
    if (indicator2) {
      countries.forEach((c, i) => {
        lines.push({
          name: label(c, indicator2, true),
          type: 'line',
          showSymbol: false,
          symbolSize: 6,
          connectNulls: false,
          lineStyle: { width: c.iso3 === focus ? 2.2 : 1.5, type: 'dashed' },
          emphasis: { focus: 'series' },
          itemStyle: { color: COLOURS[i % COLOURS.length] },
          data: (series2ByIso || {})[c.iso3] || [],
          yAxisIndex: 1,
          __unit: indicator2.unit,
          z: c.iso3 === focus ? 4 : 3,
        })
      })
    }

    // Tooltip params carry no custom fields, so the unit is looked up by index.
    const unitOf = (p) => lines[p.seriesIndex]?.__unit

    // --- event markers, attached to the focus country's line -----------------
    // Deliberately the PRIMARY series for the focus country: the secondary
    // lines are pushed after all the primaries, so index `focusIdx` is still
    // the left-axis line and markers stay attached to one line rather than two.
    const focusIdx = countries.findIndex((c) => c.iso3 === focus)
    if (focusIdx >= 0 && events.length) {
      lines[focusIdx].markLine = {
        silent: false,
        symbol: 'none',
        emphasis: { disabled: false },
        label: { show: false },
        lineStyle: { width: 1 },
        data: events.map((e) => ({
          xAxis: e.year,
          lineStyle: {
            color: eventColour(meta, e.kind),
            // A derived break is an algorithm's changepoint, not a documented act
            // of government. Dashed and fainter, so the two can never be confused
            // at a glance. See docs/ETHICS.md.
            type: isDerived(e) ? 'dashed' : 'solid',
            opacity: isDerived(e) ? 0.4 : 0.6,
          },
          // stashed for the click handler
          __event: e,
        })),
      }
    }

    // --- leader / regime bands ----------------------------------------------
    if (focusIdx >= 0 && showSpans && spans.length) {
      lines[focusIdx].markArea = {
        silent: true,
        itemStyle: { color: faint },
        label: {
          show: true,
          position: 'insideTop',
          rotate: 90,
          align: 'left',
          verticalAlign: 'middle',
          offset: [4, 6],
          color: textColour,
          fontSize: 9,
          opacity: 0.75,
          overflow: 'truncate',
          width: 90,
        },
        data: spans
          .map((s, i) => {
            const a = Number(String(s.start).slice(0, 4))
            const b = s.end ? Number(String(s.end).slice(0, 4)) : range[1]
            if (b < range[0] || a > range[1]) return null
            const lo = Math.max(a, range[0])
            const hi = Math.min(b, range[1])
            // Label only the bands wide enough to read. At 60+ years on screen a
            // one-year caretaker leader gets a sliver a few pixels wide, and
            // labelling it turns the top of the chart into noise.
            const wideEnough = (hi - lo) / (range[1] - range[0]) > 0.055
            return [
              {
                xAxis: lo,
                name: wideEnough ? s.label : '',
                itemStyle: { color: i % 2 ? faint : 'transparent' },
              },
              { xAxis: hi },
            ]
          })
          .filter(Boolean),
      }
    }

    chart.current.setOption(
      {
        backgroundColor: 'transparent',
        animationDuration: 320,
        grid: { left: 58, right: indicator2 ? 64 : 22, top: 26, bottom: 54 },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'line', lineStyle: { color: textColour, width: 1, type: 'dashed' } },
          confine: true,
          formatter: (params) => {
            if (!params.length) return ''
            const year = params[0].axisValue
            const rows = params
              .filter((p) => p.value && p.value[1] !== null && p.value[1] !== undefined)
              .map(
                (p) =>
                  `<div style="display:flex;gap:10px;justify-content:space-between">
                     <span>${p.marker} ${p.seriesName}</span>
                     <b>${fmt(p.value[1])}${
                       indicator2 && unitOf(p) ? ` <span style="font-weight:400;opacity:.65">${escapeHtml(unitOf(p))}</span>` : ''
                     }</b>
                   </div>`
              )
              .join('')
            const here = events.filter((e) => e.year === Number(year))
            const evs = here.length
              ? `<div style="margin-top:7px;padding-top:6px;border-top:1px solid ${axisColour};max-width:280px;white-space:normal">` +
                here
                  .map(
                    (e) =>
                      `<div style="margin:2px 0"><span style="color:${eventColour(
                        meta,
                        e.kind
                      )}">&#9632;</span> ${escapeHtml(e.title)}</div>`
                  )
                  .join('') +
                `</div>`
              : ''
            return `<div style="font-weight:600;margin-bottom:5px">${year}</div>${rows}${evs}`
          },
        },
        legend: {
          show: countries.length > 1,
          bottom: 0,
          textStyle: { color: textColour, fontSize: 11 },
          itemWidth: 16,
          itemHeight: 3,
        },
        xAxis: {
          type: 'value',
          min: range[0],
          max: range[1],
          minInterval: 1,
          axisLabel: { color: textColour, formatter: (v) => String(Math.round(v)) },
          axisLine: { lineStyle: { color: axisColour } },
          splitLine: { show: false },
        },
        yAxis: [
          {
            type: 'value',
            scale: !rebased,
            // With one indicator the unit is already in the chart header, and
            // repeating it collides with the rotated leader-band labels. With
            // two, the axis MUST say which unit it carries or the chart is
            // unreadable.
            name: rebased
              ? 'indexed: first year in range = 100'
              : indicator2 ? indicator?.unit || '' : '',
            nameTextStyle: { color: textColour, fontSize: 10, align: 'left' },
            nameGap: 12,
            axisLabel: { color: textColour, formatter: (v) => fmt(v) },
            splitLine: { lineStyle: { color: axisColour, type: 'dashed' } },
          },
          {
            type: 'value',
            scale: !rebased,
            show: Boolean(indicator2),
            name: indicator2 ? indicator2.unit || '' : '',
            nameTextStyle: { color: textColour, fontSize: 10, align: 'right' },
            nameGap: 12,
            axisLabel: { color: textColour, formatter: (v) => fmt(v) },
            // Only one grid of split lines, or the two sets cross into a mesh.
            splitLine: { show: false },
          },
        ],
        series: lines,
      },
      { notMerge: true }
    )

    const onClick = (p) => {
      if (p.componentType === 'markLine' && p.data?.__event) onPickEvent(p.data.__event)
    }
    chart.current.off('click')
    chart.current.on('click', onClick)
    onReady?.(chart.current)
  }, [meta, indicator, indicator2, countries, seriesByIso, series2ByIso, focus,
      events, spans, range, rebased, showSpans, dark, onPickEvent, onReady])

  return <div className="chart" ref={ref} />
}

/** A break inferred from a series, as opposed to a documented event. */
function isDerived(e) {
  return String(e?.kind || '').startsWith('derived_')
}

function fmt(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const a = Math.abs(v)
  if (a >= 1e9) return (v / 1e9).toFixed(2) + 'bn'
  if (a >= 1e6) return (v / 1e6).toFixed(2) + 'm'
  if (a >= 1e4) return Math.round(v).toLocaleString()
  if (a >= 100) return v.toFixed(0)
  if (a >= 1) return v.toFixed(2)
  return v.toFixed(3)
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
}

export { fmt, eventLabel, isDerived }
