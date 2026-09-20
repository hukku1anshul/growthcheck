import { expect, test } from '@playwright/test'

/**
 * The query surface: two axes, a leader period, composable chips, and export.
 *
 * The axis test is the one that matters most. Plotting "% per year" and
 * "constant 2015 US$" on a SHARED axis is a lie with a picture attached -
 * whichever number happens to be larger looks dominant for no reason at all -
 * so the second indicator having its own axis, with its unit named on it, is a
 * correctness property rather than a decoration.
 */

const TWO_AXIS = '/?view=countries&c=IND&i=gdp_pc&i2=inflation&from=1960&to=2025'

async function axes(page) {
  return page.evaluate(() => {
    const opt = window.__chart?.getOption()
    if (!opt) return null
    return {
      yAxisCount: (opt.yAxis || []).length,
      yAxisNames: (opt.yAxis || []).map((a) => a.name || ''),
      yAxisShown: (opt.yAxis || []).map((a) => a.show !== false),
      seriesAxes: (opt.series || []).map((s) => s.yAxisIndex ?? 0),
      seriesNames: (opt.series || []).map((s) => s.name),
    }
  })
}

test('a second indicator gets its own axis, labelled with its own unit', async ({ page }) => {
  await page.goto(TWO_AXIS)
  await expect(page.locator('.qbar')).toBeVisible()

  const a = await axes(page)
  expect(a, 'chart instance should be exposed in dev builds').toBeTruthy()
  expect(a.yAxisCount).toBe(2)
  expect(a.yAxisShown[1], 'the right axis must be visible when a second indicator is on').toBe(true)

  // Each axis names the unit it carries.
  expect(a.yAxisNames[0]).toContain('2015 US$')
  expect(a.yAxisNames[1]).toContain('% per year')

  // One series per indicator, each bound to its own axis.
  expect(a.seriesAxes).toContain(0)
  expect(a.seriesAxes).toContain(1)
  // With two indicators the country name alone is ambiguous.
  expect(a.seriesNames.join(' | ')).toMatch(/GDP per capita/)
  expect(a.seriesNames.join(' | ')).toMatch(/Inflation/)
})

test('one indicator uses a single visible axis', async ({ page }) => {
  await page.goto('/?view=countries&c=IND&i=gdp_pc&from=1960&to=2025')
  await expect(page.locator('.qbar')).toBeVisible()
  const a = await axes(page)
  expect(a.yAxisShown[1], 'the right axis must be hidden with one indicator').toBe(false)
  expect(new Set(a.seriesAxes)).toEqual(new Set([0]))
})

test('the second indicator can be removed from its chip', async ({ page }) => {
  await page.goto(TWO_AXIS)
  const chip = page.locator('.qchip-second')
  await expect(chip).toContainText('Inflation')
  await chip.click()
  await expect(page.locator('.qchip-second')).toHaveCount(0)
  await expect.poll(async () => (await axes(page)).yAxisShown[1]).toBe(false)
  // and the URL forgets it, so the shared link matches what is on screen
  expect(page.url()).not.toContain('i2=')
})

test('a leader period sets the year range and says whose it is', async ({ page }) => {
  await page.goto('/?view=countries&c=IND&i=gdp_pc&from=1960&to=2025')
  const period = page.locator('.qfield', { hasText: 'Period' }).locator('select')

  const options = await period.locator('option').evaluateAll((o) =>
    o.map((x) => ({ value: x.value, label: x.textContent })))
  // REIGN gives India's leaders from Nehru onwards; if this is empty the spans
  // data never reached the page.
  expect(options.length).toBeGreaterThan(5)
  const singh = options.find((o) => o.value.includes('Manmohan Singh'))
  expect(singh, 'expected Manmohan Singh among India leader spans').toBeTruthy()

  await period.selectOption(singh.value)
  await expect(page.locator('.qchip-period')).toContainText('Manmohan Singh')
  await expect(page.locator('.qchip-period')).toContainText('2004')

  // The range actually moved, and the URL carries it.
  await expect.poll(() => page.url()).toContain('from=2004')
  await expect.poll(() => page.url()).toContain('to=2014')
})

