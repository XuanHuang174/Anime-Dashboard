// Deployed UI only. Receives a short-lived normal-login token on stdin, never
// argv, a file, report, screenshot or the user's persistent browser profile.
import { createRequire } from 'node:module'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
let input = ''; for await (const chunk of process.stdin) input += chunk
let access = JSON.parse(input).access; input = ''
const FRONT = 'https://xh-anime.com', BACK = 'https://anime-dashboard-api-dv0s.onrender.com'
const report = { scope: '7b-deployed-browser-readonly', checks: [], resource_status: null,
  business_writes: 0, blocked_other_side_effects: 0, clipboard_writes: 0, bt_launches: 0 }
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
await context.addInitScript(({ origin, token }) => {
  if (location.origin !== origin) return
  localStorage.setItem('anime_journal_access_token', token)
  Object.defineProperty(navigator, 'clipboard', { configurable: true,
    value: { writeText: async () => { throw new Error('Clipboard disabled in readonly acceptance') } } })
}, { origin: FRONT, token: access })
access = null
let searches = 0, apiReads = 0
await context.route('**/*', route => {
  const request = route.request(), url = new URL(request.url())
  if (!['GET', 'HEAD'].includes(request.method()) || !['https:'].includes(url.protocol)) {
    if (url.origin === BACK && url.pathname.startsWith('/api/')) report.business_writes++
    else report.blocked_other_side_effects++
    return route.abort()
  }
  if (url.origin === BACK && url.pathname.startsWith('/api/')) {
    if (++apiReads > 40) return route.abort()
    if (url.pathname === '/api/utils/anime-search/') searches++
  }
  return route.continue()
})
const page = await context.newPage()
page.setDefaultTimeout(45000)
const check = (name, value) => { report.checks.push({ name, passed: Boolean(value) }); if (!value) throw new Error('check_failed') }
try {
  await page.goto(`${FRONT}/recommendations`, { waitUntil: 'domcontentloaded' })
  const first = page.locator('.rec-card .rec-card-actions > button:first-child').first()
  await first.waitFor()
  check('v1_page_label', await page.getByText('推荐 V1', { exact: true }).count() > 0)
  await first.click()
  const trigger = page.getByRole('button', { name: '🧲 寻找资源', exact: true })
  await trigger.waitFor()
  check('opening_detail_does_not_search', searches === 0)
  const resourceResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/utils/anime-search/')
  await trigger.click()
  report.resource_status = (await resourceResponse).status()
  await page.locator('.rec-dialog--resource-open').waitFor()
  await page.waitForFunction(() => document.querySelector('dialog').getAnimations({ subtree: true }).length === 0)
  const geometry = await page.evaluate(() => {
    const main = document.querySelector('.rec-dialog-main').getBoundingClientRect()
    const resource = document.querySelector('.rec-dialog-resource').getBoundingClientRect()
    return { modal: document.querySelector('dialog').matches(':modal'), side: resource.left >= main.right - 2, width: resource.width }
  })
  check('desktop_native_top_layer_and_400px_side_panel', geometry.modal && geometry.side && geometry.width === 400)
  check('resource_has_context_and_return', await page.locator('.rec-resource-context h3').innerText() !== ''
    && await page.getByRole('button', { name: '← 返回详情' }).isVisible())
  check('single_resource_search', searches === 1)
  await page.locator('#recResourceDrawer .resource-card, #recResourceDrawer .resource-empty').first().waitFor()
  check('resource_success_or_local_recovery', await page.locator('.resource-card').count() > 0
    || await page.locator('.resource-empty').count() > 0)
  await page.setViewportSize({ width: 375, height: 812 })
  await page.waitForFunction(() => document.querySelector('.rec-dialog-main').inert)
  check('mobile_same_dialog_and_inert_detail', await page.locator('dialog').count() === 1)
  check('mobile_resize_does_not_search_again', searches === 1)
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => !document.querySelector('dialog').classList.contains('rec-dialog--resource-open'))
  check('escape_returns_focus_without_closing_detail', await trigger.evaluate(node => node === document.activeElement)
    && await page.locator('dialog[open]').count() === 1)
  await page.keyboard.press('Escape')
  await page.locator('dialog').waitFor({ state: 'detached' })
  check('second_escape_returns_to_page', await first.isVisible())
  check('no_business_writes', report.business_writes === 0)
  report.result = report.resource_status === 200 ? 'passed_scoped_readonly' : 'passed_ui_with_resource_service_degraded'
} catch (error) { report.result = 'failed_or_blocked'; report.failure_type = error.name }
finally { await browser.close() }
console.log(JSON.stringify(report))
process.exitCode = report.result.startsWith('passed') ? 0 : 1
