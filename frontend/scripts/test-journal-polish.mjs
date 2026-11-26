// Real browser + real isolated Django: direct editor entries, reveal, canvas, identity and backdrop.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFile, mkdir} from 'node:fs/promises'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base='http://127.0.0.1:5191'
const fixture=JSON.parse(await readFile('../.tools/watch-plans-b2-fixture.json','utf8'))
assert.match(fixture.username,/^plans-b2-/)
const browser=await chromium.launch({channel:'msedge',headless:true})
await mkdir('../.tools/journal-polish-qa',{recursive:true})
const errors=[]
let activePage
try {
 for (const motion of ['reduce','no-preference']) {
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:motion})
  await context.addInitScript(({token,user_id})=>{localStorage.setItem('anime_journal_access_token',token);localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`,'preview')},fixture)
  const page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message))
  activePage=page
  const nav=name=>page.getByRole('navigation',{name:'私人手账工作区'}).getByRole('button',{name,exact:true})
  const mode=async name=>{await page.getByRole('button',{name:new RegExp(name)}).first().click()}
  const closeEdit=async()=>{await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();await page.getByRole('dialog').waitFor({state:'detached'})}
  const api=async path=>{const r=await context.request.get(`${base}/api/${path}`,{headers:{Authorization:`Bearer ${fixture.token}`}});assert.equal(r.status(),200,await r.text());return r.json()}
  await page.goto(`${base}/dashboard`);await nav('手账').waitFor()
  for (const view of ['列表','海报墙']) {
   await page.locator(view==='列表'?'#btnViewList':'#btnViewGrid').click()
   for(const entry of ['history','plan','identity']) {
    if(entry==='identity') {
     await mode('更多功能');await page.getByRole('button',{name:/核对标准作品/}).click()
     await page.getByRole('dialog',{name:'核对标准作品',exact:true}).getByRole('button',{name:'关闭面板',exact:true}).click()
    } else {
     const title=entry==='plan'?'B2 正常安排':'B2 首次观看'
     if(view==='列表') await page.locator('tbody tr').filter({hasText:title}).getByRole('button',{name:/详情|查看/}).first().click()
     else await page.locator('.memphis-anime-grid').getByText(title,{exact:false}).first().click()
     const region=page.getByRole('region',{name:entry==='history'?'私人观看历程':'观看安排',exact:true})
     if(entry==='history') await region.getByRole('button').first().click()
     await region.getByRole('button',{name:entry==='history'?'前往编辑模式管理':'前往编辑安排',exact:true}).click()
     await page.getByRole('dialog',{name:entry==='history'?'观看历程管理':'编辑观看安排',exact:true}).waitFor()
     assert.equal(await page.getByText('观看安排单独保存',{exact:true}).count(),0)
     await closeEdit()
    }
    const add=view==='列表'?page.locator('.add-anime-placeholder-list'):page.getByRole('button',{name:'添加一部新番剧',exact:true})
    await add.scrollIntoViewIfNeeded()
    await page.waitForFunction(()=>{const el=document.querySelector('.add-anime-placeholder');return el && getComputedStyle(el.closest('.animate-item')).opacity==='1'})
    assert.ok(await add.innerText());await add.click()
    await page.getByRole('dialog').waitFor();await page.getByRole('dialog').getByRole('button',{name:'取消',exact:true}).click();await page.getByRole('dialog').waitFor({state:'detached'})
    await mode('预览模式')
   }
  }
  // Backdrop pointer starts/ends outside only. Interior clicks and inside->outside drags stay open.
  await mode('编辑模式');await nav('观看计划').click()
  const workspace=page.getByRole('region',{name:'观看计划工作区',exact:true})
  const open=workspace.getByRole('button',{name:'＋ 添加计划',exact:true})
  await open.click();const picker=page.getByRole('dialog',{name:'从私人手账添加计划',exact:true});await picker.getByLabel('搜索可安排作品').waitFor()
  await picker.locator('header h2').click();assert.equal(await picker.count(),1)
  const box=await picker.boundingBox();await page.mouse.move(box.x+20,box.y+20);await page.mouse.down();await page.mouse.move(10,300);await page.mouse.up();assert.equal(await picker.count(),1)
  await page.mouse.click(10,300);await picker.waitFor({state:'detached'});assert.equal(await open.evaluate(el=>el===document.activeElement),true)
  await open.click();await page.keyboard.press('Escape');await picker.waitFor({state:'detached'})
  // Zero/one/many filters retain a fully painted canvas; decorations do not extend scroll area.
  for(const search of ['no-such-plan','B2 正常安排','']) {
   await workspace.getByLabel('搜索观看计划',{exact:true}).fill(search);await workspace.getByRole('button',{name:'搜索计划',exact:true}).click()
   await page.waitForTimeout(300);await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
   assert.equal(await page.locator('main.memphis-dashboard').evaluate(el=>el.getBoundingClientRect().bottom>=innerHeight-1),true)
   await page.screenshot({path:`../.tools/journal-polish-qa/canvas-${motion}-${search?'filtered':'all'}.png`})
  }
  if(motion==='reduce') {
   await nav('手账').click();await page.locator('#btnViewList').click()
   await page.locator('tbody tr').filter({hasText:'B2 首次观看'}).getByRole('button',{name:'修改',exact:true}).click()
   await page.getByRole('button',{name:'管理标准作品关联',exact:true}).click()
   const manager=page.getByRole('dialog',{name:'管理标准作品关联',exact:true})
   await manager.getByLabel('查找标准作品（名称、Bangumi 编号或条目 URL）',{exact:true}).fill('https://bgm.tv/subject/940012')
   await manager.getByRole('button',{name:'搜索',exact:true}).click()
   await manager.getByRole('radio').first().check();await manager.getByLabel('我已核对并确认此操作').check()
   const before=await api(`records/${fixture.records[0]}/`)
   const posts=[];let lost=false
   page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/identities/manage/')) posts.push(r.postDataJSON())})
   await page.route('**/api/recommendations/identities/manage/',async route=>{if(route.request().method()==='POST'&&!lost){lost=true;const r=await route.fetch();assert.equal(r.status(),200,await r.text());return route.fulfill({status:500,body:'{}',contentType:'application/json'})}return route.continue()})
   await manager.getByRole('button',{name:'确认更正',exact:true}).click();await manager.getByRole('button',{name:'重试原操作',exact:true}).click()
   await manager.getByText('关联已更新。',{exact:true}).waitFor();assert.deepEqual(posts[0],posts[1])
   let after=await api(`records/${fixture.records[0]}/`)
   assert.equal(after.anime.confirmed_bangumi_id,940012);assert.equal(after.review,before.review);assert.equal(after.watch_revision,before.watch_revision)
   assert.equal((await api(`recommendations/series/record/${fixture.records[0]}/`)).bangumi_id,940012)
   // Credits reads require PostgreSQL snapshots and are covered by the API/PG regression suite.
   for(const action of ['解除私人关联','撤销最近一次更正']) {
    await manager.getByRole('button',{name:action,exact:true}).click();await manager.getByLabel('我已核对并确认此操作').check();await manager.getByRole('button',{name:'确认更正',exact:true}).click()
    await manager.getByLabel('我已核对并确认此操作').waitFor({state:'detached'})
    after=await api(`records/${fixture.records[0]}/`);assert.equal(after.anime.confirmed_bangumi_id,action==='解除私人关联'?null:940012)
   }
   await page.setViewportSize({width:390,height:740});assert.equal(await manager.evaluate(el=>el.scrollWidth<=el.clientWidth),true)
   await page.screenshot({path:'../.tools/journal-polish-qa/identity-mobile.png'})
   await manager.getByRole('button',{name:'关闭面板',exact:true}).click()
   await closeEdit()
  }
  await context.close()
 }
 assert.deepEqual(errors,[])
 console.log(JSON.stringify({result:'passed',checks:['direct history/plan/identity editor entries in list/grid','normal/reduced reveal','add placeholder visible and clickable','backdrop close and focus restoration','inside clicks and drags protected','filtered canvas coverage','independent footer','identity replace unlink undo','exact lost-ack retry','series alignment (credits in PG tests)','mobile manager'],browserErrors:errors}))
} catch(error) {if(activePage&&!activePage.isClosed()){console.log((await activePage.locator('body').innerText()).slice(0,5500));await activePage.screenshot({path:'../.tools/journal-polish-qa/failure.png'})}throw error} finally {await browser.close()}
