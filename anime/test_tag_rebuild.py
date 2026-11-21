from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient

from . import test_tag_cleanup as legacy_tests
from .models import UserProfile, UserAnimeTag, UserQuickFilter, UserAnimeRecord, TagCleanupJob
from .tag_cleanup import tag_state, save_state
from recommendations.models import RecommendationAnime


class TagRebuildTests(APITestCase):
    setUp = legacy_tests.TagCleanupTests.setUp
    record = legacy_tests.TagCleanupTests.record
    action = legacy_tests.TagCleanupTests.action
    create = legacy_tests.TagCleanupTests.create
    finish = legacy_tests.TagCleanupTests.finish
    detail = legacy_tests.TagCleanupTests.detail

    def linked(self, name='重建作品', tags=None, custom=None, genres='科幻,机战'):
        record = self.record(name, tags=tags if tags is not None else ['2021', '导演', 'EVA', '科幻', '剧场版', '原创', 'NSFW'], custom_tags=custom or ['珍藏'])
        identity = 1000 + record.pk
        RecommendationAnime.objects.create(bangumi_id=identity, title_zh=name, nsfw=False, tags=genres)
        record.verified_bangumi_id = identity
        record.save(update_fields=['verified_bangumi_id'])
        return record

    def scan(self, **options):
        return self.finish(self.create(mode='rebuild', **options), 'scan')

    def decide(self, job, record, choices, revision=None):
        return self.action(job, 'decisions', {'record_id': record.pk, 'revision': job['revision'] if revision is None else revision,
                                             'choices': [{'name': name, 'action': action} for name, action in choices]})

    def apply(self, job, **extra):
        result = self.action(job, 'apply', {'revision': job['revision'], **extra})
        self.assertEqual(result.status_code, 200, result.data)
        return self.finish(result.data, 'apply')

    def test_scan_rebuilds_only_genres_and_explains_default_protections_without_writes(self):
        record = self.linked()
        UserAnimeTag.objects.create(record=record, name='导演', color='blue')
        profile = UserProfile.objects.get(user=self.owner); profile.tag_presets = ['2021']; profile.save(update_fields=['tag_presets'])
        UserQuickFilter.objects.create(user=self.owner, name='系列筛选', tags=['EVA'])
        before = deepcopy(tag_state(record)); timestamp = record.updated_at
        with patch('requests.get', side_effect=AssertionError('No remote taxonomy lookup')):
            job = self.scan()
        row = self.detail(job)['items'][0]
        self.assertEqual(row['after'], ['科幻', '机战', '2021', '导演', 'EVA', '剧场版', '原创', 'NSFW'])
        reasons = {c['name']: c['reasons'] for c in row['details']['candidates']}
        self.assertIn('个人常用预设', reasons['2021']); self.assertIn('快捷筛选包含条件', reasons['EVA'])
        self.assertIn('该作品已有配色', reasons['导演']); self.assertIn('本人自定义', reasons['珍藏'])
        self.assertEqual(row['details']['source']['tags'], ['科幻', '机战'])
        self.assertEqual(row['personal_removed'], 0)
        record.refresh_from_db(); self.assertEqual(tag_state(record), before); self.assertEqual(record.updated_at, timestamp)

    def test_personal_removal_requires_acknowledgment_and_undo_restores_all_tag_fields(self):
        record = self.linked()
        UserAnimeTag.objects.create(record=record, name='导演', color='blue')
        outsider = UserAnimeRecord.objects.create(user=self.other, anime=record.anime)
        before, timestamp = deepcopy(tag_state(record)), record.updated_at
        job = self.scan(keep_personal=False)
        self.assertGreater(job['personal_removals'], 0)
        self.assertEqual(self.action(job, 'apply').status_code, 400)
        self.assertEqual(self.action(job, 'apply', {'revision': job['revision']}).status_code, 400)
        job = self.apply(job, confirm_personal_removal=True)
        record.refresh_from_db(); outsider.refresh_from_db(); record.anime.refresh_from_db()
        self.assertEqual(record.private_base_tags, ['科幻', '机战', '剧场版', '原创', 'NSFW'])
        self.assertEqual(record.custom_tags, []); self.assertFalse(record.tag_preferences.exists())
        self.assertEqual(record.updated_at, timestamp); self.assertEqual(record.anime.base_tags, before[0])
        self.assertEqual(outsider.private_base_tags, before[0])
        job = self.finish(self.action(job, 'undo').data, 'undo')
        record.refresh_from_db(); self.assertEqual(tag_state(record), before); self.assertEqual(job['counts']['undone'], 1)

    def test_decisions_are_durable_versioned_and_cannot_inject_or_remove_fixed_tags(self):
        record = self.linked()
        UserAnimeTag.objects.create(record=record, name='导演', color='blue')
        before = deepcopy(tag_state(record)); job = self.scan()
        for choice in [('不存在', 'personal'), ('NSFW', 'remove'), ('科幻', 'remove')]:
            self.assertEqual(self.decide(job, record, [choice]).status_code, 400)
        result = self.decide(job, record, [('EVA', 'personal'), ('导演', 'remove'), ('珍藏', 'remove')])
        self.assertEqual(result.status_code, 200, result.data); job = result.data['job']
        self.assertEqual(job['revision'], 1)
        row = self.detail(job)['items'][0]
        self.assertEqual(row['details']['after_custom'], ['EVA']); self.assertEqual(row['personal_removed'], 2)
        self.assertNotIn('EVA', row['after']); self.assertEqual(self.detail(job)['personal_removal_records'], {record.pk: 2})
        record.refresh_from_db(); self.assertEqual(tag_state(record), before)
        self.assertEqual(self.decide(job, record, [('EVA', 'keep')], revision=0).status_code, 409)
        self.assertEqual(self.action(job, 'apply', {'revision': 0, 'confirm_personal_removal': True}).status_code, 409)
        self.assertEqual(self.decide(job, record, [('EVA', 'keep'), ('EVA', 'remove')]).status_code, 400)
        job = self.apply(job, confirm_personal_removal=True)
        record.refresh_from_db(); self.assertEqual(record.custom_tags, ['EVA']); self.assertFalse(record.tag_preferences.exists())
        self.assertEqual(self.decide(job, record, [('EVA', 'keep')]).status_code, 409)
        self.finish(self.action(job, 'undo').data, 'undo'); record.refresh_from_db()
        self.assertEqual(tag_state(record), before)

    def test_decision_cannot_exceed_custom_tag_limit_or_change_record(self):
        record = self.linked(tags=['旧系列'], custom=[f'个人{i}' for i in range(200)])
        before = deepcopy(tag_state(record))
        job = self.scan()
        result = self.decide(job, record, [('旧系列', 'personal')])
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.detail(job)['revision'], 0)
        record.refresh_from_db()
        self.assertEqual(tag_state(record), before)

    def test_fine_grained_alias_stays_personal_without_duplicate_genre_badges(self):
        record = self.linked(tags=['真百', '校园', '旧系列'], genres='百合,校园')
        job = self.scan()
        row = self.detail(job)['items'][0]
        self.assertIn('真百', row['after']); self.assertNotIn('百合', row['after']); self.assertNotIn('旧系列', row['after'])
        self.assertEqual(set(row['details']['genres']), {'百合', '校园'})
        result = self.decide(job, record, [('真百', 'remove')])
        self.assertEqual(result.status_code, 200)
        self.assertIn('百合', result.data['item']['after']); self.assertNotIn('真百', result.data['item']['after'])

    def test_missing_identity_empty_restricted_and_unknown_sources_never_clear_records(self):
        records = [self.record('未关联')]
        for name, genres, nsfw in [('空题材', '', False), ('受限', '音乐', True), ('未分级', '音乐', None)]:
            row = self.linked(name, genres=genres)
            RecommendationAnime.objects.filter(bangumi_id=row.verified_bangumi_id).update(nsfw=nsfw)
            records.append(row)
        before = {r.pk: deepcopy(tag_state(r)) for r in records}
        job = self.scan(keep_personal=False)
        self.assertEqual(job['counts']['no_source'], 4)
        self.assertEqual(self.detail(job, filter='no_source')['item_count'], 4)
        self.assertEqual(self.action(job, 'apply', {'revision': 0, 'confirm_personal_removal': True}).status_code, 400)
        self.assertEqual(self.decide(job, records[0], [('2020', 'remove')]).status_code, 400)
        for row in records:
            row.refresh_from_db(); self.assertEqual(tag_state(row), before[row.pk])

    def test_unconfirmed_row_cannot_borrow_a_confirmed_rows_catalog_identity(self):
        first = self.linked('确认关联'); second = self.record('尚未确认', tags=['用途不明'])
        resolved = [{'pk': row.pk, 'resolved_bangumi_id': first.verified_bangumi_id, 'identity_state': state}
                    for row, state in ((first, 'linked'), (second, 'ambiguous'))]
        with patch('anime.tag_cleanup.resolved_records', return_value=resolved):
            job = self.finish(self.create(), 'scan')
        item = next(row for row in self.detail(job)['items'] if row['record_id'] == second.pk)
        self.assertEqual(item['after'], ['用途不明'])
        self.assertEqual(item['details']['source']['tags'], [])

    def test_changed_source_personal_protections_and_colors_block_stale_plans(self):
        record = self.linked()
        job = self.scan()
        RecommendationAnime.objects.filter(bangumi_id=record.verified_bangumi_id).update(tags='音乐')
        self.assertEqual(self.decide(job, record, [('EVA', 'personal')]).status_code, 409)
        job = self.apply(job); self.assertEqual(job['counts']['conflict'], 1)
        job = self.scan()
        UserAnimeTag.objects.create(record=record, name='EVA', color='blue')
        job = self.apply(job); self.assertEqual(job['counts']['conflict'], 1)
        job = self.scan()
        profile = UserProfile.objects.get(user=self.owner); profile.tag_presets = ['EVA']; profile.save(update_fields=['tag_presets'])
        self.assertEqual(self.decide(job, record, [('EVA', 'personal')]).status_code, 409)

    def test_color_and_custom_changes_after_apply_block_full_undo_but_score_changes_do_not(self):
        first = self.linked('先改标签'); second = self.linked('只改评分'); third = self.linked('后改配色')
        job = self.apply(self.scan(keep_personal=False), confirm_personal_removal=True)
        first.custom_tags = ['新个人标记']; first.save(update_fields=['custom_tags'])
        second.personal_score = 9; second.save(update_fields=['personal_score'])
        UserAnimeTag.objects.create(record=third, name='科幻', color='pink')
        job = self.finish(self.action(job, 'undo').data, 'undo')
        self.assertEqual(job['counts']['undo_conflict'], 2); self.assertEqual(job['counts']['undone'], 1)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.custom_tags, ['新个人标记']); self.assertEqual(second.custom_tags, ['珍藏'])
        self.assertEqual(second.personal_score, 9)

    def test_batch_failure_rolls_back_base_custom_colors_and_cursor_together(self):
        first = self.linked('事务一'); second = self.linked('事务二')
        for row in (first, second): UserAnimeTag.objects.create(record=row, name='导演', color='blue')
        before = deepcopy(tag_state(first)); job = self.scan(keep_personal=False)
        job = self.action(job, 'apply', {'revision': 0, 'confirm_personal_removal': True}).data
        def broken(record, tags, details, side):
            save_state(record, tags, details, side)
            if record.pk == second.pk: raise RuntimeError('synthetic full-state rollback')
        with patch('anime.tag_cleanup.save_state', side_effect=broken):
            with self.assertRaises(RuntimeError): self.action(job, 'step', {'phase': 'apply', 'cursor': 0})
        first.refresh_from_db(); self.assertEqual(tag_state(first), before)
        self.assertEqual(TagCleanupJob.objects.get(pk=job['id']).apply_cursor, 0)
        self.finish(job, 'apply')

    def test_old_applied_snapshot_can_undo_and_old_scans_can_be_replaced(self):
        record = self.linked()
        job = self.finish(self.create(), 'scan'); job = self.apply(job)
        stored = TagCleanupJob.objects.get(pk=job['id']); stored.rule_version = 'tag-cleanup.v1:genres.v8'; stored.save(update_fields=['rule_version'])
        stored.items.update(details={})
        self.assertEqual(self.finish(self.action(job, 'undo').data, 'undo')['counts']['undone'], 1)
        old = self.create(); stored = TagCleanupJob.objects.get(pk=old['id']); stored.rule_version = 'tag-cleanup.v1:genres.v8'; stored.save(update_fields=['rule_version'])
        self.assertTrue(self.detail(old)['obsolete'])
        fresh = self.scan(); self.assertEqual(fresh['status'], 'ready')
        stored.refresh_from_db(); self.assertEqual(stored.status, 'expired'); self.assertEqual(stored.items.count(), 0)

    def test_decisions_and_snapshot_download_are_owner_only(self):
        record = self.linked(); job = self.scan()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.decide(job, record, [('EVA', 'personal')]).status_code, 404)
        self.assertEqual(self.client.get(reverse('anime_api:tag-cleanup-changes', kwargs={'pk': job['id']})).status_code, 404)


class TagRebuildConcurrencyTests(TransactionTestCase):
    def test_decision_and_apply_cannot_commit_different_preview_versions(self):
        if connection.vendor != 'postgresql': self.skipTest('Requires real PostgreSQL row locks')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        helper = TagRebuildTests(); helper.setUp()
        record = helper.linked(); job = helper.scan()
        barrier = Barrier(2)
        def call(which):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(get_user_model().objects.get(pk=helper.owner.pk))
                action = 'decisions' if which else 'apply'
                body = {'record_id': record.pk, 'revision': 0, 'choices': [{'name': 'EVA', 'action': 'personal'}]} if which else {'revision': 0}
                barrier.wait(timeout=10)
                response = client.post(reverse(f'anime_api:tag-cleanup-{action}', kwargs={'pk': job['id']}), body, format='json')
                return response.status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(call, [0, 1]))
        self.assertCountEqual(results, [200, 409])
