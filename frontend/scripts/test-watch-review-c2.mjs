// Real local Django + React: synthetic records, no mocked successful business replies.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFile,mkdir} from 'node:fs/promises'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base='http://127.0.0.1:5191'
const fixture=JSON.parse(await readFile('../.tools/watch-review-c2-fixture.json','utf8'))
assert.match(fixture.username,/^plans-b2-/)
const browser=await chromium.launch({channel:'msedge',headless:true})
const motion=process.env.REVIEW_TEST_MOTION || 'reduce'
assert.ok(['reduce','no-preference'].includes(motion))
const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:motion})
await context.addInitScript(({token,user_id})=>{localStorage.setItem('anime_journal_access_token',token);localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`,'edit')},fixture)
const page=await context.newPage();page.setDefaultTimeout(15000)
const errors=[],requests=[],writes=[]
page.on('pageerror',e=>errors.push(e.message))
page.on('request',r=>{if(r.url().includes('/api/watch-review/'))requests.push({url:r.url(),method:r.method()});if(r.url().includes('/api/')&&['POST','PATCH','DELETE'].includes(r.method()))writes.push(r.url())})
const nav=name=>page.getByRole('navigation',{name:'私人手账工作区'}).getByRole('button',{name,exact:true})
const review=()=>page.getByRole('region',{name:'私人观看回顾工作区',exact:true})
const ready=async()=>{await review().locator('.review-summary[aria-busy=false]').waitFor();await review().locator('.review-error').waitFor({state:'detached'})}
const api=async(method,path,body)=>{const r=await context.request[method](`${base}/api/${path}`,{headers:{Authorization:`Bearer ${fixture.token}`},...(body?{data:body}:{})});assert.equal(r.status(),200,await r.text());return r.json()}
const section=name=>review().getByRole('group',{name:'回顾内容',exact:true}).getByRole('button',{name,exact:true})
const hit=async locator=>{await locator.scrollIntoViewIfNeeded();assert.equal(await locator.evaluate(el=>{const r=el.getBoundingClientRect(),target=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return target===el||el.contains(target)}),true)}
await mkdir('../.tools/watch-review-c2-qa',{recursive:true})
try {
  await page.goto(`${base}/dashboard`);await nav('回顾').waitFor()
  assert.equal(requests.length,0)
  // A journal filter must not narrow review totals.
  await page.getByPlaceholder('输入番剧中文或日文名...').fill('this-does-not-match')
  await nav('回顾').click();await review().getByLabel('回顾月份',{exact:true}).fill(fixture.month)
  await review().getByRole('button',{name:'查看回顾',exact:true}).click();await ready()
  await review().getByRole('heading',{name:'2026年10月观看回顾',exact:true}).waitFor()
  assert.match(await review().locator('.review-stat--completed strong').innerText(),new RegExp(`^${fixture.review_expected.completed_works}`))
  assert.equal(await review().locator('.review-entry').count(),12)
  const snapshotRequest=requests.at(-1).url;assert.equal(new URL(snapshotRequest).searchParams.has('search'),false)
  await review().getByRole('navigation',{name:'底部回顾分页'}).getByRole('button',{name:'下一页回顾',exact:true}).click();await ready()
  assert.equal(await review().locator('.review-entry').count(),fixture.review_expected.related_works-12)
  assert.ok(new URL(requests.at(-1).url).searchParams.has('snapshot'))
  await section('随记与补录').click();await ready()
  assert.equal(await review().locator('.review-entry').count(),fixture.review_expected.notes)
  await review().getByText('展开完整文字',{exact:true}).click();await review().getByText('十月的独立随记',{exact:false}).last().waitFor()
  // Viewing a report and opening a detail do not issue writes; even edit mode opens read-only details.
  const before=await api('get',`records/${fixture.review_record}/`)
  await review().getByRole('button',{name:'查看详情',exact:true}).first().click()
  const detail=page.getByRole('dialog');await detail.waitFor()
  assert.equal(await detail.getByRole('button',{name:'保存全部修改',exact:true}).count(),0)
  assert.equal(await detail.getByRole('button',{name:'管理标准作品关联',exact:true}).count(),0)
  await detail.getByRole('button',{name:'关闭',exact:true}).last().click();await detail.waitFor({state:'detached'})
  assert.deepEqual(await api('get',`records/${fixture.review_record}/`),before)
  assert.deepEqual(writes,[])
  // Partial month attribution stays separate; correction requires an explicit edit action.
  await review().getByRole('button',{name:'自选时间段',exact:true}).click()
  await review().getByLabel('开始日期',{exact:true}).fill('2026-10-10');await review().getByLabel('结束日期',{exact:true}).fill('2026-10-20')
  await review().getByRole('button',{name:'查看回顾',exact:true}).click();await ready()
  await review().locator('.review-date-help > summary').click();await review().getByRole('button',{name:/查看可能相关/}).click();await ready()
  await review().getByText('日期范围与所选区间部分相交，未计入确定统计。',{exact:true}).first().waitFor()
  assert.ok((await review().locator('.review-entry').count())>0)
  await review().getByRole('button',{name:'补充／更正日期',exact:true}).first().click()
  await page.getByRole('dialog',{name:'观看历程管理',exact:true}).waitFor()
  await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();await page.getByRole('dialog').waitFor({state:'detached'})
  assert.equal(await review().getByLabel('开始日期',{exact:true}).inputValue(),'2026-10-10')
  await review().getByRole('button',{name:/查看日期待补充/}).click();await ready()
  await review().getByText('本人全部历程中日期未知的记录，无法归入本期。数量不是当月漏记数量。',{exact:true}).waitFor()
  // Responsive sizes in edit/preview, with draft fields kept through workspace changes.
  for(const [width,height] of [[320,568],[390,740],[844,390],[1440,1000]]) {
    await page.setViewportSize({width,height});await hit(nav('回顾'));await hit(review().getByLabel('开始日期',{exact:true}))
    assert.equal(await review().evaluate(el=>el.scrollWidth<=el.clientWidth+1),true)
    await page.screenshot({path:`../.tools/watch-review-c2-qa/review-edit-${width}.png`})
  }
  await nav('手账').click();assert.equal(await page.getByPlaceholder('输入番剧中文或日文名...').inputValue(),'this-does-not-match')
  await nav('回顾').click();await ready();assert.equal(await review().getByLabel('开始日期',{exact:true}).inputValue(),'2026-10-10')
  // Apply October again. A real metadata edit invalidates the token before the next page.
  await review().getByRole('button',{name:'按月份',exact:true}).click();await review().getByLabel('回顾月份',{exact:true}).fill(fixture.month)
  await review().getByRole('button',{name:'查看回顾',exact:true}).click();await section('全部作品').click();await ready()
  await api('patch',`records/${fixture.review_record}/details/`,{anime:{chinese_title:'C2 更正后的当前名称'},record:{}})
  await review().getByRole('navigation',{name:'底部回顾分页'}).getByRole('button',{name:'下一页回顾',exact:true}).click();await ready()
  await review().getByText('资料已更新，已重新读取本区第一页。',{exact:true}).waitFor()
  assert.equal(await review().locator('.review-entry').count(),12)
  await review().getByRole('button',{name:'C2 更正后的当前名称',exact:true}).waitFor()
  // Focus refresh uses applied period, keeping an unsubmitted draft intact.
  await review().getByLabel('回顾月份',{exact:true}).fill('2026-11')
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await page.waitForTimeout(450);await ready()
  assert.equal(await review().getByLabel('回顾月份',{exact:true}).inputValue(),'2026-11')
  await review().getByText('时间选择尚未应用，下方仍为已应用的回顾。点击“查看回顾”更新。',{exact:true}).waitFor()
  await review().getByRole('heading',{name:'2026年10月观看回顾',exact:true}).waitFor()
  // A temporary failure never labels partial data as a complete report.
  let failed=false
  await page.route('**/api/watch-review/entries/**',async route=>{if(!failed){failed=true;return route.fulfill({status:503,body:'{}',contentType:'application/json'})}return route.continue()})
  await review().getByRole('button',{name:'刷新回顾',exact:true}).click();await review().getByRole('button',{name:'重试回顾',exact:true}).waitFor()
  assert.equal(await review().locator('.review-entry').count(),0)
  await review().getByRole('button',{name:'重试回顾',exact:true}).click();await ready()
  await page.unroute('**/api/watch-review/entries/**')
  await page.getByRole('button',{name:/预览模式/}).first().click();await ready()
  for(const [width,height] of [[390,740],[1440,1000]]) {
    await page.setViewportSize({width,height});await hit(nav('回顾'));assert.equal(await review().evaluate(el=>el.scrollWidth<=el.clientWidth+1),true)
    await page.screenshot({path:`../.tools/watch-review-c2-qa/review-preview-${width}.png`})
  }
  // Correction remains explicit when the whole journal is in preview mode.
  await review().getByRole('button',{name:/查看日期待补充/}).click();await ready()
  await review().getByRole('button',{name:'补充／更正日期',exact:true}).first().click()
  await page.getByRole('dialog',{name:'观看历程管理',exact:true}).waitFor()
  await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();await page.getByRole('dialog').waitFor({state:'detached'})
  await review().getByRole('button',{name:'按月份',exact:true}).click();await review().getByLabel('回顾月份',{exact:true}).fill(fixture.month)
  await review().getByRole('button',{name:'上个月',exact:true}).click();await ready()
  await review().getByRole('heading',{name:'2026年9月观看回顾',exact:true}).waitFor()
  await review().getByRole('button',{name:'下个月',exact:true}).click();await ready()
  await review().getByRole('heading',{name:'2026年10月观看回顾',exact:true}).waitFor()
  await review().getByLabel('回顾月份',{exact:true}).fill('2026-12');await review().getByRole('button',{name:'查看回顾',exact:true}).click();await ready()
  await section('全部作品').click();await ready();await review().getByText('这段时间暂无可归属的记录',{exact:true}).waitFor()
  const publicPage=await context.newPage(),publicReads=[]
  publicPage.on('request',r=>{if(r.url().includes('/api/watch-review/'))publicReads.push(r.url())})
  await publicPage.goto(`${base}/shared/${fixture.username}`);await publicPage.locator('tbody tr').first().waitFor()
  assert.equal(await publicPage.getByRole('navigation',{name:'私人手账工作区'}).count(),0)
  assert.equal(await publicPage.getByRole('region',{name:'私人观看回顾工作区'}).count(),0)
  assert.deepEqual(publicReads,[]);await publicPage.close()
  assert.equal(requests.every(r=>r.method==='GET'),true)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({result:'passed',motion,checks:['private integrated nav','outer filter independence','full summary and stable paging','thought expansion','readonly details in edit mode','partial and unknown dates','explicit edit from edit and preview','workspace draft preservation','409 fresh coherent page','focus refresh keeps draft','network retry without partial data','320/390/844/1440 layouts','month navigation and honest empty state','public view privacy'],browserErrors:errors}))
} catch(error) {console.log((await page.locator('body').innerText()).slice(-5000));await page.screenshot({path:'../.tools/watch-review-c2-qa/failure.png'});throw error}
finally {await context.close();await browser.close()}
