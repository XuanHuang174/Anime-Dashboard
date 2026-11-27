import test from 'node:test'
import assert from 'node:assert/strict'
import {loadReviewExport,reviewExportBlocks,pngArchive,crc32} from '../src/utils/watchReviewExport.js'
const summary={started_works:0,started_rounds:0,completed_works:101,completed_rounds:101,first_completed_rounds:101,rewatch_started_rounds:0,rewatch_completed_rounds:0,notes:0,memories:0,viewed_works:101,related_works:101,activity_facts:101,completed_round_rating:{count:0,missing_count:101,average:null,basis:'effective_completed_round'}}
const source={contract_version:'journal.watch-review.v1',policy_version:'actual-date-contained.v1',counts_scope:'owner_all_records_selected_period',live:true,metadata_basis:'current_journal',generated_at:'2026-11-28T00:00:00Z',period:{mode:'month',start:'2026-10-01',end:'2026-10-31',timezone:'Asia/Shanghai',end_inclusive:true},snapshot:'proof',summary,coverage:{uncertain:{facts:2,works:1},undated:{facts:2,works:1},undated_scope:'owner_all_history',uncertain_scope:'overlaps_selected_period',includes_undated_in_summary:false,includes_uncertain_in_summary:false}}
const row=id=>({entry_type:'work',work:{record_id:id,title:`作品${id}`,original_title:'',airing_period:'2020-1',poster_url:'',current_score:null,current_watch_status:'completed',metadata_basis:'current_journal'},matched_fact_count:1,matched_kinds:{completed:1},latest_actual_date:'2026-10'})
function client(change=()=>{}){return{get:async(path,{params:q})=>{if(path==='watch-review/')return{data:source};const data={...source,section:'works',page:q.page,page_size:100,count:101,count_unit:'works',next_page:q.page===1?2:null,results:q.page===1?Array.from({length:100},(_,i)=>row(i+1)):[row(101)]};change(data,q);return{data}}}}
test('export loads all pages under the original snapshot and checks the source again',async()=>{
 const calls=[];const base=client();const transport={get:(path,config)=>{calls.push([path,config.params]);return base.get(path,config)}}
 const result=await loadReviewExport(transport,source,{works:true},new AbortController().signal)
 assert.equal(result.sections.works.length,101);assert.equal(calls.length,3);assert.equal(calls[1][1].snapshot,'proof');assert.equal(calls[2][0],'watch-review/')
})
test('duplicate, changed, oversized, interrupted and revoked export reads never return partial reports',async()=>{
 await assert.rejects(loadReviewExport(client((d,q)=>{if(q.page===2)d.results=[row(1)]}),source,{works:true},new AbortController().signal),/重复/)
 await assert.rejects(loadReviewExport(client(d=>{d.snapshot='changed'}),source,{works:true},new AbortController().signal),/不完整/)
 const huge={...source,summary:{...summary,related_works:501}}
 await assert.rejects(loadReviewExport(client(d=>{d.count=501;d.summary=huge.summary}),huge,{works:true},new AbortController().signal),/500/)
 const controller=new AbortController();controller.abort()
 await assert.rejects(loadReviewExport(client(),source,{works:true},controller.signal),/取消/)
 await assert.rejects(loadReviewExport({get:async()=>{throw {response:{status:409}}}},source,{works:true},new AbortController().signal))
})
test('private text is opt-in and long thoughts are continued without dropping characters',()=>{
 const note='私人随记🌸'.repeat(300),report={source,sections:{completed:[{work:{title:'作品'},round_number:1,kind:'completed',actual_date:'2026-10',rating:null,note}]}}
 assert.equal(reviewExportBlocks(report,{completed:true}).some(b=>b.text.includes('私人')),false)
 const blocks=reviewExportBlocks(report,{completed:true,notes:true}).filter(b=>b.title.startsWith('作品'))
 assert.equal(blocks.map(b=>b.text.slice(b.text.indexOf('\n')+1)).join(''),note)
 assert.ok(blocks.length>1)
})
test('ZIP STORE contains UTF-8 names, correct CRC and directory record counts',async()=>{
 assert.equal(crc32(new TextEncoder().encode('123456789')),0xcbf43926)
 const zip=await pngArchive([{name:'第一张.png',blob:new Blob(['one'])},{name:'第二张.png',blob:new Blob(['two'])}])
 const bytes=await zip.arrayBuffer(),view=new DataView(bytes)
 assert.equal(view.getUint32(0,true),0x04034b50);assert.equal(view.getUint16(6,true),0x800)
 assert.equal(view.getUint32(bytes.byteLength-22,true),0x06054b50);assert.equal(view.getUint16(bytes.byteLength-12,true),2)
})
