// Real Dashboard and native editor; every business response uses synthetic records.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, readFile } from 'node:fs/promises'
import { matchesCustomQuickFilter } from '../src/utils/quickFilter.js'
import { defaultRatingPreferences } from '../src/utils/ratingPreferences.js'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.JOURNAL_TEST_URL || 'http://127.0.0.1:5191'
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const contract = JSON.parse(await readFile(new URL('../tests/fixtures/quickConditionsContract.json', import.meta.url)))
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true })
const context = await browser.newContext({ viewport: {width:1440,height:950}, reducedMotion:'reduce' })
const page = await context.newPage(); page.setDefaultTimeout(12000)
const errors=[], writes=[], previews=[], requests=[]
let filters=[{id:31,name:'旧标签组合',tags:['日常'],exclude_tags:[],title_keywords:[],exclude_title_keywords:[],match_mode:'any',year_from:null,year_to:null}]
let previewFailure=0, saveFailure=0, delayNine=false
const preferences=defaultRatingPreferences()
await context.addInitScript(() => localStorage.setItem('anime_journal_access_token',`synthetic.${btoa(JSON.stringify({user_id:1,exp:4102444800}))}.local-signature`))
await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),method=request.method(),path=url.pathname.slice(5)
  if(!url.pathname.startsWith('/api/'))return url.origin===base||['cdn.tailwindcss.com','fonts.googleapis.com','fonts.gstatic.com','cdnjs.cloudflare.com'].includes(url.hostname)?route.continue():route.abort()
  const respond=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)}).catch(()=>{})
  if(path==='quick-filters/preview/'){
    const draft=request.postDataJSON();previews.push(draft)
    if(previewFailure){previewFailure=0;return respond({detail:'合成预览失败'},500)}
    if(delayNine&&draft.score_min===9)await new Promise(resolve=>setTimeout(resolve,1100))
    return respond({count:contract.records.filter(row=>matchesCustomQuickFilter(row,draft)).length,scope:'本人全手账'})
  }
  if(path.startsWith('quick-filters/')&&method!=='GET'){
    writes.push({path,method,body:request.postDataJSON()})
    await new Promise(resolve=>setTimeout(resolve,180))
    if(saveFailure){saveFailure=0;return respond({detail:'合成保存失败，草稿保留'},500)}
    if(method==='DELETE'){filters=filters.filter(row=>row.id!==Number(path.split('/')[1]));return respond(null,204)}
    const body=request.postDataJSON(),id=method==='POST'?32:Number(path.split('/')[1])
    const row={...body,id};filters=method==='POST'?[...filters,row]:filters.map(old=>old.id===id?row:old)
    return respond(row,method==='POST'?201:200)
  }
  assert.equal(method,'GET',`Unexpected write ${method} ${path}`)
  if(path==='user/profile/'||path==='user/me/')return respond({username:'QA',nickname:'合成主理人',public_status:'approved',rating_preferences:preferences,rating_revision:0})
  if(path.endsWith('filter-options/'))return respond({tags:['日常','治愈','后宫',...Array.from({length:60},(_,i)=>`合成标签${i}`)],years:['2020','2021','2022'],quick_filters:filters})
  if(path==='quick-filters/')return respond(filters)
  if(path.endsWith('/stats/'))return respond({rating_preferences:preferences,rating_revision:0,total_count:7,completed_count:3,watching_count:2,pending_count:2,average_score:'7.58',movie_count:0,ova_count:0,short_count:0,masterpiece_count:2})
  if(path==='records/'){
    const quick=filters.find(row=>String(row.id)===url.searchParams.get('quick_filter'));requests.push(url.searchParams.toString())
    let rows=contract.records.filter(row=>matchesCustomQuickFilter(row,quick))
    if(url.searchParams.get('status')&&url.searchParams.get('status')!=='all')rows=rows.filter(row=>row.status===url.searchParams.get('status'))
    const results=rows.map(row=>({id:row.id,personal_score:row.score,watch_status:row.status,review:row.review,custom_tags:[],anime:{id:row.id,chinese_title:row.title,japanese_title:row.japaneseTitle||'',base_tags:row.tags,airing_period:row.period}}))
    return respond(url.searchParams.get('page_size')==='all'?results:{count:results.length,results})
  }
  if(path==='featured-submissions/state/')return respond({submissions:[],max_featured_limit:3})
  if(path==='notifications/poll/')return respond({notifications:[]})
  return respond([])
})
page.on('pageerror',error=>errors.push(error.message))
const modal=()=>page.locator('.quick-workshop')
const button=name=>modal().getByRole('button',{name,exact:true})
const field=name=>modal().getByLabel(name,{exact:true})
const hit=async locator=>{await locator.scrollIntoViewIfNeeded();assert.ok(await locator.evaluate(el=>{const r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2,h=document.elementFromPoint(x,y);return x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&(h===el||el.contains(h))}),'Control must remain visible and reachable')}
const count=async n=>modal().getByRole('status').getByText(`匹配 ${n} 部`,{exact:true}).waitFor()
const open=async()=>{if(page.viewportSize().width<768)await page.getByRole('button',{name:'☰ 菜单',exact:true}).click();await page.getByRole('button',{name:'编辑自定义快速筛选',exact:true}).click();await field('筛选名称').waitFor()}
await mkdir('../.tools/quick-filter-workshop-qa',{recursive:true})
try{
  await page.goto(`${base}/dashboard`);await open();assert.equal(writes.length,0)
  await field('筛选名称').fill('合成高分收藏')
  await field('组合最低分').fill('0');await field('组合最高分').fill('0');await count(1)
  assert.equal(await field('组合评分状态').inputValue(),'rated')
  await field('看过').check();await field('组合长评状态').selectOption('without');await count(1)
  await button('标签与标题').click();await modal().getByRole('searchbox',{name:'搜索包含标签',exact:true}).fill('日常')
  await modal().getByRole('region',{name:'包含条件',exact:true}).getByRole('button',{name:'+ 日常',exact:true}).click()
  await button('常用条件').click();await count(1)
  await page.screenshot({path:'../.tools/quick-filter-workshop-qa/zero-and-states.png'})
  await field('组合最低分').fill('9');await field('组合最高分').fill('10');await field('组合长评状态').selectOption('any');await count(1)
  delayNine=true
  await field('组合最低分').fill('8.9');await count(1)
  await field('组合最低分').fill('9')
  await page.waitForRequest(request=>request.url().endsWith('/quick-filters/preview/')&&request.postDataJSON().score_min===9)
  await field('组合最低分').fill('10');await count(0);await page.waitForTimeout(1200);await count(0);delayNine=false
  await field('组合评分状态').selectOption('unrated');await count(1)
  assert.equal(await field('组合最低分').count(),0);assert.equal(previews.at(-1).score_min,null)
  await field('组合评分状态').selectOption('rated');await field('组合最低分').fill('9');await count(1)
  previewFailure=500;await field('组合最高分').fill('9.9')
  await modal().getByText('合成预览失败',{exact:true}).waitFor();const calls=previews.length;await page.waitForTimeout(650);assert.equal(previews.length,calls)
  await button('重试预览').click();await count(1)
  for(const [width,height] of [[390,720],[320,568],[844,390]]){
    await page.setViewportSize({width,height});await hit(field('组合最低分'));await hit(button('保存并应用'));await hit(button('关闭'))
    assert.equal(await modal().evaluate(el=>el.scrollWidth<=el.clientWidth),true)
    await page.screenshot({path:`../.tools/quick-filter-workshop-qa/mobile-${width}.png`})
  }
  await page.setViewportSize({width:1440,height:950})
  await button('关闭').click();await button('继续编辑').click();assert.equal(await field('筛选名称').inputValue(),'合成高分收藏')
  saveFailure=500;await button('保存组合').click();await modal().getByRole('alert').getByText('合成保存失败，草稿保留',{exact:true}).waitFor()
  const attempts=writes.length;await page.waitForTimeout(650);assert.equal(writes.length,attempts)
  await button('保存并应用').evaluate(el=>{el.click();el.click()});await modal().waitFor({state:'detached'})
  assert.equal(writes.filter(row=>row.method==='POST').length,2)
  assert.equal(filters.at(-1).score_min,9);assert.equal(filters.at(-1).score_max,9.9)
  await page.waitForFunction(()=>document.querySelectorAll('.memphis-anime-list tbody tr').length>0)
  await page.locator('.memphis-anime-list tbody tr').filter({hasText:'合成4'}).waitFor()
  const paged=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/records/'&&new URL(request.url()).searchParams.get('quick_filter')==='32')
  await page.getByLabel('选择每页显示数量',{exact:true}).selectOption('12');await paged
  assert.ok(requests.some(value=>new URLSearchParams(value).get('quick_filter')==='32'))
  await open();await button('编辑 合成高分收藏').click();await count(1)
  assert.equal(await field('组合最低分').inputValue(),'9')
  await field('组合最低分').fill('10');await button('保存组合').click();await modal().getByRole('status').filter({hasText:'评分下限不能高于上限。'}).waitFor()
  await field('组合最高分').fill('10');await count(0);await button('保存组合').click();await modal().waitFor({state:'detached'})
  assert.equal(filters.at(-1).score_min,10)
  await open();await button('删除 旧标签组合').click();await button('确认删除').click();await button('编辑 旧标签组合').waitFor({state:'detached'})
  assert.equal(filters.length,1)
  await button('编辑 合成高分收藏').click();await field('组合最低分').fill('9');await button('创建新组合').click();await button('放弃修改').click()
  assert.equal(await field('筛选名称').inputValue(),'')
  await field('组合评分状态').selectOption('unrated');await count(2)
  await page.evaluate(()=>{localStorage.setItem('anime_journal_access_token','synthetic-other-account');window.dispatchEvent(new Event('storage'))})
  await modal().waitFor({state:'detached'});assert.deepEqual(errors,[])
  console.log(JSON.stringify({result:'passed',checks:['zero versus unrated','score-only and AND groups','draft count across all records','stale response ignored','unrated clears hidden bounds','preview error explicit retry','desktop/mobile/landscape hit targets','discard preserves draft','save failure no replay','save-and-apply duplicate gate','edit persistence and range validation','inline delete','account isolation'],previews:previews.length,writes:writes.length}))
}catch(error){await page.screenshot({path:'../.tools/quick-filter-workshop-qa/failure.png'}).catch(()=>{});throw error}
finally{await browser.close()}
