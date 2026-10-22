"""Actual local PostgreSQL API responses consumed by production JS controllers."""
import shutil
import subprocess

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from rest_framework.renderers import JSONRenderer

from recommendations import content_review_batch as batch
from recommendations.models import RecommendationAnime, RecommendationPreference, RecommendationContentEligibility
from recommendations.tests import test_controlled_serving_m2 as fixtures


@override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True,
    RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True, RECOMMENDATION_ALGORITHM_VERSION='content.v1',
    RECOMMENDATION_V2_BROWSE_ENABLED=False, RECOMMENDATION_BROWSE_ENABLED=True)
class GeneralAdultFrontendContractTests(TransactionTestCase):
    setUp = fixtures.ControlledServingTests.setUp
    control = fixtures.ControlledServingTests.control
    get = fixtures.ControlledServingTests.get

    def test_permanent_batch_review_works_for_ordinary_users_across_filter_pages_detail_and_context(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the general-adult cross-stack check')
        identities = list(range(100000, 100014))
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=identity,
            title_zh=f'Synthetic reviewed work {identity}', nsfw=True, tags='音乐,日常',
            season='2010-01', status_type='OVA', score=8.0) for identity in identities])
        preview = batch.prepare_batch(self.manager, identities)
        batch.approve_batch(actor=self.manager, ticket=preview['ticket'], changes={
            'reason': 'Synthetic contract regression', 'evidence_reference': 'synthetic-contract',
            'days': 0, 'exclusions_confirmed': True, 'general_adult_confirmed': True, 'confirmed': True})
        self.control()
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        bundles = []
        for user in (self.owner, self.other):
            self.assertFalse(user.is_staff)
            self.client.force_authenticate(user)
            conditions = {'browse': True, 'type': 'OVA', 'year_from': 2008, 'year_to': 2012,
                'min_score': '7.0', 'hide_recorded': True, 'page_size': 12}
            first = self.get(**conditions)
            self.assertEqual(first.status_code, 200, first.data)
            self.assertEqual(first.data['count'], 14)
            self.assertIsNone(first.data['content_scope_context']['scope_valid_until'])
            self.assertTrue(first.data['next_page_cursor'])
            more = self.get(cursor=first.data['next_page_cursor'])
            card = first.data['results'][0]
            responses = {'first': first, 'more': more,
                'detail': self.get(f'{card["bangumi_id"]}/', evidence_token=card['evidence_token']),
                'context': self.get('context/'),
                'profile': self.client.get('/api/recommendations/profile/'),
                'preferences': self.client.get('/api/recommendations/preferences/'),
                'capabilities': self.client.get('/api/recommendations/capabilities/')}
            for response in responses.values():
                self.assertEqual(response.status_code, 200, response.data)
            bundles.append({key: response.data for key, response in responses.items()})
        self.assertFalse(RecommendationContentEligibility.objects.exists())
        self.control(False)
        closed = self.get('context/')
        self.assertEqual(closed.status_code, 403)
        payload = {'bundles': bundles, 'closed': {'status': closed.status_code, 'data': closed.data}}
        result = subprocess.run([node, '--input-type=module', '-e', '''
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { controlledScope } from './frontend/src/utils/recommendationContentScope.js';
import { createRecommendationClient } from './frontend/src/api/recommendationClient.js';
import { createBrowseController } from './frontend/src/utils/recommendationBrowseSession.js';
import { mapRecommendationItem } from './frontend/src/utils/recommendations.js';
const payload = JSON.parse(readFileSync(0, 'utf8'));
for (const data of payload.bundles) {
  let lost = 0, closed = false;
  const client = createRecommendationClient({get: async (path, config) => {
    if (path === 'recommendations/') {
      assert.equal(config.params.content_scope, 'include_adult');
      return {data: config.params.cursor ? data.more : data.first};
    }
    const key = path === 'recommendations/context/' ? 'context'
      : path.startsWith('recommendations/' + data.detail.bangumi_id + '/') ? 'detail'
      : path.replace('recommendations/', '').replace('/', '');
    if (['context', 'detail'].includes(key)) assert.equal(config.params.content_scope, 'include_adult');
    if (closed && key === 'context') throw {response: payload.closed};
    assert.ok(Object.hasOwn(data, key), path);
    return {data: data[key]};
  }}).forScope('include_adult', () => lost++);
  const controller = createBrowseController(client);
  await controller.start({key: 'synthetic-adult-filter', category: 'all', style: 'balanced',
    filters: {type: 'OVA', minScore: '7.0', yearFrom: '2008', yearTo: '2012', hideRecorded: true, includeAdult: true}});
  assert.equal(controller.getSnapshot().status, 'ready', controller.getSnapshot().error);
  assert.equal(controller.getSnapshot().items.length, 12);
  assert.equal(controlledScope(controller.getSnapshot().data.evidence_context), true);
  assert.equal(await controller.loadMore(), true);
  assert.equal(controller.getSnapshot().items.length, 14);
  const detail = mapRecommendationItem(await client.detail(data.detail.bangumi_id, undefined, data.first.results[0].evidence_token));
  assert.equal(detail.contentRating, 'adult'); assert.equal(detail.coverUrl, '');
  await client.context(); await client.context(); assert.equal(lost, 0);
  closed = true; await assert.rejects(client.context()); assert.equal(lost, 1);
  controller.dispose();
}
'''], input=JSONRenderer().render(payload), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=settings.BASE_DIR, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
