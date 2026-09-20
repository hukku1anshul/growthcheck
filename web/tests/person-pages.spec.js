import { expect, test } from '@playwright/test'
import { findPerson, index, openPerson, person } from './helpers.js'

/**
 * Person pages, in all three countries.
 *
 * Every country-specific defect found so far was found by opening an INDIAN
 * page, because that is the one anybody ever opened. The UK member carrying an
 * Indian criminal-cases caveat survived precisely that long.
 */

test('an Indian page shows public money apart from personal wealth', async ({ page }) => {
  const doc = await findPerson(page, 'IND', (d) => d.public_money?.allocated)
  await openPerson(page, doc.id)

  await expect(page.locator('.public-money')).toBeVisible()
  // The single most misleading thing this app could do is let a reader read an
  // MPLADS allocation as the member's own money.
  await expect(page.locator('.public-money')).toContainText(/PUBLIC money/i)
  await expect(page.locator('.public-money'))
    .toContainText(/not the member's own money/i)
  await expect(page.locator('.public-money'))
    .toContainText(/district authorities sanction, implement and pay/i)
})

test('the headline party is the most recent term, not an arbitrary one', async ({ page }) => {
  // A defector used to be labelled with the party they LEFT, because the
  // headline came from whichever office row was inserted first.
  const doc = await findPerson(
    page, 'IND',
    (d) => (d.offices || []).length > 1
      && new Set((d.offices || []).map((o) => o.party)).size > 1
  )
  await openPerson(page, doc.id)
  const year = (j) => Number(String(j || '').match(/(19|20)\d{2}/)?.[0] || 0)
  const latest = [...doc.offices].sort(
    (a, b) => year(b.jurisdiction) - year(a.jurisdiction))[0]
  expect(doc.party).toBe(latest.party)
  await expect(page.locator('main')).toContainText(doc.party)
})

test('a UK page shows payments in sterling and no Indian caveats', async ({ page }) => {
  const doc = await findPerson(page, 'GBR', (d) =>
    (d.claims || []).some((c) => c.predicate === 'outside_earnings'))
  await openPerson(page, doc.id)

  const main = page.locator('main')
  await expect(main).toContainText('£')
  await expect(main, 'a UK register payment is not a rupee figure').not.toContainText('₹')

  // A caveat about self-declared criminal cases on an affidavit, shown for a
  // country that publishes no such data, implies the data is there.
  const hasCases = (doc.claims || []).some((c) => c.predicate === 'criminal_cases_declared')
  expect(hasCases).toBeFalsy()
  await expect(main).not.toContainText(/self-declared PENDING cases/i)
})

test('a US page points at filings and never invents a figure', async ({ page }) => {
  const doc = await findPerson(page, 'USA', (d) => (d.filings || []).length > 0)
  await openPerson(page, doc.id)

  const main = page.locator('main')
  await expect(main).toContainText(/Financial disclosures filed/i)
  // India publishes the numbers, the US publishes the paperwork. The page must
  // not present a pointer as if it were an amount.
  await expect(main).toContainText(/records that a filing EXISTS/i)
  await expect(main).toContainText(/disclosures-clerk\.house\.gov/)
  await expect(main).not.toContainText('₹')
  await expect(main).not.toContainText(/self-declared PENDING cases/i)
})

test('relayed fact-checks are attributed and marked as name-matched', async ({ page }) => {
  const idx = await index(page)
  let doc = null
  for (const row of idx.people) {
    const d = await person(page, row.id)
    if (d.factchecks?.items?.length) { doc = d; break }
  }
  test.skip(!doc, 'no fact-checks harvested; set GOOGLE_FACTCHECK_KEY')

  await openPerson(page, doc.id)
  const panel = page.locator('.panel', { hasText: 'Fact-checks published about them' })
  await expect(panel).toBeVisible()

  // The one place this site shows a verdict about a named living person. It is
  // only defensible because the verdict is someone else's, and because the
  // match is admitted to be by name alone.
  await expect(panel.locator('p.warn')).toBeVisible()
  await expect(panel).toContainText(/not by this site/i)
  await expect(panel).toContainText(/SEARCHING FOR THE POLITICIAN'S NAME/i)
  await expect(panel).toContainText(/may concern a different person of the same name/i)
  await expect(panel).toContainText(/MATCHED BY NAME/i)
})

test('a disagreement between sources is shown, never resolved', async ({ page }) => {
  const idx = await index(page)
  const row = idx.people.find((p) => (p.n_conflicts || 0) > 0)
  test.skip(!row, 'no conflicts in this build')
  const doc = await person(page, row.id)
  await openPerson(page, doc.id)

  const panel = page.locator('.conflict-panel')
  await expect(panel).toContainText(new RegExp(`disagree on ${doc.conflicts.length}\\b`))
  // Both values stay visible; the app picks no winner.
  for (const v of doc.conflicts[0].values) {
    await expect(panel).toContainText(String(v))
  }
  await expect(panel).toContainText(/neither is picked as correct/i)
})

test('questions carry the joint-tabling caveat', async ({ page }) => {
  const doc = await findPerson(page, 'IND', (d) => d.asked?.total)
  await openPerson(page, doc.id)
  const panel = page.locator('.panel.asked')
  await expect(panel).toBeVisible()
  // "Asked 234 questions" is not PRS's measure, and conflating them would
  // overstate what the member did.
  await expect(panel).toContainText(/NAME APPEARS ON/i)
  await expect(panel).toContainText(/tabled jointly/i)
  await expect(panel).toContainText(/not comparable/i)
})

test('growth is shown against the national series, not as a score', async ({ page }) => {
  const doc = await findPerson(page, 'IND', (d) => d.context?.declared_multiple)
  await openPerson(page, doc.id)
  const box = page.locator('.context-box')
  await expect(box).toContainText(/NATIONAL GDP PER CAPITA/i)
  await expect(box).toContainText(/reason to ask a question, not an accusation/i)
  await expect(box).not.toContainText(/\bscore\b|\brank\b|\brating\b/i)
})
