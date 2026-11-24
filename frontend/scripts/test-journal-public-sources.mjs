// Real public detail component and adapters; only synthetic local fixture data.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdir} from 'node:fs/promises'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright')
const base=process.env.JOURNAL_TEST_URL||'http://127.0.0.1:5191'
assert.match(base,/^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const browser=await chromium.launch({channel:'msedge',headless:true})
const context=await browser.newContext({reducedMotion:'reduce'})
await context.route('**/*',route=>{const url=new URL(route.request().url());return url.origin===base||['cdn.tailwindcss.com','cdnjs.cloudflare.com','fonts.googleapis.com','fonts.gstatic.com'].includes(url.hostname)?route.continue():route.abort()})
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message))
await mkdir('../.tools/journal-public-sources-qa',{recursive:true})
try{
 for(const width of [1440,320])for(const mode of ['shared-source','featured-source','legacy-featured-source']){
  await page.setViewportSize({width,height:width===320?568:900})
  await page.goto(`${base}/tests/fixtures/journalFiltersBHarness.html?mode=${mode}`)
  const link=page.getByText('Bangumi 条目',{exact:true}).locator('..')
  await link.waitFor();await link.scrollIntoViewIfNeeded()
  if(mode==='legacy-featured-source'){
   assert.equal(await link.getAttribute('href'),null)
   assert.equal(await link.getAttribute('aria-disabled'),'true')
   assert.match(await link.innerText(),/未设置/)
  }else{
   assert.equal(await link.getAttribute('href'),'https://bgm.tv/subject/12345')
   assert.equal(await link.getAttribute('target'),'_blank')
   assert.equal(await link.evaluate(el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return hit===el||el.contains(hit)}),true)
  }
  assert.equal(await page.locator('.detail-modal-layout').evaluate(el=>el.scrollWidth<=el.clientWidth),true)
  await page.screenshot({path:`../.tools/journal-public-sources-qa/${mode}-${width}.png`})
 }
 assert.deepEqual(errors,[])
 console.log(JSON.stringify({result:'passed',scenarios:6,checks:['shared URL adapter','frozen featured URL adapter','legacy snapshot stays unset','desktop/mobile reachable links','no horizontal overflow','no page errors']}))
}finally{await browser.close()}
