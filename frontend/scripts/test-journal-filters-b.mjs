import {createRequire} from 'node:module'
import {mkdir,writeFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import assert from 'node:assert/strict'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
// Local Vite only; requests use synthetic data and external service traffic is blocked.
const origin=process.env.JOURNAL_B_TEST_URL || 'http://127.0.0.1:5189'
assert.ok(/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin), 'Test URL must be localhost')
const output=new URL('../../.tools/journal-b-browser/',import.meta.url);await mkdir(output,{recursive:true})
const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge',headless:true})
const report={checks:[],errors:[]},check=(name,ok,detail)=>{report.checks.push({name,passed:!!ok,detail});assert.ok(ok,name+': '+JSON.stringify(detail))}
let page
try{
 const context=await browser.newContext({reducedMotion:'reduce'})
 await context.route('**/*',route=>{const url=new URL(route.request().url());return url.origin===origin||['cdn.tailwindcss.com','cdnjs.cloudflare.com','fonts.googleapis.com','fonts.gstatic.com'].includes(url.hostname)?route.continue():route.abort()})
 page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',e=>report.errors.push(e.message))
 for(const [name,width,height] of [['mobile-100',390,740],['mobile-short',320,480],['mobile-75',520,987],['before-split',639,740],['mobile-50',780,1480],['landscape',840,420],['desktop',1440,900]]){
  await page.setViewportSize({width,height});await page.goto(origin+'/tests/fixtures/journalFiltersBHarness.html')
  const bgm=page.locator('a').filter({hasText:'Bangumi 条目'}).first();await bgm.waitFor()
  await page.waitForFunction(()=>getComputedStyle(document.querySelector('.detail-modal-layout')).display==='grid')
  const g=await page.evaluate(()=>{const box=sel=>{const r=document.querySelector(sel).getBoundingClientRect();return{x:r.x,y:r.y,bottom:r.bottom,height:r.height}};return{sources:box('.detail-modal-sources'),metadata:box('.detail-modal-metadata'),poster:box('#modalCoverImg')}})
  if(width<640)check(name+'-sources-do-not-overlap-metadata',g.sources.bottom+10<=g.metadata.y,g)
  else check(name+'-two-columns',g.metadata.x>g.sources.x,g)
  check(name+'-poster-never-collapses',g.poster.height>=100,g)
  await bgm.scrollIntoViewIfNeeded()
  check(name+'-bangumi-link-not-covered',await bgm.evaluate(el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return !!hit&&el.contains(hit)}))
  await page.screenshot({path:fileURLToPath(new URL(name+'.png',output))})
 }
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:900});await page.goto(origin+'/tests/fixtures/journalFiltersBHarness.html?mode=quick')
  await page.getByPlaceholder('例如：治愈日常').fill('搜索与年份')
  const include=page.getByRole('searchbox',{name:'搜索包含标签'}),exclude=page.getByRole('searchbox',{name:'搜索排除标签'})
  await include.fill('标签12');await page.locator('section[aria-label="包含条件"]').getByRole('button',{name:/自定义标签12$/}).click()
  await include.fill('不存在的标签')
  check(width+'-selected-inclusion-survives-search',await page.locator('section[aria-label="包含条件"]').getByRole('button',{name:/自定义标签12$/}).count()===1)
  await exclude.fill('标签45');await page.locator('section[aria-label="排除条件"]').getByRole('button',{name:/自定义标签45$/}).click()
  await page.getByRole('spinbutton',{name:'组合起始年份'}).fill('2020')
  await page.getByRole('spinbutton',{name:'组合结束年份'}).fill('2021')
  await page.getByRole('button',{name:/创建组合筛选$/}).click()
  await page.locator('#saved').waitFor()
  const saved=JSON.parse(await page.locator('#saved').textContent())
  check(width+'-saved-search-selection-and-year-bounds',saved.tags.includes('自定义标签12')&&saved.exclude_tags.includes('自定义标签45')&&saved.year_from===2020&&saved.year_to===2021,saved)
 }
 for(const mode of ['personal','similar','tags','credits']){
  await page.setViewportSize({width:390,height:844});await page.goto(origin+'/tests/fixtures/journalFiltersBHarness.html?mode='+mode)
  if(mode==='tags') await page.getByText('类型、年份与评分范围',{exact:true}).click()
  await page.getByRole('checkbox',{name:'TV',exact:true}).click();await page.getByRole('checkbox',{name:'OVA',exact:true}).click()
  check(mode+'-two-types-selected',await page.getByRole('checkbox',{name:'TV',exact:true}).getAttribute('aria-checked')==='true'&&await page.getByRole('checkbox',{name:/OVA$/}).getAttribute('aria-checked')==='true')
  await page.getByRole('button',{name:mode==='tags'?/开始探索$/:/应用筛选$/}).click()
  const saved=JSON.parse(await page.locator('#saved').textContent())
  check(mode+'-applied-canonical-type-union',(saved.filters?.type??saved.type)==='OVA,TV',saved)
 }
 await page.setViewportSize({width:1440,height:900});await page.goto(origin+'/tests/fixtures/journalFiltersBHarness.html?mode=add')
 const title=page.locator('input[name="chineseTitle"]');await title.fill('合成番剧')
 await page.getByRole('button',{name:'从 Bangumi 自动搜索番剧资料'}).click()
 await page.getByRole('button',{name:/合成番剧/}).click()
 await page.waitForFunction(()=>document.querySelector('input[name="tags"]').value==='日常,治愈,喜剧')
 check('autofill-keeps-source-link-with-cleaned-tags',await page.locator('input[name="bangumiUrl"]').inputValue()==='https://bgm.tv/subject/123')
 const buttons=await page.locator('footer').getByRole('button').allTextContents()
 check('create-before-cancel',buttons.findIndex(x=>x.includes('创建并加入手账'))<buttons.findIndex(x=>x==='取消'),buttons)
 check('no-browser-runtime-errors',report.errors.length===0,report.errors)
 await context.close()
}catch(e){report.failure=e.message;if(page)await page.screenshot({path:fileURLToPath(new URL('failure.png',output))});throw e}
finally{await writeFile(new URL('report.json',output),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({checks:report.checks.length,failed:report.checks.filter(x=>!x.passed),errors:report.errors}))}
