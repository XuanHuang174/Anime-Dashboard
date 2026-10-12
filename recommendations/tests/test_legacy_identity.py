import io
import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.db import OperationalError
from django.db.models.query import QuerySet
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from recommendations.binding import auto_link, bind_record, rollback_event, export_reference, restore_reference, IdentityConflict
from recommendations.engine import user_context
from recommendations.matching import CatalogMatcher, catalog_matcher, effective_metadata
from recommendations.models import RecommendationAnime, RecommendationIdentityEvent, RecommendationRecordIdentity
from recommendations.resolution import resolved_records, LINK_FIELDS
from recommendations.services import profile_contract
from recommendations.identity import recommendation_identity


class LegacyFixtures:
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='legacy-owner')
        self.other = get_user_model().objects.create_user(username='legacy-other')

    def work(self, identity=100, **changes):
        values = {'bangumi_id': identity, 'title_zh': '作品', 'title_jp': 'Original', 'season': '2026-04',
                  'status_type': 'TV', 'nsfw': False, 'tags': '日常,音乐'}
        values.update(changes)
        return RecommendationAnime.objects.create(**values)

    def record(self, *, user=None, title='《作品》', original='Original', period='2026-4', score='9.0', bgm_id=None):
        anime = Anime.objects.create(chinese_title=title, japanese_title=original, airing_period=period,
                                     base_tags=['日常', '音乐'], bgm_id=bgm_id)
        return UserAnimeRecord.objects.create(user=user or self.user, anime=anime, watch_status='completed',
                                              personal_score=score, review='必须完整保留的私人长评')


