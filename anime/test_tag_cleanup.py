from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from django.db import connection, close_old_connections
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from anime.models import Anime, UserAnimeRecord, UserAnimeTag, UserProfile, UserQuickFilter, TagCleanupJob
from recommendations.models import RecommendationAnime


class TagCleanupTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('clean-owner')
        self.other = get_user_model().objects.create_user('clean-other')
        self.client.force_authenticate(self.owner)
        self.url = reverse('anime_api:tag-cleanup-list')

    def record(self, name='合成作品', tags=None, **kwargs):
        anime = Anime.objects.create(chinese_title=name, studio='合成公司', base_tags=tags if tags is not None else ['2020', '治愈系', '合成公司', '合成作品', '用途不明'])
        return UserAnimeRecord.objects.create(user=self.owner, anime=anime, **kwargs)

    def action(self, job, action, data=None):
        return self.client.post(reverse(f'anime_api:tag-cleanup-{action}', kwargs={'pk': job['id']}), data or {}, format='json')

    def create(self, **extra):
        response = self.client.post(self.url, {'request_id': str(uuid4()), 'scope': 'all', **extra}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def finish(self, job, phase):
        while job['status'] == {'scan': 'scanning', 'apply': 'applying', 'undo': 'undoing'}[phase]:
            response = self.action(job, 'step', {'phase': phase, 'cursor': job[f'{phase}_cursor']})
            self.assertEqual(response.status_code, 200, response.data)
            job = response.data
        return job

    def detail(self, job, **params):
        response = self.client.get(reverse('anime_api:tag-cleanup-detail', kwargs={'pk': job['id']}), params)
        self.assertEqual(response.status_code, 200)
        return response.data

    def apply(self, job, excluded_ids=None):
        response = self.action(job, 'apply', {'excluded_ids': excluded_ids or []})
        self.assertEqual(response.status_code, 200, response.data)
        return self.finish(response.data, 'apply')

    def test_scan_is_readonly_preserves_all_protected_sources_and_unknowns(self):
        record = self.record(tags=['2020', '2021', '2022', '2023', '合成公司', '合成作品', '治愈系', '用途不明', 'TV', 'OVA', '泡面番', '二刷'], custom_tags=['2020', '自定义'])
        profile = UserProfile.objects.get(user=self.owner)
        profile.tag_presets = ['2021']; profile.save(update_fields=['tag_presets'])
        UserQuickFilter.objects.create(user=self.owner, name='保护组合', tags=['2022'])
        UserAnimeTag.objects.create(record=record, name='2023', color='blue')
        before = record.private_base_tags.copy(); timestamp = record.updated_at
        with patch('requests.get', side_effect=AssertionError('No external calls')):
            job = self.finish(self.create(), 'scan')
        item = self.detail(job)['items'][0]
        self.assertEqual(item['after'], ['2020', '2021', '2022', '2023', '治愈', '用途不明', 'TV', 'OVA', '泡面番', '二刷'])
        self.assertIn('自定义', item['protected']); self.assertIn('用途不明', item['uncertain'])
        record.refresh_from_db(); self.assertEqual(record.private_base_tags, before); self.assertEqual(record.updated_at, timestamp)
        self.assertEqual(self.detail(job, filter='protected')['item_count'], 1)
        self.assertEqual(job['change_records'], 1)
        self.assertEqual(self.detail(job, filter='conflicts')['item_count'], 0)

    def test_apply_undo_and_retries_do_not_touch_public_or_other_record_fields(self):
        record = self.record(personal_score=Decimal('8.5'), watch_status='watching', review='原评价', custom_tags=['二刷'])
        outsider = UserAnimeRecord.objects.create(user=self.other, anime=record.anime)
        before = record.private_base_tags.copy(); timestamp = record.updated_at
        job = self.finish(self.create(), 'scan'); job = self.apply(job)
        record.refresh_from_db(); self.assertEqual(record.private_base_tags, ['治愈', '用途不明'])
        self.assertEqual(record.updated_at, timestamp)
        duplicate = self.action(job, 'step', {'phase': 'apply', 'cursor': 0}); self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(self.action(job, 'apply').status_code, 200)
        record.personal_score = Decimal('9.1'); record.save(update_fields=['personal_score'])
        response = self.action(job, 'undo'); self.assertEqual(response.status_code, 200)
        job = self.finish(response.data, 'undo')
        record.refresh_from_db(); outsider.refresh_from_db(); record.anime.refresh_from_db()
        self.assertEqual(record.private_base_tags, before); self.assertEqual(outsider.private_base_tags, before); self.assertEqual(record.anime.base_tags, before)
        self.assertEqual(record.personal_score, Decimal('9.1')); self.assertEqual(record.review, '原评价'); self.assertEqual(record.watch_status, 'watching'); self.assertEqual(record.custom_tags, ['二刷'])
        self.assertEqual(self.action(job, 'undo').status_code, 200)

    def test_undo_skips_post_application_edits_and_deleted_records(self):
        first, second, third = self.record('一'), self.record('二'), self.record('三')
        job = self.apply(self.finish(self.create(), 'scan'))
        first.private_base_tags = ['用户新编辑']; first.save(update_fields=['private_base_tags'])
        deleted_id = second.pk
        second.delete()
        job = self.finish(self.action(job, 'undo').data, 'undo')
        first.refresh_from_db(); third.refresh_from_db()
        self.assertEqual(first.private_base_tags, ['用户新编辑']); self.assertEqual(job['counts']['undo_conflict'], 2)
        conflicts = self.detail(job, filter='conflicts')
        self.assertEqual(conflicts['item_count'], 2)
        self.assertEqual({row['record_id'] for row in conflicts['items']}, {first.pk, deleted_id})
        self.assertEqual(job['change_records'], 3)
        self.assertIn('2020', third.private_base_tags)

    def test_only_confirmed_safe_catalog_enriches_and_source_changes_conflict(self):
        safe = self.record('安全', tags=['2020']); adult = self.record('受限', tags=['2020']); unknown = self.record('未知', tags=['2020'])
        for identity, record, rating in ((10, safe, False), (11, adult, True), (12, unknown, None)):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=record.private_chinese_title, nsfw=rating, tags='音乐,科幻')
            record.verified_bangumi_id = identity; record.save(update_fields=['verified_bangumi_id'])
        job = self.finish(self.create(), 'scan'); rows = self.detail(job)['items']
        self.assertEqual(set(rows[0]['after']), {'音乐', '科幻'}); self.assertEqual(rows[1]['after'], []); self.assertEqual(rows[2]['after'], [])
        RecommendationAnime.objects.filter(bangumi_id=10).update(nsfw=True)
        job = self.apply(job); self.assertEqual(job['counts']['conflict'], 1)
        safe.refresh_from_db(); self.assertEqual(safe.private_base_tags, ['2020'])

    def test_user_edits_or_new_protection_since_preview_are_never_overwritten(self):
        record = self.record()
        job = self.finish(self.create(), 'scan')
        profile = UserProfile.objects.get(user=self.owner); profile.tag_presets = ['2020']; profile.save(update_fields=['tag_presets'])
        job = self.apply(job); self.assertEqual(job['counts']['conflict'], 1)
        record.refresh_from_db(); self.assertIn('2020', record.private_base_tags)
        job = self.finish(self.create(), 'scan')
        record.private_base_tags = ['之后的编辑']; record.save(update_fields=['private_base_tags'])
        job = self.apply(job); self.assertEqual(job['counts']['conflict'], 1)
        record.refresh_from_db(); self.assertEqual(record.private_base_tags, ['之后的编辑'])

    def test_filters_cover_whole_collection_and_page_items_are_bounded(self):
        for n in range(24): self.record(f'范围作品{n}')
        self.record('不匹配')
        job = self.create(scope='filtered', filters={'search': '范围', 'status': 'plan'})
        self.record('范围后来新增')
        with patch('anime.tag_cleanup.BATCH', 7):
            first = self.action(job, 'step', {'phase': 'scan', 'cursor': 0}).data
            self.assertEqual(first['scan_cursor'], 7)
            again = self.action(job, 'step', {'phase': 'scan', 'cursor': 0}).data
            self.assertEqual(again['scan_cursor'], 7)
            job = self.finish(first, 'scan')
        self.assertEqual(job['total'], 24); self.assertEqual(len(self.detail(job)['items']), 20); self.assertEqual(len(self.detail(job, page=2)['items']), 4)
        excluded = self.detail(job)['items'][0]['record_id']
        job = self.apply(job, [excluded]); self.assertEqual(job['counts']['applied'], 23)
        self.assertIn('2020', UserAnimeRecord.objects.get(pk=excluded).private_base_tags)

    def test_owner_authentication_strict_inputs_and_server_held_proposals(self):
        self.record(); job = self.finish(self.create(), 'scan')
        response = self.client.get(reverse('anime_api:tag-cleanup-detail', kwargs={'pk': job['id']}))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.action(job, 'apply', {'excluded_ids': [999999]}).status_code, 400)
        self.assertEqual(self.action(job, 'apply', {'after': ['恶意覆盖']}).status_code, 400)
        malformed_path = f"{self.url}{'-' * 36}/undo/"
        self.assertEqual(self.client.post(malformed_path, {}, format='json').status_code, 404)
        self.client.force_authenticate(self.other)
        for action, payload in (('apply', {}), ('undo', {}), ('step', {'phase': 'scan', 'cursor': 0})):
            self.assertEqual(self.action(job, action, payload).status_code, 404)
        self.assertEqual(self.client.get(reverse('anime_api:tag-cleanup-detail', kwargs={'pk': job['id']})).status_code, 404)
        self.assertEqual(self.client.get(reverse('anime_api:tag-cleanup-changes', kwargs={'pk': job['id']})).status_code, 404)
        self.client.force_authenticate(None); self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_legacy_snapshot_and_expiration_do_not_change_effective_non_tag_metadata(self):
        record = self.record()
        UserAnimeRecord.objects.filter(pk=record.pk).update(metadata_initialized=False, private_base_tags=[])
        record.refresh_from_db(); before = record.effective_anime_metadata()
        job = self.apply(self.finish(self.create(), 'scan'))
        record.refresh_from_db(); after = record.effective_anime_metadata()
        for field in before:
            if field != 'base_tags': self.assertEqual(before[field], after[field], field)
        TagCleanupJob.objects.filter(pk=job['id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.detail(job)['items'], [])
        self.assertEqual(self.action(job, 'undo').status_code, 410)
        call_command('purge_tag_cleanup_history', verbosity=0)
        self.assertEqual(TagCleanupJob.objects.get(pk=job['id']).status, 'expired')
        record.refresh_from_db(); self.assertEqual(record.private_base_tags, after['base_tags'])

    def test_recent_five_retention_create_idempotence_and_partial_resume(self):
        record = self.record()
        token = str(uuid4()); job = self.create(request_id=token)
        repeated = self.client.post(self.url, {'request_id': token, 'scope': 'all'}, format='json')
        self.assertEqual(repeated.data['id'], job['id'])
        jobs = []
        for n in range(6):
            if n: job = self.create()
            job = self.apply(self.finish(job, 'scan')); jobs.append(job)
            record.private_base_tags = ['2020']; record.save(update_fields=['private_base_tags'])
        old = TagCleanupJob.objects.get(pk=jobs[0]['id']); self.assertEqual(old.status, 'expired'); self.assertEqual(old.items.first().before, [])
        self.assertEqual(self.action(jobs[0], 'undo').status_code, 410)
        self.assertEqual(len(self.client.get(self.url).data), 5)

    def test_step_rolls_back_entire_batch_on_failure_then_can_be_retried(self):
        from anime.tag_cleanup import save_tags
        first = self.record('原一'); second = self.record('原二')
        job = self.finish(self.create(), 'scan'); job = self.action(job, 'apply').data
        def broken(record, tags):
            if record.pk == second.pk: raise RuntimeError('synthetic-write-error')
            save_tags(record, tags)
        with patch('anime.tag_cleanup.save_tags', side_effect=broken):
            with self.assertRaises(RuntimeError): self.action(job, 'step', {'phase': 'apply', 'cursor': 0})
        first.refresh_from_db(); self.assertIn('2020', first.private_base_tags)
        self.assertEqual(TagCleanupJob.objects.get(pk=job['id']).apply_cursor, 0)
        job = self.finish(job, 'apply'); self.assertEqual(job['counts']['applied'], 2)

    def test_partially_applied_task_can_be_undone_without_writing_remaining_records(self):
        first, second = self.record('部分一'), self.record('部分二')
        job = self.finish(self.create(), 'scan'); job = self.action(job, 'apply').data
        with patch('anime.tag_cleanup.BATCH', 1):
            job = self.action(job, 'step', {'phase': 'apply', 'cursor': 0}).data
        self.assertEqual(job['status'], 'applying'); self.assertEqual(job['counts']['applied'], 1)
        job = self.finish(self.action(job, 'undo').data, 'undo')
        first.refresh_from_db(); second.refresh_from_db()
        self.assertIn('2020', first.private_base_tags); self.assertIn('2020', second.private_base_tags)
        self.assertEqual(self.action(job, 'step', {'phase': 'apply', 'cursor': 1}).status_code, 409)

    def test_new_personal_protection_after_apply_blocks_undo(self):
        record = self.record()
        job = self.apply(self.finish(self.create(), 'scan'))
        profile = UserProfile.objects.get(user=self.owner)
        profile.tag_presets = ['新保护标记']; profile.save(update_fields=['tag_presets'])
        job = self.finish(self.action(job, 'undo').data, 'undo')
        self.assertEqual(job['counts']['undo_conflict'], 1)
        record.refresh_from_db(); self.assertEqual(record.private_base_tags, ['治愈', '用途不明'])

    def test_invalid_legacy_tags_are_skipped_and_obsolete_rule_cannot_apply(self):
        record = self.record()
        UserAnimeRecord.objects.filter(pk=record.pk).update(private_base_tags={'old': 'malformed'})
        job = self.finish(self.create(), 'scan')
        self.assertEqual(self.detail(job)['items'][0]['changes'], [])
        record.refresh_from_db(); self.assertEqual(record.private_base_tags, {'old': 'malformed'})
        record.private_base_tags = ['2020']; record.save(update_fields=['private_base_tags'])
        job = self.finish(self.create(), 'scan')
        TagCleanupJob.objects.filter(pk=job['id']).update(rule_version='obsolete')
        self.assertEqual(self.action(job, 'apply').status_code, 409)
        record.refresh_from_db(); self.assertEqual(record.private_base_tags, ['2020'])


class TagCleanupConcurrencyTests(TransactionTestCase):
    def test_duplicate_batches_are_serialized_by_owner_and_cursor(self):
        if connection.vendor != 'postgresql': self.skipTest('Requires real PostgreSQL row locking')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user = get_user_model().objects.create_user('clean-concurrent')
        anime = Anime.objects.bulk_create([Anime(chinese_title=f'并发{i}', base_tags=['2020']) for i in range(60)])
        UserAnimeRecord.objects.bulk_create([UserAnimeRecord(user=user, anime=row) for row in anime])
        client = APIClient(); client.force_authenticate(user)
        job = client.post(reverse('anime_api:tag-cleanup-list'), {'request_id': str(uuid4()), 'scope': 'all'}, format='json').data
        def twice(phase):
            barrier = Barrier(2)
            def call():
                close_old_connections()
                try:
                    worker = APIClient(); worker.force_authenticate(get_user_model().objects.get(pk=user.pk)); barrier.wait(timeout=10)
                    result = worker.post(reverse('anime_api:tag-cleanup-step', kwargs={'pk': job['id']}), {'phase': phase, 'cursor': 0}, format='json')
                    return result.status_code, result.data[f'{phase}_cursor']
                finally: close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool: return list(pool.map(lambda _: call(), range(2)))
        self.assertEqual(twice('scan'), [(200, 50), (200, 50)])
        client.post(reverse('anime_api:tag-cleanup-step', kwargs={'pk': job['id']}), {'phase': 'scan', 'cursor': 50}, format='json')
        client.post(reverse('anime_api:tag-cleanup-apply', kwargs={'pk': job['id']}), {}, format='json')
        self.assertEqual(twice('apply'), [(200, 50), (200, 50)])
        self.assertEqual(TagCleanupJob.objects.get(pk=job['id']).items.filter(status='applied').count(), 50)
