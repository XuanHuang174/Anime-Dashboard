import copy
import json
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from rest_framework.test import APITestCase, APIClient

from .models import (Anime, UserAnimeRecord, CurationLibrary, CurationList, CurationEntry,
                     CurationOperation, WatchEvent, WatchRound, WatchPlan)


class CurationTests(APITestCase):
    def setUp(self):
        caches['throttle'].clear()
        self.addCleanup(caches['throttle'].clear)
        self.user = get_user_model().objects.create_user('curation-owner')
        self.other = get_user_model().objects.create_user('curation-other')
        self.client.force_authenticate(self.user)
        self.record = self.work('雨天的合成作品')

    def work(self, title, user=None):
        return UserAnimeRecord.objects.create(user=user or self.user,
            anime=Anime.objects.get_or_create(chinese_title=title, japanese_title='Synthetic ' + title, airing_period='2026-01')[0],
            review='作品长评', watch_status='on_hold', watched_eps=3, total_eps=12)

    def read(self, list_id=None, **params):
        result = self.client.get(f'/api/curations/{str(list_id) + "/" if list_id else ""}', params)
        self.assertEqual(result.status_code, 200, result.data)
        return result.data

    def command(self, action, **fields):
        body = {'action': action, 'version': self.read()['version'], 'operation_id': str(uuid4()), **fields}
        return self.client.post('/api/curations/', body, format='json')

    def create(self, **fields):
        result = self.command('create', title='适合雨天慢慢看', **fields)
        self.assertEqual(result.status_code, 200, result.data)
        return result.data['list_id']

    def add(self, list_id, *records):
        result = self.command('add', list_id=list_id, record_ids=[r.pk for r in records or (self.record,)])
        self.assertEqual(result.status_code, 200, result.data)
        return self.read(list_id)['results']

    def export(self):
        result = self.client.get('/api/export/'); self.assertEqual(result.status_code, 200)
        return json.loads(result.content)

    def restore(self, payload, query=''):
        return self.client.post('/api/import/' + query, payload, format='json')

    def test_empty_reads_are_private_and_do_not_create_state(self):
        response = self.client.get('/api/curations/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['version'], 0)
        self.assertEqual(response.data['results'], [])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(CurationLibrary.objects.exists())
        self.client.force_authenticate(None)
        for method in ('get', 'post'):
            response = getattr(self.client, method)('/api/curations/')
            self.assertIn(response.status_code, (401, 403))
            self.assertIn('no-store', response['Cache-Control'])

    def test_manual_membership_order_reason_and_cover_do_not_change_personal_facts(self):
        original = list(UserAnimeRecord.objects.values())
        first = self.create(description='专题简介')
        second = self.work('第二部')
        rows = self.add(first, second, self.record)
        self.assertEqual([e['record']['id'] for e in rows], [second.pk, self.record.pk])
        self.command('update-entry', list_id=first, entry_id=rows[1]['id'], reason='与本片单有关的理由🌸')
        self.command('update', list_id=first, cover_entry_id=rows[1]['id'], pinned=True, archived=True)
        self.assertEqual(self.read(first)['collection']['cover']['id'], self.record.pk)
        self.assertEqual(self.read(first)['results'][1]['reason'], '与本片单有关的理由🌸')
        self.assertEqual(self.read()['count'], 0)
        self.assertEqual(self.read(state='archived')['count'], 1)
        self.assertEqual(UserAnimeRecord.objects.values().get(pk=self.record.pk), original[0])
        self.assertEqual(WatchEvent.objects.count() + WatchRound.objects.count() + WatchPlan.objects.count(), 0)
        self.record.personal_score = 9; self.record.save()
        self.assertEqual([e['record']['id'] for e in self.read(first)['results']], [second.pk, self.record.pk])
        self.assertEqual(self.add(first, self.record)[1]['id'], rows[1]['id'])
        other_list = self.create(); self.add(other_list, self.record)
        self.assertEqual(CurationEntry.objects.filter(record=self.record).count(), 2)

    def test_receipt_retry_is_exact_and_cannot_resurrect_a_deleted_list(self):
        body = {'action': 'create', 'title': '幂等创建', 'version': 0, 'operation_id': str(uuid4())}
        result = self.client.post('/api/curations/', body, format='json')
        list_id = result.data['list_id']; self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.post('/api/curations/', body, format='json').data['replayed'], True)
        self.assertEqual(self.client.post('/api/curations/', {**body, 'title': '不同请求'}, format='json').status_code, 409)
        self.command('delete', list_id=list_id)
        retried = self.client.post('/api/curations/', body, format='json')
        self.assertEqual(retried.data['collection'], None)
        self.assertEqual(CurationList.objects.count(), 0)

    def test_reorder_requires_full_unique_members_and_rejects_old_versions(self):
        list_id = self.create(); rows = self.add(list_id, self.record, self.work('二'), self.work('三'))
        ids = [row['id'] for row in rows]
        for bad in (ids[:1], ids + [str(uuid4())], [ids[0]] * 3):
            self.assertEqual(self.command('reorder', list_id=list_id, entry_ids=bad).status_code, 400)
        old = self.read()['version']
        self.assertEqual(self.command('reorder', list_id=list_id, entry_ids=ids[::-1]).status_code, 200)
        self.assertEqual([e['id'] for e in self.read(list_id)['results']], ids[::-1])
        body = {'action': 'update', 'title': '不能覆盖', 'list_id': list_id, 'version': old, 'operation_id': str(uuid4())}
        self.assertEqual(self.client.post('/api/curations/', body, format='json').status_code, 409)
        self.assertEqual(self.client.get(f'/api/curations/{list_id}/', {'version': old}).status_code, 409)

    def test_invalid_commands_and_other_owner_records_fail_atomically(self):
        list_id = self.create(); foreign = self.work('外部私人作品', self.other)
        for body in ({'record_ids': [self.record.pk, foreign.pk]}, {'record_ids': [self.record.pk, 99999999]}, {'record_ids': [True]}, {'record_ids': [self.record.pk]*2}):
            self.assertEqual(self.command('add', list_id=list_id, **body).status_code, 400)
        self.assertEqual(CurationEntry.objects.count(), 0)
        for fields in ({'pinned': 'false'}, {'title': ' '}, {'description': 'x'*2001}, {'user': self.other.pk}, {'position': 4}):
            self.assertEqual(self.command('update', list_id=list_id, **fields).status_code, 400)
        self.assertEqual(self.command('update', list_id=list_id, cover_entry_id=str(uuid4())).status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.read()['count'], 0)
        self.assertEqual(self.client.get(f'/api/curations/{list_id}/').status_code, 404)
        self.assertEqual(self.command('delete', list_id=list_id).status_code, 404)
        self.assertEqual(self.client.get(f'/api/records/{self.record.pk}/curation-impact/').status_code, 404)

    def test_member_ids_and_cover_must_belong_to_target_list(self):
        one, two = self.create(), self.create()
        entry = self.add(one)[0]
        for action in ('update-entry', 'remove-entry', 'relink'):
            fields = {'reason': 'x'} if action == 'update-entry' else {'record_id': self.record.pk} if action == 'relink' else {}
            self.assertEqual(self.command(action, list_id=two, entry_id=entry['id'], **fields).status_code, 404)
        self.assertEqual(self.command('update', list_id=two, cover_entry_id=entry['id']).status_code, 400)

    def test_deletion_keeps_latest_title_reason_position_and_explicit_relink(self):
        list_id = self.create(); second = self.work('保留封面')
        rows = self.add(list_id, self.record, second)
        self.command('update-entry', list_id=list_id, entry_id=rows[0]['id'], reason='删除后也保留')
        self.command('update', list_id=list_id, cover_entry_id=rows[0]['id'])
        impact = self.client.get(f'/api/records/{self.record.pk}/curation-impact/')
        self.assertEqual(impact.data['count'], 1)
        self.record.private_chinese_title = '删除前更正的名称'; self.record.save()
        revision = self.read()['version']
        self.assertEqual(self.client.delete(f'/api/records/{self.record.pk}/').status_code, 204)
        data = self.read(list_id)
        self.assertGreater(data['version'], revision)
        self.assertTrue(data['results'][0]['unavailable'])
        self.assertEqual(data['results'][0]['reason'], '删除后也保留')
        self.assertEqual(data['results'][0]['title_snapshot']['title'], '删除前更正的名称')
        self.assertEqual(data['collection']['cover']['id'], second.pk)
        replacement = self.work('雨天的合成作品')
        self.assertTrue(self.read(list_id)['results'][0]['unavailable'])
        self.assertEqual(self.command('relink', list_id=list_id, entry_id=rows[0]['id'], record_id=second.pk).status_code, 400)
        self.assertEqual(self.command('relink', list_id=list_id, entry_id=rows[0]['id'], record_id=replacement.pk).status_code, 200)
        self.assertEqual(self.read(list_id)['results'][0]['id'], rows[0]['id'])
        self.assertEqual(self.command('remove-entry', list_id=list_id, entry_id=rows[0]['id']).status_code, 200)
        self.assertTrue(UserAnimeRecord.objects.filter(pk=replacement.pk).exists())

    def test_orm_and_account_deletion_preserve_or_cascade_appropriately(self):
        list_id = self.create(); self.add(list_id)
        self.record.delete()
        self.assertEqual(CurationEntry.objects.get().record_id, None)
        self.user.delete()
        for model in (CurationEntry, CurationList, CurationLibrary, CurationOperation): self.assertFalse(model.objects.exists())

    def test_filters_pagination_and_member_order_are_stable(self):
        ids = [self.create() for _ in range(3)]
        self.command('update', list_id=ids[2], pinned=True, title='置顶主题')
        self.assertEqual(self.read()['results'][0]['id'], ids[2])
        self.assertEqual(self.read(search='置顶', pinned='true')['count'], 1)
        one = self.read(page_size=1); two = self.read(page=2, page_size=1, version=one['version'])
        self.assertNotEqual(one['results'][0]['id'], two['results'][0]['id'])
        for params in ({'page': 0}, {'page': 2}, {'page_size': 101}, {'state': 'public'}, {'pinned': 'yes'}, {'unknown': 1}):
            self.assertEqual(self.client.get('/api/curations/', params).status_code, 400)
        self.assertEqual(self.client.get('/api/curations/?page=1&page=2').status_code, 400)

    def test_public_records_and_backup_of_other_owner_do_not_leak_curation(self):
        list_id = self.create(description='私人策展秘密'); self.add(list_id)
        private_record = self.client.get(f'/api/records/{self.record.pk}/').data
        self.assertNotIn('私人策展秘密', json.dumps(private_record, ensure_ascii=False))
        self.client.force_authenticate(self.other)
        self.assertEqual(self.export()['curations'], {'version': 1, 'lists': []})

    def test_backup_round_trip_remaps_record_pks_and_can_restore_to_another_account(self):
        list_id = self.create(description='保留简介', pinned=True)
        entry = self.add(list_id)[0]
        self.command('update-entry', list_id=list_id, entry_id=entry['id'], reason='独立理由\n第二行🌸')
        self.command('update', list_id=list_id, cover_entry_id=entry['id'])
        payload = self.export(); old_pk = self.record.pk
        self.assertEqual(payload['curations']['lists'][0]['entries'][0]['record_ref'], payload['records'][0]['record_ref'])
        self.assertNotIn('record_id', json.dumps(payload['curations']))
        response = self.restore(payload); self.assertEqual(response.status_code, 200, response.data)
        restored = self.read(list_id)['results'][0]
        self.assertNotEqual(restored['record']['id'], old_pk)
        self.assertEqual(restored['id'], entry['id'])
        self.assertEqual(restored['reason'], '独立理由\n第二行🌸')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.restore(payload).status_code, 200)
        self.assertEqual(self.read(list_id)['results'][0]['record']['id'], UserAnimeRecord.objects.get(user=self.other).pk)
        self.assertEqual(CurationList.objects.count(), 2)

    def test_backup_unavailable_entry_survives_and_never_relinks_by_title(self):
        list_id = self.create(); entry = self.add(list_id)[0]; self.record.delete()
        self.work('雨天的合成作品')
        payload = self.export()
        self.assertEqual(payload['curations']['lists'][0]['entries'][0]['record_ref'], None)
        self.assertEqual(self.restore(payload).status_code, 200)
        self.assertTrue(self.read(list_id)['results'][0]['unavailable'])
        self.assertEqual(self.read(list_id)['results'][0]['id'], entry['id'])

    def test_legacy_restore_preserves_lists_and_marks_removed_members_unavailable(self):
        list_id = self.create(); self.add(list_id, self.record, self.work('未包含的作品'))
        payload = self.export(); payload.pop('curations'); payload['records'] = payload['records'][:1]
        for item in payload['records']: item.pop('record_ref')
        preview = self.restore(payload, '?preview=true')
        self.assertEqual(preview.data['curations']['mode'], 'preserve')
        self.assertEqual(preview.data['curations']['preserved_lists'], 1)
        self.assertEqual(preview.data['curations']['unavailable_entries'], 1)
        self.assertEqual(self.restore(payload).status_code, 200)
        self.assertEqual([e['unavailable'] for e in self.read(list_id)['results']], [False, True])

    def test_explicit_empty_lists_clear_and_preview_distinguishes_replacement(self):
        list_id = self.create(); self.add(list_id)
        payload = self.export(); payload['curations']['lists'] = []
        preview = self.restore(payload, '?preview=true').data
        self.assertEqual(preview['curations']['removed_lists'], 1)
        self.assertEqual(preview['curations']['mode'], 'replace')
        self.assertEqual(self.read()['count'], 1)
        self.assertEqual(self.restore(payload).status_code, 200)
        self.assertEqual(self.read()['count'], 0)
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.user).count(), 1)

    def test_backup_references_and_versions_validate_before_any_replacement(self):
        list_id = self.create(); self.add(list_id)
        good = self.export()
        bad = []
        for ref in ('unknown', 123, ''):
            payload = copy.deepcopy(good); payload['curations']['lists'][0]['entries'][0]['record_ref'] = ref; bad.append(payload)
        for value in (None, {}, {'version': 2, 'lists': []}, {'version': True, 'lists': []}):
            payload = copy.deepcopy(good); payload['curations'] = value; bad.append(payload)
        payload = copy.deepcopy(good); payload['curations']['lists'][0]['entries'] *= 2; bad.append(payload)
        payload = copy.deepcopy(good); payload['curations']['lists'][0]['cover_entry_id'] = str(uuid4()); bad.append(payload)
        payload = copy.deepcopy(good); payload['curations']['lists'][0]['user'] = self.other.pk; bad.append(payload)
        before = self.read()['version']
        for payload in bad:
            response = self.restore(payload)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertTrue(UserAnimeRecord.objects.filter(pk=self.record.pk).exists())
            self.assertEqual(self.read()['version'], before)

    def test_restore_confirmation_binds_private_curation_and_file_changes(self):
        list_id = self.create(); entry = self.add(list_id)[0]
        payload = self.export()
        preview = self.restore(payload, '?preview=true').data
        self.command('update-entry', list_id=list_id, entry_id=entry['id'], reason='预览之后的修改')
        response = self.restore({'data': json.dumps(payload), 'confirmation_token': preview['token']}, '?confirm=true')
        self.assertEqual(response.status_code, 409)
        preview = self.restore(payload, '?preview=true').data
        payload['curations']['lists'][0]['description'] = '改变文件'
        response = self.restore({'data': json.dumps(payload), 'confirmation_token': preview['token']}, '?confirm=true')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.read(list_id)['results'][0]['reason'], '预览之后的修改')

    def test_restore_failure_rolls_back_members_records_and_receipts(self):
        list_id = self.create(); self.add(list_id)
        payload = self.export(); before = list(CurationEntry.objects.values()); rev = self.read()['version']
        with patch('anime.curations.apply_restore', side_effect=RuntimeError('synthetic failure')):
            with self.assertLogs('anime.views', level='ERROR'):
                self.assertEqual(self.restore(payload).status_code, 400)
        self.assertEqual(list(CurationEntry.objects.values()), before)
        self.assertEqual(self.read()['version'], rev)
        self.assertTrue(UserAnimeRecord.objects.filter(pk=self.record.pk).exists())

    def test_limits_reject_whole_add_and_oversized_backup(self):
        list_id = self.create()
        with patch('anime.curations.MAX_ENTRIES', 1):
            self.assertEqual(self.command('add', list_id=list_id, record_ids=[self.record.pk, self.work('二').pk]).status_code, 400)
        self.assertEqual(CurationEntry.objects.count(), 0)
        self.add(list_id)
        payload = self.export()
        with patch('anime.curations.MAX_TOTAL', 0): self.assertEqual(self.restore(payload).status_code, 400)

    def test_legacy_ambiguous_private_identity_becomes_placeholder_without_guessing(self):
        list_id = self.create(); self.add(list_id)
        another = self.work('另一个目录版本')
        metadata = self.record.effective_anime_metadata()
        another.private_chinese_title = metadata['chinese_title']
        another.private_japanese_title = metadata['japanese_title']
        another.private_airing_period = metadata['airing_period']; another.save()
        payload = self.export(); payload.pop('curations'); payload['records'] = payload['records'][:1]
        # Complete history/plan fields keep the pre-existing import contract unambiguous.
        preview = self.restore(payload, '?preview=true')
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['curations']['unavailable_entries'], 1)
        self.assertEqual(self.restore(payload).status_code, 200)
        self.assertTrue(self.read(list_id)['results'][0]['unavailable'])

    def test_legacy_preservation_handles_same_entry_uuid_in_different_lists(self):
        first, second = self.create(), self.create()
        other = self.work('另一部'); entry = self.add(first)[0]; self.add(second, other)
        CurationEntry.objects.filter(collection__portable_id=second).update(portable_id=entry['id'])
        payload = self.export(); payload.pop('curations')
        self.assertEqual(self.restore(payload).status_code, 200)
        self.assertEqual(self.read(first)['results'][0]['record']['title'], '雨天的合成作品')
        self.assertEqual(self.read(second)['results'][0]['record']['title'], '另一部')


class CurationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        caches['throttle'].clear()
        self.addCleanup(caches['throttle'].clear)

    def race(self, same_operation):
        if connection.vendor != 'postgresql': self.skipTest('Requires PostgreSQL row locks')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user = get_user_model().objects.create_user('curation-race')
        barrier = Barrier(2)
        operation = str(uuid4())
        def write(index):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                body = {'action': 'create', 'title': '并发创建', 'version': 0,
                        'operation_id': operation if same_operation else str(uuid4())}
                barrier.wait(timeout=10)
                return client.post('/api/curations/', body, format='json').status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: codes = sorted(pool.map(write, [0, 1]))
        self.assertEqual(codes, [200, 200] if same_operation else [200, 409])
        self.assertEqual(CurationList.objects.count(), 1)
        self.assertEqual(CurationOperation.objects.count(), 1)
        self.assertEqual(CurationLibrary.objects.get().revision, 1)

    def test_initial_commands_have_one_version_winner(self): self.race(False)
    def test_same_operation_commits_once(self): self.race(True)

    def competing_mutations(self, mode):
        if connection.vendor != 'postgresql': self.skipTest('Requires PostgreSQL row locks')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user = get_user_model().objects.create_user('curation-compete')
        record = UserAnimeRecord.objects.create(user=user, anime=Anime.objects.create(chinese_title='并发作品', airing_period='2026-01'))
        api = APIClient(); api.force_authenticate(user)
        created = api.post('/api/curations/', {'action': 'create', 'title': '并发片单', 'version': 0, 'operation_id': str(uuid4())}, format='json')
        list_id = created.data['list_id']
        api.post('/api/curations/', {'action': 'add', 'list_id': list_id, 'record_ids': [record.pk], 'version': 1, 'operation_id': str(uuid4())}, format='json')
        entry_id = str(CurationEntry.objects.get().portable_id)
        payload = json.loads(api.get('/api/export/').content)
        preview = api.post('/api/import/?preview=true', payload, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        token = preview.data['token']
        barrier = Barrier(2)
        def write(index):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                barrier.wait(timeout=10)
                if index == 0:
                    if mode == 'restore':
                        return client.post('/api/import/?confirm=true', {'data': json.dumps(payload), 'confirmation_token': token}, format='json').status_code
                    return client.delete(f'/api/records/{record.pk}/').status_code
                return client.post('/api/curations/', {'action': 'update-entry', 'list_id': list_id, 'entry_id': entry_id,
                    'reason': '并发保留的理由', 'version': 2, 'operation_id': str(uuid4())}, format='json').status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: codes = list(pool.map(write, [0, 1]))
        if mode == 'restore':
            self.assertEqual(sorted(codes), [200, 409])
            self.assertEqual(CurationEntry.objects.get().reason, '并发保留的理由' if codes[1] == 200 else '')
        else:
            self.assertIn(codes, ([204, 200], [204, 409]))
            self.assertEqual(CurationEntry.objects.get().record_id, None)
        self.assertEqual(str(CurationEntry.objects.get().portable_id), entry_id)
        self.assertEqual(CurationList.objects.count(), 1)

    def test_restore_confirmation_and_reason_edit_have_one_winner(self): self.competing_mutations('restore')
    def test_record_deletion_and_reason_edit_keep_lock_order(self): self.competing_mutations('delete')
