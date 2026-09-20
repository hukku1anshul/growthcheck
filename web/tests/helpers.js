import { expect } from '@playwright/test'

/**
 * Shared fixtures.
 *
 * Subjects are chosen FROM THE SHIPPED DATA at run time rather than hard-coded
 * by id. A test that says "person 497 has fact-checks" breaks the next time the
 * store is rebuilt and the ids shift, and a suite that breaks on every harvest
 * gets switched off. These helpers ask the index for "a person who has X", so
 * the assertions stay about behaviour.
 */

let cached = null

export async function index(page) {
  if (cached) return cached
  const res = await page.request.get('/data/people/index.json')
  expect(res.ok()).toBeTruthy()
  cached = await res.json()
  return cached
}

export async function person(page, id) {
  const res = await page.request.get(`/data/people/${id}.json`)
  expect(res.ok(), `person ${id} bundle should be served`).toBeTruthy()
  return res.json()
}

/** The first person in `country` whose bundle satisfies `pred`. */
export async function findPerson(page, country, pred, limit = 120) {
  const idx = await index(page)
  const candidates = idx.people.filter((p) => !country || p.country === country)
  for (const row of candidates.slice(0, limit)) {
    const doc = await person(page, row.id)
    if (pred(doc)) return doc
  }
  throw new Error(`no ${country || 'any'} person matched in the first ${limit}`)
}

export async function openPerson(page, id) {
  await page.goto(`/?view=people&p=${id}`)
  await expect(page.locator('.claims, .panel').first()).toBeVisible()
}

export async function openPeopleList(page) {
  await page.goto('/?view=people')
  await expect(page.locator('.ptable')).toBeVisible()
}

/** Run a claim through the in-page checker and return its label/value rows. */
export async function check(page, claim) {
  await page.locator('.checkclaim input.search').fill(claim)
  await page.locator('.checkclaim button', { hasText: /^Check/ }).click()
  await expect(page.locator('.cc-result')).toBeVisible()
  const rows = {}
  for (const line of await page.locator('.cc-result .cc-line').all()) {
    const k = ((await line.locator('.cc-k').textContent()) || '').trim().toLowerCase()
    const v = ((await line.locator('.cc-v').textContent()) || '').trim()
    if (k) rows[k] = rows[k] ? `${rows[k]} ${v}` : v
  }
  return { rows, text: (await page.locator('.cc-result').textContent()) || '' }
}
