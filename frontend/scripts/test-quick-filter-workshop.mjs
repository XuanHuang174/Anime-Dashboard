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
const errors=[], writes=[], previews=[], requests=[], orderWrites=[]
const seed=(id,name,sort_order)=>({id,name,sort_order,tags:['日常'],exclude_tags:[],title_keywords:[],exclude_title_keywords:[],match_mode:'any',year_from:null,year_to:null})
let filters=[seed(31,'旧标签组合',0),seed(41,'萌系组合',1),seed(42,'百合组合',2)]
let previewFailure=0, saveFailure=0, delayNine=false
let orderRevision=0, orderConflict=false, orderFailure=false
const orderSnapshot=()=>({filters,version:String(orderRevision).padStart(64,'0')})
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
  if(path==='quick-filters/order/'){
    if(method==='GET')return respond(orderSnapshot())
    const body=request.postDataJSON();orderWrites.push(body);await new Promise(resolve=>setTimeout(resolve,180))
    if(orderConflict){orderConflict=false;orderRevision++;filters=filters.reverse().map((row,index)=>({...row,sort_order:index}));return respond({detail:'组合列表或顺序已变化，请重新读取顺序后再保存。条件草稿会保留。'},409)}
    if(orderFailure){orderFailure=false;return respond({detail:'合成顺序保存失败'},500)}
    assert.equal(body.version,orderSnapshot().version)
    assert.deepEqual([...body.ids].sort((a,b)=>a-b),filters.map(row=>row.id).sort((a,b)=>a-b))
    filters=body.ids.map((id,index)=>({...filters.find(row=>row.id===id),sort_order:index}));orderRevision++
    return respond(orderSnapshot())
  }
  if(path.startsWith('quick-filters/')&&method!=='GET'){
    writes.push({path,method,body:request.postDataJSON()})
    await new Promise(resolve=>setTimeout(resolve,180))
    if(saveFailure){saveFailure=0;return respond({detail:'合成保存失败，草稿保留'},500)}
    if(method==='DELETE'){filters=filters.filter(row=>row.id!==Number(path.split('/')[1]));return respond(null,204)}
    const body=request.postDataJSON(),id=method==='POST'?32:Number(path.split('/')[1])
    const row={...body,id,sort_order:method==='POST'?Math.max(...filters.map(row=>row.sort_order))+1:filters.find(row=>row.id===id).sort_order};filters=method==='POST'?[...filters,row]:filters.map(old=>old.id===id?row:old)
    return respond(row,method==='POST'?201:200)
  }
  assert.equal(method,'GET',`Unexpected write ${method} ${path}`)
  if(path==='user/profile/'||path==='user/me/')return respond({username:'QA',nickname:'合成主理人',public_status:'approved',rating_preferences:preferences,rating_revision:0})
  if(path.endsWith('filter-options/'))return respond({tags:['日常','治愈','后宫',...Array.from({length:60},(_,i)=>`合成标签${i}`)],years:['2020','2021','2022'],quick_filters:filters})
  if(path==='quick-filters/'||path==='showcase-filters/')return respond(filters)
  if(path.endsWith('/stats/'))return respond({rating_preferences:preferences,rating_revision:0,total_count:7,completed_count:3,watching_count:2,pending_count:2,average_score:'7.58',movie_count:0,ova_count:0,short_count:0,masterpiece_count:2})
  if(path==='records/'||path==='showcase/'||path.endsWith('/records/')){
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
  assert.equal(await button('编辑 旧标签组合').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(78, 205, 196)')
  assert.equal(await button('删除 旧标签组合').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 107, 107)')
  await field('筛选名称').fill('合成高分收藏')
  await field('组合最低分').fill('0');await field('组合最高分').fill('0');await count(1)
  assert.equal(await field('组合评分状态').inputValue(),'rated')
  await field('看过').check();await field('组合长评状态').selectOption('without');await count(1)
  await button('标签与标题').click();await modal().getByRole('searchbox',{name:'搜索包含标签',exact:true}).fill('日常')
  await modal().getByRole('region',{name:'包含条件',exact:true}).getByRole('button',{name:'+ 日常',exact:true}).click()
  const include=modal().getByRole('region',{name:'包含条件',exact:true}),exclude=modal().getByRole('region',{name:'排除条件',exact:true})
  const positive=include.getByRole('button',{name:'取消包含：日常',exact:true})
  const includeColor=await positive.evaluate(el=>getComputedStyle(el).backgroundColor)
  assert.equal(await positive.locator('.fa-check').count(),1)
  await exclude.getByRole('button',{name:'移到排除：日常',exact:true}).click()
  const negative=exclude.getByRole('button',{name:'取消排除：日常',exact:true})
  assert.equal(await negative.locator('.fa-ban').count(),1);assert.equal(await negative.locator('.fa-check').count(),0)
  assert.notEqual(await negative.evaluate(el=>getComputedStyle(el).backgroundColor),includeColor)
  await button('撤销标签移动').click();await positive.waitFor()
  await exclude.getByRole('button',{name:'+ 后宫',exact:true}).click()
  await include.getByRole('searchbox',{name:'搜索包含标签',exact:true}).fill('不存在的标签')
  await positive.waitFor();await include.getByRole('searchbox',{name:'搜索包含标签',exact:true}).fill('')
  await page.screenshot({path:'../.tools/quick-filter-workshop-qa/tag-states.png'})
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
  await field('筛选名称').fill('排序时保留的条件草稿')
  await button('调整顺序').click();await modal().getByRole('list',{name:'组合排序'}).waitFor()
  await button('上移 百合组合').click();await button('取消排序').click()
  assert.equal(orderWrites.length,0);assert.equal(await field('筛选名称').inputValue(),'排序时保留的条件草稿')
  await button('调整顺序').click();await button('上移 百合组合').waitFor({state:'visible'})
  await page.waitForFunction(()=>!document.querySelector('.quick-order-handle')?.disabled)
  await page.emulateMedia({reducedMotion:'no-preference'})
  const activeAnimations=await button('上移 百合组合').evaluate(el=>new Promise(resolve=>{el.click();requestAnimationFrame(()=>resolve(document.querySelector('.quick-order-list').getAnimations({subtree:true}).length))}))
  assert.ok(activeAnimations>0,'Reordering animates affected rows when motion is enabled')
  await page.emulateMedia({reducedMotion:'reduce'})
  const grip=button('拖动排序：百合组合，上下方向键移动')
  assert.equal(await grip.evaluate(el=>el===document.activeElement),true)
  await grip.press('ArrowUp')
  assert.equal(await modal().locator('[data-order-id]').first().getAttribute('data-order-id'),'42')
  assert.equal(await modal().locator('.quick-order-list').evaluate(el=>el.getAnimations({subtree:true}).length),0)
  await button('拖动排序：合成高分收藏，上下方向键移动').dragTo(modal().locator('[data-order-id="31"]'))
  assert.deepEqual(await modal().locator('[data-order-id]').evaluateAll(nodes=>nodes.map(node=>Number(node.dataset.orderId))),[42,32,31,41])
  assert.equal(await button('保存组合').isDisabled(),true)
  await page.screenshot({path:'../.tools/quick-filter-workshop-qa/order-desktop.png'})
  await button('关闭').click();await button('继续编辑').click()
  for(const [width,height] of [[320,568],[844,390]]){
    await page.setViewportSize({width,height});await hit(button('保存顺序'));await hit(button('取消排序'));await hit(button('关闭'))
    await hit(button('关闭自定义筛选编辑器'))
    assert.equal(await modal().evaluate(el=>el.scrollWidth<=el.clientWidth),true)
    await page.screenshot({path:`../.tools/quick-filter-workshop-qa/order-${width}.png`})
  }
  await page.setViewportSize({width:1440,height:950})
  orderConflict=true;await button('保存顺序').click();await modal().getByRole('alert').filter({hasText:'组合列表或顺序已变化'}).waitFor()
  await button('重新读取顺序（替换排序草稿）').click()
  assert.equal(await field('筛选名称').inputValue(),'排序时保留的条件草稿')
  const idsNow=await modal().locator('[data-order-id]').evaluateAll(nodes=>nodes.map(node=>Number(node.dataset.orderId)))
  await button(idsNow.indexOf(42)===0?'下移 百合组合':'上移 百合组合').click()
  orderFailure=true;await button('保存顺序').click();await modal().getByRole('alert').filter({hasText:'合成顺序保存失败'}).waitFor()
  const orderAttempts=orderWrites.length;await page.waitForTimeout(650);assert.equal(orderWrites.length,orderAttempts)
  await button('保存顺序').evaluate(el=>{el.click();el.click()})
  await button('调整顺序').waitFor();assert.equal(orderWrites.length,orderAttempts+1)
  assert.equal(await field('筛选名称').inputValue(),'排序时保留的条件草稿')
  await button('关闭').click();await button('放弃修改').click();await modal().waitFor({state:'detached'})
  const barOrder=()=>page.locator('.memphis-filter button').evaluateAll((nodes,names)=>nodes.map(node=>node.textContent.trim()).filter(name=>names.includes(name)),filters.map(row=>row.name))
  assert.deepEqual(await barOrder(),filters.map(row=>row.name))
  await page.reload();await page.getByRole('button',{name:'编辑自定义快速筛选',exact:true}).waitFor()
  assert.deepEqual(await barOrder(),filters.map(row=>row.name))
  await page.goto(`${base}/shared/QA`);await page.locator('.memphis-filter').waitFor()
  await page.getByRole('button',{name:'合成高分收藏',exact:true}).waitFor()
  assert.deepEqual(await barOrder(),filters.map(row=>row.name))
  await page.goto(`${base}/`);await page.getByRole('button',{name:'合成高分收藏',exact:true}).waitFor()
  assert.deepEqual(await barOrder(),filters.map(row=>row.name))
  await page.goto(`${base}/dashboard`);await open();await button('编辑 合成高分收藏').click();await count(1)
  await field('组合最低分').fill('10');await button('保存组合').click();await modal().getByRole('status').filter({hasText:'评分下限不能高于上限。'}).waitFor()
  await field('组合最高分').fill('10');await count(0);await button('保存组合').click();await modal().waitFor({state:'detached'})
  assert.equal(filters.find(row=>row.id===32).score_min,10)
  await open();await button('删除 旧标签组合').click();await button('确认删除').click();await button('编辑 旧标签组合').waitFor({state:'detached'})
  assert.equal(filters.length,3)
  await button('编辑 合成高分收藏').click();await field('组合最低分').fill('9');await button('创建新组合').click();await button('放弃修改').click()
  assert.equal(await field('筛选名称').inputValue(),'')
  await field('组合评分状态').selectOption('unrated');await count(2)
  await page.evaluate(()=>{localStorage.setItem('anime_journal_access_token','synthetic-other-account');window.dispatchEvent(new Event('storage'))})
  await modal().waitFor({state:'detached'});assert.deepEqual(errors,[])
  console.log(JSON.stringify({result:'passed',checks:['restored edit/delete colors and icons','include/exclude distinct states','tag transfer and undo','selected tags survive search','zero versus unrated','score-only and AND groups','draft count across all records','stale response ignored','unrated clears hidden bounds','preview error explicit retry','desktop/mobile/landscape hit targets','discard preserves draft','save failure no replay','save-and-apply duplicate gate','keyboard/arrow/drag ordering','sort preserves condition draft','order conflict explicit reread','order failure no replay and duplicate gate','private/shared order and reload','edit persistence and range validation','inline delete','account isolation'],previews:previews.length,writes:writes.length,orderWrites:orderWrites.length}))
}catch(error){await page.screenshot({path:'../.tools/quick-filter-workshop-qa/failure.png'}).catch(()=>{});throw error}
finally{await browser.close()}
