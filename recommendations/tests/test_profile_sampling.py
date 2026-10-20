"""P1 isolated PostgreSQL settings/preview contracts; no production fixtures."""
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError, transaction, DatabaseError
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITransactionTestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.comparison import context_snapshot
from recommendations.extraction import extract_profile
from recommendations.features import feature_tags
from recommendations.models import RecommendationAnime, RecommendationPreference, UserRecommendationFeedbackState
from recommendations.profile_policy import PROFILE_POLICY_VERSION, validate_profile_min_score
from recommendations.resolution import resolved_records
from recommendations.services import preference_contract, profile_data

PREFERENCES = '/api/recommendations/preferences/'
PROFILE = '/api/recommendations/profile/'
PREVIEW = '/api/recommendations/profile/preview/'


class SamplingValidationTests(SimpleTestCase):
    def test_exact_nine_choices_and_no_bool_nonfinite_or_fraction_guessing(self):
        for value in ('6.0', 6.5, 7, '7.5', 8, '8.5', 9, '9.5', '10.0'):
            self.assertEqual(validate_profile_min_score(value), Decimal(str(value)))
        for value in (True, False, None, '', 'NaN', 'Infinity', '-Infinity', 5.5, 10.5, 7.3, {}, []):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_profile_min_score(value)


