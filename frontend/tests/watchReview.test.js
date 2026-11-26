import test from 'node:test'
import assert from 'node:assert/strict'
import { createReviewController, currentReviewMonth, reviewParams, reviewPeriod, shiftReviewMonth, validReviewEntries } from '../src/utils/watchReview.js'

const params = { month: '2026-10' }
const summary = { started_works: 15, started_rounds: 15, completed_works: 15, completed_rounds: 16, first_completed_rounds: 15,
  rewatch_started_rounds: 0, rewatch_completed_rounds: 1, notes: 1, memories: 0, viewed_works: 15, related_works: 15, activity_facts: 31,
  completed_round_rating: { count: 1, missing_count: 15, average: '9.00', basis: 'effective_completed_round' } }
const coverage = { uncertain: { facts: 1, works: 1 }, undated: { facts: 1, works: 1 }, undated_scope: 'owner_all_history',
  uncertain_scope: 'overlaps_selected_period', includes_undated_in_summary: false, includes_uncertain_in_summary: false }
const work = id => ({ record_id: id, title: `合成作品 ${id}`, original_title: '', airing_period: '2020-1', poster_url: '', current_score: '9.0', current_watch_status: 'completed', metadata_basis: 'current_journal' })
function response(query = {}, token = 'snapshot-one') {
  const section = query.section || 'works', page = query.page || 1, size = query.page_size || 12
  const dateParams=query.start?{start:query.start,end:query.end}:{month:query.month||params.month}
  const period=reviewPeriod(dateParams),actualDate=query.start || dateParams.month
  const count = { works: 15, started: 15, completed: 16, rewatch: 1, activity: 31, notes: 1, uncertain: 1, undated: 1 }[section]
  return { contract_version: 'journal.watch-review.v1', policy_version: 'actual-date-contained.v1', period,
    snapshot: token, counts_scope: 'owner_all_records_selected_period', live: true, metadata_basis: 'current_journal', generated_at: '2026-11-26T00:00:00Z',
    summary: structuredClone(summary), coverage: structuredClone(coverage), section, page, page_size: size, count,
    count_unit: section === 'works' ? 'works' : 'facts', next_page: page * size < count ? page + 1 : null,
    results: Array.from({ length: Math.min(size, Math.max(0, count - (page - 1) * size)) }, (_, index) => {
      const id = (page - 1) * size + index + 1
      return section === 'works' ? { entry_type: 'work', work: work(id), matched_fact_count: 1, matched_kinds: { completed: 1 }, latest_actual_date: actualDate }
        : { entry_type: 'fact', id: `fact:${id}`, record_id: id, work: work(id), kind: section === 'notes' ? 'note' : section === 'started' ? 'started' : 'completed', round_number: 1,
          actual_date: section === 'undated' ? '' : section === 'uncertain' ? '2026' : actualDate, date_precision: section === 'undated' ? 'unknown' : section === 'uncertain' ? 'year' : query.start?'day':'month',
          date_bounds: section === 'undated' ? null : section === 'uncertain' ? { start: '2026-01-01', end: '2026-12-31' } : { start: period.start, end: query.start?period.start:period.end },
          attribution: ['uncertain', 'undated'].includes(section) ? section : 'definite', is_rewatch: section === 'rewatch', note: '合成感想', date_source: 'explicit_event', rating: null, rating_origin: '' }
    }) }
}
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b }); return { promise, resolve, reject } }
function env(client) { let session = 'owner'; return { engine: createReviewController({client, getSession:()=>session, initialParams:params}), switch:()=>{session='other'} } }
const options = { params, section:'works', page:1, pageSize:12 }

