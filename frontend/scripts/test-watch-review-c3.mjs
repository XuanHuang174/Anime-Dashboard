import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFile,mkdir} from 'node:fs/promises'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright')
const f=JSON.parse(await readFile('../.tools/watch-review-c2-fixture.json','utf8')),base='http://127.0.0.1:5191'
assert.match(f.username,/^plans-b2-/)
const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,reducedMotion:'reduce'})
await context.addInitScript(({token})=>localStorage.setItem('anime_journal_access_token',token),f)
const page=await context.newPage();page.setDefaultTimeout(20000)
const errors=[],downloads=[],writes=[];page.on('pageerror',e=>errors.push(e.message));page.on('download',d=>downloads.push(d));page.on('request',r=>{if(r.url().includes('/api/')&&r.method()!=='GET')writes.push(r.url())})
const review=()=>page.getByRole('region',{name:'私人观看回顾工作区'}),dialog=()=>page.getByRole('dialog',{name:'导出观看回顾'})
const ready=async()=>{await review().locator('.review-summary[aria-busy=false]').waitFor();await review().locator('.review-error').waitFor({state:'detached'})}
await mkdir('../.tools/review-c3-qa',{recursive:true})
try{
 const poster=await context.request.patch(`${base}/api/records/${f.review_record}/details/`,{headers:{Authorization:`Bearer ${f.token}`},data:{anime:{poster_url:'https://media.xh-anime.com/synthetic-failure.jpg'},record:{}}});assert.equal(poster.status(),200)
 await page.route('**/api/utils/image-proxy/**',route=>route.fulfill({status:503,contentType:'application/json',body:'{}'}))
 await page.goto(base+'/dashboard');await page.getByRole('navigation',{name:'私人手账工作区'}).getByRole('button',{name:'回顾',exact:true}).click()
 await review().getByLabel('回顾月份',{exact:true}).fill(f.month);await review().getByRole('button',{name:'查看回顾',exact:true}).click();await ready()
 await review().getByRole('button',{name:'导出回顾长图',exact:true}).click()
 assert.equal(await dialog().getByLabel('随记与补录／轮次备注',{exact:true}).isChecked(),false)
 await dialog().getByLabel('包含海报（最多24张，失败或超限显示占位）',{exact:true}).check()
 await dialog().getByRole('button',{name:'读取并预览完整内容',exact:true}).click();await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).waitFor()
 assert.equal(await page.locator('.review-export-stage .review-export-sheet').count(),3)
 assert.equal(await page.locator('.review-export-stage').getByText('C2 本期作品 00',{exact:true}).count(),1)
 assert.equal(await page.locator('.review-export-stage').getByText(/十月的独立随记/).count(),0)
 await dialog().getByText(/张海报使用占位/).waitFor()
 const zipDownload=page.waitForEvent('download');await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).click();const zip=await zipDownload
 assert.match(zip.suggestedFilename(),/\.zip$/);await zip.saveAs('../.tools/review-c3-qa/full-report.zip')
 await dialog().getByRole('button',{name:'关闭面板',exact:true}).click()
 // A single summary produces one PNG. Private text stays opt-in.
 await review().getByRole('button',{name:'导出回顾长图',exact:true}).click();await dialog().getByLabel('完整作品清单',{exact:true}).uncheck()
 await dialog().getByRole('button',{name:'读取并预览完整内容',exact:true}).click();await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).waitFor()
 const pngDownload=page.waitForEvent('download');await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).click();const png=await pngDownload
 assert.match(png.suggestedFilename(),/\.png$/);await png.saveAs('../.tools/review-c3-qa/summary.png')
 await dialog().getByLabel('随记与补录／轮次备注',{exact:true}).check();await dialog().getByRole('button',{name:'读取并预览完整内容',exact:true}).click();await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).waitFor()
 assert.ok((await page.locator('.review-export-stage').innerText()).includes('十月的独立随记'))
 for(const width of [320,390,1440]){await page.setViewportSize({width,height:800});assert.equal(await dialog().evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);await page.screenshot({path:`../.tools/review-c3-qa/export-${width}.png`})}
 // Actual server data change between preview and download must prevent a file.
 const changed=await context.request.patch(`${base}/api/records/${f.review_record}/details/`,{headers:{Authorization:`Bearer ${f.token}`},data:{anime:{chinese_title:'C3 合成资料更正'},record:{}}});assert.equal(changed.status(),200)
 const before=downloads.length;await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).click();await dialog().getByRole('alert').waitFor();assert.equal(downloads.length,before)
 await dialog().getByRole('button',{name:'关闭面板',exact:true}).click();await review().getByRole('button',{name:'刷新回顾',exact:true}).click();await ready()
 await review().getByRole('button',{name:'导出回顾长图',exact:true}).click();await dialog().getByRole('button',{name:'读取并预览完整内容',exact:true}).click();await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).waitFor()
 await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).click();await dialog().getByRole('button',{name:'取消并关闭',exact:true}).click();await dialog().waitFor({state:'detached'});await page.waitForTimeout(1000);assert.equal(downloads.length,before)
 // Custom periods retain their own API mode, title and PNG export after annual support.
 await review().getByRole('button',{name:'自选时间段',exact:true}).click()
 await review().getByLabel('开始日期',{exact:true}).fill('2026-09-01');await review().getByLabel('结束日期',{exact:true}).fill('2026-10-31')
 await review().getByRole('button',{name:'查看回顾',exact:true}).click();await ready()
 await review().getByRole('button',{name:'导出回顾长图',exact:true}).click();await dialog().getByLabel('完整作品清单',{exact:true}).uncheck()
 assert.equal(await dialog().getByLabel('十二个月分布／月份待补充说明',{exact:true}).count(),0)
 await dialog().getByRole('button',{name:'读取并预览完整内容',exact:true}).click();await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).waitFor()
 assert.equal(await page.locator('.review-export-stage .review-export-sheet').count(),1)
 assert.match(await page.locator('.review-export-stage').innerText(),/2026-09-01 至 2026-10-31/)
 const customDownload=page.waitForEvent('download');await dialog().getByRole('button',{name:'下载回顾长图',exact:true}).click();const custom=await customDownload
 assert.match(custom.suggestedFilename(),/_观看回顾_01\.png$/);await custom.saveAs('../.tools/review-c3-qa/custom-summary.png')
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
 console.log(JSON.stringify({result:'passed',checks:['all pages ZIP','single PNG','custom-period PNG','private text opt-in','long notes','responsive preview','real mutation blocks download','cancel blocks download','no writes'],downloads:downloads.length}))
}catch(e){console.log((await page.locator('body').innerText()).slice(-3500));await page.screenshot({path:'../.tools/review-c3-qa/failure.png'});throw e}finally{await context.close();await browser.close()}
