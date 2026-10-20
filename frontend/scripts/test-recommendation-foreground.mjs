// Production page + transport, synthetic API only; no online account or writes.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { browsePage } from '../tests/fixtures/browseContract.js'
import { preferences, profileData, versions, itemData } from '../tests/fixtures/profileSampling.js'

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const server = await createServer({configFile:false,root:process.cwd(),plugins:[react()],server:{host:'127.0.0.1',port:0},logLevel:'error'})
await server.listen()
const origin=server.resolvedUrls.local[0].replace(/\/$/,''), browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'msedge'})
const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),errors=[],external=[],writes=[],calls=[]
page.setDefaultTimeout(15000)
page.on('pageerror',error=>errors.push(error.message))
await page.addInitScript(()=>{
  let offset=0,visibility='visible'; const nativeNow=Date.now
  Date.now=()=>nativeNow()+offset
  Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>visibility})
  window.foregroundQA={advance(ms=31000){offset+=ms},
    focus(){window.dispatchEvent(new Event('focus'))},
    visibility(value){visibility=value;document.dispatchEvent(new Event('visibilitychange'))}}
})
let version=1,serial=0,activeSnapshot,activeParams,probeError=false,probeHold=false,arrived,releaseProbe
const prefs=()=>preferences('8.5',version), profile=()=>profileData(prefs()), evidenceContext=()=>({...versions('8.5',version),feedback_version:0})
const row=id=>({bangumi_id:id,content_rating:'non_adult',title_zh:`作品${id}`,title_jp:'',score:8,tags:['音乐'],season:'2026-01',status_type:'TV',
  evidence:{...itemData('9.0','8.5').evidence,versions:evidenceContext()},reason:'你对来源作品打了9分，共有音乐题材。',
  evidence_context:evidenceContext(),evidence_token:'b'.repeat(64),synopsis:'合成测试简介',watch_status:null,cover_url:''})
