import { useMemo, useState } from 'react'
import { fmt } from './Chart.jsx'

const BASE = `${import.meta.env.BASE_URL}data`

/**
 * Check a numeric claim against the sourced record.
 *
 * Fact-checkers check virality - doctored images, fake quotes. Nobody checks a
 * NUMBER about a politician against that politician's own sworn affidavit, or a
 * claim about public money against the scheme portal. This site holds both with
 * receipts, so it can.
 *
 * Three rules, all load-bearing:
 *
 *   1. It never says "true" or "false". It shows the claimed figure, the sourced
 *      figure, and the gap. A claim can be numerically wrong and substantially
 *      right ("grew 100x" when the record says 118x), or numerically exact and
 *      deeply misleading (0% MPLADS for someone elected three months ago).
 *   2. It refuses loudly. "Cannot check" is the correct answer far more often
 *      than any answer, and a tool that always produces something is a tool
 *      nobody should trust.
 *   3. No language model. A model would return a fluent, confident answer for
 *      "Narendra Modi flew to the moon" - which is precisely the failure mode a
 *      fact-checking tool cannot have. Rules can only match what they recognise.
 */

const TOPICS = [
  ['criminal_cases', /\b(criminal|cases?|charges?|fir|accused)\b/i],
  ['budget_spent', /\b(mplads|constituency fund|development fund|spent|utilis|utiliz)/i],
  ['budget_allocated', /\b(allocat|entitle)/i],
  ['attendance', /\battendance\b/i],
  ['questions', /\bquestions?\b/i],
  ['assets', /\b(assets?|wealth|net ?worth|property|crore|crorepati|rich)\b/i],
]

const UNITS = { crore: 1e7, cr: 1e7, lakh: 1e5, lakhs: 1e5, lac: 1e5 }

function parseNumbers(text) {
  const t = text.toLowerCase()
  const out = { years: (t.match(/\b(19[5-9]\d|20[0-4]\d)\b/g) || []).map(Number) }
  let m
  if ((m = t.match(/(\d+(?:\.\d+)?)\s*(?:x|times|fold)\b/))) out.multiple = parseFloat(m[1])
  if ((m = t.match(/(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)/))) out.percent = parseFloat(m[1])
  if ((m = t.match(/(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)\s*(crore|cr|lakhs?|lac)\b/)))
    out.amount = parseFloat(m[1]) * UNITS[m[2]]
  if (/\b(no|zero|none|nil)\b/.test(t)) out.zero = true
  if (out.amount === undefined && out.multiple === undefined) {
    const nums = (t.match(/\b\d{1,4}\b/g) || []).map(Number).filter((n) => !out.years.includes(n))
    if (nums.length) out.count = nums[0]
  }
  return out
}

const norm = (s) =>
  s
    .toLowerCase()
    .replace(/[^a-z\s]/g, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOP.has(w))
const STOP = new Set([
  'the', 'has', 'have', 'had', 'his', 'her', 'their', 'and', 'for', 'from', 'with',
  'that', 'this', 'was', 'were', 'are', 'since', 'than', 'crore', 'lakh', 'rupees',
  'assets', 'wealth', 'cases', 'criminal', 'money', 'spent', 'grew', 'times', 'worth',
  'owns', 'funds', 'fund', 'used', 'about', 'over',
])

function rupees(v) {
  if (v == null) return '—'
  return v >= 1e7 ? `₹${(v / 1e7).toFixed(2)} crore` : `₹${(v / 1e5).toFixed(2)} lakh`
}

