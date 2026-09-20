import { defineConfig, devices } from '@playwright/test'

/**
 * Browser tests for the two views.
 *
 * WHY THESE EXIST
 * ---------------
 * The same defect was found four separate times by opening a page and noticing
 * something wrong: a citation pointing at the wrong document, a defector shown
 * under the party they left, an India-only caveat printed on a UK member's page.
 * Every one of them was a rendering fact that no Python check could see, and
 * finding them by hand is not a method - it only works while someone is looking.
 *
 * `tools/audit.py` covers the data. This covers what the data LOOKS like once a
 * browser has it. Between them a push is checked end to end.
 *
 * The server is started by Playwright against a PRODUCTION build, not the dev
 * server: a stale hot-module state once made a working page look broken for
 * several minutes, and a test that can fail for that reason is worse than no
 * test.
 */
export default defineConfig({
  testDir: './tests',
  // A failing UI test usually means the page changed, not that it is flaky.
  // Retrying would hide a real regression, so it retries once locally and not
  // at all in CI, where a green-on-retry run is a lie.
  retries: process.env.CI ? 0 : 1,
  workers: process.env.CI ? 2 : 4,
  reporter: [['list']],
  timeout: 45_000,
  expect: { timeout: 15_000 },
  use: {
    // PF_BASE_URL runs the same suite against a deployed site:
    //     PF_BASE_URL=https://politicalfindings.onrender.com npx playwright test
    // A local pass says the code is right; only this says the thing readers
    // actually load is right, which is a different claim and the one that
    // matters after a deploy.
    baseURL: process.env.PF_BASE_URL || 'http://localhost:4178',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    // `testIgnore` is load-bearing, not tidiness: without it the responsive
    // specs also run at desktop width, where "fits on a phone" passes for the
    // wrong reason and reports a green tick for something never tested.
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: /responsive\.spec\.js/,
    },
    { name: 'mobile', use: { ...devices['Pixel 7'] }, testMatch: /responsive\.spec\.js/ },
  ],
  // No local server when testing a deployed site - building one and then not
  // using it would be a slow way to prove nothing.
  webServer: process.env.PF_BASE_URL ? undefined : {
    command: 'npm run build && npm run preview -- --port 4178 --strictPort',
    url: 'http://localhost:4178',
    // NEVER reuse. `reuseExistingServer: !process.env.CI` is the documented
    // default and it silently invalidates the whole suite: an already-running
    // preview is reused WITHOUT rebuilding, so the tests run against whatever
    // was last built. Caught by mutation testing - a deliberately reintroduced
    // citation bug passed all six checker tests, because the browser was still
    // being served the previous build. Rebuilding costs about ten seconds and
    // buys the guarantee that a pass refers to the code on disk.
    reuseExistingServer: false,
    timeout: 180_000,
  },
})
