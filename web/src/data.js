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
