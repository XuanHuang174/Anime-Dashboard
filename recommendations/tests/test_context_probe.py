"""Foreground hint uses only own inputs, not ranking or public feature scans."""
from unittest.mock import patch
from django.db import connection, DatabaseError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITransactionTestCase

from anime.models import UserAnimeRecord
from recommendations.context_probe import CONTEXT_KEYS
from recommendations.models import RecommendationAnime, RecommendationPreference, UserRecommendationFeedbackState
from recommendations.tests import test_profile_sampling as fixtures

URL = '/api/recommendations/context/'


@override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=True)
class ContextProbeTests(APITransactionTestCase):
    setUp = fixtures.ProfileSamplingAPITests.setUp
    seed = fixtures.ProfileSamplingAPITests.seed
    candidates = fixtures.ProfileSamplingAPITests.candidates
    save = fixtures.ProfileSamplingAPITests.save

    def context(self):
        result = self.client.get(URL)
        self.assertEqual(result.status_code, 200, result.data)
        return result.data

    def test_authenticated_strict_readonly_shape_and_no_private_fields(self):
        for identity in (1, 2, 3): self.seed(identity, 9)
        before = list(UserAnimeRecord.objects.values())
        with (patch('recommendations.engine.rank_context', side_effect=AssertionError('Never rank')),
              patch('recommendations.engine.rank_candidates', side_effect=AssertionError('Never rank')),
              patch('recommendations.engine.public_features', side_effect=AssertionError('Never read public index')),
              patch('recommendations.engine.build_feature_index', side_effect=AssertionError('Never build public index')),
              CaptureQueriesContext(connection) as queries):
            result = self.client.get(URL)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(set(result.data), {'contract_version', 'context', 'rollout_revision', 'browse_enabled', 'content_scope_context'})
        self.assertEqual(set(result.data['context']), set(CONTEXT_KEYS))
        self.assertLessEqual(len(queries), 10)
        self.assertFalse(any('"review"' in row['sql'] for row in queries))
        self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for row in queries))
        self.assertIn('private', result['Cache-Control']); self.assertIn('no-store', result['Cache-Control'])
        self.assertIn('Authorization', result['Vary'])
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertFalse(RecommendationPreference.objects.exists())
        self.assertFalse(UserRecommendationFeedbackState.objects.exists())
        for path in (URL+'?user_id=2', URL+'?algorithm_version=content.v2', URL+'?a=1&a=2'):
            self.assertEqual(self.client.get(path).status_code, 400)
        self.assertEqual(self.client.post(URL, {}, format='json').status_code, 405)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_initial_and_continued_pages_share_the_same_probe_profile_revision(self):
        for identity in (1, 2, 3): self.seed(identity, 9)
        self.candidates()
        for category in ('all', 'familiar', 'explore'):
            page = self.client.get('/api/recommendations/', {'browse': True, 'category': category})
            self.assertEqual(page.status_code, 200, page.data)
            expected = {key: page.data['evidence_context'][key] for key in CONTEXT_KEYS}
            self.assertEqual(self.context()['context'], expected)
            if page.data['next_page_cursor']:
                second = self.client.get('/api/recommendations/', {'cursor': page.data['next_page_cursor']})
                self.assertEqual(second.status_code, 200, second.data)
                self.assertEqual(self.context()['context'], {key: second.data['evidence_context'][key] for key in CONTEXT_KEYS})

    def test_personal_score_tags_identity_and_source_safety_changes_are_detected_without_writes(self):
        records = [self.seed(identity, 9) for identity in (1, 2, 3)]
        current = self.context()['context']['profile_version']
        for changes in ({'personal_score': 7}, {'custom_tags': ['治愈']}, {'watch_status': 'on_hold'}):
            UserAnimeRecord.objects.filter(pk=records[0].pk).update(**changes)
            latest = self.context()['context']['profile_version']
            self.assertNotEqual(latest, current); current = latest
        anime = records[1].anime
        anime.bgm_id = None; anime.save(update_fields=['bgm_id'])
        latest = self.context()['context']['profile_version']
        self.assertNotEqual(latest, current)
        RecommendationAnime.objects.filter(bangumi_id=3).update(nsfw=None)
        self.assertNotEqual(self.context()['context']['profile_version'], latest)

    def test_preferences_feedback_gate_and_rollout_changes_are_independent_and_other_users_do_not_pollute(self):
        for identity in (1, 2, 3): self.seed(identity, 9)
        before = self.context()
        self.seed(4, 10, user=self.other)
        self.assertEqual(self.context(), before)
        self.assertEqual(self.save('7.0', tags=['音乐']).status_code, 200)
        after = self.context()
        self.assertNotEqual(after['context']['profile_version'], before['context']['profile_version'])
        self.assertEqual(after['context']['preference_version'], 1)
        UserRecommendationFeedbackState.objects.create(user=self.owner, feedback_version=2)
        feedback = self.context()
        self.assertEqual(feedback['context']['feedback_version'], 2)
        self.assertEqual(feedback['context']['profile_version'], after['context']['profile_version'])
        with override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=False):
            self.assertEqual(self.context()['context']['profile_min_score'], '8.5')
        with override_settings(RECOMMENDATION_BROWSE_EPOCH='changed'):
            self.assertNotEqual(self.context()['rollout_revision'], feedback['rollout_revision'])
        with override_settings(RECOMMENDATION_BROWSE_ENABLED=False):
            self.assertFalse(self.context()['browse_enabled'])
        self.client.force_authenticate(self.other)
        self.assertNotEqual(self.context()['context']['profile_version'], feedback['context']['profile_version'])

    def test_database_failure_is_503_not_changed_or_empty_profile(self):
        with patch('recommendations.context_probe.user_context', side_effect=DatabaseError('synthetic')):
            result = self.client.get(URL)
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('context', result.data)