test('Shanghai month, month stepping and inclusive custom dates avoid local timezone and leap-year drift',()=>{
  assert.equal(currentReviewMonth(new Date('2026-10-31T18:00:00Z')), '2026-11')
  assert.equal(shiftReviewMonth('2026-01', -1), '2025-12')
  assert.equal(shiftReviewMonth('2026-12', 1), '2027-01')
  assert.throws(()=>shiftReviewMonth('9999-12',1))
  assert.deepEqual(reviewParams({mode:'month',month:'2024-02'}), {month:'2024-02'})
  assert.equal(reviewPeriod({month:'2024-02'}).end, '2024-02-29')
  assert.deepEqual(reviewParams({mode:'custom',start:'2024-01-01',end:'2024-12-31'}), {start:'2024-01-01',end:'2024-12-31'})
  for(const draft of [{mode:'month',month:'2026-13'},{mode:'custom',start:'2026-02-30',end:'2026-03-01'},
    {mode:'custom',start:'2026-10-20',end:'2026-10-10'},{mode:'custom',start:'2024-01-01',end:'2025-01-01'}]) assert.throws(()=>reviewParams(draft))
})
test('contracts reject missing summaries, duplicate works, mixed snapshot counts and invented dates',()=>{
  assert.equal(validReviewEntries(response(),options),true)
  const bad=[]
  bad.push({...response(),summary:{}})
  const duplicate=response();duplicate.results[1]=duplicate.results[0];bad.push(duplicate)
  const partial=response();partial.results.pop();bad.push(partial)
  const counts=response();counts.summary.completed_rounds=99;bad.push(counts)
  bad.push({...response(),period:reviewPeriod({month:'2026-11'})})
  for(const data of bad) assert.equal(validReviewEntries(data,options),false)
  const coarse=response({section:'uncertain'});assert.equal(validReviewEntries(coarse,{...options,section:'uncertain'}),true)
  coarse.results[0].date_bounds.start='2026-10-01';assert.equal(validReviewEntries(coarse,{...options,section:'uncertain'}),false)
  const unknown=response({section:'undated'});assert.equal(validReviewEntries(unknown,{...options,section:'undated'}),true)
  unknown.results[0].actual_date='2026-10-01';assert.equal(validReviewEntries(unknown,{...options,section:'undated'}),false)
  const changed=response();changed.summary.notes=9
  assert.equal(validReviewEntries(changed,{...options,snapshot:'snapshot-one',baseline:response()}),false)
  const reordered=response();reordered.summary=Object.fromEntries(Object.entries(reordered.summary).reverse())
  assert.equal(validReviewEntries(reordered,{...options,snapshot:'snapshot-one',baseline:response()}),true)
})
test('one atomic entries read supplies full summary; paging and sections share original snapshot',async()=>{
  const calls=[];const e=env({get:async(path,{params:q})=>{calls.push({path,q});return {data:response(q)}}})
  assert.equal(await e.engine.read(),true)
  assert.equal(calls[0].path,'watch-review/entries/')
  assert.equal(e.engine.getSnapshot().data.summary.completed_works,15)
  await e.engine.read({page:2});assert.equal(calls[1].q.snapshot,'snapshot-one')
  assert.equal(e.engine.getSnapshot().data.results.length,3)
  await e.engine.read({section:'notes'});assert.equal(calls[2].q.snapshot,'snapshot-one')
  assert.equal(e.engine.getSnapshot().data.results[0].kind,'note')
  assert.equal(calls.length,3)
})
test('latest request wins, previous reads abort, and old responses never reach another account',async()=>{
  const a=deferred(),b=deferred(),calls=[]
  const e=env({get:(_path,config)=>{calls.push(config);return calls.length===1?a.promise:b.promise}})
  const old=e.engine.read(),fresh=e.engine.read({params:{month:'2026-11'}})
  assert.equal(calls[0].signal.aborted,true)
  b.resolve({data:response({month:'2026-11'})});await fresh
  a.resolve({data:response()});await old
  assert.equal(e.engine.getSnapshot().data.period.start,'2026-11-01')
  e.switch();await e.engine.refresh();assert.equal(calls.length,2)
})
test('409 discards cohort and returns to the same section first page with a bounded fresh retry',async()=>{
  let phase=0;const calls=[]
  const e=env({get:async(_path,{params:q})=>{calls.push(q);if(phase===1 && q.snapshot)throw {response:{status:409}}
    return {data:response(q,phase===1?'snapshot-two':'snapshot-one')}}})
  await e.engine.read();phase=1
  await e.engine.read({page:2})
  assert.equal(calls.length,3);assert.equal(calls[2].snapshot,undefined);assert.equal(calls[2].page,1)
  assert.equal(e.engine.getSnapshot().data.snapshot,'snapshot-two')
  assert.match(e.engine.getSnapshot().message,/已更新/)
})
test('repeated conflicts and malformed replies show an error without stale or partial results',async()=>{
  let calls=0;const e=env({get:async()=>{calls++;throw {response:{status:409}}}})
  assert.equal(await e.engine.read(),false);assert.equal(calls,2)
  assert.equal(e.engine.getSnapshot().data,null);assert.equal(e.engine.getSnapshot().status,'error')
  const malformed=env({get:async()=>({data:{...response(),summary:{}}})})
  assert.equal(await malformed.engine.read(),false);assert.equal(malformed.engine.getSnapshot().data,null)
})
test('refresh preserves applied period and section while new dates start a new cohort',async()=>{
  const calls=[];const e=env({get:async(_path,{params:q})=>{calls.push(q);return {data:response(q)}}})
  await e.engine.read({section:'notes'});await e.engine.refresh()
  assert.equal(calls[1].section,'notes');assert.equal(calls[1].month,'2026-10');assert.equal(calls[1].snapshot,undefined)
  assert.equal(await e.engine.read({params:{month:'2026-11'}}),true)
  assert.equal(calls[2].snapshot,undefined);assert.equal(e.engine.getSnapshot().section,'notes')
})
test('deactivation cancels reads, and a late response cannot repopulate a disposed workspace',async()=>{
  const pending=deferred();let signal
  const e=env({get:(_path,config)=>{signal=config.signal;return pending.promise}})
  const read=e.engine.read();e.engine.dispose();assert.equal(signal.aborted,true)
  pending.resolve({data:response()});await read
  assert.equal(e.engine.getSnapshot().data,null)
})
test('a response arriving after an account switch is discarded even without an abort',async()=>{
  const pending=deferred();const e=env({get:()=>pending.promise})
  const read=e.engine.read();e.switch();pending.resolve({data:response()});await read
  assert.equal(e.engine.getSnapshot().data,null)
})
