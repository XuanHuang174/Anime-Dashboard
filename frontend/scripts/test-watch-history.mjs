// Real Dashboard, editor and detail; all business APIs below are synthetic.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdir} from 'node:fs/promises'
import {defaultRatingPreferences} from '../src/utils/ratingPreferences.js'
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright')
const base=process.env.JOURNAL_TEST_URL||'http://127.0.0.1:5191'
assert.match(base,/^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const browser=await chromium.launch({channel:'msedge',headless:true})
const context=await browser.newContext({viewport:{width:1440,height:950},reducedMotion:'reduce'})
const page=await context.newPage();page.setDefaultTimeout(12000)
const clone=value=>JSON.parse(JSON.stringify(value)),errors=[],posts=[],historyReads=[],operations=new Map()
let version=0,conflict=false,unknown=false,readFailure=false
const record={id:1,watch_status:'watching',personal_score:'9.0',watched_eps:2,total_eps:12,watch_revision:0,review:'保留作品长评',custom_tags:[],anime:{id:1,chinese_title:'合成观看历程',japanese_title:'Synthetic history',airing_period:'2020-1',studio:'合成公司',episodes:'12',base_tags:['日常'],description:'合成剧情',bangumi_url:'https://bgm.tv/subject/12345'}}
const rounds=[{number:1,is_current:true,voided:false,origin:'baseline',status:'watching',watched_eps:2,total_eps:12,started_on:'',completed_on:'',rating:null,rating_origin:'',note:''}],events=[]
const current=()=>rounds.find(row=>row.is_current)
const state=row=>({record:{watch_status:record.watch_status,watched_eps:record.watched_eps,total_eps:record.total_eps},round:clone(row)})
function append(kind,before,row,note='',occurred_on=''){
 version++;record.watch_revision=version
 record.watch_status_display={plan:'想看',watching:'在看',completed:'看过',on_hold:'搁置'}[record.watch_status]
 events.push({revision:version,kind,source:'history_command',note,occurred_on,before,after:state(row),recorded_at:'2026-11-25T08:00:00Z'})
}
const snapshot=(p=1)=>({contract_version:'journal.watch-history.v1',version,rounds:clone(rounds),completed_rounds:rounds.filter(row=>!row.voided&&row.status==='completed').length,rewatch_count:Math.max(0,rounds.filter(row=>!row.voided).length-1),count:events.length,page:p,page_size:10,events:clone([...events].reverse().slice((p-1)*10,p*10)),record:clone(record)})
await context.addInitScript(()=>localStorage.setItem('anime_journal_access_token',`synthetic.${btoa(JSON.stringify({user_id:1,exp:4102444800}))}.local-signature`))
await context.route('**/*',async route=>{
 const request=route.request(),url=new URL(request.url()),path=url.pathname.slice(5),method=request.method()
 if(!url.pathname.startsWith('/api/'))return url.origin===base||['cdn.tailwindcss.com','fonts.googleapis.com','fonts.gstatic.com','cdnjs.cloudflare.com'].includes(url.hostname)?route.continue():route.abort()
 const respond=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)}).catch(()=>{})
 if(path==='records/1/watch-history/'){
  if(method==='GET'){
   historyReads.push(url.search);if(readFailure){readFailure=false;return respond({detail:'合成读取失败'},500)}
   return respond(snapshot(Number(url.searchParams.get('page')||1)))
  }
  const body=request.postDataJSON();posts.push(body);await new Promise(resolve=>setTimeout(resolve,160))
  if(operations.has(body.operation_id)){assert.deepEqual(body,operations.get(body.operation_id));return respond({...snapshot(),replayed:true})}
  if(conflict){conflict=false;append('note',state(current()),current(),'另一页面的随记');return respond({detail:'观看记录已变化，请重新读取历程后再操作。'},409)}
  assert.equal(body.version,version)
  let row=current(),before=state(row)
  if(body.action==='transition'){
   Object.assign(record,{watch_status:body.watch_status,watched_eps:body.watched_eps,total_eps:body.total_eps})
   Object.assign(row,{status:record.watch_status,watched_eps:record.watched_eps,total_eps:record.total_eps})
   if(row.status==='completed'){row.rating='9.0';row.rating_origin='completion_snapshot';row.completed_on=body.occurred_on||''}
  }else if(body.action==='start-rewatch'){
   row.is_current=false
   row={number:rounds.length+1,is_current:true,voided:false,origin:'observed',status:'watching',watched_eps:0,total_eps:12,started_on:body.started_on||'',completed_on:'',rating:null,rating_origin:'',note:body.note||''};rounds.push(row)
   record.watch_status='watching';record.watched_eps=0
  }else if(body.action==='update-round'){
   row=rounds.find(item=>item.number===body.round_number);before=state(row)
   Object.assign(row,{started_on:body.started_on,completed_on:body.completed_on,rating:body.rating===null?null:String(body.rating),rating_origin:body.rating===null?'':'manual',note:body.note})
  }else if(body.action==='undo'){
   const target=events.at(-1);assert.equal(body.target_revision,target.revision)
   if(target.before.round.number!==target.after.round.number){row.is_current=false;row.voided=true}
   row=rounds.find(item=>item.number===target.before.round.number);Object.assign(row,clone(target.before.round));Object.assign(record,target.before.record)
  }
  append(body.action,before,row,body.note||'',body.occurred_on||'');operations.set(body.operation_id,clone(body))
  if(unknown){unknown=false;return respond({detail:'合成保存结果未确认'},500)}
  return respond({...snapshot(),replayed:false})
 }
 assert.equal(method,'GET',`Unexpected mutation ${path}`)
 if(path==='user/profile/'||path==='user/me/')return respond({username:'QA',nickname:'合成主理人',public_status:'approved',rating_preferences:defaultRatingPreferences()})
 if(path.endsWith('filter-options/'))return respond({tags:['日常'],years:['2020'],quick_filters:[]})
 if(path==='records/'||path.endsWith('/records/'))return respond(url.searchParams.get('page_size')==='all'?[record]:{count:1,results:[record]})
 if(path.endsWith('/stats/'))return respond({total_count:1,completed_count:record.watch_status==='completed'?1:0,watching_count:record.watch_status==='watching'?1:0,pending_count:0,average_score:'9.0',movie_count:0,ova_count:0,short_count:0,masterpiece_count:0,rating_preferences:defaultRatingPreferences()})
 if(path==='featured-submissions/state/')return respond({submissions:[],max_featured_limit:3})
 if(path==='tag-presets/')return respond({tags:[]})
 if(path==='featured/')return respond([{id:1,chinese_title:'合成精选',score:'9.0',watch_status:'completed',tags:['日常'],rating_preferences:defaultRatingPreferences()}])
 if(path==='notifications/poll/')return respond({notifications:[]})
 return respond([])
})
page.on('pageerror',error=>errors.push(error.message))
const panel=()=>page.getByRole('region',{name:'私人观看历程',exact:true})
const button=name=>panel().getByRole('button',{name,exact:true})
const field=name=>panel().getByLabel(name,{exact:true})
const hit=async locator=>{await locator.scrollIntoViewIfNeeded();assert.equal(await locator.evaluate(el=>{const r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2,h=document.elementFromPoint(x,y);return x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&(el===h||el.contains(h))}),true,'Control must be reachable')}
const save=async()=>{await button('保存这条记录').click();await panel().locator('.watch-form').waitFor({state:'detached'})}
await mkdir('../.tools/watch-history-qa',{recursive:true})
try{
 await page.goto(`${base}/dashboard`)
 await page.getByRole('button',{name:'修改',exact:true}).click()
 const edit=page.getByRole('dialog')
 const historyEntry=page.getByRole('button',{name:'观看历程',exact:true})
 await historyEntry.waitFor();assert.equal(historyReads.length,0)
 for(const [width,height]of[[320,568],[844,390]]){
  await page.setViewportSize({width,height});await hit(historyEntry);await hit(page.getByRole('button',{name:/保存全部修改/}));
  assert.equal(await edit.evaluate(el=>el.scrollWidth<=el.clientWidth),true)
 }
 await page.setViewportSize({width:1440,height:950})
 const title=page.locator('input[name="title"]');await title.fill('还没保存的作品名称')
 assert.equal(await historyEntry.isDisabled(),true)
 await page.getByText('请先保存当前作品修改，再打开观看历程；历程操作会单独保存。',{exact:true}).waitFor()
 await title.fill('合成观看历程');await historyEntry.click();await button('记录进度').waitFor()
 assert.equal(await panel().locator('.watch-body').evaluate(el=>getComputedStyle(el).animationName),'none')
 assert.equal(posts.length,0)
 await button('写随记').click();await field('随记内容').fill('未保存的私人草稿')
 await button('补录往事').click();await button('继续编辑').click();assert.equal(await field('随记内容').inputValue(),'未保存的私人草稿')
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();await button('继续编辑').click()
 await button('取消填写').click();await button('放弃草稿并取消本次填写').click();assert.equal(posts.length,0)
 await button('写随记').click();await field('随记内容').fill('第一条观看随记')
 for(const [width,height]of[[390,740],[320,568],[844,390]]){
  await page.setViewportSize({width,height});await hit(field('随记内容'));await hit(button('保存这条记录'));await hit(button('取消填写'))
  assert.equal(await panel().evaluate(el=>el.scrollWidth<=el.clientWidth),true)
  await page.screenshot({path:`../.tools/watch-history-qa/note-${width}.png`})
 }
 await page.setViewportSize({width:1440,height:950});await save();await panel().locator('.watch-timeline').getByText('第一条观看随记',{exact:true}).first().waitFor()
 assert.equal(events.at(-1).occurred_on,'')
 await button('补录往事').click();await field('随记内容').fill('只记得大致月份的往事')
 await button('保存这条记录').click();await panel().getByRole('alert').getByText('补录往事需要填写实际日期或大致年月。',{exact:true}).waitFor()
 await page.waitForFunction(()=>document.activeElement?.classList.contains('watch-error'))
 await field('回忆发生日期精度').selectOption('month');await field('回忆发生日期').fill('2009-03');await save()
 assert.equal(record.watched_eps,2)
 await button('记录进度').click();await field('本轮已看集数').fill('5');await save()
 assert.equal(record.watched_eps,5);await panel().getByText('在看 · 5 / 12 集',{exact:true}).waitFor()
 await button('标记完成').click();await field('本轮已看集数').fill('12');await field('观看日期精度').selectOption('day');await field('观看日期').fill('2020-02-29');await save()
 await button('开始重看').click();await field('重看开始日期精度').selectOption('year');await field('重看开始日期').fill('2026');await field('随记内容').fill('再次观看')
 await page.screenshot({path:'../.tools/watch-history-qa/rewatch-desktop.png'})
 const before=posts.length;await button('确认开始重看').evaluate(el=>{el.click();el.click()});await panel().locator('.watch-form').waitFor({state:'detached'})
 assert.equal(posts.length,before+1);assert.equal(rounds[0].status,'completed');assert.equal(record.watched_eps,0);assert.equal(record.personal_score,'9.0')
 await panel().locator('.watch-timeline').getByText('实际日期：2026年',{exact:true}).waitFor()
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();await panel().waitFor({state:'detached'})
 await page.getByRole('button',{name:'修改',exact:true}).click();await page.getByRole('button',{name:'观看历程',exact:true}).click();await button('记录进度').waitFor()
 await panel().getByText('在看 · 0 / 12 集',{exact:true}).waitFor()
 await button('撤销最近一次操作').click();await button('确认撤销最近操作').click();await panel().locator('.watch-form').waitFor({state:'detached'})
 assert.equal(current().number,1);assert.equal(rounds[1].voided,true);assert.equal(record.watch_status,'completed')
 await button('更正第 1 轮').click();await field('开始日期精度').selectOption('month');await field('开始日期').fill('2020-01');await field('轮次评分').fill('8.5');await save()
 assert.equal(record.personal_score,'9.0');assert.equal(rounds[0].rating,'8.5')
 await button('写随记').click();await field('随记内容').fill('冲突后保留的草稿');conflict=true;await button('保存这条记录').click();await panel().getByRole('alert').getByText('草稿已保留。请重新读取，再核对后继续。',{exact:true}).waitFor()
 assert.equal(await field('随记内容').inputValue(),'冲突后保留的草稿')
 await button('重新读取历程').click();await button('核对后使用最新版本').waitFor();assert.equal(await button('保存这条记录').isDisabled(),true)
 await button('核对后使用最新版本').click();await save()
 await button('写随记').click();await field('随记内容').fill('结果确认不会重复');unknown=true;await button('保存这条记录').click();await button('重试原操作并核对结果').waitFor()
 const attempts=posts.length;await page.waitForTimeout(500);assert.equal(posts.length,attempts)
 await button('重新读取历程').click();await button('重试原操作并核对结果').waitFor()
 await button('重试原操作并核对结果').click();await panel().locator('.watch-form').waitFor({state:'detached'})
 assert.deepEqual(posts.at(-1),posts.at(-2));assert.equal(events.filter(e=>e.note==='结果确认不会重复').length,1)
 for(let i=0;i<15;i++)append('note',state(current()),current(),`分页随记${i}`)
 await button('重新读取历程').click();await button('下一页历程').first().waitFor();await button('下一页历程').first().click()
 await page.waitForFunction(()=>document.querySelector('.watch-pagination')?.textContent.includes('2 /'))
 assert.equal(await button('撤销最近一次操作').count(),0)
 await page.screenshot({path:'../.tools/watch-history-qa/timeline-desktop.png'})
 readFailure=true;await button('重新读取历程').click();await panel().getByRole('alert').getByText('合成读取失败',{exact:true}).waitFor()
 await button('重新读取历程').click();await button('写随记').waitFor()
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();await panel().waitFor({state:'detached'})
 await page.evaluate(()=>{localStorage.setItem('anime-dashboard:journal-display-mode:v1:1','preview')})
 await page.reload();await page.getByRole('button',{name:'详情',exact:true}).click()
 assert.equal(await panel().getByRole('button',{name:/观看历程/}).getAttribute('aria-expanded'),'false')
 await panel().getByRole('button',{name:/观看历程/}).click();await button('前往编辑模式管理').waitFor()
 assert.equal(await button('写随记').count(),0)
 await button('前往编辑模式管理').click();await button('写随记').waitFor()
 await button('写随记').click();await field('随记内容').fill('切换账号后应隐藏')
 await page.evaluate(()=>{localStorage.setItem('anime_journal_access_token','synthetic-other-account');window.dispatchEvent(new Event('storage'))})
 await page.waitForFunction(()=>!document.querySelector('.watch-workshop textarea'))
 assert.equal(await page.getByText('切换账号后应隐藏',{exact:true}).count(),0)
 assert.equal(await page.getByRole('textbox',{name:'随记内容',exact:true}).count(),0)
 const reads=historyReads.length
 await page.goto(`${base}/shared/QA`);await page.getByRole('button',{name:'详情',exact:true}).click();await page.getByRole('button',{name:'关闭详情',exact:true}).waitFor()
 assert.equal(await panel().count(),0);assert.equal(historyReads.length,reads)
 await page.goto(`${base}/featured`);await page.getByText('合成精选',{exact:true}).click();await page.getByRole('button',{name:'关闭详情',exact:true}).waitFor()
 assert.equal(await panel().count(),0);assert.equal(historyReads.length,reads)
 assert.deepEqual(errors,[])
 console.log(JSON.stringify({result:'passed',checks:['edit entry protects work draft','lazy preview entry','draft guards for switch/cancel/close','unknown and partial dates','note and memory','progress canonical record sync','completed round and rewatch','double submission gate','undo rewatch','independent round score','409 reread and explicit rebase','500 exact-operation retry','timeline pagination','mobile/landscape hit targets','account isolation','public privacy'],writes:posts.length,historyReads:historyReads.length}))
}catch(error){console.error(JSON.stringify({pageErrors:errors,posts:posts.length,reads:historyReads.length}));await page.screenshot({path:'../.tools/watch-history-qa/failure.png'}).catch(()=>{});throw error}
finally{await browser.close()}
