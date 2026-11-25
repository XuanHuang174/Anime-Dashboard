import test from 'node:test'
import assert from 'node:assert/strict'
import { matchesCustomQuickFilter, quickFilterPayload, emptyQuickFilter, quickFilterDraftError, quickFilterSummary } from '../src/utils/quickFilter.js'
import { createPlanController, validPlanBatch } from '../src/utils/watchPlans.js'

test('private plan quick filters distinguish absence, archive, active phase and priority with AND semantics',()=>{
  const item={score:9,status:'watching',tags:[],rawRecord:{watch_plan_summary:{state:'ongoing',priority:1}}}
  for(const config of [{plan_state:'active'},{plan_state:'ongoing',plan_priority:1,watch_statuses:['watching']}]) assert.equal(matchesCustomQuickFilter(item,config),true)
  for(const config of [{plan_state:'none'},{plan_state:'pending'},{plan_priority:2},{plan_state:'active',score_min:9.5}]) assert.equal(matchesCustomQuickFilter(item,config),false)
  assert.equal(matchesCustomQuickFilter({...item,rawRecord:{watch_plan_summary:{state:'archived',priority:1}}},{plan_state:'active'}),false)
  assert.equal(matchesCustomQuickFilter({...item,rawRecord:{}},{plan_state:'none'}),true)
})
test('a plan-only draft is valid and normalizes nullable priority without changing old tag filters',()=>{
  const form={...emptyQuickFilter(),name:'优先待看',plan_state:'pending',plan_priority:'1'}
  assert.equal(quickFilterDraftError(form),'')
  assert.equal(quickFilterPayload(form).plan_priority,1)
  assert.match(quickFilterSummary(quickFilterPayload(form)),/待开始.*优先看/)
  assert.match(quickFilterDraftError({...form,plan_state:'none'}),/不能/)
  assert.equal(quickFilterPayload(emptyQuickFilter()).plan_priority,null)
})
const record={id:1,title:'合成作品',japanese_title:'',cover:'',watch_status:'plan',watch_revision:0,watched_eps:0,total_eps:0}
const plan={version:1,priority:1,position:0,planned_for:'',note:'',purpose:'first',state:'pending',target_round_number:1,anchor_round_number:1,history_base:0,enrolled_at:'2026-11-25T00:00:00Z'}
const queue=version=>({contract_version:'journal.watch-plans.v1',version,count:1,page:1,page_size:20,counts_scope:'owner_all_plans',counts:{pending:1,ongoing:0,completed:0,archived:0},results:[{record,plan}]})
const batch=()=>({contract_version:'journal.watch-plan-batch.v1',version:2,record_ids:[1],results:[{record,plan}]})
test('batch acknowledgements require every explicitly selected member and complete private rows',()=>{
  assert.equal(validPlanBatch(batch(),[1]),true)
  assert.equal(validPlanBatch({...batch(),results:[]},[1]),false)
  assert.equal(validPlanBatch({...batch(),record_ids:[2]},[1]),false)
  assert.equal(validPlanBatch({...batch(),results:[{record,plan:null}]},[1]),false)
})
test('lost batch acknowledgement retains exact selection, request and UUID across reread and retry',async()=>{
  const calls=[];let fail=true
  const engine=createPlanController({getSession:()=> 'owner',uuid:()=> 'fixed-id',client:{get:async()=>({data:queue(fail?1:2)}),post:async(path,body)=>{calls.push({path,body});if(fail){fail=false;throw new Error('lost response')}return {data:batch()}}}})
  await engine.read()
  assert.equal(await engine.submit({action:'update',version:1,record_ids:[1],priority:1}),false)
  await engine.read()
  assert.equal(await engine.submit({action:'remove',version:2,record_ids:[1]}),false)
  assert.equal(await engine.submit(null,true),true)
  assert.equal(calls[0].path,'watch-plans/batch/');assert.deepEqual(calls[0],calls[1])
  assert.equal(engine.getSnapshot().needsRead,true)
})
