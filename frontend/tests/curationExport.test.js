import test from 'node:test'
import assert from 'node:assert/strict'
import { curationExportBlocks, curationExportFilename, loadCurationExport, validCurationExport, verifyCurationExport } from '../src/utils/curationExport.js'
import { validCurationReceipt } from '../src/utils/curations.js'
const uuid=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`
function source(count=101) {
  const entries=Array.from({length:count},(_,i)=>({id:uuid(i+2),position:i,reason:'私人入选理由🌸',title_snapshot:{title:`作品${i}`,original_title:'',period:'2026-01'},unavailable:false,
    record:{id:i+1,title:`作品${i}`,japanese_title:'',period:'2026-01',cover:'https://example.com/cover.jpg',personal_score:'9.0',watch_status:'completed',watched_eps:12,total_eps:12,watch_revision:1}}))
  return {contract_version:'journal.curation-export.v1',version:3,snapshot:'signed-proof',generated_at:'2026-11-28T00:00:00Z',
    collection:{id:uuid(1),title:'雨天/慢慢看',description:'私人专题简介',pinned:false,archived:false,position:0,cover_entry_id:null,cover:entries[0]?.record||null,entry_count:count,unavailable_count:0},entries}
}
test('collection export validates all entries independently of the visible page and keeps private fields opt-in',async()=>{
  const data=source(),calls=[]
  const result=await loadCurationExport({get:async(path,config)=>{calls.push([path,config.params]);return{data}}},{id:uuid(1),version:3},{},new AbortController().signal,()=>true)
  assert.equal(result.entries.length,101);assert.equal(calls.length,1);assert.deepEqual(calls[0][1],{version:3})
  const blocks=curationExportBlocks(result,{works:true}),text=JSON.stringify(blocks)
  assert.equal(blocks.length,101);assert.ok(text.includes('作品100'));assert.equal(text.includes('私人'),false);assert.equal(text.includes('9.0'),false);assert.equal(text.includes('12/12'),false)
  assert.equal(curationExportFilename(result),'雨天_慢慢看_私人片单')
})
test('private text continuations retain Unicode exactly, with optional score/progress and unavailable placeholders',()=>{
  const data=source(1),note='私人理由🌸'.repeat(300);data.entries[0].reason=note
  const blocks=curationExportBlocks(data,{works:true,reasons:true,score:true,status:true,intro:true})
  assert.ok(blocks[0].text.includes('私人专题简介'))
  const joined=blocks.slice(1).map(block=>block.text.split('入选理由：')[1]).join('');assert.equal(joined,note)
  assert.match(blocks[1].text,/个人评分：9.0/);assert.match(blocks[1].text,/12\/12/)
  data.entries[0].record=null;data.entries[0].unavailable=true;data.collection.unavailable_count=1
  assert.equal(validCurationExport(data,uuid(1)),true);assert.match(curationExportBlocks(data,{works:true})[0].text,/已从私人手账移除/)
})
test('verification rejects changed data under the same proof but accepts fresh artwork URL timestamps',async()=>{
  const data=source(1),changed=structuredClone(data);changed.entries[0].reason='被修改'
  await assert.rejects(verifyCurationExport({get:async()=>({data:changed})},data,new AbortController().signal,()=>true),/变化/)
  const fresh=structuredClone(data);fresh.generated_at='2026-11-28T01:00:00Z';fresh.entries[0].record.cover='https://example.com/new-signed-url';fresh.collection.cover.cover='https://example.com/new-signed-url'
  await verifyCurationExport({get:async()=>({data:fresh})},data,new AbortController().signal,()=>true)
  fresh.entries.pop();assert.equal(validCurationExport(fresh,uuid(1)),false)
})
test('invalid source, cancellation and revoked proof never return partial export success',async()=>{
  const data=source(2);data.entries[1].id=data.entries[0].id
  await assert.rejects(loadCurationExport({get:async()=>({data})},{id:uuid(1),version:3},{},new AbortController().signal,()=>true),/不完整/)
  const stop=new AbortController();stop.abort()
  await assert.rejects(loadCurationExport({}, {id:uuid(1),version:3},{},stop.signal,()=>true),/取消/)
  await assert.rejects(verifyCurationExport({get:async()=>{throw{response:{status:409}}}},source(),new AbortController().signal,()=>true))
})
test('atomic creation receipts accept the two scoped member-creation actions without weakening target checks',()=>{
  const data=source(1),receipt={contract_version:'journal.curations.v1',version:4,replayed:false,list_id:uuid(1),collection:data.collection}
  for(const action of ['create-with-members','create-from-review'])assert.equal(validCurationReceipt(receipt,{action,version:3}),true)
  assert.equal(validCurationReceipt({...receipt,list_id:uuid(2)},{action:'add',version:3,list_id:uuid(1)}),false)
})
