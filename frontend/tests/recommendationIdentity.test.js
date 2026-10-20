import test from 'node:test'
import assert from 'node:assert/strict'
import { identitySummary, validateIdentityReview, validateRecommendationProfile } from '../src/utils/recommendations.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'

test('pending identities are explained rather than presented as zero preferences', () => {
  assert.match(identitySummary({ diagnostics: { high_score_record_count: 77, pending_identity_count: 77 } }), /77 条符合取样门槛/)
  assert.match(identitySummary({ diagnostics: { high_score_record_count: 77, pending_identity_count: 77 } }), /无需重新评分/)
  assert.equal(identitySummary({ diagnostics: { pending_identity_count: 0 } }), '')
  assert.equal(identitySummary(undefined), '')
})

test('candidate contract fails closed on unknown/adult classifications', () => {
  const data={count:1,page:1,next_page:null,results:[{record_id:1,title:'旧作品',identity_version:0,
    candidates:[{bangumi_id:100,title_zh:'标准作品',ticket:'scoped-ticket',content_rating:'non_adult'}]}]}
  assert.equal(validateIdentityReview(data),data)
  for(const classification of ['adult',null,undefined]) {
    assert.throws(()=>validateIdentityReview({...data,results:[{...data.results[0],candidates:[{...data.results[0].candidates[0],content_rating:classification}]}]}))
  }
})

test('confirmation forwards scoped ticket and version to the private identity endpoint only', async () => {
  const calls=[]
  const client=createRecommendationClient({ get:async (...args)=>{calls.push(args);return {data:{}}},
    patch:async (...args)=>{calls.push(args);return {data:{}}} })
  const signal=new AbortController().signal
  await client.identities({page:1},signal)
  await client.confirmIdentity({record_id:1,bangumi_id:100,ticket:'signed',expected_version:0,confirmed:true},signal)
  assert.equal(calls[0][0],'recommendations/identities/')
  assert.equal(calls[1][0],'recommendations/identities/')
  assert.equal(calls[1][2].signal,signal)
  assert.equal(calls[1][1].expected_version,0)
})