class MatchingTests(LegacyFixtures, TestCase):
    def test_missing_alias_table_rollout_preserves_authoritative_profile_and_explains_pending(self):
        self.work(); self.record(bgm_id=100)
        original = QuerySet.values
        def without_alias_table(query, *fields, **kwargs):
            if any(field in LINK_FIELDS for field in fields):
                raise OperationalError('no such table: recommendations_recommendationrecordidentity')
            return original(query, *fields, **kwargs)
        with patch.object(QuerySet, 'values', without_alias_table):
            profile = profile_contract(self.user)
        self.assertFalse(profile['identity_service_ready'])
        self.assertEqual(profile['eligible_sample_count'],1)

    def test_unrelated_database_failures_are_not_disguised_as_missing_identities(self):
        with patch.object(QuerySet,'values',side_effect=OperationalError('database connection timed out')):
            with self.assertRaises(OperationalError):
                profile_contract(self.user)

    def test_profile_and_recommendation_gets_never_auto_attach(self):
        self.work(); self.record()
        profile_contract(self.user); user_context(self.user)
        self.assertFalse(RecommendationRecordIdentity.objects.exists())

    def test_strict_match_handles_brackets_and_month_padding_not_season_guessing(self):
        self.work()
        record = self.record()
        self.assertEqual(auto_link(record)['status'], 'linked')
        record.refresh_from_db(); record.anime.refresh_from_db()
        self.assertIsNone(record.anime.bgm_id)
        self.assertEqual(str(record.personal_score), '9.0')
        self.assertEqual(record.review, '必须完整保留的私人长评')
        self.assertEqual(record.watch_status, 'completed')
        self.assertEqual(record.private_chinese_title, '《作品》')

    def test_existing_public_bgm_identity_does_not_block_private_alias(self):
        self.work()
        Anime.objects.create(chinese_title='作品', japanese_title='Original', airing_period='2026-04', bgm_id=100)
        first = self.record()
        second = self.record(user=self.other, title='作品 ')
        auto_link(first); auto_link(second)
        self.assertEqual(RecommendationRecordIdentity.objects.count(), 2)
        self.assertEqual(profile_contract(self.user)['eligible_sample_count'], 1)
        self.assertEqual(profile_contract(self.other)['eligible_sample_count'], 1)
        self.assertEqual(recommendation_identity(user=self.user,bangumi_id=100)['record_id'],first.pk)
        self.assertEqual(recommendation_identity(user=self.user,bangumi_id=100)['mapping_status'],'aligned_alias')

    def test_same_title_multiple_targets_including_unsafe_is_not_automatic(self):
        self.work()
        self.work(101, nsfw=True)
        record = self.record()
        self.assertEqual(auto_link(record)['status'], 'needs_confirmation')
        self.assertFalse(RecommendationRecordIdentity.objects.exists())

    def test_unknown_dates_different_quarters_and_format_conflicts_require_review(self):
        self.work()
        for index, period in enumerate(['2026', '2026-7', '未定档']):
            record = self.record(title=f'作品{index}', period=period)
            self.assertNotEqual(auto_link(record)['status'], 'linked')
        self.work(200, title_zh='OVA 作品', title_jp='OVA Story', status_type='TV')
        record = self.record(title='OVA 作品', original='OVA Story')
        self.assertEqual(auto_link(record)['status'], 'no_safe_candidate')

    def test_duplicates_are_one_seed_and_highest_rating_chosen(self):
        self.work()
        first = self.record(score='8.5')
        second = self.record(title='作品', score='9.5')
        auto_link(first); auto_link(second)
        profile = profile_contract(self.user)
        self.assertEqual(profile['eligible_sample_count'], 1)
        self.assertEqual(profile['diagnostics']['linked_high_score_record_count'], 2)
        self.assertEqual(user_context(self.user)['seeds'][0]['score'], 9.5)
        self.assertIn(100, user_context(self.user)['owned'])

    def test_private_change_affects_only_one_user_and_marks_stale_without_overwrite(self):
        self.work()
        first = self.record()
        second = UserAnimeRecord.objects.create(user=self.other, anime=first.anime, watch_status='completed', personal_score='9.0')
        auto_link(first); auto_link(second)
        first.private_chinese_title = '另一部作品'; first.save(update_fields=['private_chinese_title'])
        self.assertEqual(profile_contract(self.user)['readiness'], 'needs_identity')
        self.assertEqual(profile_contract(self.user)['diagnostics']['stale_identity_count'], 1)
        self.assertEqual(profile_contract(self.other)['eligible_sample_count'], 1)
        self.assertEqual(auto_link(first)['status'], 'existing_link')

    def test_fresh_nsfw_and_catalog_changes_fail_closed(self):
        work = self.work(); record = self.record(); auto_link(record)
        RecommendationAnime.objects.filter(pk=work.pk).update(nsfw=True)
        profile = profile_contract(self.user)
        self.assertEqual(profile['eligible_sample_count'], 0)
        self.assertEqual(profile['diagnostics']['unavailable_identity_count'], 1)
        RecommendationAnime.objects.filter(pk=work.pk).update(nsfw=False, title_zh='资料已变更')
        self.assertEqual(profile_contract(self.user)['diagnostics']['stale_identity_count'], 1)

    def test_signed_backup_preserves_confirmed_alias_not_arbitrary_ids(self):
        self.work(); record = self.record(title='我的自定义标题')
        bind_record(record_id=record.pk, user_id=self.user.pk, target_id=100, source='user_confirmed')
        row = resolved_records(self.user)[0]
        reference = export_reference(self.user, row)
        clone = self.record(title='我的自定义标题 ', score='8.8')
        self.assertTrue(restore_reference(self.user, clone, reference))
        other = self.record(user=self.other, title='我的自定义标题  ')
        self.assertFalse(restore_reference(self.other, other, reference))
        self.assertFalse(restore_reference(self.user, clone, {'bangumi_id':100,'proof':'forged'}))

    def test_rollback_is_recoverable_and_never_reactivates_on_auto_scan(self):
        self.work(); record = self.record(); run = uuid.uuid4()
        auto_link(record, batch_id=run)
        event = RecommendationIdentityEvent.objects.get(batch_id=run)
        self.assertEqual(rollback_event(event), 'rolled_back')
        self.assertFalse(RecommendationRecordIdentity.objects.get(record=record).active)
        self.assertEqual(rollback_event(event), 'changed_link')
        self.assertEqual(auto_link(record)['status'], 'existing_link')
        self.assertEqual(UserAnimeRecord.objects.get(pk=record.pk).review, record.review)

    def test_rollback_skips_later_user_confirmation_or_metadata_edit(self):
        self.work(); record=self.record(); run=uuid.uuid4(); auto_link(record,batch_id=run)
        event=RecommendationIdentityEvent.objects.get(batch_id=run)
        record.private_chinese_title='新标题'; record.save(update_fields=['private_chinese_title'])
        self.assertEqual(rollback_event(event), 'changed_metadata')
        bind_record(record_id=record.pk,user_id=self.user.pk,target_id=100,source='user_confirmed',expected_version=1)
        self.assertEqual(rollback_event(event), 'changed_link')

    def test_command_audit_apply_idempotence_and_production_guard(self):
        self.work(); self.record(); output=io.StringIO()
        call_command('sync_recommendation_identities', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['counts'], {'strong_unique':1})
        self.assertFalse(RecommendationRecordIdentity.objects.exists())
        with override_settings(DATABASE_URL='configured-remote-not-a-real-secret'):
            with self.assertRaises(CommandError):
                call_command('sync_recommendation_identities', mode='apply', stdout=io.StringIO())
        for _ in range(2):
            call_command('sync_recommendation_identities', mode='apply', stdout=io.StringIO(),stderr=io.StringIO())
        self.assertEqual(RecommendationRecordIdentity.objects.count(),1)
        self.assertEqual(RecommendationIdentityEvent.objects.count(),1)

    def test_rollback_requires_an_explicit_original_batch(self):
        with self.assertRaises(CommandError):
            call_command('sync_recommendation_identities',mode='rollback',stdout=io.StringIO())


