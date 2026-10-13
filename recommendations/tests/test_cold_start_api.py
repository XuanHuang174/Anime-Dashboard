from unittest.mock import patch

from django.core.cache import cache
from django.db import DatabaseError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from recommendations.models import RecommendationAnime, RecommendationPreference
from .test_contracts_api import ContractFixtures


class ColdStartAPITests(ContractFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client.force_authenticate(self.user)

    def profile(self):
        response = self.client.get('/api/recommendations/profile/')
        self.assertEqual(response.status_code, 200)
        return response.data

    def state(self):
        data = self.profile()['cold_start_state']
        self.assertEqual(data['contract_version'], 'recommendations.cold-start.v1')
        return data

    def test_empty_without_preferences_is_not_fake_personalization(self):
        state = self.state()
        self.assertEqual(state['code'], 'empty_journal')
        self.assertEqual(state['recommendation_basis'], 'none')
        self.assertEqual(state['journal_record_count'], 0)
        self.assertEqual(self.client.get('/api/recommendations/').data['cold_start_state'], state)

    def test_explicit_preferences_work_for_empty_journal_and_remain_independent(self):
        preference = RecommendationPreference.objects.create(user=self.user, tags=['音乐'], version=4)
        RecommendationAnime.objects.create(bangumi_id=999, title_zh='候选', tags='音乐', nsfw=False, score=8)
        state = self.state()
        response = self.client.get('/api/recommendations/').data
        self.assertEqual(state['code'], 'empty_journal')
        self.assertEqual(state['recommendation_basis'], 'explicit_preferences')
        self.assertEqual(response['mode'], 'preference_based')
        self.assertEqual(response['cold_start_state'], state)
        self.assertEqual(response['results'][0]['evidence']['kind'], 'explicit_preferences')
        preference.refresh_from_db()
        self.assertEqual((preference.tags, preference.version), (['音乐'], 4))

    def test_all_unrated_reports_exact_count_including_other_watch_states(self):
        for identity, status in enumerate(('completed', 'plan', 'watching', 'on_hold'), 1):
            self.seed(identity, score=None, status=status)
        state = self.state()
        self.assertEqual(state['code'], 'all_unrated')
        self.assertEqual(state['unrated_record_count'], 4)
        self.assertEqual(state['rated_record_count'], 0)

    def test_zero_is_rated_and_high_score_watching_does_not_qualify(self):
        self.seed(1, score='0.0')
        self.seed(2, score='8.4')
        self.seed(3, score=None)
        self.seed(4, score='9.9', status='watching')
        state = self.state()
        self.assertEqual(state['code'], 'no_qualifying_high_scores')
        self.assertEqual((state['rated_record_count'], state['unrated_record_count']), (3, 1))
        self.assertEqual(state['high_score_record_count'], 0)

    def test_one_two_samples_then_ready(self):
        for identity in (1, 2, 3):
            self.seed(identity)
            state = self.state()
            self.assertEqual(state['usable_sample_count'], identity)
            self.assertEqual(state['code'], 'ready' if identity == 3 else 'insufficient_samples')
            self.assertEqual(state['is_cold_start'], identity < 3)

    def test_pending_identity_reports_count_without_guessing_or_writes(self):
        self.seed(1, linked=False)
        self.seed(2)
        with CaptureQueriesContext(connection) as queries:
            state = self.state()
        self.assertEqual(state['code'], 'pending_identity')
        self.assertEqual(state['pending_identity_count'], 1)
        self.assertIn('insufficient_samples', state['reasons'])
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))

    def test_missing_genres_is_separate_from_linked_sample_count(self):
        for identity in (1, 2, 3):
            self.seed(identity, tags=['二刷', '神作'])
        state = self.state()
        self.assertEqual(state['code'], 'missing_genres')
        self.assertEqual((state['eligible_sample_count'], state['usable_sample_count']), (3, 0))
        self.assertEqual(state['missing_genre_count'], 3)
        self.assertEqual(self.profile()['tag_summary'], [])

    def test_multiple_blockers_keep_counts_and_ready_takes_priority(self):
        self.seed(1, linked=False)
        self.seed(2, tags=[])
        self.seed(3)
        state = self.state()
        self.assertEqual(state['reasons'], ['pending_identity', 'missing_genres', 'insufficient_samples'])
        self.seed(4); self.seed(5)
        state = self.state()
        self.assertEqual(state['code'], 'ready')
        self.assertEqual(state['reasons'], ['ready'])
        self.assertEqual(state['pending_identity_count'], 1)
        self.assertEqual(state['missing_genre_count'], 1)

    def test_adult_unknown_unavailable_are_not_treated_as_empty_or_recommended(self):
        self.seed(1, nsfw=True, tags=['音乐'])
        self.seed(2, nsfw=None, tags=['悬疑'])
        self.seed(3, in_catalog=False)
        data = self.profile()
        self.assertEqual(data['cold_start_state']['code'], 'unavailable_samples')
        self.assertEqual(data['cold_start_state']['unavailable_sample_count'], 3)
        self.assertEqual(data['tag_summary'], [])
        self.assertEqual(data['extraction']['common_topics']['tag_summary'], [])
        for identity in (1, 2):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/').status_code, 404)

    def test_filtered_empty_keeps_ready_profile_and_failures_have_no_cold_contract(self):
        for identity in (1, 2, 3):
            self.seed(identity)
        response = self.client.get('/api/recommendations/', {'min_score': 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])
        self.assertEqual(response.data['mode'], 'personalized')
        self.assertEqual(response.data['cold_start_state']['code'], 'ready')
        with patch('recommendations.views.profile_contract', side_effect=DatabaseError('private detail')):
            response = self.client.get('/api/recommendations/profile/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('cold_start_state', response.data)
        self.assertNotIn('private detail', str(response.data))

    def test_user_isolation_and_anonymous_guard(self):
        self.seed(1, user=self.other)
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'])
        self.assertEqual(self.state()['code'], 'empty_journal')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.state()['code'], 'insufficient_samples')
        self.assertEqual(self.state()['explicit_preference_count'], 1)
        self.assertEqual(self.client.get('/api/recommendations/profile/', {'user_id': self.user.pk}).status_code, 400)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/recommendations/profile/').status_code, 401)

    @override_settings(RECOMMENDATION_PROFILE_MIN_SAMPLES=2)
    def test_minimum_comes_from_policy_not_hardcoded_three(self):
        self.seed(1); self.seed(2)
        self.assertEqual(self.state()['minimum_samples'], 2)
        self.assertEqual(self.state()['code'], 'ready')
