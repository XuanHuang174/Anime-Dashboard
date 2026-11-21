import test from 'node:test'
import assert from 'node:assert/strict'
import { loadCompleteQuickFilters, sortedQuickFilters, moveQuickFilter } from '../src/utils/quickFilterOrder.js'
import { emptyQuickFilter, quickFilterPayload, transferQuickTag, quickFilterSummary } from '../src/utils/quickFilter.js'

test('persistent positions order by position then ID while legacy lists keep their input order', () => {
  const rows = [{id:3,sort_order:1},{id:2,sort_order:0},{id:1,sort_order:0}]
  assert.deepEqual(sortedQuickFilters(rows).map(row=>row.id),[1,2,3])
  assert.deepEqual(rows.map(row=>row.id),[3,2,1])
  assert.deepEqual(sortedQuickFilters([{id:3},{id:1}]).map(row=>row.id),[3,1])
})
test('all pages are read from the trusted endpoint without following response URLs', async () => {
  const calls=[],controller=new AbortController()
  const client={get:async(endpoint,options)=>{calls.push({endpoint,...options});return {data:options.params.page===1?{count:3,results:[{id:2,sort_order:1},{id:1,sort_order:0}],next:'https://ignored.invalid/page2'}:{count:3,results:[{id:3,sort_order:2}],next:null}}}}
  const result=await loadCompleteQuickFilters(client,'quick-filters/',controller.signal)
  assert.deepEqual(result.data.map(row=>row.id),[1,2,3])
  assert.deepEqual(calls.map(row=>row.endpoint),['quick-filters/','quick-filters/'])
  assert.equal(calls[1].params.page,2);assert.equal(calls[0].signal,controller.signal)
})
test('incomplete or changing collections fail instead of exposing a truncated order',async()=>{
  for(const responses of [[{count:2,results:[{id:1}],next:null}],[{count:2,results:[{id:1}],next:'next'},{count:2,results:[{id:1}],next:null}],[{count:2,results:[{id:1}],next:'next'},{count:1,results:[{id:2}],next:null}],[{}]]) {
    const queue=[...responses];await assert.rejects(loadCompleteQuickFilters({get:async()=>({data:queue.shift()})},'quick-filters/'))
  }
})
test('arrow and drag permutations are immutable and preserve every existing identity',()=>{
  const ids=[1,2,3,4]
  assert.deepEqual(moveQuickFilter(ids,3,2),[1,3,2,4])
  assert.deepEqual(moveQuickFilter(ids,1,4),[2,3,4,1])
  assert.equal(moveQuickFilter(ids,99,1),ids);assert.equal(moveQuickFilter(ids,1,1),ids)
  assert.deepEqual(ids,[1,2,3,4])
})
test('tag transfer removes the opposite condition and preserves unrelated draft fields',()=>{
  const form={...emptyQuickFilter(),name:'私人草稿',score_min:'9',tags:['日常'],exclude_tags:['治愈']}
  const moved=transferQuickTag(form,'exclude_tags','日常')
  assert.deepEqual(moved.tags,[]);assert.deepEqual(moved.exclude_tags,['治愈','日常'])
  assert.equal(moved.name,'私人草稿');assert.equal(moved.score_min,'9')
  assert.deepEqual(form.tags,['日常'])
  assert.deepEqual(transferQuickTag(moved,'exclude_tags','日常').exclude_tags,['治愈'])
  const full={...form,tags:Array.from({length:8},(_,i)=>String(i))}
  assert.equal(transferQuickTag(full,'tags','治愈'),full)
  assert.equal(quickFilterSummary(quickFilterPayload(emptyQuickFilter())),'尚未设置筛选条件')
})
