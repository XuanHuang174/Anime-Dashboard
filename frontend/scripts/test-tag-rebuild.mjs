// Real Dashboard UI, synthetic API fixtures only. Backend rules are tested separately on isolated PostgreSQL.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.JOURNAL_TEST_URL || 'http://127.0.0.1:5191'
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 950 }, reducedMotion: 'reduce' })
const page = await context.newPage(); page.setDefaultTimeout(12000)
const errors = [], writes = []
const clone = value => JSON.parse(JSON.stringify(value))
let job = null, rows = [], selection = [], applyCalls = 0, decisionCalls = 0
const initial = () => {
  const candidates = [
    { name: '2021', action: 'remove', reasons: ['非标准题材标签'], reason: '非标准题材标签', personal: false, fixed: false },
    { name: 'EVA', action: 'remove', reasons: ['非标准题材标签'], reason: '非标准题材标签', personal: false, fixed: false },
    { name: '导演', action: 'keep', reasons: ['该作品已有配色'], reason: '非标准题材标签', personal: true, fixed: false },
    { name: '珍藏', action: 'keep', reasons: ['本人自定义'], reason: '非标准题材标签', personal: true, fixed: false },
    { name: '剧场版', action: 'keep', reasons: ['作品类型、来源或分级'], reason: '独立保留的作品属性', personal: false, fixed: true },
  ]
  rows = [
    { record_id: 1, title: '合成重建作品', before: ['2021','EVA','导演','科幻','剧场版'], after: ['科幻','机战','剧场版','导演'], protected: ['导演','珍藏','剧场版'], uncertain: ['2021','EVA','导演','珍藏'], personal_removed: 0,
      changes: [{ kind:'remove', from:'2021', reason:'按本次标准题材方案替换' },{ kind:'remove', from:'EVA', reason:'按本次标准题材方案替换' },{ kind:'add',to:'机战',reason:'本地标准题材' }],
      details: { before_custom:['珍藏'],after_custom:['珍藏'],before_colors:[['导演','blue']],after_colors:[['导演','blue']],genres:['科幻','机战'],source:{id:101,state:'linked',tags:['科幻','机战']},candidates }, source_note:'已读取已确认作品的本地标准题材',status:'proposed',message:'' },
    { record_id:2,title:'未确认身份作品',before:['2020','旧标签'],after:['2020','旧标签'],protected:[],uncertain:[],changes:[],personal_removed:0,details:{before_custom:[],after_custom:[],before_colors:[],after_colors:[],genres:[],candidates:[]},source_note:'请先核对标准作品身份',status:'no_source',message:'' },
    { record_id:3,title:'细分标记作品',before:['真百','校园','旧系列'],after:['校园','真百'],protected:['真百'],uncertain:['旧系列'],changes:[{kind:'remove',from:'旧系列',reason:'非标准题材标签'}],personal_removed:0,details:{before_custom:[],after_custom:[],before_colors:[],after_colors:[],genres:['百合','校园'],candidates:[{name:'真百',action:'keep',reasons:['个人细分标记'],reason:'非标准题材标签',personal:true,fixed:false}]},source_note:'已读取本地标准题材',status:'proposed',message:'' },
  ]
}
const snapshot = () => ({ ...job, counts:Object.fromEntries(['proposed','no_source','unchanged','applied','undone','conflict'].map(status=>[status,rows.filter(r=>r.status===status).length])), change_records:rows.filter(r=>r.changes.length).length, protected_records:rows.filter(r=>r.protected.length).length,needs_review:rows.filter(r=>r.uncertain.length).length,personal_removals:rows.reduce((sum,r)=>sum+r.personal_removed,0) })
await context.addInitScript(() => localStorage.setItem('anime_journal_access_token','synthetic-rebuild-fixture'))
await context.route('**/*',async route=>{
  const request=route.request(), url=new URL(request.url()), method=request.method()
  if (!url.pathname.startsWith('/api/')) return url.origin===base||['cdn.tailwindcss.com','fonts.googleapis.com','fonts.gstatic.com','cdnjs.cloudflare.com'].includes(url.hostname)?route.continue():route.abort()
  const path=url.pathname.slice(5), body=request.postDataJSON()
  const reply=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)})
  if(method!=='GET') writes.push({path,body})
  if(path==='user/profile/') return reply({username:'QA',nickname:'合成主理人',public_status:'private'})
  if(path==='export/') return reply({records:[]})
  if(!path.startsWith('tag-cleanup/')) {
    assert.equal(method,'GET',`Unexpected write outside cleanup: ${path}`)
    if(path==='records/') return reply({count:3,results:Array.from({length:3},(_,i)=>({id:i+1,watch_status:'plan',personal_score:null,custom_tags:[],anime:{id:i+1,chinese_title:`合成记录${i+1}`,airing_period:'2020-4',base_tags:['日常'],description:'合成'}}))})
    if(path.endsWith('filter-options/')) return reply({tags:['日常'],years:['2020'],quick_filters:[]})
    if(path.endsWith('/stats/')) return reply({total_count:3,completed_count:0,average_score:'N/A',movie_count:0,ova_count:0,short_count:0,masterpiece_count:0})
    return reply([])
  }
  if(path==='tag-cleanup/') {
    if(method==='GET') return reply(job?[snapshot()]:[])
    assert.equal(body.mode,'rebuild'); assert.equal(body.keep_personal,true)
    initial(); job={id:'00000000-0000-0000-0000-000000000001',mode:'rebuild',keep_personal:true,revision:0,status:'scanning',scope:body.scope,filters:body.filters,total:3,scan_cursor:0,apply_cursor:0,undo_cursor:0,selected_count:0,created_at:new Date().toISOString(),expires_at:new Date(Date.now()+86400000).toISOString(),expired:false,obsolete:false}
    return reply(snapshot(),201)
  }
  if(path.endsWith('/decisions/')) {
    decisionCalls++; assert.equal(body.record_id,1); assert.equal(body.choices.length,1)
    if(body.revision!==job.revision) return reply({detail:'取舍方案已变化，请重新读取。'},409)
    const choice=body.choices[0], row=rows[0]; row.details.candidates.find(c=>c.name===choice.name).action=choice.action
    if(choice.name==='EVA') { assert.equal(choice.action,'personal'); row.details.after_custom=['珍藏','EVA']; row.changes[1]={kind:'move',from:'EVA',to:'EVA',reason:'保留为个人标记'} }
    if(choice.name==='导演') { assert.equal(choice.action,'remove'); row.after=row.after.filter(t=>t!=='导演'); row.protected=row.protected.filter(t=>t!=='导演'); row.details.after_colors=[]; row.personal_removed=1; row.changes.push({kind:'remove',from:'导演',reason:'本次取舍'}) }
    job.revision++; return reply({job:snapshot(),item:clone(row)})
  }
  if(path.endsWith('/apply/')) {
    applyCalls++; assert.equal(body.revision,job.revision); assert.equal(body.confirm_personal_removal,true)
    selection=rows.filter(r=>r.status==='proposed'&&!body.excluded_ids.includes(r.record_id)).map(r=>r.record_id)
    job.selected_count=selection.length; job.status='applying'; return reply(snapshot())
  }
  if(path.endsWith('/undo/')) { selection=rows.filter(r=>r.status==='applied').map(r=>r.record_id); job.selected_count=selection.length; job.status='undoing'; return reply(snapshot()) }
  if(path.endsWith('/step/')) {
    if(body.phase==='scan') {job.status='ready';job.scan_cursor=3}
    if(body.phase==='apply') {rows.filter(r=>selection.includes(r.record_id)).forEach(r=>r.status='applied');job.apply_cursor=selection.length;job.status='applied'}
    if(body.phase==='undo') {rows.filter(r=>selection.includes(r.record_id)).forEach(r=>r.status='undone');job.undo_cursor=selection.length;job.status='undone'}
    return reply(snapshot())
  }
  if(path.endsWith('/changes/')) return reply({job:snapshot(),items:rows})
  const filter=url.searchParams.get('filter'), filtered=rows.filter(r=>filter==='changes'?r.changes.length:filter==='review'?r.uncertain.length:filter==='protected'?r.protected.length:filter==='no_source'?r.status==='no_source':true)
  return reply({...snapshot(),items:clone(filtered),item_count:filtered.length,page:1,personal_removal_records:Object.fromEntries(rows.filter(r=>r.status==='proposed'&&r.personal_removed).map(r=>[r.record_id,r.personal_removed]))})
})
page.on('pageerror',error=>errors.push(error.message))
const modal=()=>page.locator('.tag-cleanup-modal')
const button=name=>modal().getByRole('button',{name,exact:true})
const card=()=>modal().locator('.tag-cleanup-card').filter({has:page.getByText('合成重建作品',{exact:true})})
const open=async()=>{ if(page.viewportSize().width<768) await page.getByRole('button',{name:'☰ 菜单',exact:true}).click(); await page.getByRole('button',{name:/📦 数据管理/}).click(); await page.getByRole('button',{name:/🧹 整理作品标签/}).click(); await modal().waitFor() }
const hit=async locator=>{await locator.scrollIntoViewIfNeeded();assert.ok(await locator.evaluate(el=>{const r=el.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,target=document.elementFromPoint(x,y);return target===el||el.contains(target)}))}
await mkdir('../.tools/tag-rebuild-qa',{recursive:true})
try {
  await page.goto(`${base}/dashboard`);await open();await button('标准题材重建').click()
  const keep=modal().getByRole('checkbox',{name:'默认保留个人标记、预设引用和已有配色标签',exact:true})
  await keep.uncheck();await modal().getByText('个人标记也会列入拟移除项，应用前需要单独确认。',{exact:true}).waitFor();await keep.check()
  assert.equal(writes.length,0)
  await page.screenshot({path:'../.tools/tag-rebuild-qa/setup.png'})
  await button('扫描并生成预览').click();await button('应用选中的 2 部').waitFor()
  assert.equal(applyCalls,0)
  await modal().getByRole('button',{name:/^无标准来源/}).click();await modal().getByText('未确认身份作品',{exact:true}).waitFor()
  assert.equal(await modal().getByRole('checkbox',{name:/整理 /}).count(),0)
  await modal().getByRole('button',{name:/^有变化/}).click()
  await card().getByRole('button',{name:'查看完整对比与保护说明',exact:true}).click()
  const eva=()=>card().getByLabel('合成重建作品：EVA的处理方式',{exact:true})
  await eva().selectOption('personal');await modal().getByRole('status').filter({hasText:'本次取舍已保存'}).waitFor()
  assert.equal(job.revision,1);assert.equal(applyCalls,0);assert.equal(await eva().inputValue(),'personal')
  assert.equal(await card().getByRole('button',{name:'收起对比与保护说明',exact:true}).getAttribute('aria-expanded'),'true')
  job.revision++ // Another tab updated this server-held plan.
  await card().getByLabel('合成重建作品：导演的处理方式',{exact:true}).selectOption('remove')
  await modal().getByRole('alert').filter({hasText:'取舍方案已变化'}).waitFor();assert.equal(rows[0].personal_removed,0)
  await button('重新读取任务').click();await card().getByRole('button',{name:'查看完整对比与保护说明',exact:true}).click()
  await card().getByLabel('合成重建作品：导演的处理方式',{exact:true}).selectOption('remove')
  await modal().getByRole('status').filter({hasText:'本次取舍已保存'}).waitFor();assert.equal(job.revision,3)
  assert.equal(await eva().inputValue(),'personal');assert.equal(rows[0].personal_removed,1)
  await page.screenshot({path:'../.tools/tag-rebuild-qa/desktop-decisions.png'})
  for(const [width,height] of [[390,720],[320,568],[844,390]]) {
    await page.setViewportSize({width,height});await hit(eva());await hit(button('应用选中的 2 部'));await hit(button('关闭'))
    assert.equal(await modal().evaluate(el=>el.scrollWidth<=el.clientWidth),true)
    await page.screenshot({path:`../.tools/tag-rebuild-qa/decisions-${width}.png`})
  }
  await page.setViewportSize({width:1440,height:950})
  await modal().getByRole('checkbox',{name:'整理 细分标记作品',exact:true}).uncheck()
  await button('应用选中的 1 部').click();assert.equal(applyCalls,0)
  const confirm=button('确认应用标准题材方案')
  assert.equal(await confirm.isDisabled(),true)
  await button('返回核对').click();await button('应用选中的 1 部').click()
  await page.setViewportSize({width:320,height:568})
  const acknowledge=modal().getByRole('checkbox',{name:'我已核对将移除的 1 个个人标记',exact:true})
  await hit(acknowledge);await acknowledge.check();await hit(confirm)
  await hit(button('返回核对'));await hit(button('关闭'));await hit(confirm)
  await page.screenshot({path:'../.tools/tag-rebuild-qa/mobile-confirm.png'})
  await page.setViewportSize({width:1440,height:950})
  await confirm.click();await button('撤销本次整理').waitFor();assert.equal(applyCalls,1);assert.equal(job.apply_cursor,1)
  const downloadPromise=page.waitForEvent('download');await button('下载变更清单').click();await downloadPromise
  await button('撤销本次整理').click();await button('确认撤销本次整理').click()
  await modal().getByRole('status').filter({hasText:'撤销已完成'}).waitFor()
  assert.equal(rows[0].status,'undone');assert.equal(rows[2].status,'proposed')
  await modal().getByRole('button',{name:/^无标准来源/}).click();await modal().getByRole('button',{name:'核对此作品',exact:true}).click();await modal().waitFor({state:'detached'})
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({result:'passed',checks:['new policy setup without writes','no-source rows cannot be selected','durable per-tag decisions','in-place preview and disclosure preserved','409 plan conflict and explicit reread','protected removal second confirmation','exclude selection','desktop/mobile/landscape hit targets','download and undo','identity-review handoff'],decisionCalls,applyCalls}))
} catch(error) {await page.screenshot({path:'../.tools/tag-rebuild-qa/failure.png'}).catch(()=>{});throw error}
finally {await browser.close()}