test('a period does not survive switching to a country it belongs to', async ({ page }) => {
  // Nehru is not a period in Brazil. Carrying the id across would leave a chip
  // naming a tenure that country never had.
  await page.goto('/?view=countries&c=IND&i=gdp_pc&period=leader:Nehru:1947')
  await expect(page.locator('.qchip-period')).toContainText('Nehru')

  await page.goto('/?view=countries&c=BRA&i=gdp_pc&period=leader:Nehru:1947')
  await expect(page.locator('.qbar')).toBeVisible()
  await expect(page.locator('.qchip-period')).toHaveCount(0)
})

test('removing a country chip drops it, and the last one cannot be removed', async ({ page }) => {
  await page.goto('/?view=countries&c=IND%2CCHN&i=gdp_pc')
  await expect(page.locator('.qchip', { hasText: 'China' })).toBeVisible()
  await page.locator('.qchip', { hasText: 'China' }).click()
  await expect(page.locator('.qchip', { hasText: 'China' })).toHaveCount(0)

  // An empty chart is not a useful state to be able to reach by clicking.
  await page.locator('.qchip-focus').click()
  await expect(page.locator('.qchip-focus')).toBeVisible()
})

test('the chart downloads as a PNG named after the question', async ({ page }) => {
  await page.goto(TWO_AXIS)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator('.qexport button', { hasText: 'Save image' }).click(),
  ])
  const name = download.suggestedFilename()
  expect(name).toMatch(/\.png$/)
  // chart(3).png tells a reader nothing three weeks later.
  expect(name).toContain('gdp_pc')
  expect(name).toContain('inflation')
  expect(name).toContain('IND')
})

test('the data downloads as CSV carrying both indicators and their units', async ({ page }) => {
  await page.goto(TWO_AXIS)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator('.qexport button', { hasText: 'Download data' }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/\.csv$/)

  const stream = await download.createReadStream()
  const text = await new Promise((resolve, reject) => {
    let buf = ''
    stream.on('data', (c) => { buf += c })
    stream.on('end', () => resolve(buf))
    stream.on('error', reject)
  })

  const lines = text.replace(/^﻿/, '').trim().split('\n')
  expect(lines[0]).toBe('country,iso3,indicator,unit,year,value')
  expect(lines.length).toBeGreaterThan(50)

  // Long format: both indicators present, each row naming its own unit, so the
  // file cannot be read as though every number shared a scale.
  const body = lines.slice(1)
  expect(body.some((l) => l.includes('GDP per capita'))).toBeTruthy()
  expect(body.some((l) => l.includes('Inflation'))).toBeTruthy()
  expect(body.every((l) => l.startsWith('India,IND,'))).toBeTruthy()

  // Every row is country,iso3,indicator,unit,year,value - units containing a
  // comma must be quoted, not silently shifting every later column.
  for (const line of body.slice(0, 20)) {
    const cells = line.match(/("([^"]|"")*"|[^,]*)(,|$)/g)
    expect(cells.length, `malformed row: ${line}`).toBeGreaterThanOrEqual(6)
  }
})

test('the stats strip reports endpoints that exist, and no rate it cannot justify',
  async ({ page }) => {
    await page.goto('/?view=countries&c=IND&i=gdp_pc&from=2004&to=2014')
    const strip = page.locator('.stats-strip')
    await expect(strip).toBeVisible()

    const row = strip.locator('.stats-row').first()
    const span = (await row.locator('.s-span').textContent()) || ''
    const years = span.match(/(\d{4})\s*→\s*(\d{4})/)
    expect(years, `span should name both endpoint years: ${span}`).toBeTruthy()

    // The endpoints are the first and last years that carry a number, which
    // must lie inside the selected range - never outside it.
    expect(Number(years[1])).toBeGreaterThanOrEqual(2004)
    expect(Number(years[2])).toBeLessThanOrEqual(2014)

    await expect(strip).toContainText(/never across a sign change/i)
  })
