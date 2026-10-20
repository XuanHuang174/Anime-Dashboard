import test from 'node:test'
import assert from 'node:assert/strict'
import { subscribeRecommendationReload, notifyRecommendationChange } from '../src/utils/recommendationReload.js'
import { contextChanged } from '../src/utils/recommendationContext.js'
import { createBrowseController } from '../src/utils/recommendationBrowseSession.js'
import { subscribeFeedbackChanges } from '../src/utils/recommendationFeedbackSync.js'
import { preferences, profileData, versions } from './fixtures/profileSampling.js'
import { browsePage } from './fixtures/browseContract.js'

const deferred = () => { let resolve, reject; const promise = new Promise((yes,no) => { resolve=yes; reject=no }); return {promise,resolve,reject} }
const settle = async () => { for(let n=0;n<30;n++) await Promise.resolve() }
const baseline = () => ({context:{...versions('8.5',1),feedback_version:0},rollout_revision:'a'.repeat(64),browse_enabled:true})
const response = (current = baseline()) => ({contract_version:'recommendations.context.v1',...current})
function events() {
  const target=new EventTarget(), document=new EventTarget(), jobs=new Map(); let id=0, clock=0
  document.visibilityState='visible'
  target.setTimeout=fn=>{jobs.set(++id,fn);return id}; target.clearTimeout=key=>jobs.delete(key)
  target.localStorage={setItem(){}}
  return {target,document,now:()=>clock, tick:(value=31000)=>{clock+=value},
    async run(){for(const [key,fn] of [...jobs]){jobs.delete(key);await fn()}await settle()},
    focus(){target.dispatchEvent(new Event('focus'))},
    visibility(value){document.visibilityState=value;document.dispatchEvent(new Event('visibilitychange'))},
    storage(key='anime-dashboard:recommendation-invalidate:v1'){const event=new Event('storage');Object.assign(event,{key});target.dispatchEvent(event)} }
}

test('foreground alone preserves third batch, items, cursors and object identity; unchanged checks are rate limited', async () => {
  const prefs=preferences(), profile=profileData(prefs), context=baseline().context; let reads=0, probes=0
  const controller=createBrowseController({profile:async()=>profile,preferences:async()=>prefs,
    browse:async({cursor})=>{reads++;return {...browsePage({offset:cursor?Number(cursor.split('-')[1]):0,context}),cold_start_state:profile.cold_start_state}}})
  await controller.start({key:'one',category:'all',style:'balanced',filters:{type:'all',minScore:'0',hideRecorded:true,yearFrom:'',yearTo:''}})
  await controller.nextBatch();await controller.nextBatch()
  const before=controller.getSnapshot();assert.equal(before.data.batch,3)
  const env=events(), sync=subscribeRecommendationReload({...env, getContext:baseline,
    probe:async()=>{probes++;return response()},onReload:()=>{throw new Error('Must not reload')}})
  env.focus();env.visibility('hidden');env.visibility('visible');await env.run()
  assert.equal(probes,0)
  env.tick();env.focus();env.focus();env.visibility('visible');await env.run()
  assert.equal(probes,1);env.focus();await env.run();assert.equal(probes,1)
  assert.equal(controller.getSnapshot(),before);assert.equal(reads,3)
  assert.deepEqual(before.items.map(row=>row.bangumiId),[149,150])
  sync.dispose();controller.dispose()
})

test('known changes notify once, defer while editing/hidden, and do not require a rank request',async()=>{
  const env=events();let notifications=0,blocked=true
  const sync=subscribeRecommendationReload({...env,canReload:()=>!blocked,onReload:()=>notifications++})
  env.storage();env.storage();env.focus();await env.run();assert.equal(notifications,0)
  blocked=false;sync.flush();await env.run();assert.equal(notifications,1)
  env.visibility('hidden');notifyRecommendationChange({},env.target);await env.run();assert.equal(notifications,1)
  env.visibility('visible');await env.run();assert.equal(notifications,2)
  env.focus();await env.run();assert.equal(notifications,2);sync.dispose()
})

test('changed personal context is a notification, not automatic replacement; expiry is also explicit',async()=>{
  const env=events(), notifications=[];let current=baseline()
  const sync=subscribeRecommendationReload({...env,getContext:()=>current,
    probe:async()=>response({...current,context:{...current.context,preference_version:2}}),onReload:reason=>notifications.push(reason)})
  env.tick();env.focus();await env.run();await env.run();assert.deepEqual(notifications,['changed'])
  current={...current,expires_at:1};env.focus();await env.run();assert.deepEqual(notifications,['changed','expired'])
  sync.dispose()
})