const finish=async(route,data,status=200)=>{try{await route.fulfill({status,json:data})}catch(error){if(!route.request().failure())throw error}}
await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url())
  if(url.origin!==origin){external.push(url.origin);return route.abort()}
  if(!url.pathname.startsWith('/__9bqa__/'))return route.continue()
  if(request.method()!=='GET'){writes.push(url.pathname);return finish(route,{},405)}
  const suffix=url.pathname.slice('/__9bqa__/recommendations/'.length),params=Object.fromEntries(url.searchParams)
  calls.push({suffix,params})
  if(suffix==='capabilities/')return finish(route,{contract_version:'recommendations.capabilities.v1',algorithm_version:'content.v1',browse_enabled:true,rollout_revision:'a'.repeat(64)})
  if(suffix==='profile/')return finish(route,profile())
  if(suffix==='preferences/')return finish(route,prefs())
  if(suffix==='context/'){
    const body={contract_version:'recommendations.context.v1',context:evidenceContext(),browse_enabled:true,rollout_revision:'a'.repeat(64)}
    if(probeHold)await new Promise(resolve=>{releaseProbe=resolve;arrived?.()})
    return finish(route,probeError?{}:body,probeError?503:200)
  }
  if(suffix==='feedback/hidden/')return finish(route,{contract_version:'recommendations.feedback.v1',feedback_version:0,count:0,results:[],page:1,page_size:1,next_page:null})
  if(/^\d+\/$/.test(suffix))return finish(route,row(Number(suffix.slice(0,-1))))
  assert.equal(suffix,'')
  if(!params.cursor){activeSnapshot=(++serial).toString(16).padStart(32,'0');activeParams=params}
  const data=browsePage({context:evidenceContext(),snapshot:activeSnapshot,
    offset:params.cursor?Number(params.cursor.split('-')[1]):0,row,
    filters:{type:activeParams.type||'all',minScore:activeParams.min_score||'0',hideRecorded:activeParams.hide_recorded!=='false'},
    category:activeParams.category||'all',style:activeParams.style||'balanced'})
  data.cold_start_state=profile().cold_start_state
  return finish(route,data)
})
const output=path.resolve('../.tools/acceptance-foreground')
await mkdir(output,{recursive:true})
const report={scope:'foreground-isolated-browser',production_writes:false,checks:[]}
const check=name=>{report.checks.push({name,passed:true});console.log('PASS: '+name)}
const count=suffix=>calls.filter(row=>row.suffix===suffix).length
const focus=async(advance=false)=>page.evaluate(value=>{if(value)foregroundQA.advance();foregroundQA.visibility('hidden');foregroundQA.visibility('visible');foregroundQA.focus();foregroundQA.focus()},advance)
const completeProbe=async()=>{await page.waitForTimeout(650)} // exercise real 250ms debounce + response/notify timers
const batch=()=>page.locator('.rec-batch-progress')
try{
  await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html`)
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  const next=()=>page.getByRole('button',{name:'换一批，看看其他作品 →',exact:true})
  await next().click();await page.locator('[data-rec-motion-id="125"]').waitFor()
  await next().click();await page.locator('[data-rec-motion-id="149"]').waitFor()
  await page.locator('[data-rec-motion-id="150"]').scrollIntoViewIfNeeded()
  await page.locator('[data-rec-motion-id="150"] .rec-evidence summary').click()
  const readCount=count(''), y=await page.evaluate(()=>scrollY)
  await page.evaluate(()=>{foregroundQA.card=document.querySelector('[data-rec-motion-id="150"]')})
  await focus();await completeProbe()
  assert.equal(count(''),readCount);assert.equal(count('context/'),0)
  assert.equal(await page.locator('[data-rec-motion-id="150"]').evaluate(node=>node===foregroundQA.card),true)
  assert.equal(await page.evaluate(()=>scrollY),y)
  assert.equal(await page.locator('[data-rec-motion-id="150"] details').getAttribute('open'),'')
  check('ordinary tab/background returns keep third batch, exact DOM, scroll and evidence disclosure without list/probe reads')

  await focus(true);await completeProbe()
  assert.equal(count('context/'),1);assert.equal(count(''),readCount)
  assert.match(await batch().innerText(),/第 3 批/)
  assert.equal(await page.evaluate(()=>scrollY),y)
  await focus();await completeProbe();assert.equal(count('context/'),1)
  check('after 30 seconds only one coalesced context probe runs; unchanged context does not replace or rerank')

  await page.getByRole('button',{name:'查看作品150详情',exact:true}).click()
  await page.getByRole('heading',{name:'故事简介',exact:true}).waitFor()
  const details=count('150/');await focus(true);await completeProbe()
  assert.equal(count('150/'),details);assert.equal(await page.locator('dialog[open]').count(),1)
  assert.equal(count(''),readCount)
  await page.keyboard.press('Escape');await page.locator('dialog').waitFor({state:'detached'})
  probeError=true;await focus(true);await completeProbe()
  assert.equal(await page.locator('.rec-context-update').count(),0);assert.equal(count(''),readCount)
  probeError=false
  check('unchanged foreground checks preserve open details; failed probes never clear cards or enter a page error')

  version++ // synthetic external preference change; no real write
  await focus(true);await page.locator('.rec-context-update').waitFor()
  assert.equal(count(''),readCount)
  assert.equal(await page.locator('[data-rec-motion-id="150"]').count(),1)
  assert.equal(await page.locator('[data-rec-motion-id="150"] .rec-evidence-policy').count(),0)
  assert.equal(await page.getByRole('button',{name:'查看作品150详情',exact:true}).isDisabled(),true)
  assert.match(await page.locator('.rec-batch-actions').innerText(),/第 3 批浏览位置仍保留/)
  await page.screenshot({path:path.join(output,'changed-keeps-third-batch.png')})
  await page.locator('.rec-context-update').getByRole('button',{name:'重新加载推荐',exact:true}).click()
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.equal(await page.locator('.rec-context-update').count(),0)
  assert.equal(count(''),readCount+1)
  check('external change retains third-batch shells, invalidates proof and disables stale actions; only explicit reload starts batch one')

  const noChangeReads=count('')
  // Same-origin nonce from another account must not affect this user's stream.
  await page.evaluate(()=>window.dispatchEvent(new StorageEvent('storage',{key:'anime-dashboard:recommendation-invalidate:v1',newValue:'opaque-synthetic-nonce'})))
  await completeProbe();assert.equal(await page.locator('.rec-context-update').count(),0)
  version++
  await page.evaluate(()=>window.dispatchEvent(new StorageEvent('storage',{key:'anime-dashboard:recommendation-invalidate:v1',newValue:'another-opaque-nonce'})))
  await page.locator('.rec-context-update').waitFor()
  assert.equal(count(''),noChangeReads)
  await page.locator('.rec-context-update').getByRole('button',{name:'重新加载推荐',exact:true}).click()
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  const slowArrived=new Promise(resolve=>{arrived=resolve});probeHold=true
  await focus(true);await slowArrived
  await page.getByRole('button',{name:'换一种口味',exact:true}).click()
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  probeHold=false;releaseProbe();await completeProbe()
  assert.equal(await page.locator('.rec-context-update').count(),0)
  check('cross-tab notifications never auto-reset; a probe arriving after category change cannot invalidate the new stream')

  await page.setViewportSize({width:390,height:844})
  await next().click();await page.locator('[data-rec-motion-id="125"]').waitFor()
  const mobileReads=count('');await focus();await completeProbe()
  assert.equal(count(''),mobileReads);assert.match(await batch().innerText(),/第 2 批/)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
  await page.evaluate(()=>{localStorage.setItem('anime_journal_access_token','different-synthetic-owner');window.dispatchEvent(new Event('focus'))})
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.match(await batch().innerText(),/第 1 批/)
  assert.deepEqual(writes,[]);assert.deepEqual(external,[]);assert.deepEqual(errors,[])
  check('mobile foreground retains its batch, while a different account still discards all previous browser context')
  report.result='passed';report.read_count=calls.length
}catch(error){report.result='failed';report.failure=error.stack;throw error}
finally{
  releaseProbe?.()
  await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2))
  await browser.close();await server.close()
}
