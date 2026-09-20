// Data access. Everything is static JSON produced by `python -m etl.build`,
// so the whole app can be served from any static host with no backend.

const BASE = `${import.meta.env.BASE_URL}data`

const cache = new Map()

async function getJSON(path) {
  if (cache.has(path)) return cache.get(path)
  const p = fetch(`${BASE}/${path}`).then((r) => {
    if (!r.ok) throw new Error(`${path}: ${r.status}`)
    return r.json()
  })
  cache.set(path, p)
  return p
}

export const loadMeta = () => getJSON('meta.json')
export const loadSeries = (indicatorId) => getJSON(`series/${indicatorId}.json`)

// Country event files only exist where there is something to show.
export async function loadEvents(iso3) {
  try {
    return await getJSON(`events/${iso3}.json`)
  } catch {
    return { events: [], spans: [] }
  }
}

/** Index a series to 100 at the first year present in `range`, per country. */
export function rebase(pairs, range) {
  const inRange = pairs.filter(([y]) => y >= range[0] && y <= range[1])
  const first = inRange.find(([, v]) => v !== null && v !== 0)
  if (!first) return inRange
  const base = first[1]
  return inRange.map(([y, v]) => [y, v === null ? null : (v / base) * 100])
}

export const COLOURS = [
  '#3b82f6', // blue
  '#f97316', // orange
  '#10b981', // green
  '#a855f7', // purple
  '#ef4444', // red
  '#eab308', // yellow
]

// Event colour families. Kept deliberately muted - markers must never out-shout
// the data line they sit behind.
export const EVENT_COLOURS = {
  reform: '#0891b2',
  crisis: '#dc2626',
  conflict: '#7f1d1d',
  politics: '#6366f1',
  rupture: '#c2410c',
  social: '#059669',
}

export function eventColour(meta, kind) {
  const family = meta?.event_kinds?.[kind]?.colour
  return EVENT_COLOURS[family] || '#64748b'
}

export function eventLabel(meta, kind) {
  return meta?.event_kinds?.[kind]?.label || kind
}

/* --------------------------------------------------------------- url state */
/**
 * The app's state lives in the query string so a view can be sent to someone.
 * A chart showing "India vs China, corruption index, 1975-2000, coups only" is
 * an argument; without a URL it can only be described, not handed over.
 *
 * replaceState, not pushState: dragging a year field should not bury the back
 * button under fifty history entries.
 */
export function readUrlState() {
  const q = new URLSearchParams(window.location.search)
  const get = (k) => q.get(k) || undefined
  const list = (k) => (q.get(k) ? q.get(k).split(',').filter(Boolean) : undefined)
  const num = (k) => (q.get(k) != null && q.get(k) !== '' ? Number(q.get(k)) : undefined)
  return {
    view: get('view'),
    countries: list('c'),
    indicator: get('i'),
    // A second indicator, drawn on its own right-hand axis. Two series with
    // different units on ONE axis is a lie with a picture attached - "% of GDP"
    // and "constant 2015 US$" share no scale, and forcing them onto one makes
    // whichever number is larger look dominant for no reason.
    indicator2: get('i2'),
    from: num('from'),
    to: num('to'),
    // A named leader or regime period, so "GDP under Vajpayee" is a link.
    period: get('period'),
    focus: get('focus'),
    kinds: list('k'),
    rebased: q.get('rebased') === '1' ? true : q.get('rebased') === '0' ? false : undefined,
    spans: q.get('spans') === '1' ? true : q.get('spans') === '0' ? false : undefined,
    person: num('p'),
  }
}

export function writeUrlState(patch) {
  const q = new URLSearchParams(window.location.search)
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined || v === null || v === '') q.delete(k)
    else q.set(k, Array.isArray(v) ? v.join(',') : String(v))
  }
  const next = `${window.location.pathname}?${q.toString()}`
  window.history.replaceState(null, '', next)
}
