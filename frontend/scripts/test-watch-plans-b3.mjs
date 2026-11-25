// Actual B3 components, private APIs and isolated local Django. Seed fresh before each run.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFile,mkdir} from 'node:fs/promises'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base=process.env.JOURNAL_TEST_URL || 'http://127.0.0.1:5191'
assert.match(base,/^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const fixture=JSON.parse(await readFile('../.tools/watch-plans-b2-fixture.json','utf8'))
assert.match(fixture.username,/^plans-b2-/)
const [,b,c,d,e]=fixture.records
const browser=await chromium.launch({channel:'msedge',headless:true})
const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'})
await context.addInitScript(({token,user_id})=>{localStorage.setItem('anime_journal_access_token',token);localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`,'edit')},fixture)
const page=await context.newPage();page.setDefaultTimeout(15000)
const errors=[],posts=[];page.on('pageerror',error=>errors.push(error.message))
page.on('request',request=>{if(request.method()==='POST' && /\/watch-plans\/batch\//.test(request.url()))posts.push(request.postDataJSON())})
const api=async path=>{const result=await context.request.get(`${base}/api/${path}`,{headers:{Authorization:`Bearer ${fixture.token}`}});assert.equal(result.status(),200,await result.text());return result.json()}
const nav=name=>page.getByRole('navigation',{name:'私人手账工作区'}).getByRole('button',{name,exact:true})
const workspace=()=>page.getByRole('region',{name:'观看计划工作区',exact:true})
const panel=()=>page.getByRole('region',{name:'观看安排',exact:true})
const close=async()=>{await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();await page.getByRole('dialog').waitFor({state:'detached'})}
const hit=async node=>{await node.scrollIntoViewIfNeeded();assert.equal(await node.evaluate(el=>{const r=el.getBoundingClientRect(),target=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return target===el||el.contains(target)}),true)}
await mkdir('../.tools/watch-plans-b3-qa',{recursive:true})
try {
  await page.goto(`${base}/dashboard`)
  await nav('手账').waitFor()
  assert.equal(await page.locator('header.dashboard-memphis-console').getByRole('navigation',{name:'私人手账工作区'}).count(),1)
  for(const [width,height] of [[320,568],[390,740],[844,390],[1440,1000]]){
    await page.setViewportSize({width,height});await nav('手账').scrollIntoViewIfNeeded();await page.mouse.move(0,0)
    assert.equal(await nav('手账').evaluate(el=>{const r=el.parentElement.getBoundingClientRect();return r.x>=0 && r.right<=innerWidth}),true)
    assert.equal(await nav('手账').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 230, 109)')
    await page.screenshot({path:`../.tools/watch-plans-b3-qa/header-edit-${width}.png`})
  }
  await page.locator('tbody tr').filter({hasText:'B2 正常安排'}).getByRole('button',{name:'修改',exact:true}).click()
  assert.equal(await panel().count(),0)
  const history=page.getByRole('button',{name:'观看历程',exact:true}),arrange=page.getByRole('button',{name:'观看安排',exact:true})
  assert.ok((await arrange.boundingBox()).x>(await history.boundingBox()).x)
  await arrange.click();await panel().getByRole('button',{name:'调整安排',exact:true}).waitFor()
  await panel().getByRole('button',{name:'调整安排',exact:true}).click();await panel().getByLabel('计划备注',{exact:true}).fill('独立安排草稿')
  await page.getByRole('button',{name:'返回作品编辑',exact:true}).click();await panel().getByRole('button',{name:'继续编辑安排',exact:true}).click()
  assert.equal(await panel().getByLabel('计划备注',{exact:true}).inputValue(),'独立安排草稿')
  await panel().getByRole('button',{name:'保存观看安排',exact:true}).click();await panel().locator('.plan-form').waitFor({state:'detached'})
  await page.getByRole('button',{name:'返回作品编辑',exact:true}).click();assert.equal(await panel().count(),0);await close()
  await nav('观看计划').click();await workspace().getByRole('heading',{name:'观看计划',exact:true}).waitFor()
  for(const [width,height] of [[320,568],[390,740],[844,390],[1440,1000]]){
    await page.setViewportSize({width,height});await workspace().getByRole('button',{name:'＋ 添加计划',exact:true}).click()
    const picker=page.getByRole('dialog',{name:'从私人手账添加计划',exact:true});await picker.getByRole('button',{name:'调整安排',exact:true}).first().waitFor()
    await hit(picker.getByLabel('搜索可安排作品',{exact:true}));await hit(picker.getByRole('button',{name:'关闭作品选择',exact:true}))
    assert.equal(await picker.evaluate(el=>el.scrollWidth<=el.clientWidth),true)
    await page.screenshot({path:`../.tools/watch-plans-b3-qa/picker-${width}.png`})
    await picker.getByRole('button',{name:'关闭作品选择',exact:true}).click()
    await hit(workspace().getByRole('button',{name:'搜索计划',exact:true}))
    await page.screenshot({path:`../.tools/watch-plans-b3-qa/queue-${width}.png`})
  }
  const beforeB=await api(`records/${b}/`),beforeD=await api(`records/${d}/`)
  await workspace().getByRole('button',{name:'批量调整',exact:true}).click()
  const batch=page.getByRole('form',{name:'批量调整观看计划',exact:true})
  await batch.getByLabel('B2 恢复搁置',{exact:true}).check();await batch.getByLabel('B2 进行中',{exact:true}).check()
  await batch.getByLabel('批量优先级',{exact:true}).selectOption('1');await batch.getByLabel('统一计划时间',{exact:true}).check()
  await batch.getByLabel('批量计划开始时间精度',{exact:true}).selectOption('month');await batch.getByLabel('批量计划开始时间',{exact:true}).fill('2031-02')
  await nav('手账').click();await workspace().getByRole('button',{name:'继续调整计划',exact:true}).click()
  let lost=false
  await page.route('**/api/watch-plans/batch/',async route=>{if(!lost){lost=true;const result=await route.fetch();assert.equal(result.status(),200);return route.fulfill({status:500,contentType:'application/json',body:'{}'})}return route.continue()})
  await batch.getByRole('button',{name:'核对批量调整',exact:true}).click();await batch.getByRole('button',{name:'确认批量保存',exact:true}).click()
  await workspace().getByRole('button',{name:'重试原操作并核对结果',exact:true}).waitFor()
  await workspace().getByRole('button',{name:'刷新计划',exact:true}).click();await workspace().getByRole('button',{name:'重试原操作并核对结果',exact:true}).click();await batch.waitFor({state:'detached'})
  assert.deepEqual(posts[0],posts[1]);assert.deepEqual(await api(`records/${b}/`),{...beforeB,watch_plan_summary:{state:'pending',priority:1}})
  assert.deepEqual(await api(`records/${d}/`),{...beforeD,watch_plan_summary:{state:'ongoing',priority:1}})
  assert.equal((await api(`records/${b}/watch-plan/`)).plan.planned_for,'2031-02')
  await nav('手账').click();await page.getByRole('button',{name:'编辑自定义快速筛选',exact:true}).click()
  const quick=page.getByRole('dialog',{name:'自定义快速筛选',exact:true})
  await quick.getByLabel('筛选名称',{exact:true}).fill('B3 优先待看');await quick.getByLabel('组合计划阶段',{exact:true}).selectOption('pending');await quick.getByLabel('组合计划优先级',{exact:true}).selectOption('1')
  await quick.getByText('匹配 2 部',{exact:true}).waitFor();await quick.getByRole('button',{name:'保存并应用',exact:true}).click();await quick.waitFor({state:'detached'})
  await page.waitForFunction(()=>document.querySelectorAll('tbody tr').length===2)
  const options=await api(`public-journal/users/${fixture.username}/filter-options/`);assert.ok(!options.quick_filters.some(row=>row.name==='B3 优先待看'))
  await page.getByRole('button',{name:'预览模式',exact:false}).click();await nav('观看计划').click()
  assert.equal(await workspace().getByRole('button',{name:'批量调整',exact:true}).count(),0)
  assert.equal(await workspace().getByRole('button',{name:'＋ 添加计划',exact:true}).count(),0)
  await page.mouse.move(0,0)
  for(const [width,height] of [[320,568],[390,740],[844,390],[1440,1000]]){
    await page.setViewportSize({width,height});await nav('观看计划').scrollIntoViewIfNeeded()
    assert.equal(await nav('观看计划').evaluate(el=>{const r=el.parentElement.getBoundingClientRect();return r.x>=0 && r.right<=innerWidth}),true)
    await page.screenshot({path:`../.tools/watch-plans-b3-qa/preview-${width}.png`})
  }
  await page.screenshot({path:'../.tools/watch-plans-b3-qa/preview-desktop.png'})
  // Same real recommendation action component and scope-aware client; no business mock.
  await page.goto(`${base}/tests/fixtures/watchPlansB3Recommendation.html`)
  await page.getByRole('button',{name:'安排B3 推荐安排合成作品观看计划',exact:true}).click()
  await page.getByRole('button',{name:'加入想看并安排',exact:true}).click()
  await page.waitForURL(/\/dashboard\?arrange=\d+/)
  const recordId=Number(new URL(page.url()).searchParams.get('arrange'))
  assert.equal((await api(`records/${recordId}/watch-plan/`)).plan,null)
  await page.reload();await panel().getByRole('button',{name:'加入观看计划',exact:true}).click()
  await panel().getByRole('button',{name:'保存观看安排',exact:true}).click();await panel().locator('.plan-form').waitFor({state:'detached'})
  assert.equal((await api(`records/${recordId}/watch-plan/`)).plan.state,'pending');await close()
  // Preview signed restore counts without applying a destructive replacement.
  const backup=await api('export/');delete backup.records.find(row=>row.anime.chinese_title==='B2 恢复搁置').watch_plan
  backup.records.find(row=>row.anime.chinese_title==='B2 准备重看').watch_plan=null
  await page.getByRole('button',{name:/数据管理/}).click();await page.getByRole('button',{name:'📥 导入模板 (JSON)',exact:true}).click()
  await page.locator('input[type=file][accept=".json,application/json"]').setInputFiles({name:'synthetic-b3.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(backup))})
  const restore=page.getByLabel('观看计划恢复影响',{exact:true});await restore.waitFor();assert.match(await restore.textContent(),/旧格式匹配保留 1 部/);assert.match(await restore.textContent(),/明确清除 1 部/)
  await page.screenshot({path:'../.tools/watch-plans-b3-qa/restore-counts.png'})
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({result:'passed',backend:'real local Django',checks:['header integration','footer plan entry and guarded return','responsive drawer','explicit batch selection','lost batch ack exact retry','metadata-only writes','plan quick filters and full preview','public privacy','readonly preview','recommendation want then explicit enrollment','restore plan counts'],writes:posts.length}))
}catch(error){await page.screenshot({path:'../.tools/watch-plans-b3-qa/failure.png'}).catch(()=>{});console.error(JSON.stringify({errors,posts:posts.length}));throw error}
finally{await browser.close()}
