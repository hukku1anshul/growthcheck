import { expect, test } from '@playwright/test'
import { index, openPerson, person } from './helpers.js'

/**
 * The promise the whole project rests on: no figure without a receipt, and no
 * secret in the receipt.
 */

test('every rendered claim shows its source, archive date and hash', async ({ page }) => {
  const idx = await index(page)
  const doc = await person(page, idx.people[0].id)
  await openPerson(page, doc.id)

  const rows = page.locator('.claim')
  const n = await rows.count()
  expect(n).toBeGreaterThan(0)

  for (let i = 0; i < Math.min(n, 25); i++) {
    const src = rows.nth(i).locator('.cl-src')
    // A claim whose receipt is only a live URL is exactly what this project
    // refuses: openbudgetsindia.org lapsed to a casino site, and any figure
    // sourced to it became unverifiable overnight.
    await expect(src).toContainText(/archived \d{4}-\d{2}-\d{2}/)
    await expect(src).toContainText(/sha256 [0-9a-f]{8,}/)
  }
})

test('no published source URL carries an API key', async ({ page }) => {
  const idx = await index(page)
  // The request URL is published on every claim, so an API that authenticates
  // with ?key= would put the operator's secret on the public web. Redaction
  // happens in ingest/archive.py; this is the check that it reached the page.
  const suspects = idx.people.slice(0, 40)
  for (const row of suspects) {
    const doc = await person(page, row.id)
    for (const c of doc.claims || []) {
      expect(c.src_url, `${doc.name}: ${c.predicate}`).not.toMatch(/AIza[0-9A-Za-z_-]{20,}/)
      expect(c.src_url).not.toMatch(/[?&](api_key|apikey|access_token|client_secret)=[^&]{8,}/i)
    }
  }
})

test('a field selector named "key" is NOT mistaken for a credential', async ({ page }) => {
  // MPLADS asks for a metric with key=Allocated Limit for Hon'ble MPs. Masking
  // it would destroy the archive identity of every MPLADS document and collapse
  // distinct queries into one colliding URL, so redaction judges the value, not
  // the parameter name.
  const idx = await index(page)
  // Look at people the index says hold MPLADS figures, rather than the first
  // N rows in whatever order the default sort happens to produce.
  const withMplads = idx.people.filter((p) => p.allocated != null).slice(0, 25)
  expect(withMplads.length, 'the index should carry MPLADS people').toBeGreaterThan(0)

  let seen = 0
  for (const row of withMplads) {
    const doc = await person(page, row.id)
    for (const c of doc.claims || []) {
      const url = c.src_url || ''
      if (url.includes('mplads.mospi.gov.in') && url.includes('key=')) {
        expect(url, 'a readable field selector must survive redaction')
          .not.toContain('key=REDACTED')
        seen++
      }
    }
  }
  expect(seen, 'expected MPLADS URLs carrying a key= field selector').toBeGreaterThan(0)
})

test('the build states where the data came from and when', async ({ page }) => {
  await page.goto('/')
  const foot = page.locator('.foot')
  await expect(foot).toContainText(/Built \d{4}-\d{2}-\d{2}/)
  await expect(foot).toContainText(/World Bank/i)
  await expect(foot).toContainText(/hashed and cached/i)
})

test('the site never publishes a score or ranking of a politician', async ({ page }) => {
  const idx = await index(page)
  await openPerson(page, idx.people[0].id)
  const body = (await page.locator('main').textContent()) || ''
  // docs/ETHICS.md forbids publishing an opinion about a named living person.
  expect(body).not.toMatch(/integrity score|corruption score|\brank(ed|ing)? #?\d/i)
})
