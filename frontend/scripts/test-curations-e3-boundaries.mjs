// Additional real UI boundaries, intentionally ordinary-motion mode.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFile,mkdir} from 'node:fs/promises'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright')
const f=JSON.parse(await readFile('../.tools/curations-e3-fixture.json','utf8')),base='http://127.0.0.1:5191',out='../.tools/curations-e3-qa'
assert.match(f.username,/^plans-b2-/);await mkdir(out,{recursive:true})
const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'no-preference'})
await context.addInitScript(({token,user_id})=>{localStorage.setItem('anime_journal_access_token',token);localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`,'edit');localStorage.setItem(`anime-dashboard:journal-list-preferences:v1:${user_id}`,JSON.stringify({view:'grid',pageSize:'12'}))},f)
await context.route('https://media.xh-anime.com/**',r=>r.abort())
const page=await context.newPage();page.setDefaultTimeout(25000);const errors=[];page.on('pageerror',e=>errors.push(e.message))
const dialog=title=>page.getByRole('dialog',{name:title,exact:true}),nav=name=>page.getByRole('navigation',{name:'私人手账工作区'}).getByRole('button',{name,exact:true}),ws=()=>page.getByRole('region',{name:'私人片单工作区',exact:true}),review=()=>page.getByRole('region',{name:'私人观看回顾工作区'})
const api=async(method,path,data)=>{const r=await context.request[method](base+'/api/'+path,{headers:{Authorization:`Bearer ${f.token}`},...(data?{data}:{})});assert.equal(r.status(),200,await r.text());return r.json()}
const rr=async()=>{await review().locator('.review-summary[aria-busy=false]').waitFor();await review().locator('.review-error').waitFor({state:'detached'})}
const rd=()=>dialog('从回顾创建主题片单')
try{
  await page.goto(base+'/dashboard');const tile=()=>page.locator('#viewGridContainer .animate-item').first()
  await tile().getByRole('button',{name:/收录到片单$/}).waitFor()
  for(const width of [320,390,1440]){await page.setViewportSize({width,height:1000});const boxes=await tile().evaluate(el=>{const a=el.querySelector('.curation-grid-collect').getBoundingClientRect(),b=el.querySelector('.memphis-grid-score').getBoundingClientRect();return{overlap:a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top,overflow:el.scrollWidth>el.clientWidth+1}});assert.deepEqual(boxes,{overlap:false,overflow:false});await tile().getByRole('button',{name:/收录到片单$/}).click();await dialog('收录到私人片单').getByLabel('收录方式',{exact:true}).waitFor();assert.equal(await dialog('收录到私人片单').evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);await page.screenshot({path:`${out}/collect-${width}.png`});await page.keyboard.press('Escape');await dialog('收录到私人片单').waitFor({state:'detached'})}
  await tile().locator('h3').click();await dialog('编辑私人作品').waitFor();const title=dialog('编辑私人作品').getByLabel('番剧名称（中文）',{exact:true}),original=await title.inputValue();await title.fill(original+' 草稿');assert.equal(await dialog('编辑私人作品').getByRole('button',{name:'收录到片单',exact:true}).isDisabled(),true);await title.fill(original)
  await dialog('编辑私人作品').getByRole('button',{name:'收录到片单',exact:true}).click();await dialog('收录到私人片单').getByLabel('收录方式',{exact:true}).waitFor();await page.keyboard.press('Escape');await dialog('收录到私人片单').waitFor({state:'detached'});await dialog('编辑私人作品').getByRole('button',{name:'关闭',exact:true}).first().click()
  await page.goto(base+`/dashboard?curation=${f.list_id}`);await ws().getByRole('button',{name:'下一页',exact:true}).click();await ws().getByRole('button',{name:'观看安排',exact:true}).first().click();const pd=()=>dialog('编辑观看安排')
  await pd().getByRole('button',{name:/^(加入观看计划|调整安排)$/}).click();await pd().getByLabel('观看优先级',{exact:true}).selectOption('2');await pd().getByLabel('计划开始时间精度',{exact:true}).selectOption('month');await pd().getByLabel('计划开始时间',{exact:true}).fill('2027-02');await pd().getByLabel('计划备注',{exact:true}).fill('E3 独立安排合成备注')
  const saved=page.waitForResponse(r=>r.url().includes('/watch-plan/')&&r.request().method()==='POST');await pd().getByRole('button',{name:'保存观看安排',exact:true}).click();assert.equal((await saved).status(),200);await pd().getByRole('button',{name:'关闭',exact:true}).first().click();assert.equal(await ws().locator('.curation-number').first().innerText(),'13')
  await nav('回顾').click();await review().getByRole('button',{name:'按月份',exact:true}).click();await review().getByLabel('回顾月份',{exact:true}).fill('2026-01');await review().getByRole('button',{name:'查看回顾',exact:true}).click();await rr()
  await review().getByRole('button',{name:'从回顾创建片单',exact:true}).click();await rd().getByRole('checkbox').first().check();await rd().getByRole('button',{name:'确认收录 1 部作品',exact:true}).click();await rd().getByRole('button',{name:'完成',exact:true}).click()
  const monthly=(await api('get','curations/?search=2026年1月')).results;assert.ok(monthly.some(row=>row.description.includes('2026-01-01 至 2026-01-31')))
  await review().getByRole('button',{name:'自选时间段',exact:true}).click();await review().getByLabel('开始日期',{exact:true}).fill('2026-01-01');await review().getByLabel('结束日期',{exact:true}).fill('2026-02-28');await review().getByRole('button',{name:'查看回顾',exact:true}).click();await rr()
  await review().getByRole('button',{name:'从回顾创建片单',exact:true}).click();await rd().getByRole('checkbox').first().check();await rd().getByLabel('片单名称',{exact:true}).fill('E3 自选固定专题');await rd().getByRole('button',{name:'确认收录 1 部作品',exact:true}).click();await rd().getByRole('button',{name:'完成',exact:true}).click()
  assert.match((await api('get','curations/?search=E3%20自选固定专题')).results[0].description,/2026-01-01 至 2026-02-28/)
  // Real change between full source read and confirmation fails atomically, preserves selection/draft.
  await review().getByRole('button',{name:'从回顾创建片单',exact:true}).click();await rd().getByRole('checkbox').first().check();await rd().getByLabel('片单名称',{exact:true}).fill('E3 不能保存过期来源')
  await api('patch',`records/${f.review_record}/details/`,{anime:{},record:{personal_score:'8.1'}})
  await rd().getByRole('button',{name:'确认收录 1 部作品',exact:true}).click();await rd().getByRole('alert').waitFor();assert.equal(await rd().getByLabel('片单名称',{exact:true}).inputValue(),'E3 不能保存过期来源');assert.equal((await api('get','curations/?search=E3%20不能保存过期来源')).count,0)
  await rd().getByRole('button',{name:'取消',exact:true}).click();await rd().getByRole('button',{name:'放弃并关闭收录',exact:true}).click()
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,checks:['ordinary motion','grid buttons never cover score','320/390/1440 collect layout','detail dirty blocks collection','independent actual arrangement save and page return','month/custom provenance','stale review rejects atomic creation with draft preserved'],pageErrors:0}))
}catch(error){await page.screenshot({path:out+'/boundaries-failure.png',fullPage:true});throw error}finally{await browser.close()}