export default function CheckClaim({ people }) {
  const [claim, setClaim] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  const index = useMemo(
    () => (people || []).map((p) => ({ ...p, toks: norm(p.name) })),
    [people]
  )

  async function run(text) {
    setBusy(true)
    try {
      setResult(await check(text, index))
    } catch (e) {
      setResult({ lines: [{ k: 'error', v: String(e) }], verdict: null })
    }
    setBusy(false)
  }

  return (
    <div className="panel checkclaim">
      <h3>Check a claim against the record</h3>
      <p className="note" style={{ marginTop: -4 }}>
        Type a claim about a politician's declared assets, pending cases, MPLADS
        spending or attendance. This shows the sourced figure beside the claimed
        one. It never returns a verdict, and it says so when it cannot check.
      </p>
      <div className="cc-row">
        <input
          className="search"
          style={{ marginBottom: 0 }}
          placeholder="e.g. Amit Shah owns assets worth Rs 500 crore"
          value={claim}
          onChange={(e) => setClaim(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && claim.trim() && run(claim)}
        />
        <button className="ghost" disabled={!claim.trim() || busy} onClick={() => run(claim)}>
          {busy ? 'Checking…' : 'Check'}
        </button>
      </div>

      <div className="cc-examples">
        {[
          "Jyotiraditya Scindia's assets grew 100 times since 2004",
          'Rahul Gandhi has 20 criminal cases',
          'Narendra Modi flew to the moon in 2019',
        ].map((ex) => (
          <button key={ex} className="cc-eg" onClick={() => { setClaim(ex); run(ex) }}>
            {ex}
          </button>
        ))}
      </div>

      {result && (
        <div className={`cc-result ${result.cannot ? 'cc-cannot' : ''}`}>
          {result.lines.map((l, i) => (
            <div className="cc-line" key={i}>
              <span className="cc-k">{l.k}</span>
              <span className={`cc-v ${l.flag || ''}`}>{l.v}</span>
            </div>
          ))}
          <p className="cc-verdict">
            No verdict. The record is above; the judgement is yours.
          </p>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ engine */

async function check(text, index) {
  const nums = parseNumbers(text)
  const words = new Set(norm(text))

  // a person is only a match if EVERY substantial token of their name appears
  let best = null
  for (const p of index) {
    if (p.toks.length < 2) continue
    if (!p.toks.every((t) => words.has(t))) continue
    if (!best || p.toks.length > best.toks.length) best = p
  }
  if (!best) {
    return {
      cannot: true,
      lines: [
        { k: 'cannot check', v: 'No politician in the record matches a name in this claim.', flag: 'warn' },
      ],
    }
  }

  const topic = TOPICS.find(([, rx]) => rx.test(text))?.[0]
  const lines = [{ k: 'person', v: `${best.name} (${best.country})` }]
  if (!topic) {
    lines.push({
      k: 'cannot check',
      v: 'Could not tell which kind of fact this claim is about. This tool checks declared assets, pending cases, MPLADS spending, attendance and questions.',
      flag: 'warn',
    })
    return { cannot: true, lines }
  }

  const person = await fetch(`${BASE}/people/${best.id}.json`).then((r) => r.json())
  lines.push({ k: 'fact type', v: topic.replace(/_/g, ' ') })

  // The citation must be the document the CHECKED fact came from. Taking the
  // last claim in the array cited an MPLADS works URL as the source for an
  // asset figure read off a MyNeta affidavit - a wrong receipt, which is worse
  // than no receipt in a tool whose whole value is provenance.
  const PRED = {
    assets: ['declared_assets'],
    criminal_cases: ['criminal_cases_declared'],
    budget_spent: ['budget_spent', 'budget_allocated'],
    budget_allocated: ['budget_allocated', 'budget_spent'],
    attendance: ['attendance_pct'],
    questions: ['questions_asked', 'questions_topics'],
  }[topic] || []
  const used = (person.claims || []).filter((c) => PRED.includes(c.predicate))

  const verdict = (ok) => (ok ? 'CONSISTENT' : 'NOT CONSISTENT')
  const flag = (ok) => (ok ? 'ok' : 'bad')

  if (topic === 'assets') {
    const pts = person.asset_points || []
    if (!pts.length) return cannot(lines, 'The record holds no declared-asset figures for this person.')
    lines.push({ k: 'record', v: pts.map(([y, v]) => `${y}: ${rupees(v)}`).join('; ') })
    if (nums.multiple && pts.length >= 2) {
      const start = pts.find(([y]) => !nums.years.length || y >= Math.min(...nums.years)) || pts[0]
      const mult = pts[pts.length - 1][1] / start[1]
      const ok = Math.abs(mult - nums.multiple) / mult < 0.25
      lines.push({
        k: 'claimed', flag: flag(ok),
        v: `×${nums.multiple}  ·  record ×${mult.toFixed(1)} (${start[0]}→${pts[pts.length - 1][0]})  ·  ${verdict(ok)} (within 25%)`,
      })
    } else if (nums.amount) {
      const [y, v] = pts[pts.length - 1]
      const ok = Math.abs(v - nums.amount) / v < 0.1
      lines.push({
        k: 'claimed', flag: flag(ok),
        v: `${rupees(nums.amount)}  ·  record (${y}) ${rupees(v)}  ·  ${verdict(ok)} (within 10%)`,
      })
    }
  } else if (topic === 'criminal_cases') {
    const c = (person.claims || []).filter((x) => x.predicate === 'criminal_cases_declared' && x.num != null)
    if (!c.length) return cannot(lines, 'No declared criminal-case figure on record for this person.')
    const latest = c[c.length - 1]
    lines.push({
      k: 'record',
      v: `${latest.num} declared PENDING cases as of ${String(latest.as_of).slice(0, 4)} — self-declared on the affidavit, NOT convictions`,
    })
    const claimed = nums.zero ? 0 : nums.count
    if (claimed != null) {
      const ok = claimed === latest.num
      lines.push({ k: 'claimed', flag: flag(ok), v: `${claimed}  ·  record ${latest.num}  ·  ${verdict(ok)}` })
    }
  } else if (topic === 'budget_spent' || topic === 'budget_allocated') {
    const pm = person.public_money
    if (!pm || !pm.allocated) return cannot(lines, 'No MPLADS allocation on record for this person.')
    lines.push({
      k: 'record',
      v: `allocated ${rupees(pm.allocated)} · completed works ${rupees(pm.spent)} · ${pm.utilisation}% utilised over ${pm.works} works`,
    })
    if (nums.zero) {
      const ok = !pm.spent
      lines.push({ k: 'claimed', flag: flag(ok), v: `none spent  ·  record ${pm.utilisation}%  ·  ${verdict(ok)}` })
    } else if (nums.percent != null) {
      const ok = Math.abs(pm.utilisation - nums.percent) <= 5
      lines.push({
        k: 'claimed', flag: flag(ok),
        v: `${nums.percent}%  ·  record ${pm.utilisation}%  ·  ${verdict(ok)} (within 5 points)`,
      })
    }
    lines.push({
      k: 'caveat',
      v: 'The member RECOMMENDS works; district authorities sanction, implement and pay. Utilisation reflects them as much as the member.',
      flag: 'warn',
    })
  } else if (topic === 'attendance' || topic === 'questions') {
    const key = topic === 'attendance' ? 'attendance_pct' : 'questions_asked'
    const a = person.activity?.[key]
    if (!a) return cannot(lines, `No ${topic} figure on record for this person.`)
    lines.push({ k: 'record', v: `${a.value}${topic === 'attendance' ? '%' : ''} — ${a.benchmark || 'no benchmark published'}` })
    const claimed = topic === 'attendance' ? nums.percent : nums.count
    if (claimed != null) {
      const ok = Math.abs(claimed - a.value) <= 3
      lines.push({ k: 'claimed', flag: flag(ok), v: `${claimed}  ·  record ${a.value}  ·  ${verdict(ok)}` })
    }
  }

  const src = used[used.length - 1]
  if (src) {
    lines.push({ k: 'source', v: src.src_url })
    lines.push({ k: 'archived', v: `${String(src.src_fetched).slice(0, 10)} · sha256 ${src.src_sha}…` })
  } else {
    lines.push({
      k: 'source',
      v: 'No archived document could be identified for this specific fact.',
      flag: 'warn',
    })
  }
  return { lines }
}

function cannot(lines, msg) {
  lines.push({ k: 'cannot check', v: msg, flag: 'warn' })
  return { cannot: true, lines }
}
