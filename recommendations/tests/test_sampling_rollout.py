"""P3 release gates, rollback and read-only audit guards. Local PostgreSQL only."""
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from urllib.parse import urlsplit

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITransactionTestCase

from anime.models import Anime, UserAnimeRecord
from recommendations.models import RecommendationPreference, UserRecommendationFeedbackState
from recommendations.tests import test_profile_sampling as fixtures
from scripts.prepare_recommendation_sampling_rollout import environment_patch
from scripts.verify_recommendation_sampling_deployment import main, SamplingDeploymentAudit, valid_preferences, valid_policy, CHOICES


class SamplingRolloutGuardTests(SimpleTestCase):
    def test_operator_patch_never_enables_v2_or_changes_browse_or_database(self):
        for enabled in (True, False):
            result = environment_patch(enabled)
            self.assertEqual(set(result), {'RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED',
                'RECOMMENDATION_ALGORITHM_VERSION', 'RECOMMENDATION_V2_BROWSE_ENABLED'})
            self.assertEqual(result['RECOMMENDATION_ALGORITHM_VERSION'], 'content.v1')
            self.assertEqual(result['RECOMMENDATION_V2_BROWSE_ENABLED'], 'False')
            self.assertEqual(result['RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED'], str(enabled))

    def test_confirmed_deployment_and_explicit_expected_gate_required_before_network(self):
        with patch('scripts.verify_recommendation_sampling_deployment.SamplingDeploymentAudit') as client:
            for args in (['--expected-account', 'synthetic@example.invalid'],
                         ['--expected-account', 'synthetic@example.invalid', '--expect-enabled']):
                with self.assertRaises(SystemExit):
                    main(args)
            client.assert_not_called()

    def test_every_business_mutation_rejected_before_network(self):
        session = Mock()
        audit = SamplingDeploymentAudit(session)
        for method in ('PATCH', 'PUT', 'POST', 'DELETE'):
            for path in ('/api/recommendations/preferences/', '/api/recommendations/100/want/',
                         '/api/recommendations/feedback/', '/api/recommendations/profile/preview/'):
                with self.assertRaises(ValueError):
                    audit.request(path, method=method)
        session.request.assert_not_called()
        self.assertEqual(audit.report['requests'], 0)
        self.assertFalse(audit.report['enabled_release_verified'])

    def test_disabled_contract_cannot_pass_enabled_acceptance_or_invent_preview_policy(self):
        prefs = {'contract_version': 'recommendations.3a.v1', 'profile_min_score': '7.0',
            'effective_profile_min_score': '8.5', 'profile_policy_version': 'profile-sampling.v1', 'version': 1,
            'profile_min_score_control': {'enabled': False, 'choices': CHOICES, 'default': '8.5',
                'minimum': '6.0', 'maximum': '10.0', 'step': '0.5'}}
        self.assertTrue(valid_preferences(prefs, False))
        self.assertFalse(valid_preferences(prefs, True))
        policy = {'profile_policy_version': 'profile-sampling.v1', 'configured_minimum_score': '7.0',
            'minimum_score': '7.0', 'customization_enabled': False, 'source': 'unsaved_preview',
            'watch_status': 'completed', 'minimum_samples': 3,
            'requires_safe_bangumi_identity': True, 'requires_usable_tags': True}
        self.assertTrue(valid_policy(policy, prefs, preview_score='7.0'))
        self.assertFalse(valid_policy(policy, prefs))
        self.assertFalse(valid_policy({**policy, 'requires_safe_bangumi_identity': False}, prefs, preview_score='7.0'))


