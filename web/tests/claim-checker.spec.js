import { expect, test } from '@playwright/test'
import { check, openPeopleList } from './helpers.js'

/**
 * The claim checker, and the citation bug that kept coming back.
 *
 * Twice now the checker has compared against one figure and cited a DIFFERENT
 * document as its source: first an MPLADS works URL beside an asset figure,
 * then - after the 2019 affidavits were harvested and every person gained a
 * second `declared_assets` claim - the 2019 candidate page beside a 2024
 * number. The second time it survived a fix to the browser because the same
 * bug also lived in tools/checkclaim.py, which nobody re-tested.
 *
 * So the assertion here is the RULE, not a remembered URL: the year in the
 * cited source must be the year of the figure that was actually compared. That
 * stays true across re-harvests, which a hard-coded candidate id would not.
 */
test.beforeEach(async ({ page }) => {
  await openPeopleList(page)
})

test('an asset check cites a document from the year it compared', async ({ page }) => {
  const { rows } = await check(page, 'Amit Shah owns assets worth Rs 500 crore')

  expect(rows.person, 'should resolve the politician').toContain('Amit Shah')
  expect(rows['fact type']).toContain('assets')

  // "record (2024) ₹65.67 crore" -> the year the comparison used
  const year = rows.claimed?.match(/\((\d{4})\)/)?.[1]
  expect(year, `claimed row should name the year it used: ${rows.claimed}`).toBeTruthy()
  expect(
    rows.source,
    `cited ${rows.source} for a ${year} figure - the receipt must be that year's document`
  ).toContain(year)
})

test('a criminal-case check cites the contemporaneous affidavit', async ({ page }) => {
  const { rows } = await check(page, 'Rahul Gandhi has 20 criminal cases')

  expect(rows.person).toContain('Rahul Gandhi')
  const year = rows.record?.match(/as of (\d{4})/)?.[1]
  expect(year, `record row should state its year: ${rows.record}`).toBeTruthy()
  expect(rows.source, `cited ${rows.source} for a ${year} figure`).toContain(year)

  // Pending cases are not convictions, and the page must keep saying so.
  expect(rows.record).toMatch(/PENDING/i)
  expect(rows.record).toMatch(/NOT convictions/i)
})

test('an MPLADS check carries the who-actually-spends-it caveat', async ({ page }) => {
  const { rows, text } = await check(page, 'Priya Saroj has spent none of her MPLADS money')
  expect(rows.record).toMatch(/allocated/i)
  expect(rows.record).toMatch(/utilised/i)
  expect(text).toMatch(/district authorities sanction, implement and pay/i)
})

test('it refuses a claim it cannot check instead of guessing', async ({ page }) => {
  const { text } = await check(page, 'Narendra Modi flew to the moon in 2019')
  expect(text).toMatch(/CANNOT CHECK/i)
  // A refusal must not also print a record or a source, which would imply it
  // half-checked something.
  expect(text).not.toMatch(/sha256/i)
})

test('it never returns a verdict of its own', async ({ page }) => {
  const { text } = await check(page, 'Amit Shah owns assets worth Rs 500 crore')
  // "NOT CONSISTENT" describes the gap between two numbers. A bare TRUE/FALSE
  // would be this site passing judgement on a named living person, which
  // docs/ETHICS.md forbids.
  expect(text).not.toMatch(/\b(TRUE|FALSE|LIE|FAKE|DEBUNKED)\b/)
  await expect(page.locator('.cc-verdict')).toContainText(/no verdict/i)
})

test('every checked claim shows an archive hash, not just a live link', async ({ page }) => {
  const { rows } = await check(page, 'Amit Shah owns assets worth Rs 500 crore')
  expect(rows.source).toMatch(/^https?:\/\//)
  expect(rows.archived, 'a citation without a hash is not provenance').toMatch(/sha256\s+[0-9a-f]{8,}/)
})
