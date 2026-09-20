import { expect, test } from '@playwright/test'
import { index, openPeopleList } from './helpers.js'

const PAGE_SIZE = 100

test.beforeEach(async ({ page }) => {
  await openPeopleList(page)
})

test('the list pages through everyone rather than truncating', async ({ page }) => {
  const idx = await index(page)
  const total = idx.people.length

  // The list was once .slice(0, 400) with no way past it. At 2,196 people that
  // left most of them reachable only by someone who already knew a name.
  await expect(page.locator('.ptr:not(.phead)')).toHaveCount(PAGE_SIZE)
  await expect(page.locator('.pg-at')).toHaveText(`1–${PAGE_SIZE} of ${total}`)

  const first = await page.locator('.ptr:not(.phead) .pname').first().textContent()
  await page.locator('.pg', { hasText: 'Next' }).click()

  await expect(page.locator('.pg-at')).toHaveText(`${PAGE_SIZE + 1}–${PAGE_SIZE * 2} of ${total}`)
  await expect(page.locator('.pg', { hasText: 'Previous' })).toBeEnabled()
  const second = await page.locator('.ptr:not(.phead) .pname').first().textContent()
  expect(second, 'page 2 must show different people').not.toBe(first)
})

test('Previous is disabled on the first page and Next on the last', async ({ page }) => {
  await expect(page.locator('.pg', { hasText: 'Previous' })).toBeDisabled()
  const idx = await index(page)
  const pages = Math.ceil(idx.people.length / PAGE_SIZE)
  for (let i = 1; i < pages; i++) {
    await page.locator('.pg', { hasText: 'Next' }).click()
  }
  await expect(page.locator('.pg', { hasText: 'Next' })).toBeDisabled()
})

test('changing a filter returns to page 1', async ({ page }) => {
  await page.locator('.pg', { hasText: 'Next' }).click()
  await expect(page.locator('.pg-at')).toContainText(`${PAGE_SIZE + 1}–`)

  // Page 7 of a different result set is not where the reader was.
  await page.locator('input.search').first().fill('singh')
  await expect(page.locator('.pg-at')).toContainText('1–')
})

test('narrowing a search while deep in the list does not blank the table', async ({ page }) => {
  await page.locator('input.search').first().fill('singh')
  await page.locator('.pg', { hasText: 'Next' }).click()
  await expect(page.locator('.pg-at')).toContainText(`${PAGE_SIZE + 1}–`)

  // Now narrow to a result set with only one page. Without clamping, the page
  // index is stranded past the end and the table renders empty for a search
  // that did match.
  await page.locator('input.search').first().fill('singh yadav')
  await expect(page.locator('.ptr:not(.phead)')).not.toHaveCount(0)
})

test('the country filter matches the counts in the index', async ({ page }) => {
  const idx = await index(page)
  for (const country of idx.countries) {
    const expected = idx.people.filter((p) => p.country === country).length
    await page.locator('select').first().selectOption(country)
    await expect(page.locator('.side .note').first())
      .toContainText(`${expected} of ${idx.people.length} match`)
  }
})

test('every sort option reorders without emptying the list', async ({ page }) => {
  const sort = page.locator('select').nth(1)
  const options = await sort.locator('option').evaluateAll((o) => o.map((x) => x.value))
  expect(options.length).toBeGreaterThanOrEqual(6)
  const seen = new Set()
  for (const value of options) {
    await sort.selectOption(value)
    await expect(page.locator('.ptr:not(.phead)')).toHaveCount(PAGE_SIZE)
    seen.add(await page.locator('.ptr:not(.phead) .pname').first().textContent())
  }
  expect(seen.size, 'different sorts should not all start with the same person').toBeGreaterThan(1)
})

test('the review queue opens the person it names', async ({ page }) => {
  const idx = await index(page)
  test.skip(!idx.review_queue?.length, 'no ambiguous pairs queued')

  // Refusing to merge and asking a human is a stated commitment; the queue has
  // to actually be navigable for that to mean anything.
  const first = page.locator('.rp-name').first()
  const name = (await first.textContent())?.trim()
  await first.click()
  await expect(page.locator('main')).toContainText(name)
  expect(page.url()).toMatch(/[?&]p=\d+/)
})

test('selecting a person puts them in the URL so the view can be shared', async ({ page }) => {
  await page.locator('.ptr:not(.phead)').first().click()
  await expect(page).toHaveURL(/[?&]view=people&p=\d+/)

  const url = page.url()
  await page.goto(url)
  await expect(page.locator('main')).toContainText(/all people/i)
})
