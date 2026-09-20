import { expect, test } from '@playwright/test'

/**
 * The country explorer: the half of the project that has to make a number
 * legible before it can be trusted.
 */

async function meta(page) {
  const res = await page.request.get('/data/meta.json')
  expect(res.ok()).toBeTruthy()
  return res.json()
}

test('an indicator ships with its plain meaning and its blind spots', async ({ page }) => {
  const m = await meta(page)
  await page.goto('/?view=countries&c=IND&i=gdp_pc')
  await expect(page.locator('.charthead')).toBeVisible()

  // etl/build.py refuses to ship a series missing these, and the page has to
  // actually render them or the refusal buys nothing.
  // Targeted by content, not by position: the chart page grew a stats panel
  // above the explainer, and `.panel.first()` silently started pointing at a
  // different panel.
  const explainer = page.locator('.panel', {
    has: page.getByText(/WHAT THIS NUMBER ACTUALLY IS/i),
  })
  await expect(explainer).toContainText(/WHAT THIS NUMBER ACTUALLY IS/i)
  await expect(page.locator('dd.warn').first()).toBeVisible()
  const blind = m.indicators.find((i) => i.id === 'gdp_pc').blindspots
  await expect(page.locator('dd.warn').first()).toContainText(blind.slice(0, 40))
})

test('switching indicator changes the series, unit and coverage', async ({ page }) => {
  await page.goto('/?view=countries&c=IND&i=gdp_pc')
  const before = await page.locator('.coverage').textContent()

  await page.goto('/?view=countries&c=IND&i=gdp_growth')
  await expect(page.locator('.charthead')).toContainText(/GDP growth/i)
  await expect(page.locator('.charthead')).toContainText(/% per year/i)
  await expect(page.locator('.coverage')).not.toHaveText(before || '')

  // A gap in a line is missing data, not a zero, and saying so is the
  // difference between an honest chart and a misleading one.
  await expect(page.locator('.coverage')).toContainText(/did not report that year/i)
  await expect(page.locator('.coverage')).toContainText(/not zeros/i)
})

test('curated decisions and derived breaks are distinguishable', async ({ page }) => {
  await page.goto('/?view=countries&c=IND&i=gdp_growth')
  const rows = page.locator('.event-row')
  await expect(rows.first()).toBeVisible()

  // A computed break says "something discontinuous happened here"; a curated
  // decision says "a government did X". Presenting them alike would let the
  // app assert causes it cannot support.
  const derived = page.locator('.event-row', { has: page.locator('.badge') })
  expect(await derived.count()).toBeGreaterThan(0)
  await expect(derived.first().locator('.badge')).toHaveText(/derived/i)

  const total = await rows.count()
  expect(total).toBeGreaterThan(await derived.count())
})

test('a curated decision opens with what is disputed about it', async ({ page }) => {
  await page.goto('/?view=countries&c=IND&i=gdp_growth')
  const curated = page.locator('.event-row', { hasNot: page.locator('.badge') }).first()
  await curated.click()

  const detail = page.locator('.event-detail')
  await expect(detail).toBeVisible()
  // docs/ETHICS.md: we mark events, we never assert causes. The dispute is not
  // optional decoration.
  await expect(detail.locator('.contested')).toBeVisible()
  await expect(detail).toContainText(/WHAT IS DISPUTED/i)
  await expect(detail).toContainText(/Source/i)
})

test('a derived break warns that it is computed, not documented', async ({ page }) => {
  await page.goto('/?view=countries&c=IND&i=gdp_growth')
  await page.locator('.event-row', { has: page.locator('.badge') }).first().click()
  await expect(page.locator('.derived-warn')).toBeVisible()
})

test('two countries can be compared on one chart', async ({ page }) => {
  await page.goto('/?view=countries&c=IND%2CCHN&i=gdp_pc')
  await expect(page.locator('.chips')).toContainText('India')
  await expect(page.locator('.chips')).toContainText('China')
  await expect(page.locator('canvas, svg').first()).toBeVisible()
})

test('the theme toggle switches and the warning blocks survive it', async ({ page }) => {
  await page.goto('/?view=countries&c=IND&i=gdp_pc')
  const themeOf = () =>
    page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
      || document.body.className
      || getComputedStyle(document.body).backgroundColor)

  const before = await themeOf()
  await page.locator('button[title="Toggle theme"], button', { hasText: /^[☾☀]/ })
    .first().click().catch(async () => {
      await page.getByRole('button', { name: /toggle theme/i }).click()
    })
  await expect.poll(themeOf).not.toBe(before)
  // Amber-on-dark is where contrast regressions hide.
  await expect(page.locator('dd.warn').first()).toBeVisible()
})
