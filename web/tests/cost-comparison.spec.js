import { expect, test } from '@playwright/test'
import { index, openPerson, person } from './helpers.js'

/**
 * The cost comparison, and the line it must not cross.
 *
 * This is the project's nearest thing to Rosie: it points at works that cost
 * far more than others of the same kind. That is only publishable because of
 * HOW it is stated - arithmetic over published figures with the comparison set
 * attached - so the guardrails are tested as hard as the feature.
 */

/**
 * A person whose works carry a cost comparison.
 *
 * Searched among people the index says hold MPLADS works, rather than the first
 * N rows in whatever order the default sort produces - the first version
 * scanned 600 people in index order, found nobody, and SKIPPED all three
 * assertions. A skipped test reads like a passing one in the summary, which is
 * the same "green tick for something never tested" failure as a stale preview
 * server. This refuses to skip when the data says there should be something.
 */
async function withOutliers(page) {
  const idx = await index(page)
  const mplads = idx.people.filter((p) => p.utilisation != null || p.allocated != null)
  if (!mplads.length) return null           // MPLADS genuinely not harvested

  for (const row of mplads.slice(0, 150)) {
    const doc = await person(page, row.id)
    if (doc.cost_outliers?.length) return doc
  }
  throw new Error(
    `${mplads.length} people hold MPLADS works but none of the first 150 carried a ` +
    `cost comparison - either the export stopped emitting them or the threshold moved`
  )
}

test('a flagged work always shows its ratio, median and comparison size', async ({ page }) => {
  const doc = await withOutliers(page)
  test.skip(!doc, 'no cost comparisons in this build')
  await openPerson(page, doc.id)

  const panel = page.locator('.panel', { hasText: 'cost more than others of the same kind' })
  await expect(panel).toBeVisible()

  // A ratio without its denominator is an insinuation, not a measurement.
  const first = panel.locator('.work').first()
  await expect(first).toContainText(/\d+(\.\d+)?×\s*the median for/)
  await expect(first).toContainText(/median\s*₹/)
  await expect(first).toContainText(/across\s*[\d,]+\s*works/)

  for (const w of doc.cost_outliers) {
    expect(w.ratio, 'every row carries its ratio').toBeGreaterThanOrEqual(10)
    expect(w.median, 'and the median it was compared against').toBeGreaterThan(0)
    expect(w.n, 'and how many works that median came from').toBeGreaterThanOrEqual(30)
    expect(w.category, 'and the category compared within').toBeTruthy()
    expect(w.src_url, 'and the document it came from').toMatch(/^https?:\/\//)
  }
})

test('the comparison never calls anything suspicious', async ({ page }) => {
  const doc = await withOutliers(page)
  test.skip(!doc, 'no cost comparisons in this build')
  await openPerson(page, doc.id)

  const panel = page.locator('.panel', { hasText: 'cost more than others of the same kind' })
  const text = (await panel.textContent()) || ''

  // These words carry a verdict the arithmetic does not support. The heading
  // and body describe the measurement and stop.
  for (const word of [
    'suspicious', 'irregular', 'anomal', 'fraud', 'corrupt', 'misuse',
    'embezzl', 'scam', 'red flag', 'wrongdoing by',
  ]) {
    expect(text.toLowerCase(), `panel must not say "${word}"`).not.toContain(word)
  }
})

test('the caveat is a warning, and says who actually sets the price', async ({ page }) => {
  const doc = await withOutliers(page)
  test.skip(!doc, 'no cost comparisons in this build')
  await openPerson(page, doc.id)

  const panel = page.locator('.panel', { hasText: 'cost more than others of the same kind' })
  await expect(panel.locator('p.warn')).toBeVisible()
  await expect(panel).toContainText(/not a finding/i)
  await expect(panel).toContainText(/not evidence of wrongdoing/i)
  // Under MPLADS the member recommends and the district pays. Omitting that
  // would let the reader attribute a price the member did not set.
  await expect(panel).toContainText(/RECOMMENDS/)
  await expect(panel).toContainText(/district authorities sanction, implement and pay/i)
  await expect(panel).toContainText(/ordinary explanation/i)
})

test('no politician can be ranked by how many works were flagged', async ({ page }) => {
  // The index drives the people table and every sort option. If a count of
  // flagged works reached it, the app would offer a league table of
  // politicians by suspicion, which docs/ETHICS.md forbids as a score.
  const idx = await index(page)
  const keys = new Set()
  for (const row of idx.people) for (const k of Object.keys(row)) keys.add(k)
  const leaked = [...keys].filter((k) => /outlier|flag|suspic|anomal/i.test(k))
  expect(leaked, `index must not carry a per-person flag count: ${leaked}`).toEqual([])

  // and no sort option exposes one
  await page.goto('/?view=people')
  const sorts = await page.locator('select').nth(1).locator('option')
    .evaluateAll((o) => o.map((x) => `${x.value} ${x.textContent}`))
  expect(sorts.join(' ').toLowerCase()).not.toMatch(/outlier|flag|suspic/)
})
