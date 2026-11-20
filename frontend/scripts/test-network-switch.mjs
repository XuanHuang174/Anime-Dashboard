import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdir,writeFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
// Local Vite only; every API is synthetic and all other service traffic is blocked.
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const origin=process.env.NETWORK_SWITCH_TEST_URL || 'http://127.0.0.1:5189'
assert.ok(/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin), 'Test URL must be localhost')
const output=new URL('../../.tools/network-switch-browser/',import.meta.url)
await mkdir(output,{recursive:true})
const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge'})
const report={checks:[],errors:[],writes:[]},check=(name,ok)=>{report.checks.push({name,passed:!!ok});assert.ok(ok,name)}
let page
try{
for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
 const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'})
 let offline=true
 const reads=[]
 const record={id:1,watch_status:'completed',personal_score:'8.5',review:'合成评价',anime:{id:1,chinese_title:'本机合成作品',japanese_title:'Synthetic',airing_period:'2026-7',studio:'Synthetic',episodes:'12',base_tags:['日常'],description:'仅本机验证的剧情简介'}}
 await context.addInitScript(()=>{localStorage.setItem('anime_journal_access_token','synthetic.'+btoa(JSON.stringify({user_id:90001}))+'.fixture');localStorage.setItem('anime_journal_refresh_token','synthetic-refresh')})
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname.replace(/^\/api\//,'')
  if(url.pathname.startsWith('/api/')){
   if(req.method()==='GET')reads.push(path)
   else report.writes.push({name,path,method:req.method()})
   if(offline)return route.abort('namenotresolved')
   let data={}
   if(path==='user/profile/')data={username:'QA',nickname:'本机恢复验收',public_status:'approved',showcase_subtitle:'合成资料'}
   if(path==='records/')data=url.searchParams.get('page_size')==='all'?[record]:{count:1,results:[record],next:null,previous:null}
   if(path==='records/stats/')data={total_count:1,completed_count:1,average_score:8.5}
   if(path==='records/filter-options/')data={tags:['日常'],years:['2026']}
   if(path==='quick-filters/')data=[]
   if(path==='notifications/poll/')data=[]
   if(path==='tag-presets/')data={tags:[]}
   if(path==='featured-submissions/state/')data={submissions:[],max_featured_limit:3}
   if(path==='records/showcase-publication/')data={contract_version:'showcase.publication.v1',can_publish:false,count:0,results:[],next_page:null}
   if(path==='records/1/details/'&&req.method()==='PATCH')data=record
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)})
  }
  if(url.origin===origin||['cdn.tailwindcss.com','cdnjs.cloudflare.com','fonts.googleapis.com','fonts.gstatic.com'].includes(url.hostname))return route.continue()
  return route.abort()
 })
 page=await context.newPage();page.setDefaultTimeout(15000)
 page.on('pageerror',e=>report.errors.push(e.message))
 await page.goto(origin+'/dashboard')
 await page.getByText('暂时无法连接数据服务',{exact:true}).waitFor()
 await page.waitForTimeout(1300)
 check(name+'-one-connection-notice',await page.locator('.connection-notice').count()===1)
 check(name+'-no-fallback-nickname',await page.getByText('我的番剧手账房',{exact:true}).count()>0||await page.getByText('我的番剧手账',{exact:true}).count()>0)
 check(name+'-no-toast-storm',await page.getByText('个人资料加载失败，暂时使用默认头像。',{exact:true}).count()===0&&await page.getByText('精选专栏状态加载失败，请稍后刷新重试。',{exact:true}).count()===0)
 const before=reads.filter(p=>p==='records/').length
 await page.waitForTimeout(1600)
 check(name+'-bounded-read-attempts',before<=4&&reads.filter(p=>p==='records/').length===before)
 check(name+'-tokens-preserved',await page.evaluate(()=>!!localStorage.getItem('anime_journal_access_token')&&!!localStorage.getItem('anime_journal_refresh_token')))
 await page.screenshot({path:fileURLToPath(new URL(name+'-dns-failure.png',output))})
 offline=false
 await page.evaluate(()=>window.dispatchEvent(new Event('online')))
 await page.waitForSelector('.memphis-anime-list, .memphis-anime-grid')
 await page.locator('.connection-notice').waitFor({state:'detached'})
 check(name+'-profile-recovered',await page.getByText(/本机恢复验收.*番剧/).count()>0)
 if(width>=768){
  const search=page.locator('input[placeholder*="输入番剧"]').first()
  await search.fill('本机')
  offline=true
  await page.getByRole('combobox',{name:'选择每页显示数量'}).first().selectOption('12')
  await page.getByText('当前显示上次成功读取的内容，新的分页或筛选结果暂未同步。',{exact:true}).waitFor()
  check(name+'-last-record-remains',await page.locator('.memphis-anime-list, .memphis-anime-grid').getByText('本机合成作品',{exact:true}).count()>0)
  offline=false
  await page.getByRole('button',{name:'重新连接',exact:true}).click()
  await page.locator('.connection-notice').waitFor({state:'detached'})
  check(name+'-search-kept',await search.inputValue()==='本机')
  check(name+'-page-size-kept',await page.getByRole('combobox',{name:'选择每页显示数量'}).first().inputValue()==='12')
 }
 const edit=page.locator('button.memphis-edit-action').first()
 if(await edit.count())await edit.click()
 else await page.locator('.memphis-anime-grid .memphis-entry').first().click()
 await page.waitForSelector('.edit-record-modal')
 await page.getByRole('button',{name:/剧情简介$/}).click()
 const text=page.getByRole('textbox',{name:'剧情简介',exact:true})
 await text.fill('断网期间仍保留的未保存输入')
 offline=true
 await page.getByRole('button',{name:/保存全部修改$/}).click()
 await page.getByText('暂时无法连接数据服务',{exact:true}).waitFor()
 await page.waitForTimeout(4500)
 check(name+'-write-never-auto-replayed',report.writes.filter(w=>w.name===name&&w.method==='PATCH').length===1)
 check(name+'-editing-input-preserved',await text.inputValue()==='断网期间仍保留的未保存输入')
 offline=false
 await page.getByRole('button',{name:'重新连接',exact:true}).click()
 await page.locator('.connection-notice').waitFor({state:'detached'})
 check(name+'-reconnect-does-not-save',report.writes.filter(w=>w.name===name&&w.method==='PATCH').length===1)
 check(name+'-modal-not-remounted',await text.inputValue()==='断网期间仍保留的未保存输入')
 await page.screenshot({path:fileURLToPath(new URL(name+'-recovered-edit.png',output))})
 await context.close()
}
check('no-runtime-errors',report.errors.length===0)
}catch(error){report.failure=error.message;if(page)await page.screenshot({path:fileURLToPath(new URL('failure.png',output))});throw error}
finally{await writeFile(new URL('report.json',output),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify(report))}
