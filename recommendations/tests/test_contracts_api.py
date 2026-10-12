from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.exceptions import NotFound
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from recommendations.identity import recommendation_identity
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.serializers import RecommendationIdentitySerializer, RecommendationPublicAnimeSerializer
from recommendations.services import profile_contract


class ContractFixtures:
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='rec-contract-owner')
        self.other = get_user_model().objects.create_user(username='rec-contract-other')

    def seed(self, identity, *, user=None, score='8.5', status='completed', nsfw=False,
             tags=None, linked=True, in_catalog=True):
        if in_catalog:
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'作品 {identity}',
                                                title_jp=f'Original {identity}', nsfw=nsfw)
        anime = Anime.objects.create(bgm_id=identity if linked else None, chinese_title=f'作品 {identity}',
                                     japanese_title=f'Original {identity}', airing_period='2026-4',
                                     base_tags=tags if tags is not None else ['日常', '治愈'])
        return UserAnimeRecord.objects.create(user=user or self.user, anime=anime,
                                              watch_status=status, personal_score=score)


class ProfileAPITests(ContractFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('recommendations:profile')
        self.client.force_authenticate(self.user)

    def test_empty_cold_contract_no_get_side_effects(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'contract_version', 'taxonomy_version', 'eligible_sample_count',
                         'cold_start', 'has_usable_tags', 'readiness', 'tag_summary', 'sample_policy'})
        self.assertEqual(response.data['eligible_sample_count'], 0)
        self.assertTrue(response.data['cold_start'])
        self.assertEqual(response.data['tag_summary'], [])
        self.assertEqual(response.data['readiness'], 'needs_more_samples')
        self.assertEqual(response.data['sample_policy']['minimum_score'], '8.5')
        self.assertEqual(response.data['sample_policy']['minimum_samples'], 3)
        self.assertEqual(RecommendationPreference.objects.count(), 0)

    def test_three_safe_seeds_ready_and_two_cold(self):
        self.seed(1001)
        self.seed(1002)
        self.assertTrue(self.client.get(self.url).data['cold_start'])
        self.seed(1003)
        response = self.client.get(self.url)
        self.assertEqual(response.data['eligible_sample_count'], 3)
        self.assertFalse(response.data['cold_start'])
        self.assertEqual(response.data['readiness'], 'ready')
        self.assertEqual(response.data['tag_summary'][0], {'tag': '日常', 'sample_count': 3})

    def test_exclude_all_unsafe_unlinked_and_ineligible_samples(self):
        for identity, options in enumerate([
            {'nsfw': True}, {'nsfw': None}, {'linked': False}, {'in_catalog': False},
            {'score': '8.4'}, {'score': None}, {'status': 'watching'},
            {'status': 'plan'}, {'status': 'on_hold'}, {'user': self.other},
        ], start=2001):
            self.seed(identity, **options)
        response = self.client.get(self.url)
        self.assertEqual(response.data['eligible_sample_count'], 0)
        self.assertEqual(response.data['tag_summary'], [])
        self.assertNotIn('作品', str(response.data))

    def test_threshold_is_personal_score_not_catalog_score(self):
        record = self.seed(3001, score='8.5')
        RecommendationAnime.objects.filter(bangumi_id=3001).update(score=0)
        self.assertEqual(self.client.get(self.url).data['eligible_sample_count'], 1)
        record.personal_score = Decimal('8.4')
        record.save(update_fields=['personal_score'])
        RecommendationAnime.objects.filter(bangumi_id=3001).update(score=10)
        self.assertEqual(self.client.get(self.url).data['eligible_sample_count'], 0)

    def test_only_effective_private_base_tags_no_reviews_or_custom_tags(self):
        record = self.seed(4001, tags=['战斗'])
        record.private_base_tags = ['搞笑', '喜剧', '日常', '日本', '2026', '成人', '年度最佳']
        record.custom_tags = ['科幻']
        record.review = '不能返回给接口的私人长评'
        record.save(update_fields=['private_base_tags', 'custom_tags', 'review'])
        response = self.client.get(self.url)
        self.assertEqual(response.data['tag_summary'], [
            {'tag': '日常', 'sample_count': 1}, {'tag': '喜剧', 'sample_count': 1}])
        self.assertNotIn(record.review, str(response.data))
        self.assertNotIn('成人', str(response.data))

    def test_legacy_snapshot_fallback_and_metadata_empty_not_overridden(self):
        record = self.seed(4101, tags=['音乐'])
        UserAnimeRecord.objects.filter(pk=record.pk).update(metadata_initialized=False)
        self.assertEqual(self.client.get(self.url).data['tag_summary'][0]['tag'], '音乐')
        UserAnimeRecord.objects.filter(pk=record.pk).update(metadata_initialized=True, private_base_tags=[])
        self.assertEqual(self.client.get(self.url).data['tag_summary'], [])

    def test_no_tags_readiness_and_source_classification_change_fail_closed(self):
        for identity in (4201, 4202, 4203):
            self.seed(identity, tags=['日本', '2026'])
        response = self.client.get(self.url)
        self.assertFalse(response.data['cold_start'])
        self.assertEqual(response.data['readiness'], 'needs_tags')
        self.assertFalse(response.data['has_usable_tags'])
        RecommendationAnime.objects.filter(bangumi_id=4201).update(nsfw=True)
        self.assertTrue(self.client.get(self.url).data['cold_start'])

    def test_permission_and_forged_owner_query(self):
        self.seed(5001, user=self.other)
        for query in ({'user_id': self.other.pk}, {'user': self.other.pk}, {'username': self.other.username}, {'nsfw': 'true'}):
            self.assertEqual(self.client.get(self.url, query).status_code, 400)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_multi_user_isolation_and_query_bound(self):
        self.seed(5101, tags=['日常'])
        self.seed(5102, user=self.other, tags=['机战'])
        with self.assertNumQueries(1):
            own = profile_contract(self.user)
        self.assertEqual(own['tag_summary'], [{'tag': '日常', 'sample_count': 1}])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).data['tag_summary'], [{'tag': '机战', 'sample_count': 1}])

    def test_real_jwt_authentication_uses_token_owner(self):
        from rest_framework_simplejwt.tokens import AccessToken
        self.seed(5151, tags=['音乐'])
        self.seed(5152, user=self.other, tags=['机战'])
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['tag_summary'], [{'tag': '音乐', 'sample_count': 1}])

    @override_settings(RECOMMENDATION_PROFILE_MIN_SAMPLES=1, RECOMMENDATION_PROFILE_SCORE_THRESHOLD=9.0)
    def test_configurable_policy(self):
        self.seed(5201, score='9.0')
        response = self.client.get(self.url)
        self.assertFalse(response.data['cold_start'])
        self.assertEqual(response.data['sample_policy']['minimum_score'], '9.0')


