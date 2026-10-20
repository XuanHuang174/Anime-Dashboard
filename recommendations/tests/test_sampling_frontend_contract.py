"""P2 real isolated PostgreSQL responses consumed by production JS validators."""
import shutil
import subprocess

from django.conf import settings
from django.test import override_settings
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APITransactionTestCase

from recommendations.tests import test_profile_sampling as fixtures


@override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=True)
class SamplingFrontendContractTests(APITransactionTestCase):
    setUp = fixtures.ProfileSamplingAPITests.setUp
    seed = fixtures.ProfileSamplingAPITests.seed
    candidates = fixtures.ProfileSamplingAPITests.candidates
    save = fixtures.ProfileSamplingAPITests.save

    def validate_in_frontend(self, score):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the P2 cross-stack contract check')
        prefs = self.client.get(fixtures.PREFERENCES)
        profile = self.client.get(fixtures.PROFILE)
        preview = self.client.get(fixtures.PREVIEW, {'profile_min_score': score})
        page = self.client.get('/api/recommendations/')
        for response in (prefs, profile, preview, page):
            self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(page.data['results'])
        row = page.data['results'][0]
        detail = self.client.get(f"/api/recommendations/{row['bangumi_id']}/", {'evidence_token': row['evidence_token']})
        self.assertEqual(detail.status_code, 200, detail.data)
        payload = {key: response.data for key, response in (
            ('preferences', prefs), ('profile', profile), ('preview', preview), ('page', page), ('detail', detail))}
        payload['score'] = score
        context = self.client.get('/api/recommendations/context/')
        self.assertEqual(context.status_code, 200, context.data)
        payload['context'] = context.data
        result = subprocess.run([node, '--input-type=module', '-e', '''
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateRecommendationPreferences, validateRecommendationProfile,
  validateRecommendationSnapshot, mapRecommendationPage, mapRecommendationItem } from './frontend/src/utils/recommendations.js';
import { validateProfilePreview } from './frontend/src/utils/recommendationSampling.js';
import { recommendationEvidence } from './frontend/src/utils/recommendationExplanation.js';
import { contextChanged } from './frontend/src/utils/recommendationContext.js';
const data = JSON.parse(readFileSync(0, 'utf8'));
const prefs = validateRecommendationPreferences(data.preferences);
const profile = validateRecommendationProfile(data.profile);
const page = mapRecommendationPage(data.page);
validateRecommendationSnapshot(profile, prefs, page);
assert.equal(contextChanged(data.context, {context: page.evidence_context}), false);
validateProfilePreview(data.preview, data.score, prefs);
const detail = mapRecommendationItem(data.detail);
assert.deepEqual(recommendationEvidence(detail), recommendationEvidence(page.items[0]));
assert.equal(recommendationEvidence(detail).kind, 'rated_work');
assert.equal(recommendationEvidence(detail).minimumScore, prefs.effective_profile_min_score);
'''], input=JSONRenderer().render(payload), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=settings.BASE_DIR, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))

    def test_saved_low_threshold_preview_list_and_detail_share_real_policy(self):
        for identity in (1, 2, 3):
            self.seed(identity, 7)
        self.candidates()
        for version, score in enumerate(('6.0', '7.0')):
            saved = self.save(score, version)
            self.assertEqual(saved.status_code, 200, saved.data)
            self.validate_in_frontend(score)

    @override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=False)
    def test_disabled_rollout_keeps_configured_and_effective_values_distinct(self):
        for identity in (1, 2, 3):
            self.seed(identity, 9)
        self.candidates()
        self.assertEqual(self.save('7.0').status_code, 200)
        self.validate_in_frontend('7.0')