test('offline, malformed and rolling old server probes preserve the current browse session',async()=>{
  for(const value of [null,{}, {...response(),browse_enabled:undefined},{...response(),context:{...baseline().context,profile_version:'bad'}}]) {
    const env=events();let changes=0
    const sync=subscribeRecommendationReload({...env,getContext:baseline,probe:async()=>value,onReload:()=>changes++})
    env.tick();env.focus();await env.run();await env.run();assert.equal(changes,0);sync.dispose()
  }
  for(const status of [404,429,503]) {
    const env=events();let changes=0
    const sync=subscribeRecommendationReload({...env,getContext:baseline,probe:async()=>{throw {response:{status}}},onReload:()=>changes++})
    env.tick();env.focus();await env.run();assert.equal(changes,0);sync.dispose()
  }
})

test('late checks cannot affect newer snapshots, acknowledgement, hidden tabs or disposed accounts',async()=>{
  for(const action of ['new-snapshot','acknowledge','hidden','dispose']) {
    const env=events(), pending=deferred();let current=baseline(),changes=0,signal
    const sync=subscribeRecommendationReload({...env,getContext:()=>current,probe:value=>{signal=value;return pending.promise},onReload:()=>changes++})
    env.tick();env.focus();const checking=env.run();await settle()
    if(action==='new-snapshot')current={...current,context:{...current.context,profile_version:'b'.repeat(64)}}
    if(action==='acknowledge')sync.acknowledge()
    if(action==='hidden')env.visibility('hidden')
    if(action==='dispose')sync.dispose()
    pending.resolve(response({...baseline(),context:{...baseline().context,preference_version:9}}))
    await checking;await env.run();assert.equal(changes,0)
    if(action!=='new-snapshot')assert.equal(signal.aborted,true)
    sync.dispose()
  }
})

test('context contract detects only relevant policy/version changes, not optional fields',()=>{
  assert.equal(contextChanged({...response(),extra:'ignored'},baseline()),false)
  for(const patch of [{rollout_revision:'b'.repeat(64)},{browse_enabled:false},
    {context:{...baseline().context,feedback_version:2}},{context:{...baseline().context,profile_min_score:'7.0'}}]) {
    assert.equal(contextChanged({...response(),...patch},baseline()),true)
  }
})

test('opaque notifications verify the current account even inside the throttle window; unrelated accounts never invalidate it',async()=>{
  const env=events();let reads=0,changes=0,remote=baseline()
  const sync=subscribeRecommendationReload({...env,getContext:baseline,probe:async()=>{reads++;return response(remote)},onReload:()=>changes++})
  env.storage();env.storage();await env.run();await env.run()
  assert.equal(reads,1);assert.equal(changes,0)
  remote={...remote,context:{...remote.context,preference_version:2}}
  env.visibility('hidden');env.storage();await env.run();assert.equal(reads,1)
  env.visibility('visible');await env.run();await env.run()
  assert.equal(reads,2);assert.equal(changes,1)
  sync.dispose()
})

test('external invalidation retains ordered third-batch shells, stops stale evidence and cannot append until explicit restart',async()=>{
  const context=versions(), controller=createBrowseController({profile:async()=>({contract_version:'recommendations.3a.v1',eligible_sample_count:3,cold_start:false,tag_summary:[],sample_policy:{minimum_score:'8.5'}}),
    preferences:async()=>({contract_version:'recommendations.3a.v1',tags:[],version:1,available_tags:[]}),
    browse:async({cursor})=>browsePage({offset:cursor?Number(cursor.split('-')[1]):0,context})})
  await controller.start({key:'a',category:'all',style:'balanced',filters:{type:'all',minScore:'0',hideRecorded:true,yearFrom:'',yearTo:''}})
  await controller.nextBatch();await controller.nextBatch();const before=controller.getSnapshot()
  controller.contextChanged();const after=controller.getSnapshot()
  assert.equal(after.data,before.data);assert.equal(after.data.batch,3);assert.equal(after.contextUpdated,true)
  assert.deepEqual(after.items.map(row=>row.bangumiId),before.items.map(row=>row.bangumiId))
  assert.ok(after.items.every(row=>!row.evidenceContext&&!row.evidenceToken&&!row.evidence))
  assert.equal(await controller.loadMore(),false);assert.equal(await controller.nextBatch(),false)
  await controller.retry();assert.equal(controller.getSnapshot().data.batch,1);assert.equal(controller.getSnapshot().contextUpdated,undefined)
  controller.dispose()
})

test('feedback foreground checks can be suppressed without losing a change received while hidden',async()=>{
  const env=events();let reads=0,changes=0
  const dispose=subscribeFeedbackChanges({...env,checkForeground:false,getVersion:()=>1,onChanged:()=>changes++,
    client:{hiddenFeedback:async()=>{reads++;return {contract_version:'recommendations.feedback.v1',feedback_version:2,count:0,results:[],page:1,page_size:1,next_page:null}}}})
  env.focus();await env.run();assert.equal(reads,0)
  env.visibility('hidden');env.storage('anime-dashboard:feedback-invalidate:v1');env.focus();await env.run();assert.equal(reads,0)
  env.visibility('visible');await env.run();assert.equal(reads,1);assert.equal(changes,1)
  env.focus();await env.run();assert.equal(reads,1);dispose()
})
