// Real local Django + React. Only synthetic account data is changed.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const f = JSON.parse(await readFile('../.tools/curations-e2-fixture.json', 'utf8')), base = 'http://127.0.0.1:5191', out = '../.tools/curations-e2-qa'
assert.match(f.username, /^curation-e1-/); await mkdir(out, { recursive: true })
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const motion = process.env.CURATION_TEST_MOTION || 'reduce'
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: motion })
await context.addInitScript(({ token, user_id }) => { localStorage.setItem('anime_journal_access_token', token); localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`, 'edit') }, f)
const page = await context.newPage(); page.setDefaultTimeout(20000)
const errors = [], writes = [], reads = []
page.on('pageerror', e => errors.push(e.message)); page.on('request', r => { if (r.url().includes('/api/curations/')) { if (r.method() === 'POST') writes.push(r.postDataJSON()); else reads.push(new URL(r.url())) } })
const ws = () => page.getByRole('region', { name: '私人片单工作区', exact: true }), nav = name => page.getByRole('navigation', { name: '私人手账工作区' }).getByRole('button', { name, exact: true })
const btn = name => ws().getByRole('button', { name, exact: true }), modal = title => page.getByRole('dialog', { name: title, exact: true })
const ready = async () => { await ws().locator('[aria-busy=false]').waitFor({ state: 'attached' }); await ws().getByText('正在读取片单…', { exact: true }).waitFor({ state: 'detached' }) }
const api = async (path, data) => { const response = await context.request[data ? 'post' : 'get'](base + '/api/' + path, { headers: { Authorization: `Bearer ${f.token}` }, ...(data ? { data } : {}) }); assert.equal(response.status(), 200, await response.text()); return response.json() }
const detail = () => api(`curations/${f.list_id}/`, undefined)
const save = async title => { await modal(title).getByRole('button', { name: '保存片单修改', exact: true }).click(); await modal(title).waitFor({ state: 'detached' }); await ready() }
const searchCandidate = async (title, name) => { await modal(title).getByLabel('搜索片单候选作品', { exact: true }).fill(name); await modal(title).getByRole('button', { name: '筛选作品', exact: true }).click(); await modal(title).getByText('正在读取私人作品…', { exact: true }).waitFor({ state: 'detached' }) }
try {
  await page.goto(base + '/dashboard'); await nav('片单').click(); await ready()
  assert.equal(await ws().locator('.curation-card').count(), 12)
  await ws().getByLabel('搜索主题片单', { exact: true }).fill('E2 片单'); await btn('搜索片单').click(); await ready()
  await btn('下一页片单').click(); await ready(); assert.equal(await ws().locator('.curation-card').count(), 1)
  await btn('E2 片单 12').click(); await ready(); await btn('← 返回片单首页').click(); await ready()
  assert.equal(await ws().locator('.curation-card').count(), 1)
  assert.equal(await ws().getByLabel('搜索主题片单', { exact: true }).inputValue(), 'E2 片单')
  assert.equal(await btn('打开片单E2 片单 12').evaluate(el => el === document.activeElement), true)
  await ws().getByLabel('搜索主题片单', { exact: true }).fill(''); await btn('搜索片单').click(); await ready()
  await btn('E2 跨页主题').click(); await ready(); assert.equal(await ws().locator('.curation-entry').count(), 12)
  await btn('下一页成员').click(); await ready()
  // Browsing details from edit mode remains read-only and returns to the same member page.
  await ws().getByRole('button', { name: '查看作品详情', exact: true }).first().click()
  await page.getByRole('dialog').waitFor(); assert.equal(await page.getByRole('button', { name: /保存全部修改/ }).count(), 0)
  await page.getByRole('button', { name: '关闭详情', exact: true }).click(); await page.getByRole('dialog').waitFor({ state: 'detached' }); await ready()
  assert.match(await ws().locator('.curation-number').first().innerText(), /^13$/)
  await ws().getByRole('button', { name: '编辑入选理由', exact: true }).first().click()
  await modal('编辑入选理由').getByLabel('入选理由', { exact: true }).fill('E2 保留的私人理由🌸\n第二行')
  await page.keyboard.press('Escape'); await ws().getByRole('group', { name: '确认离开片单调整' }).waitFor()
  await btn('继续编辑片单').click(); assert.match(await modal('编辑入选理由').getByLabel('入选理由', { exact: true }).inputValue(), /第二行/)
  await save('编辑入选理由')
  // Metadata uses a complete, version-bound membership read, including covers beyond page 100.
  await btn('编辑片单').click(); await modal('编辑片单资料').waitFor()
  assert.equal(await modal('编辑片单资料').getByLabel('片单封面', { exact: true }).locator('option').count(), 105)
  await modal('编辑片单资料').getByLabel('置顶这张片单', { exact: true }).check()
  await modal('编辑片单资料').getByLabel('专题简介', { exact: true }).fill('E2 专题简介，和作品长评分别保存。')
  await modal('编辑片单资料').getByLabel('片单封面', { exact: true }).selectOption({ label: 'E2 合成作品 102' }); await save('编辑片单资料')
  assert.equal((await detail()).collection.cover.title, 'E2 合成作品 102')
  await btn('编辑片单').click(); await modal('编辑片单资料').getByLabel('归档这张片单', { exact: true }).check(); await save('编辑片单资料')
  await btn('← 返回片单首页').click(); await ready(); assert.equal(await btn('E2 跨页主题').count(), 0)
  await ws().getByLabel('片单范围', { exact: true }).selectOption('archived'); await ready(); await btn('E2 跨页主题').click(); await ready()
  await btn('编辑片单').click(); await modal('编辑片单资料').getByLabel('归档这张片单', { exact: true }).uncheck(); await save('编辑片单资料')
  const all = await api(`curations/${f.list_id}/?page_size=100`), tail = await api(`curations/${f.list_id}/?page=2&page_size=100&version=${all.version}`)
  const last = tail.results.at(-1), initialCount = all.count
  await btn('调整片单顺序').click(); await ws().getByRole('region', { name: '调整片单顺序' }).waitFor()
  assert.equal(await ws().locator('.curation-order > li').count(), f.full_count)
  const move = ws().getByLabel(`将${last.record.title}移至位置`, { exact: true }); await move.fill('1'); await move.locator('..').getByRole('button', { name: '移动', exact: true }).click()
  await nav('回顾').click(); await btn('继续编辑片单').click(); assert.equal(await nav('片单').getAttribute('aria-pressed'), 'true')
  await btn('保存片单顺序').click(); await ready(); await ws().getByRole('region', { name: '调整片单顺序' }).waitFor({ state: 'detached' })
  assert.equal((await detail()).results[0].id, last.id)
  // A candidate already in another page is disabled; selected works survive filter changes.
  await btn('＋ 收录作品').click(); await modal('从私人手账收录作品').waitFor()
  await searchCandidate('从私人手账收录作品', 'E2 合成作品 100')
  assert.equal(await modal('从私人手账收录作品').getByRole('checkbox', { name: '选择E2 合成作品 100', exact: true }).isDisabled(), true)
  for (const name of ['E2 合成作品 103','E2 合成作品 104']) { await searchCandidate('从私人手账收录作品', name); await modal('从私人手账收录作品').getByRole('checkbox', { name: `选择${name}`, exact: true }).check() }
  await page.keyboard.press('Escape'); await btn('继续编辑片单').click()
  // Lose one real server acknowledgement and prove exact UUID replay, not duplicate enrollment.
  let dropped = false
  await page.route('**/api/curations/', async route => {
    if (!dropped && route.request().method() === 'POST' && route.request().postDataJSON().action === 'add') { dropped = true; const response = await route.fetch(); assert.equal(response.status(), 200); await route.abort('failed') }
    else await route.continue()
  })
  await modal('从私人手账收录作品').getByRole('button', { name: '收录所选 2 部作品', exact: true }).click()
  await modal('从私人手账收录作品').getByRole('button', { name: '重试原片单操作并核对结果', exact: true }).click()
  await modal('从私人手账收录作品').waitFor({ state: 'detached' }); await ready(); await page.unroute('**/api/curations/')
  const adds = writes.filter(body => body.action === 'add'); assert.deepEqual(adds.at(-1), adds.at(-2)); assert.equal((await detail()).count, initialCount+2)
  // A genuine external reason edit rejects the old draft without replacing its text.
  const first = (await detail()).results[0]
  await ws().getByRole('button', { name: '编辑入选理由', exact: true }).first().click(); await modal('编辑入选理由').getByLabel('入选理由', { exact: true }).fill('冲突时保留这份草稿')
  const v = (await detail()).version
  await api('curations/', { action: 'update-entry', list_id: f.list_id, entry_id: first.id, version: v, operation_id: crypto.randomUUID(), reason: '外部已保存理由' })
  await modal('编辑入选理由').getByRole('button', { name: '保存片单修改', exact: true }).click(); await modal('编辑入选理由').getByRole('alert').waitFor()
  assert.equal(await modal('编辑入选理由').getByLabel('入选理由', { exact: true }).inputValue(), '冲突时保留这份草稿')
  await modal('编辑入选理由').getByRole('button', { name: '关闭片单面板', exact: true }).click(); await btn('放弃草稿并关闭面板').click()
  await btn('刷新当前片单').click(); await ready(); assert.equal((await detail()).results[0].reason, '外部已保存理由')
  // Explicit relinking retains the original entry id/position; removing it leaves the work intact.
  const placeholder = ws().locator(`[data-entry-id="${f.placeholder_id}"]`)
  await placeholder.getByRole('button', { name: '重新关联作品', exact: true }).click(); await modal('重新关联片单作品').waitFor()
  await searchCandidate('重新关联片单作品', 'E2 合成作品 105'); await modal('重新关联片单作品').getByRole('radio', { name: '选择E2 合成作品 105', exact: true }).check()
  await modal('重新关联片单作品').getByRole('button', { name: '确认重新关联', exact: true }).click(); await modal('重新关联片单作品').waitFor({ state: 'detached' }); await ready()
  assert.equal((await detail()).results.find(row => row.id === f.placeholder_id).unavailable, false)
  await placeholder.getByRole('button', { name: '移出片单', exact: true }).click(); await modal('移出片单作品').getByRole('button', { name: '确认移出作品', exact: true }).click(); await modal('移出片单作品').waitFor({ state: 'detached' }); await ready()
  const recordResponse = await context.request.get(base + '/api/records/' + f.free_record_id + '/', { headers: { Authorization: `Bearer ${f.token}` } }); assert.equal(recordResponse.status(), 200)
  // Actual mobile/desktop layouts and native modal focus/close behavior.
  for (const width of [320,390,844,1440]) {
    await page.setViewportSize({ width, height: 1000 }); await ws().locator(':scope > header').scrollIntoViewIfNeeded()
    assert.equal(await ws().evaluate(el => el.scrollWidth <= el.clientWidth+1), true)
    assert.equal(await page.getByRole('navigation', { name: '私人手账工作区' }).evaluate(el => el.scrollWidth <= el.clientWidth+1), true)
    await page.screenshot({ path: `${out}/detail-${width}.png` })
    await btn('编辑片单').click(); await modal('编辑片单资料').waitFor()
    assert.equal(await modal('编辑片单资料').evaluate(el => el.scrollWidth <= el.clientWidth+1), true)
    await modal('编辑片单资料').getByRole('button', { name: '关闭片单面板', exact: true }).click()
    await btn('＋ 收录作品').click(); await modal('从私人手账收录作品').waitFor()
    assert.equal(await modal('从私人手账收录作品').evaluate(el => el.scrollWidth <= el.clientWidth+1), true)
    await page.screenshot({ path: `${out}/picker-${width}.png` }); await page.keyboard.press('Escape'); await modal('从私人手账收录作品').waitFor({ state: 'detached' })
  }
  await page.setViewportSize({ width:1440,height:1000 })
  let heldPrepare = false
  const detailRoute = '**/api/curations/'+f.list_id+'/**'
  await page.route(detailRoute, async route => {
    if (!heldPrepare && new URL(route.request().url()).searchParams.get('page_size') === '100') { heldPrepare = true; await new Promise(resolve=>setTimeout(resolve,350)); await route.continue().catch(()=>{}) }
    else await route.continue()
  })
  await btn('编辑片单').click(); await btn('取消读取').click(); await btn('＋ 收录作品').click(); await modal('从私人手账收录作品').waitFor()
  await page.waitForTimeout(500); assert.equal(await modal('编辑片单资料').count(),0)
  await page.keyboard.press('Escape'); await modal('从私人手账收录作品').waitFor({state:'detached'}); await page.unroute(detailRoute)
  await btn('编辑片单').click(); await modal('编辑片单资料').waitFor(); await page.mouse.click(5,500)
  await modal('编辑片单资料').waitFor({state:'detached'})
  assert.equal(await btn('编辑片单').evaluate(el => el === document.activeElement),true)
  // Create/cancel/delete operate only on the explicit collection, retaining private works.
  await btn('← 返回片单首页').click(); await ready()
  await btn('＋ 新建片单').click(); await modal('新建主题片单').getByLabel('片单名称',{exact:true}).fill('不应被保存的草稿')
  await page.keyboard.press('Escape'); await btn('放弃草稿并关闭面板').click()
  assert.equal((await api('curations/?state=all&search='+encodeURIComponent('不应被保存的草稿'))).count,0)
  const recordsBefore = (await api('export/')).records.map(row=>({anime:row.anime,score:row.personal_score,status:row.watch_status,watched:row.watched_eps,total:row.total_eps,review:row.review,history:row.watch_history,plan:row.watch_plan}))
  await btn('＋ 新建片单').click(); await modal('新建主题片单').getByLabel('片单名称',{exact:true}).fill('E2 界面新建专题'); await save('新建主题片单')
  assert.equal(await ws().getByRole('heading',{name:'E2 界面新建专题',exact:true}).count(),1)
  await btn('删除片单').click(); await modal('删除这张片单').getByRole('button',{name:'确认删除片单',exact:true}).click(); await modal('删除这张片单').waitFor({state:'detached'}); await ready()
  const recordsAfter = (await api('export/')).records.map(row=>({anime:row.anime,score:row.personal_score,status:row.watch_status,watched:row.watched_eps,total:row.total_eps,review:row.review,history:row.watch_history,plan:row.watch_plan}))
  assert.deepEqual(recordsAfter,recordsBefore)
  await ws().getByLabel('片单范围',{exact:true}).selectOption('active'); await ready(); await btn('E2 跨页主题').click(); await ready()
  // Preview mode allows reading only. Public journals have no curation navigation or reads.
  await page.getByRole('button', { name: /预览模式/ }).click(); await ready()
  const beforePreview = writes.length
  assert.equal(await btn('编辑片单').count(),0); assert.equal(await btn('＋ 收录作品').count(),0)
  await nav('手账').click(); await nav('片单').click(); await ready(); assert.equal(writes.length,beforePreview)
  const publicPage = await context.newPage(), publicReads = []
  publicPage.on('request', r => { if (r.url().includes('/api/curations/')) publicReads.push(r.url()) })
  await publicPage.goto(base+'/shared/'+f.username); await publicPage.locator('tbody tr').first().waitFor()
  assert.equal(await publicPage.getByRole('region',{name:'私人片单工作区'}).count(),0); assert.deepEqual(publicReads,[]); await publicPage.close()
  assert.deepEqual(errors,[])
  assert.ok(reads.some(url => url.searchParams.get('page')==='2' && url.searchParams.get('page_size')==='100'))
  console.log(JSON.stringify({result:'passed',motion,checks:['home filters/page/focus return','readonly detail return','draft guard','all-page cover/order','pin/archive','exact lost-ack retry','conflict preserves draft','relink/remove','320/390/844/1440 layouts','cancelled late preparation','backdrop close and focus','UI create/cancel/delete without record changes','preview no writes','public privacy'],writes:writes.length,browserErrors:errors}))
} catch(error) { console.log((await page.locator('body').innerText()).slice(-3500)); await page.screenshot({path:out+'/failure.png'}); throw error }
finally { await context.close(); await browser.close() }