@override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=True)
class SamplingRolloutPostgresTests(APITransactionTestCase):
    setUp = fixtures.ProfileSamplingAPITests.setUp
    seed = fixtures.ProfileSamplingAPITests.seed
    candidates = fixtures.ProfileSamplingAPITests.candidates
    save = fixtures.ProfileSamplingAPITests.save

    def assert_expired(self, page):
        row = page['results'][0]
        result = self.client.get(f"/api/recommendations/{row['bangumi_id']}/", {'evidence_token': row['evidence_token']})
        self.assertEqual(result.status_code, 409, result.data)
        self.assertNotIn('evidence', result.data)
        result = self.client.get('/api/recommendations/', {'cursor': page['next_page_cursor']})
        self.assertEqual(result.status_code, 409, result.data)

    def test_full_enable_save_raise_rollback_reenable_preserves_all_private_fields(self):
        for identity, score in ((1, 7), (2, 7), (3, 7), (4, 9), (5, 9), (6, 9), (7, 0), (8, None)):
            self.seed(identity, score)
        self.candidates()
        original = [list(model.objects.values()) for model in (Anime, UserAnimeRecord)]
        self.assertEqual(self.save('7.0', tags=['音乐']).status_code, 200)
        low = self.client.get('/api/recommendations/', {'browse': True}).data
        self.assertEqual(self.client.get(fixtures.PROFILE).data['usable_sample_count'], 6)
        self.assertEqual(self.save('9.0', 1).status_code, 200)
        self.assert_expired(low)
        self.assertEqual(self.client.get(fixtures.PROFILE).data['usable_sample_count'], 3)
        self.assertEqual(self.save('7.0', 2).status_code, 200)
        active = self.client.get('/api/recommendations/', {'browse': True}).data
        settings_before = list(RecommendationPreference.objects.values())
        with override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=False):
            self.assert_expired(active)
            prefs = self.client.get(fixtures.PREFERENCES).data
            self.assertEqual((prefs['profile_min_score'], prefs['effective_profile_min_score']), ('7.0', '8.5'))
            self.assertEqual(self.client.get(fixtures.PROFILE).data['usable_sample_count'], 3)
            rolled_back = self.client.get('/api/recommendations/', {'browse': True}).data
            self.assertEqual(rolled_back['algorithm_version'], 'content.v1')
            self.assertEqual(rolled_back['evidence_context']['profile_min_score'], '8.5')
        self.assert_expired(rolled_back)
        self.assertEqual(self.client.get(fixtures.PROFILE).data['usable_sample_count'], 6)
        self.assertEqual(list(RecommendationPreference.objects.values()), settings_before)
        self.assertFalse(UserRecommendationFeedbackState.objects.exists())
        for model, before in zip((Anime, UserAnimeRecord), original):
            self.assertEqual(list(model.objects.values()), before)

    def test_two_users_nondefault_thresholds_and_stale_retries_do_not_cross(self):
        for identity in (1, 2, 3):
            self.seed(identity, 7)
        self.seed(4, 10, user=self.other)
        self.assertEqual(self.save('7.0', tags=['音乐']).status_code, 200)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.save('9.5', tags=['日常']).status_code, 200)
        self.assertEqual(self.client.get(fixtures.PROFILE).data['usable_sample_count'], 1)
        self.client.force_authenticate(self.owner)
        saved = list(RecommendationPreference.objects.values())
        self.assertEqual(self.save('6.0', 0, tags=['科幻']).status_code, 409)
        self.assertEqual(self.save('7.0', 1, tags=['音乐']).status_code, 200)
        self.assertEqual(list(RecommendationPreference.objects.values()), saved)
        self.assertEqual(self.client.get(fixtures.PROFILE).data['usable_sample_count'], 3)

    def test_deployment_auditor_consumes_real_api_contracts_with_gate_on_and_off(self):
        for identity in (1, 2, 3):
            self.seed(identity, 9)
        self.candidates()
        self.assertEqual(self.save('7.0').status_code, 200)
        original = list(UserAnimeRecord.objects.values())

        def request(method, url, **kwargs):
            path = urlsplit(url).path
            if method == 'POST':
                self.assertEqual(path, '/api/token/')
                return SimpleNamespace(status_code=200, json=lambda: {'access': 'synthetic'})
            self.assertEqual(method, 'GET')
            authenticated = kwargs['headers'].get('Authorization') == 'Bearer synthetic'
            self.client.force_authenticate(self.owner if authenticated else None)
            response = self.client.get(path, data=kwargs.get('params', {}))
            return SimpleNamespace(status_code=response.status_code, json=response.json, headers=response.headers)

        for enabled in (True, False):
            session = Mock()
            session.request.side_effect = request
            audit = SamplingDeploymentAudit(session)
            with override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=enabled), \
                    patch('getpass.getpass', return_value='synthetic-not-a-real-password'), redirect_stdout(StringIO()):
                audit.authenticated_checks(self.owner.username, enabled=enabled)
            self.assertEqual(audit.report['enabled_release_verified'], enabled)
            self.assertTrue(all(result['passed'] for result in audit.report['checks'].values()))
            self.assertLessEqual(audit.report['requests'], 40)
            audit.close()
        self.assertEqual(list(UserAnimeRecord.objects.values()), original)