class PreferenceAPITests(ContractFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('recommendations:preferences')
        self.client.force_authenticate(self.user)

    def patch(self, payload):
        return self.client.patch(self.url, payload, format='json')

    def test_get_default_and_empty_patch_validation_do_not_create(self):
        response = self.client.get(self.url)
        self.assertEqual(response.data['tags'], [])
        self.assertEqual(response.data['version'], 0)
        self.assertIsNone(response.data['updated_at'])
        self.assertIn('科幻', response.data['available_tags'])
        self.assertEqual(self.patch({}).status_code, 400)
        self.assertEqual(self.patch({'tags': []}).status_code, 200)
        self.assertEqual(RecommendationPreference.objects.count(), 0)

    def test_replace_alias_dedupe_and_idempotence(self):
        response = self.patch({'tags': ['搞笑', '喜剧', ' 日常 ']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['tags'], ['日常', '喜剧'])
        self.assertEqual(response.data['version'], 1)
        same = self.patch({'tags': ['喜剧', '日常']})
        self.assertEqual(same.data['version'], 1)
        self.assertEqual(same.data['updated_at'], response.data['updated_at'])

    def test_incremental_add_remove_preserves_other_tags(self):
        self.patch({'tags': ['日常', '科幻', '治愈']})
        response = self.patch({'add_tags': ['音乐'], 'remove_tags': ['科幻'], 'expected_version': 1})
        self.assertEqual(response.data['tags'], ['日常', '治愈', '音乐'])
        self.assertEqual(response.data['version'], 2)
        self.assertEqual(self.patch({'remove_tags': ['科幻']}).data['version'], 2)
        self.assertEqual(self.patch({'tags': []}).data['version'], 3)

    def test_conflicting_operations_and_invalid_values_rejected(self):
        for payload in [
            {'tags': ['日常'], 'add_tags': ['音乐']},
            {'add_tags': ['搞笑'], 'remove_tags': ['喜剧']},
            {'tags': ['NSFW']}, {'tags': ['成人']}, {'tags': ['<script>']},
            {'tags': '日常'}, {'tags': [None]}, {'tags': ['日常'] * 21},
            {'expected_version': 1}, {'tags': [], 'expected_version': -1},
            {'tags': [], 'expected_version': 2_147_483_648},
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(self.patch(payload).status_code, 400)
        self.assertEqual(RecommendationPreference.objects.count(), 0)

    def test_cumulative_limit_rollback(self):
        available = self.client.get(self.url).data['available_tags']
        self.patch({'tags': available[:20]})
        response = self.patch({'add_tags': available[20:21]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.url).data['version'], 1)
        self.assertEqual(len(self.client.get(self.url).data['tags']), 20)

    def test_stale_version_does_not_create_or_overwrite(self):
        self.assertEqual(self.patch({'tags': ['日常'], 'expected_version': 1}).status_code, 409)
        self.assertEqual(RecommendationPreference.objects.count(), 0)
        self.patch({'tags': ['日常'], 'expected_version': 0})
        self.assertEqual(self.patch({'tags': ['音乐'], 'expected_version': 0}).status_code, 409)
        self.assertEqual(self.client.get(self.url).data['tags'], ['日常'])

    def test_owner_body_query_and_unsafe_switches_rejected(self):
        for field in ('user', 'user_id', 'owner_id', 'username', 'allow_nsfw', 'id', 'version'):
            self.assertEqual(self.patch({'tags': ['日常'], field: self.other.pk}).status_code, 400)
        self.assertEqual(self.client.patch(f'{self.url}?user_id={self.other.pk}', {'tags': ['日常']}, format='json').status_code, 400)
        self.assertEqual(self.client.get(self.url, {'user_id': self.other.pk}).status_code, 400)
        self.assertEqual(RecommendationPreference.objects.count(), 0)

    def test_two_users_isolated_and_no_journal_changes(self):
        record = self.seed(6101, score='9.3')
        record.review = '完整保留的个人长评'
        record.save(update_fields=['review'])
        self.patch({'tags': ['日常']})
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).data['tags'], [])
        self.patch({'tags': ['科幻']})
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(self.url).data['tags'], ['日常'])
        record.refresh_from_db()
        self.assertEqual((record.watch_status, record.personal_score, record.review), ('completed', Decimal('9.3'), '完整保留的个人长评'))
        self.assertEqual(UserAnimeRecord.objects.count(), 1)

    def test_unauthenticated_methods_and_unsupported_methods(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.patch({'tags': ['日常']}).status_code, 401)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_one_preference_per_user_and_model_validator(self):
        RecommendationPreference.objects.create(user=self.user, tags=['日常'])
        with self.assertRaises(IntegrityError), transaction.atomic():
            RecommendationPreference.objects.create(user=self.user)
        with self.assertRaises(ValidationError):
            RecommendationPreference(user=self.other, tags=['成人']).full_clean()

    def test_corrupt_private_preference_not_exposed_and_version_overflow_safe(self):
        preference = RecommendationPreference.objects.create(user=self.user, tags=['成人', '日常', '2026'])
        self.assertEqual(self.client.get(self.url).data['tags'], ['日常'])
        preference.version = 2_147_483_647
        preference.save(update_fields=['version'])
        self.assertEqual(self.patch({'tags': ['音乐']}).status_code, 409)

    def test_invalid_json_non_object_form_and_oversize_do_not_write(self):
        self.assertEqual(self.client.patch(self.url, '{broken', content_type='application/json').status_code, 400)
        self.assertEqual(self.patch(['日常']).status_code, 400)
        self.assertEqual(self.client.patch(self.url, 'tags=日常', content_type='application/x-www-form-urlencoded').status_code, 415)
        self.assertEqual(self.client.patch(self.url, ' ' * 17000 + '{}', content_type='application/json').status_code, 400)
        self.assertEqual(RecommendationPreference.objects.count(), 0)

    def test_account_deletion_cascades_only_own_preferences(self):
        RecommendationPreference.objects.create(user=self.user, tags=['日常'])
        RecommendationPreference.objects.create(user=self.other, tags=['科幻'])
        self.user.delete()  # Isolated disposable test fixtures only.
        self.assertEqual(RecommendationPreference.objects.count(), 1)
        self.assertEqual(RecommendationPreference.objects.get().user_id, self.other.pk)