@override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=True)
class ProfileSamplingAPITests(APITransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='sampling-owner')
        self.other = get_user_model().objects.create_user(username='sampling-other')
        self.client.force_authenticate(self.owner)

    def seed(self, identity, score=7, *, user=None, status='completed', nsfw=False, linked=True, tags=None):
        RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'来源{identity}', nsfw=nsfw,
            tags='音乐,日常', score=10, season='2026-01', status_type='TV')
        anime = Anime.objects.create(bgm_id=identity if linked else None, chinese_title=f'来源{identity}',
            japanese_title=f'Original{identity}', airing_period='2026-1', base_tags=['音乐', '日常'] if tags is None else tags)
        return UserAnimeRecord.objects.create(user=user or self.owner, anime=anime, watch_status=status,
            personal_score=score, custom_tags=['二刷'], review='PRIVATE_REVIEW_NOT_READ_OR_CHANGED')

    def candidates(self):
        for identity in range(100, 130):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'候选{identity}', nsfw=False,
                tags='音乐,日常' if identity % 2 else '音乐,成长', score=8, season='2026-01', status_type='TV')

    def save(self, score, version=0, **changes):
        return self.client.patch(PREFERENCES, {'profile_min_score': score, 'expected_version': version, **changes}, format='json')

    def test_default_policy_and_preview_get_never_create_settings(self):
        with CaptureQueriesContext(connection) as queries:
            prefs = self.client.get(PREFERENCES).data
            with (patch('recommendations.engine.rank_context', side_effect=AssertionError('Preview never ranks')),
                  patch('recommendations.engine.public_features', side_effect=AssertionError('Preview never builds public index'))):
                preview = self.client.get(PREVIEW, {'profile_min_score': '7.0'})
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(prefs['profile_min_score'], '8.5')
        self.assertEqual(prefs['effective_profile_min_score'], '8.5')
        self.assertEqual(prefs['profile_min_score_control']['choices'], [f'{6+n*.5:.1f}' for n in range(9)])
        self.assertEqual(prefs['profile_policy_version'], PROFILE_POLICY_VERSION)
        self.assertTrue(preview.data['is_preview'])
        self.assertFalse(preview.data['saved'])
        self.assertEqual(preview.data['sample_policy']['minimum_score'], '7.0')
        self.assertFalse(RecommendationPreference.objects.exists())
        self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for row in queries))
        self.assertEqual(self.client.get(PROFILE).data['sample_policy']['minimum_score'], '8.5')
        self.assertIn('no-store', preview['Cache-Control'])

    def test_score_and_tags_save_atomically_omitted_fields_and_noops_preserve_other_settings(self):
        first = self.save('7.0', tags=['音乐'])
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual((first.data['version'], first.data['tags']), (1, ['音乐']))
        same = self.save('7.0', 1, tags=['音乐'])
        self.assertEqual((same.data['version'], same.data['updated_at']), (1, first.data['updated_at']))
        only_score = self.save('6.0', 1)
        self.assertEqual((only_score.data['version'], only_score.data['tags']), (2, ['音乐']))
        old_client = self.client.patch(PREFERENCES, {'add_tags': ['日常']}, format='json')
        self.assertEqual(old_client.data['profile_min_score'], '6.0')
        clear_tags = self.client.patch(PREFERENCES, {'tags': [], 'expected_version': 3}, format='json')
        self.assertEqual(clear_tags.data['profile_min_score'], '6.0')
        self.assertEqual(clear_tags.data['tags'], [])
        reset = self.save('8.5', 4)
        self.assertEqual(reset.data['tags'], [])
        self.assertEqual(reset.data['version'], 5)

    def test_invalid_scores_unknown_fields_and_missing_version_are_atomic(self):
        for score in (None, True, False, '', 'NaN', 'Infinity', 5.5, 10.5, 7.3, '7.01', [], {}):
            with self.subTest(score=score):
                self.assertEqual(self.save(score, tags=['音乐']).status_code, 400)
                self.assertFalse(RecommendationPreference.objects.exists())
        for body in ({'profile_min_score': 7}, {'profile_min_score': 7, 'expected_version': 0, 'user_id': self.other.pk},
                     {'profile_min_score': 7, 'expected_version': 0, 'profile_policy_version': 'forged'}):
            self.assertEqual(self.client.patch(PREFERENCES, body, format='json').status_code, 400)
        self.assertEqual(self.save('7.0', tags=['NSFW']).status_code, 400)
        self.assertFalse(RecommendationPreference.objects.exists())

    def test_database_constraint_rejects_unsupported_steps_and_limits(self):
        for value in ('5.5', '7.3', '10.5'):
            with self.subTest(value=value), self.assertRaises(IntegrityError), transaction.atomic():
                RecommendationPreference.objects.create(user=self.owner, profile_min_score=Decimal(value))
        with self.assertRaises(ValidationError):
            RecommendationPreference(user=self.owner, profile_min_score=Decimal('7.3')).full_clean()

    def test_two_users_versions_and_conflicts_are_isolated(self):
        self.assertEqual(self.save('7.0').status_code, 200)
        stale = self.save('6.0', 0, tags=['科幻'])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(PREFERENCES).data['tags'], [])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(PREFERENCES).data['profile_min_score'], '8.5')
        self.assertEqual(self.save('9.0').data['version'], 1)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(PREFERENCES).data['profile_min_score'], '7.0')

    def test_preview_parameter_authentication_and_duplicates_are_strict(self):
        for query in ('', '?profile_min_score=', '?profile_min_score=7.0&profile_min_score=8.0',
                      '?profile_min_score=7.0&user_id=2', '?profile_min_score=7.0&algorithm=content.v2',
                      '?profile_min_score=7.3'):
            self.assertEqual(self.client.get(PREVIEW+query).status_code, 400)
        self.assertEqual(self.client.post(PREVIEW, {}, format='json').status_code, 405)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(PREVIEW, {'profile_min_score': 7}).status_code, 401)
        self.assertFalse(RecommendationPreference.objects.exists())

    def test_all_nine_thresholds_use_personal_scores_and_preserve_zero_unrated_and_records(self):
        for identity, score in enumerate((6, 7, 8, 8.5, 9, 9.5, 10, 0, None), 1):
            self.seed(identity, score)
        originals = [list(model.objects.values()) for model in (UserAnimeRecord, Anime, RecommendationAnime)]
        with CaptureQueriesContext(connection) as queries:
            for score in ('6.0','6.5','7.0','7.5','8.0','8.5','9.0','9.5','10.0'):
                result = self.client.get(PREVIEW, {'profile_min_score': score})
                self.assertEqual(result.status_code, 200, result.data)
                expected = sum(Decimal(str(value)) >= Decimal(score) for value in (6,7,8,8.5,9,9.5,10))
                self.assertEqual(result.data['usable_sample_count'], expected)
                self.assertEqual(result.data['cold_start'], expected < 3)
            self.assertEqual(self.client.get(PROFILE).data['extraction']['record_stats']['unrated_record_count'], 1)
        self.assertFalse(any('"review"' in row['sql'] for row in queries))
        for model, original in zip((UserAnimeRecord, Anime, RecommendationAnime), originals):
            self.assertEqual(list(model.objects.values()), original)
        self.assertFalse(RecommendationPreference.objects.exists())

    def test_security_status_identity_and_genres_are_required_even_at_six(self):
        self.seed(1,6)
        self.seed(2,10,nsfw=True)
        self.seed(3,10,nsfw=None)
        self.seed(4,10,linked=False)
        self.seed(5,10,status='watching')
        self.seed(6,10,status='plan')
        self.seed(7,10,tags=[])
        self.seed(8,10,user=self.other)
        self.save('6.0')
        result = self.client.get(PROFILE).data
        self.assertEqual(result['usable_sample_count'], 1)
        self.assertTrue(result['cold_start'])
        self.assertEqual(result['diagnostics']['pending_identity_count'], 1)
        for identity in (2,3):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code,404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/',{},format='json').status_code,404)

    def test_lowering_and_raising_threshold_changes_readiness_not_explicit_preferences(self):
        for identity in (1,2,3): self.seed(identity,7)
        self.save('7.0',tags=['科幻'])
        ready = self.client.get(PROFILE).data
        self.assertFalse(ready['cold_start'])
        self.save('9.0',1)
        cold = self.client.get(PROFILE).data
        self.assertTrue(cold['cold_start'])
        self.assertEqual(cold['cold_start_state']['recommendation_basis'],'explicit_preferences')
        self.assertEqual(cold['extraction']['explicit_preferences']['tags'],['科幻'])

    def test_old_identity_review_default_scope_follows_effective_threshold(self):
        record = self.seed(1,7,linked=False)
        self.assertEqual(self.client.get('/api/recommendations/identities/').data['count'],0)
        self.save('7.0')
        result = self.client.get('/api/recommendations/identities/')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['count'],1)
        self.assertEqual(result.data['results'][0]['record_id'],record.pk)
        self.save('9.0',1)
        self.assertEqual(self.client.get('/api/recommendations/identities/').data['count'],0)

    def test_low_score_evidence_is_real_and_shared_with_detail_after_save(self):
        for identity in (1,2,3): self.seed(identity,7)
        self.candidates()
        self.save('7.0')
        page = self.client.get('/api/recommendations/').data
        self.assertTrue(page['results'])
        for row in page['results']:
            self.assertEqual(row['evidence']['source']['personal_score'],'7.0')
            self.assertEqual(row['evidence_context']['profile_min_score'],'7.0')
            self.assertEqual(row['evidence_context']['profile_policy_version'],PROFILE_POLICY_VERSION)
            detail = self.client.get(f"/api/recommendations/{row['bangumi_id']}/",{'evidence_token':row['evidence_token']})
            self.assertEqual(detail.status_code,200,detail.data)
            self.assertEqual(detail.data['evidence'],row['evidence'])

    def test_change_invalidates_legacy_page_browse_and_detail_even_when_seeds_same(self):
        for identity in (1,2,3): self.seed(identity,9)
        self.candidates()
        self.save('8.5')  # No settings row/no version bump for a default no-op.
        page = self.client.get('/api/recommendations/',{'page_size':1}).data
        browse = self.client.get('/api/recommendations/',{'browse':True}).data
        row = page['results'][0]
        self.save('8.0')
        self.assertEqual(self.client.get('/api/recommendations/',{'page':2,'page_size':1,'state_token':page['state_token']}).status_code,409)
        self.assertEqual(self.client.get('/api/recommendations/',{'cursor':browse['next_page_cursor']}).status_code,409)
        self.assertEqual(self.client.get(f"/api/recommendations/{row['bangumi_id']}/",{'evidence_token':row['evidence_token']}).status_code,409)

    def test_weight_formula_uses_effective_threshold_and_default_matches_old_arithmetic(self):
        context={'mode':'personalized','preferences':{'tags':[]},'seeds':[
            {'score':7.0,'tags':frozenset(['音乐'])},{'score':9.0,'tags':frozenset(['日常'])}],
            'profile':{'sample_policy':{'minimum_score':Decimal('7.0')}}}
        vector=engine.taste_vector(context)
        self.assertEqual(vector['音乐'],.25)
        self.assertEqual(vector['日常'],.75)
        context['profile']['sample_policy']['minimum_score']=Decimal('8.5')
        context['seeds'][0]['score']=8.5
        expected={'音乐':1/2.5,'日常':1.5/2.5}
        self.assertEqual(engine.taste_vector(context),expected)

    def test_default_85_full_v1_order_scores_and_evidence_match_pre_policy_extraction(self):
        for identity in (1,2,3): self.seed(identity,9)
        self.candidates()
        current=engine.user_context(self.owner)
        old=deepcopy(current)
        extracted,seeds=extract_profile(resolved_records(self.owner),threshold=Decimal('8.5'),minimum=3)
        old['seeds']=seeds
        old['profile']=extracted  # Pre-P1 ranker fell back to the global 8.5 constant.
        index=engine.public_features()
        for category in ('all','familiar','explore'):
            options={'category':category,'type':'all','min_score':0,'hide_recorded':True}
            actual=engine.rank_context(current,options,index)[0]
            expected=engine.rank_context(old,options,index)[0]
            values=lambda rows:[(r['anime'].bangumi_id,r['value'],r['evidence'],r['reason']) for r in rows]
            self.assertEqual(values(actual),values(expected))

    @override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=False)
    def test_disabled_rollout_preserves_saved_value_but_runs_85_and_preview_is_labeled(self):
        self.seed(1,7)
        saved=self.save('7.0',tags=['音乐'])
        self.assertEqual(saved.status_code,200,saved.data)
        self.assertEqual(saved.data['profile_min_score'],'7.0')
        self.assertEqual(saved.data['effective_profile_min_score'],'8.5')
        self.assertFalse(saved.data['profile_min_score_control']['enabled'])
        self.assertEqual(self.client.get(PROFILE).data['usable_sample_count'],0)
        preview=self.client.get(PREVIEW,{'profile_min_score':'7.0'}).data
        self.assertEqual(preview['usable_sample_count'],1)
        self.assertEqual(preview['sample_policy']['source'],'unsaved_preview')
        self.assertEqual(RecommendationPreference.objects.get(user=self.owner).profile_min_score,Decimal('7.0'))

    def test_gate_change_invalidates_old_context_without_clearing_saved_data(self):
        for identity in (1,2,3): self.seed(identity,9)
        self.candidates()
        self.save('7.0')
        page=self.client.get('/api/recommendations/',{'browse':True}).data
        with override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=False):
            result=self.client.get('/api/recommendations/',{'cursor':page['next_page_cursor']})
            self.assertEqual(result.status_code,409)
            self.assertEqual(self.client.get(PREFERENCES).data['profile_min_score'],'7.0')

    def test_fixed_evaluation_ignores_custom_threshold_without_modifying_saved_settings(self):
        for identity,score in ((1,7),(2,9),(3,9),(4,9)): self.seed(identity,score)
        self.save('6.0')
        live=engine.user_context(self.owner)
        baseline=engine.user_context(self.owner,evaluation_baseline=True)
        comparison=context_snapshot(resolved_records(self.owner),preference_contract(self.owner),self.owner.pk,engine.TAXONOMY_VERSION)
        self.assertEqual(len(live['seeds']),4)
        self.assertEqual(len(baseline['seeds']),3)
        self.assertEqual(len(comparison['seeds']),3)
        self.assertEqual(baseline['profile']['sample_policy']['minimum_score'],Decimal('8.5'))
        self.assertEqual(comparison['profile']['sample_policy']['minimum_score'],Decimal('8.5'))
        self.assertEqual(self.client.get(PREFERENCES).data['profile_min_score'],'6.0')
        self.assertFalse(UserRecommendationFeedbackState.objects.exists())

    def test_feedback_revision_is_independent_and_preview_failure_is_not_cold_start(self):
        state=UserRecommendationFeedbackState.objects.create(user=self.owner,feedback_version=5)
        self.save('7.0')
        state.refresh_from_db()
        self.assertEqual(state.feedback_version,5)
        with patch('recommendations.services.resolved_records',side_effect=DatabaseError('synthetic outage')):
            result=self.client.get(PREVIEW,{'profile_min_score':'7.0'})
        self.assertEqual(result.status_code,503)
        self.assertNotIn('cold_start',result.data)
