import { expect, test } from '@playwright/test'
import { index, openPeopleList, openPerson, person } from './helpers.js'

/**
 * Phone width. Runs under the `mobile` project only.
 *
 * A horizontal scrollbar is the symptom that matters here: it means a table,
 * a long source URL or a caveat block is running off the screen, and a caveat
 * the reader cannot see is a caveat that is not being made.
 */

const noHorizontalScroll = async (page) => {
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }))
  expect(
    overflow.scrollWidth,
    `page scrolls horizontally: ${overflow.scrollWidth} > ${overflow.innerWidth}`
  ).toBeLessThanOrEqual(overflow.innerWidth + 1)
}

test('the people list fits the screen and can still be paged', async ({ page }) => {
  await openPeopleList(page)
  await noHorizontalScroll(page)

  await expect(page.locator('.pager')).toBeVisible()
  await page.locator('.pg', { hasText: 'Next' }).click()
  await expect(page.locator('.pg-at')).toContainText('101–200')
  await noHorizontalScroll(page)
})

test('a person page fits, including long source URLs', async ({ page }) => {
  const idx = await index(page)
  const row = idx.people.find((p) => (p.n_claims || 0) > 20) || idx.people[0]
  await openPerson(page, row.id)
  await noHorizontalScroll(page)
})

test('the fact-check caveat is readable on a phone', async ({ page }) => {
  const idx = await index(page)
  let doc = null
  for (const r of idx.people.slice(0, 400)) {
    const d = await person(page, r.id)
    if (d.factchecks?.items?.length) { doc = d; break }
  }
  test.skip(!doc, 'no fact-checks harvested')

  await openPerson(page, doc.id)
  const warn = page.locator('.panel p.warn').first()
  await warn.scrollIntoViewIfNeeded()
  await expect(warn).toBeVisible()
  const box = await warn.boundingBox()
  expect(box.width).toBeGreaterThan(200)   // not collapsed to a sliver
  await noHorizontalScroll(page)
})

test('the country chart fits the screen', async ({ page }) => {
  await page.goto('/?view=countries&c=IND%2CCHN&i=gdp_pc')
  await expect(page.locator('.charthead')).toBeVisible()
  await noHorizontalScroll(page)
})