@override_settings(AWS_S3_CUSTOM_DOMAIN='media.xh-anime.com')
class IdentityContractTests(ContractFixtures, TestCase):
    def test_exact_bangumi_mapping_and_owned_record_namespace(self):
        record = self.seed(7001)
        identity = recommendation_identity(user=self.user, bangumi_id=7001)
        self.assertEqual(identity['bangumi_id'], 7001)
        self.assertEqual(identity['anime_id'], record.anime_id)
        self.assertEqual(identity['record_id'], record.pk)
        self.assertEqual(identity['mapping_status'], 'aligned')
        other = recommendation_identity(user=self.other, bangumi_id=7001)
        self.assertIsNone(other['record_id'])
        self.assertIsNone(other['watch_status'])
        self.assertNotIn('user_id', RecommendationIdentitySerializer(identity).data)

    def test_record_or_anime_pk_never_guessed_as_bangumi_identity(self):
        record = self.seed(7101)
        RecommendationAnime.objects.create(bangumi_id=record.pk, title_zh='另一个命名空间的作品', nsfw=False)
        identity = recommendation_identity(user=self.user, bangumi_id=record.pk)
        self.assertIsNone(identity['record_id'])
        self.assertIsNone(identity['anime_id'])

    def test_adult_unknown_missing_invalid_and_anonymous_not_found(self):
        for identity, marker in ((7201, True), (7202, None)):
            self.seed(identity, nsfw=marker)
        from django.contrib.auth.models import AnonymousUser
        for value in (7201, 7202, 99999, None, False, 0, -1, 2_147_483_648):
            with self.assertRaises(NotFound):
                recommendation_identity(user=self.user, bangumi_id=value)
        with self.assertRaises(NotFound):
            recommendation_identity(user=AnonymousUser(), bangumi_id=7201)

    def test_legacy_conflict_requires_confirmation_never_auto_links(self):
        record = self.seed(7301, linked=False)
        record.private_chinese_title = '《作品 7301》'
        record.save(update_fields=['private_chinese_title'])
        identity = recommendation_identity(user=self.user, bangumi_id=7301)
        self.assertTrue(identity['requires_identity_confirmation'])
        self.assertIsNone(identity['record_id'])
        record.anime.refresh_from_db()
        self.assertIsNone(record.anime.bgm_id)

    def test_other_users_legacy_record_is_not_returned(self):
        self.seed(7401, linked=False, user=self.other)
        identity = recommendation_identity(user=self.user, bangumi_id=7401)
        self.assertFalse(identity['requires_identity_confirmation'])
        self.assertIsNone(identity['record_id'])

    def test_release_identity_conflict_and_length_difference(self):
        RecommendationAnime.objects.create(bangumi_id=7501, title_zh='已有作品', title_jp='Existing', season='2026-04', nsfw=False)
        Anime.objects.create(bgm_id=99999, chinese_title='已有作品', japanese_title='Existing', airing_period='2026-4')
        identity = recommendation_identity(user=self.user, bangumi_id=7501)
        self.assertEqual(identity['mapping_status'], 'identity_conflict')
        RecommendationAnime.objects.create(bangumi_id=7502, title_zh='长' * 201, title_jp='原' * 201, nsfw=False)
        identity = recommendation_identity(user=self.user, bangumi_id=7502)
        self.assertFalse(identity['journal_compatible'])
        self.assertEqual(identity['field_conflicts'], ['chinese_title_too_long', 'japanese_title_too_long'])
        self.assertFalse(Anime.objects.filter(bgm_id=7502).exists())

    def test_public_serializer_rejects_unsafe_and_omits_source_fields(self):
        for marker in (True, None):
            anime = RecommendationAnime(bangumi_id=7601, title_zh='不可公开条目', nsfw=marker)
            with self.assertRaises(NotFound):
                _ = RecommendationPublicAnimeSerializer(anime).data
        anime = RecommendationAnime(bangumi_id=7602, title_zh='正常作品', nsfw=False,
                                    tags='成人,日本,搞笑,日常', cover_url='https://external.invalid/image.jpg',
                                    source_cover_url='https://external.invalid/private-source.jpg')
        data = RecommendationPublicAnimeSerializer(anime).data
        self.assertEqual(data['cover_url'], '')
        self.assertEqual(data['tags'], ['日常', '喜剧'])
        self.assertNotIn('source_cover_url', data)
        self.assertNotIn('nsfw', data)

    def test_hosted_cover_requires_exact_host_key_and_version(self):
        anime = RecommendationAnime(bangumi_id=7701, title_zh='正常', nsfw=False)
        for cover in ('https://media.xh-anime.com.evil.test/covers/7701.jpg',
                      'https://media.xh-anime.com/covers/7702.jpg',
                      'https://media.xh-anime.com/covers/7701.jpg?token=secret',
                      'https://user:pass@media.xh-anime.com/covers/7701.jpg'):
            anime.cover_url = cover
            self.assertEqual(RecommendationPublicAnimeSerializer(anime).data['cover_url'], '')
        anime.cover_url = 'https://media.xh-anime.com/covers/7701.jpg?v=0123456789abcdef'
        self.assertEqual(RecommendationPublicAnimeSerializer(anime).data['cover_url'], anime.cover_url)
