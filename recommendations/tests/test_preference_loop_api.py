from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from anime.models import UserAnimeRecord
from recommendations.binding import bind_record
from recommendations.models import RecommendationPreference
from .test_contracts_api import ContractFixtures


class PreferenceLoopAPITests(ContractFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client.force_authenticate(self.user)

    def profile(self):
        response = self.client.get('/api/recommendations/profile/')
        self.assertEqual(response.status_code, 200)
        return response.data

    def save(self, tags, version):
        return self.client.patch('/api/recommendations/preferences/',
                                 {'tags': tags, 'expected_version': version}, format='json')

    def test_readonly_policy_and_observations_need_explicit_confirmation(self):
        self.seed(1, score=None, tags=['音乐'])
        with (patch('requests.sessions.Session.request', side_effect=AssertionError('No remote requests')),
              CaptureQueriesContext(connection) as queries):
            profile = self.profile()
        extraction = profile['extraction']
        self.assertEqual(extraction['preference_policy'], {'maximum_tags': 20, 'suggested_initial_min': 3,
            'suggested_initial_max': 5, 'maximum_observed_topics': 8,
            'explicit_preferences_are_hard_filters': False, 'common_topics_require_confirmation': True})
        self.assertEqual(extraction['common_topics']['tag_summary'][0]['tag'], '音乐')
        self.assertEqual(extraction['explicit_preferences']['tags'], [])
        self.assertEqual(self.client.get('/api/recommendations/').data['mode'], 'cold_start')
        self.assertFalse(any('"review"' in query['sql'] for query in queries))
        self.assertEqual(RecommendationPreference.objects.count(), 0)
        confirmed = self.save(['音乐'], 0)
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.data['version'], 1)
        self.assertEqual(self.profile()['cold_start_state']['recommendation_basis'], 'explicit_preferences')
        self.assertEqual(self.client.get('/api/recommendations/').data['mode'], 'preference_based')

    def test_save_clear_and_idempotency_preserve_every_journal_field_and_trusted_profile(self):
        for identity in (1, 2, 3):
            self.seed(identity, tags=['日常'])
        before = list(UserAnimeRecord.objects.filter(user=self.user).order_by('pk').values())
        trusted = self.profile()['extraction']['trusted_profile']
        for tags, version, next_version in ((['音乐'], 0, 1), (['音乐'], 1, 1), ([], 1, 2), ([], 2, 2)):
            response = self.save(tags, version)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['version'], next_version)
            self.assertEqual(list(UserAnimeRecord.objects.filter(user=self.user).order_by('pk').values()), before)
            self.assertEqual(self.profile()['extraction']['trusted_profile'], trusted)
            self.assertEqual(self.profile()['cold_start_state']['code'], 'ready')

    def test_conflicting_tabs_do_not_overwrite_new_settings_or_journal(self):
        row = self.seed(1)
        before = UserAnimeRecord.objects.filter(pk=row.pk).values().get()
        self.save(['日常'], 0)
        self.save(['日常', '科幻'], 1)
        conflict = self.save(['音乐'], 1)
        self.assertEqual(conflict.status_code, 409)
        latest = self.client.get('/api/recommendations/preferences/').data
        self.assertEqual(latest['tags'], ['日常', '科幻'])
        self.assertEqual(latest['version'], 2)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=row.pk).values().get(), before)

    def test_scores_tags_deletion_and_identity_changes_recompute_without_overwriting_preferences(self):
        pref = RecommendationPreference.objects.create(user=self.user, tags=['科幻'], version=7)
        rows = [self.seed(identity, score='9.0') for identity in (1, 2, 3)]
        self.assertEqual(self.profile()['cold_start_state']['code'], 'ready')
        UserAnimeRecord.objects.filter(pk=rows[2].pk).update(personal_score='8.4')
        self.assertEqual(self.profile()['usable_sample_count'], 2)
        self.assertEqual(self.profile()['cold_start_state']['code'], 'insufficient_samples')
        UserAnimeRecord.objects.filter(pk=rows[2].pk).update(personal_score='9.0', private_base_tags=[])
        self.assertEqual(self.profile()['cold_start_state']['code'], 'missing_genres')
        UserAnimeRecord.objects.filter(pk=rows[2].pk).update(private_base_tags=['音乐'])
        self.assertEqual(self.profile()['cold_start_state']['code'], 'ready')
        UserAnimeRecord.objects.filter(pk=rows[2].pk).delete()  # One exact fixture record, isolated test DB.
        unlinked = self.seed(4, linked=False, tags=['音乐'])
        self.assertEqual(self.profile()['cold_start_state']['code'], 'pending_identity')
        bind_record(record_id=unlinked.pk, user_id=self.user.pk, target_id=4, source='user_confirmed')
        self.assertEqual(self.profile()['cold_start_state']['code'], 'ready')
        pref.refresh_from_db()
        self.assertEqual((pref.tags, pref.version), (['科幻'], 7))

    def test_responses_are_uncacheable_and_owner_isolated(self):
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'], version=9)
        self.seed(1, user=self.other, tags=['音乐'])
        for url in ('/api/recommendations/profile/', '/api/recommendations/preferences/', '/api/recommendations/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.assertIn('Authorization', response['Vary'])
        self.assertEqual(self.profile()['usable_sample_count'], 0)
        self.assertEqual(self.profile()['extraction']['common_topics']['tag_summary'], [])
        self.assertEqual(self.save(['科幻'], 0).status_code, 200)
        other = RecommendationPreference.objects.get(user=self.other)
        self.assertEqual((other.tags, other.version), (['音乐'], 9))

    def test_unsafe_observations_are_never_suggested_or_inferred(self):
        self.seed(1, nsfw=True, score=None, tags=['音乐'])
        self.seed(2, nsfw=None, score=None, tags=['科幻'])
        self.seed(3, score=None, tags=['治愈'])
        data = self.profile()['extraction']
        self.assertEqual([topic['tag'] for topic in data['common_topics']['tag_summary']], ['治愈'])
        self.assertEqual(data['explicit_preferences']['tags'], [])
