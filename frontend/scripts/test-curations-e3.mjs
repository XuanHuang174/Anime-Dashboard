// Real isolated Django API and production React components, synthetic data only.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFile,mkdir,writeFile} from 'node:fs/promises'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright')
const f=JSON.parse(await readFile('../.tools/curations-e3-fixture.json','utf8')),base='http://127.0.0.1:5191',out='../.tools/curations-e3-qa'
assert.match(f.username,/^plans-b2-/);await mkdir(out,{recursive:true})
const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,reducedMotion:process.env.CURATION_TEST_MOTION||'reduce'})
await context.addInitScript(({token,user_id})=>{localStorage.setItem('anime_journal_access_token',token);localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`,'edit')},f)
await context.route('https://media.xh-anime.com/**',route=>route.abort())
const page=await context.newPage();page.setDefaultTimeout(25000)
const errors=[],writes=[],downloads=[]
page.on('pageerror',e=>errors.push(e.message));page.on('download',d=>downloads.push(d));page.on('request',r=>{if(r.url().includes('/api/curations/')&&r.method()==='POST')writes.push(r.postDataJSON())})
const nav=name=>page.getByRole('navigation',{name:'私人手账工作区'}).getByRole('button',{name,exact:true}),ws=()=>page.getByRole('region',{name:'私人片单工作区',exact:true}),btn=name=>ws().getByRole('button',{name,exact:true}),dialog=title=>page.getByRole('dialog',{name:title,exact:true}),collect=()=>dialog('收录到私人片单')
const ready=async()=>{await ws().locator('[aria-busy=false]').waitFor({state:'attached'});await ws().getByText('正在读取片单…',{exact:true}).waitFor({state:'detached'})}
const api=async(method,path,data)=>{const r=await context.request[method](base+'/api/'+path,{headers:{Authorization:`Bearer ${f.token}`},...(data?{data}:{})});assert.equal(r.status(),200,await r.text());return r.json()}
const command=async body=>api('post','curations/',{version:(await api('get','curations/')).version,operation_id:crypto.randomUUID(),...body})
const detail=()=>api('get',`curations/${f.list_id}/`)
const closeCollect=async()=>{await collect().getByRole('button',{name:'取消',exact:true}).click();if(await collect().getByRole('button',{name:'放弃并关闭收录',exact:true}).count())await collect().getByRole('button',{name:'放弃并关闭收录',exact:true}).click();await collect().waitFor({state:'detached'})}
const exp=()=>dialog('导出私人片单'),stage=()=>page.locator('.review-export-stage'),eb=name=>exp().getByRole('button',{name,exact:true})
const prepare=async()=>{await eb('读取并预览完整内容').click();await eb('下载片单长图').waitFor();assert.equal(await eb('下载片单长图').isEnabled(),true)}
const openExport=async()=>{await btn('导出片单长图').click();await exp().waitFor()}
const closeExport=async()=>{await eb('关闭面板').click();await exp().waitFor({state:'detached'})}
try{
  await page.goto(base+'/dashboard');await page.getByLabel('选择每页显示数量',{exact:true}).selectOption('12');await page.getByRole('button',{name:'收录本页作品到片单',exact:true}).waitFor()
  const initialWrites=writes.length
  await page.getByRole('button',{name:'收录本页作品到片单',exact:true}).click();await collect().getByRole('checkbox').first().waitFor()
  assert.equal(await collect().getByRole('checkbox').evaluateAll(nodes=>nodes.every(n=>!n.checked)),true)
  await collect().getByRole('checkbox').first().check();await closeCollect();assert.equal(writes.length,initialWrites)
  // Explicit one-work collection creation. Lose the actual server acknowledgement once.
  await page.getByRole('button',{name:/^将.+收录到片单$/}).first().click();await collect().getByLabel('收录方式',{exact:true}).selectOption('new')
  await collect().getByLabel('片单名称',{exact:true}).fill('E3 手账新建片单')
  let dropped=false
  await page.route('**/api/curations/',async route=>{if(!dropped&&route.request().method()==='POST'&&route.request().postDataJSON().action==='create-with-members'){dropped=true;const r=await route.fetch();assert.equal(r.status(),200);await route.abort('failed')}else await route.continue()})
  await collect().getByRole('button',{name:'确认收录 1 部作品',exact:true}).click();await collect().getByRole('button',{name:'重试原收录操作并核对',exact:true}).click();await collect().getByRole('button',{name:'查看片单',exact:true}).waitFor()
  assert.deepEqual(writes.at(-1),writes.at(-2));assert.equal(await nav('手账').getAttribute('aria-pressed'),'true')
  await collect().getByRole('button',{name:'查看片单',exact:true}).click();await ready();await page.unroute('**/api/curations/')
  assert.equal(await ws().locator('.curation-entry').count(),1)
  // Deep link is read-only navigation and consumed once; later workspace switches keep page 2.
  const beforeLink=writes.length;await page.goto(base+`/dashboard?curation=${f.list_id}`);await ready();assert.equal(new URL(page.url()).searchParams.has('curation'),false);assert.equal(writes.length,beforeLink)
  await btn('下一页成员').click();await ready();await nav('回顾').click();await nav('片单').click();await ready();assert.equal(await ws().locator('.curation-number').first().innerText(),'13')
  const first=(await detail()).results[0]
  const beforeRecord=await api('get',`records/${first.record.id}/`)
  // Direct viewing arrangement uses its independent save; collection page and reason stay intact.
  await ws().getByRole('button',{name:'观看安排',exact:true}).first().click();await page.getByRole('dialog',{name:/观看安排/}).waitFor()
  await page.getByRole('dialog',{name:/观看安排/}).getByRole('button',{name:'关闭',exact:true}).first().click();await ready();assert.equal(await ws().locator('.curation-number').first().innerText(),'13')
  assert.deepEqual(await api('get',`records/${first.record.id}/`),beforeRecord)
  await openExport()
  for(const label of ['专题简介','入选理由','观看状态与进度','个人评分'])assert.equal(await exp().getByLabel(label,{exact:true}).isChecked(),false)
  await prepare();assert.equal(await stage().locator('h2').count(),106)
  let text=await stage().innerText();assert.ok(text.includes('106. E3 待保留的失效条目'));assert.ok(!text.includes('E3 私人专题简介'));assert.ok(!text.includes('E3 私人入选理由'));assert.ok(!text.includes('个人评分：'))
  const pages=await stage().locator('.review-export-sheet').count();assert.ok(pages>1)
  const zipTask=page.waitForEvent('download',{timeout:120000});await eb('下载片单长图').click();const zip=await zipTask;assert.equal(zip.suggestedFilename(),'E3 完整主题片单_私人片单.zip');await zip.saveAs(out+'/collection-full.zip')
  await writeFile(out+'/metadata.json',JSON.stringify({pages,entries:106}))
  for(const label of ['专题简介','入选理由','观看状态与进度','个人评分'])await exp().getByLabel(label,{exact:true}).check()
  await prepare();text=await stage().innerText();assert.ok(text.includes('E3 私人专题简介'));assert.ok(text.includes('E3 私人入选理由🌸'));assert.ok(text.includes('个人评分：'));assert.ok(text.includes('（续）'))
  // Summary-only remains a single PNG; fit-width preview on every target viewport.
  await exp().getByLabel('完整作品清单',{exact:true}).uncheck();await prepare();assert.equal(await stage().locator('.review-export-sheet').count(),1)
  for(const width of [320,390,1440]){await page.setViewportSize({width,height:900});await exp().locator('.review-export-preview').scrollIntoViewIfNeeded();assert.equal(await exp().evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);assert.equal(await exp().locator('.review-export-preview').evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);await page.screenshot({path:`${out}/preview-${width}.png`})}
  const pngTask=page.waitForEvent('download');await eb('下载片单长图').click();const png=await pngTask;await png.saveAs(out+'/collection-overview.png');assert.match(png.suggestedFilename(),/_01.png$/)
  // Private work metadata changes without a library version change invalidate the source.
  const v=(await detail()).version
  await api('patch',`records/${first.record.id}/details/`,{anime:{},record:{personal_score:'7.3'}});assert.equal((await detail()).version,v)
  const downloaded=downloads.length;await eb('下载片单长图').click();await exp().getByRole('alert').waitFor();assert.equal(downloads.length,downloaded)
  await closeExport();await page.setViewportSize({width:1440,height:1000});await btn('刷新当前片单').click();await ready()
  await openExport();let held=false
  await page.route(`**/api/curations/${f.list_id}/export/**`,async route=>{if(!held){held=true;await new Promise(resolve=>setTimeout(resolve,350))}await route.continue().catch(()=>{})})
  await eb('读取并预览完整内容').click();await eb('取消并关闭').click();await exp().waitFor({state:'detached'});await page.waitForTimeout(500);assert.equal(await stage().count(),0);assert.equal(downloads.length,downloaded);await page.unroute(`**/api/curations/${f.list_id}/export/**`)
  // Annual candidates are complete across 100-row API boundaries; edits after preview reject creation.
  await nav('回顾').click();const review=()=>page.getByRole('region',{name:'私人观看回顾工作区'})
  await review().getByRole('button',{name:'按年度',exact:true}).click();await review().getByLabel('回顾年份',{exact:true}).fill(f.year);await review().getByRole('button',{name:'查看回顾',exact:true}).click();await review().locator('.review-summary[aria-busy=false]').waitFor()
  await review().getByLabel('回顾年份',{exact:true}).fill('2025')
  await review().getByRole('button',{name:'从回顾创建片单',exact:true}).click();const rd=()=>dialog('从回顾创建主题片单')
  await rd().getByRole('checkbox').nth(100).waitFor();assert.equal(await rd().getByLabel('片单名称',{exact:true}).inputValue(),'2026年观看回顾片单');assert.equal(await rd().getByRole('checkbox').evaluateAll(nodes=>nodes.every(n=>!n.checked)),true)
  await rd().getByRole('button',{name:'勾选本次全部候选',exact:true}).click();const candidates=await rd().getByRole('checkbox').count();assert.ok(candidates>100)
  await rd().getByRole('button',{name:`确认收录 ${candidates} 部作品`,exact:true}).click();await rd().getByRole('button',{name:'查看片单',exact:true}).click();await ready()
  assert.equal(writes.at(-1).action,'create-from-review');assert.equal(writes.at(-1).review.year,'2026');assert.match((await api('get',`curations/${(await api('get','curations/?search=2026')).results[0].id}/`)).collection.description,/2026-01-01 至 2026-12-31/)
  // Shared production recommendation action joins want explicitly, then collects independently.
  await page.goto(base+'/tests/fixtures/curationsE3Recommendation.html');await page.getByRole('button',{name:'将B3 推荐安排合成作品收录到私人片单',exact:true}).click();await dialog('从推荐收录到片单').getByRole('button',{name:'加入想看并选片单',exact:true}).click();await collect().getByLabel('目标片单',{exact:true}).waitFor()
  await collect().getByLabel('目标片单',{exact:true}).selectOption(f.list_id);await closeCollect()
  const records=await api('get','records/?search=B3');assert.ok(records.results.some(r=>r.watch_status==='plan'))
  const recRecord=records.results.find(r=>r.watch_status==='plan');const plan=await api('get',`records/${recRecord.id}/watch-plan/`);assert.equal(plan.plan,null)
  await page.getByRole('button',{name:'将B3 推荐安排合成作品收录到私人片单',exact:true}).click();await dialog('从推荐收录到片单').getByRole('button',{name:'加入想看并选片单',exact:true}).click();await collect().getByLabel('目标片单',{exact:true}).selectOption(f.list_id);await collect().getByRole('button',{name:'确认收录 1 部作品',exact:true}).click();await collect().getByRole('button',{name:'完成',exact:true}).click()
  assert.equal((await detail()).count,107);assert.deepEqual(errors,[])
  console.log(JSON.stringify({ok:true,pages,completeEntries:106,annualCandidates:candidates,downloads:downloads.length,pageErrors:errors.length}))
}catch(error){await page.screenshot({path:out+'/failure.png',fullPage:true});throw error}finally{await browser.close()}