class IdentityAPITests(LegacyFixtures, APITestCase):
    url='/api/recommendations/identities/'

    def setUp(self):
        super().setUp()
        self.work(); self.record = self.record(title='私人别名')
        self.client.force_authenticate(self.user)

    def candidate(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        return response.data['results'][0]['candidates'][0]

    def body(self,candidate):
        return {'record_id':self.record.pk,'bangumi_id':candidate['bangumi_id'],'ticket':candidate['ticket'],
                'expected_version':0,'confirmed':True}

    def test_auth_owner_and_unknown_fields(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code,401)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).data['count'],0)
        self.assertEqual(self.client.get(self.url,{'record_id':self.record.pk}).status_code,404)
        self.assertEqual(self.client.get(self.url,{'user_id':self.user.pk}).status_code,400)

    def test_confirm_ticket_is_owned_scoped_and_idempotent(self):
        candidate=self.candidate(); body=self.body(candidate)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(self.url,body,format='json').status_code,404)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.patch(self.url,body,format='json').status_code,200)
        self.assertEqual(self.client.patch(self.url,body,format='json').status_code,200)
        self.assertEqual(RecommendationIdentityEvent.objects.count(),1)
        self.assertEqual(self.client.get('/api/recommendations/profile/').data['eligible_sample_count'],1)

    def test_forged_target_and_changed_record_rejected(self):
        candidate=self.candidate(); body=self.body(candidate)
        self.work(200,title_zh='别的作品')
        self.assertEqual(self.client.patch(self.url,{**body,'bangumi_id':200},format='json').status_code,409)
        self.record.private_japanese_title='另一个原名'; self.record.save(update_fields=['private_japanese_title'])
        self.assertEqual(self.client.patch(self.url,body,format='json').status_code,409)
        self.assertFalse(RecommendationRecordIdentity.objects.exists())

    def test_unsafe_targets_not_published_and_current_safety_is_rechecked(self):
        self.work(200,title_zh='成人秘密标题',nsfw=True)
        candidate=self.candidate()
        response=self.client.get(self.url,{'record_id':self.record.pk,'keyword':'成人秘密标题'})
        self.assertEqual(response.data['results'][0]['candidates'],[])
        RecommendationAnime.objects.filter(bangumi_id=100).update(nsfw=None)
        self.assertEqual(self.client.patch(self.url,self.body(candidate),format='json').status_code,404)

    def test_missing_confirmation_and_extra_fields_fail(self):
        candidate=self.candidate(); body=self.body(candidate)
        for changed in [{**body,'confirmed':False},{**body,'user_id':self.other.pk},{**body,'ticket':'forged'}]:
            self.assertIn(self.client.patch(self.url,changed,format='json').status_code,[400,409])
        self.assertFalse(RecommendationRecordIdentity.objects.exists())

    def test_search_can_find_safe_alias_but_never_auto_writes(self):
        response=self.client.get(self.url,{'record_id':self.record.pk,'keyword':'作品'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['results'][0]['candidates'][0]['bangumi_id'],100)
        self.assertFalse(RecommendationRecordIdentity.objects.exists())

    def test_backup_api_roundtrip_preserves_identity_and_private_fields(self):
        bind_record(record_id=self.record.pk,user_id=self.user.pk,target_id=100,source='user_confirmed')
        payload=json.loads(self.client.get('/api/export/').content)
        self.assertIsNotNone(payload['records'][0]['anime']['identity_reference'])
        # Existing import is destructive by design; only disposable memory fixtures are used here.
        result=self.client.post('/api/import/',payload,format='json')
        self.assertEqual(result.status_code,200)
        restored=UserAnimeRecord.objects.get(user=self.user)
        self.assertEqual(restored.review,'必须完整保留的私人长评')
        self.assertEqual(str(restored.personal_score),'9.0')
        self.assertEqual(profile_contract(self.user)['eligible_sample_count'],1)

    def test_create_record_attempts_strict_local_match_and_preserves_ambiguity(self):
        clean=Anime.objects.create(chinese_title='《作品》',japanese_title='Original',airing_period='2026-4')
        result=self.client.post('/api/records/',{'anime_id':clean.pk,'watch_status':'completed','personal_score':'9.0'},format='json')
        self.assertEqual(result.status_code,201)
        self.assertTrue(RecommendationRecordIdentity.objects.filter(record__anime=clean).exists())

    def test_export_reference_forgery_does_not_attach_an_arbitrary_catalog_id(self):
        payload=json.loads(self.client.get('/api/export/').content)
        payload['records'][0]['anime']['japanese_title']='Unmatched'
        payload['records'][0]['anime']['identity_reference']={'bangumi_id':100,'proof':'forged'}
        result=self.client.post('/api/import/',payload,format='json')
        self.assertEqual(result.status_code,200)
        self.assertFalse(RecommendationRecordIdentity.objects.exists())
