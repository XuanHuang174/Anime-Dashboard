// Synthetic account only: verify deletion warning and preservation with real E1 API.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const f = JSON.parse(await readFile('../.tools/curations-e1-fixture.json','utf8')), base='http://127.0.0.1:5191'
assert.match(f.username,/^curation-e1-/)
const browser = await chromium.launch({channel:'msedge',headless:true}), context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'})
await context.addInitScript(({token,user_id})=>{localStorage.setItem('anime_journal_access_token',token);localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`,'edit')},f)
const page=await context.newPage(), errors=[], deletes=[];page.setDefaultTimeout(20000)
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='DELETE')deletes.push(r.url())})
const api=async path=>{const r=await context.request.get(base+'/api/'+path,{headers:{Authorization:`Bearer ${f.token}`}});assert.equal(r.status(),200);return r.json()}
try {
  const before=await api(`curations/${f.list_id}/`), member=before.results.find(row=>row.id===f.entry_id)
  await page.goto(base+'/dashboard'); await page.getByRole('button',{name:'修改',exact:true}).first().click()
  if(await page.locator('input[name=title]').inputValue()!=='E1 合成雨天作品') {await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).last().click();await page.getByRole('dialog').waitFor({state:'detached'});await page.getByRole('button',{name:'修改',exact:true}).last().click()}
  assert.equal(await page.locator('input[name=title]').inputValue(),'E1 合成雨天作品')
  let warning=''
  page.once('dialog',async d=>{warning=d.message();await d.dismiss()})
  await page.getByRole('button',{name:'删除私人记录',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('button:disabled .fa-spinner'))
  assert.match(warning,/1 张私人片单/);assert.match(warning,/保留失效条目、原顺序和入选理由/);assert.equal(deletes.length,0)
  await page.route('**/curation-impact/',route=>route.fulfill({status:503,json:{detail:'synthetic unavailable'}}))
  await page.getByRole('button',{name:'删除私人记录',exact:true}).click();await page.getByRole('alert').filter({hasText:/尚未删除/}).first().waitFor()
  assert.equal(deletes.length,0);await page.unroute('**/curation-impact/')
  page.once('dialog',d=>d.accept())
  await page.getByRole('button',{name:'删除私人记录',exact:true}).click();await page.getByRole('dialog').waitFor({state:'detached'})
  assert.equal(deletes.length,1)
  const after=(await api(`curations/${f.list_id}/`)).results.find(row=>row.id===member.id)
  assert.equal(after.unavailable,true);assert.equal(after.reason,member.reason);assert.equal(after.position,member.position)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({result:'passed',checks:['real curation count warning','dismiss leaves work','unavailable impact blocks delete','confirmed synthetic deletion keeps member/reason/order'],browserErrors:errors}))
} finally {await context.close();await browser.close()}
