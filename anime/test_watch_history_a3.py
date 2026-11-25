import json
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from rest_framework.test import APITestCase
from rest_framework.test import APIClient

from .models import Anime, UserAnimeRecord, WatchEvent, WatchRound
from . import test_watch_history as a1_tests


class WatchHistoryA3Tests(APITestCase):
    setUp = a1_tests.WatchHistoryTests.setUp
    read = a1_tests.WatchHistoryTests.read
    command = a1_tests.WatchHistoryTests.command
    export = a1_tests.WatchHistoryTests.export

    def clear_body(self):
        return {'action': 'clear', 'version': self.read()['version'], 'operation_id': str(uuid4()), 'confirm_clear': True}

    def test_clear_removes_private_content_preserves_work_and_never_rewinds_version(self):
        self.command('transition', watch_status='completed', watched_eps=12)
        self.command('start-rewatch', note='old private secret', started_on='2020')
        self.command('update-round', round_number=1, note='round secret', rating='8.5', completed_on='2019')
        old = UserAnimeRecord.objects.get(pk=self.record.pk)
        before = self.export()['records'][0]
        body = self.clear_body()
        result = self.client.post(self.url, body, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['version'], 4)
        self.assertEqual(result.data['events'], [])
        self.assertEqual(result.data['base_revision'], 4)
        self.assertEqual(result.data['rewatch_count'], 0)
        self.assertEqual(WatchEvent.objects.count(), 0)
        self.assertEqual(WatchRound.objects.count(), 1)
        after = self.export()['records'][0]
        self.assertEqual({k: v for k, v in before.items() if k != 'watch_history'}, {k: v for k, v in after.items() if k != 'watch_history'})
        self.assertNotIn('secret', json.dumps(after))
        self.assertEqual(after['watch_history']['base_revision'], 4)
        # An instance fetched before clear must not overwrite the barrier/receipt.
        old.watched_eps = 2; old.save()
        self.assertEqual(self.read()['version'], 5)
        replay = self.client.post(self.url, body, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data['replayed'])
        self.assertEqual(replay.data['version'], 5)
        self.assertEqual(replay.data['total_count'], 1)
        self.assertEqual(self.client.post(self.url, {**body, 'confirm_clear': False}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, {**body, 'operation_id': str(uuid4())}, format='json').status_code, 409)
        self.assertEqual(self.command('undo', target_revision=4).status_code, 409)

    def test_clear_transaction_rollback_and_owner_isolation(self):
        self.command('note', note='keep on failure')
        with patch('anime.watch_history.ensure_round', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError): self.client.post(self.url, self.clear_body(), format='json')
        self.assertEqual(self.read()['version'], 1)
        self.assertEqual(WatchEvent.objects.count(), 1)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.url, {'action': 'clear', 'version': 1, 'operation_id': str(uuid4()), 'confirm_clear': True}, format='json').status_code, 404)

    def test_old_requests_cannot_recreate_cleared_content(self):
        body = {'action': 'note', 'version': 0, 'operation_id': str(uuid4()), 'note': 'do not resurrect'}
        self.assertEqual(self.client.post(self.url, body, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, self.clear_body(), format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, body, format='json').status_code, 409)
        self.assertNotIn('resurrect', json.dumps(self.read()))

    def test_cleared_history_round_trips_and_old_backup_can_restore_intentionally(self):
        self.command('note', note='explicit old backup')
        old = self.export()
        self.client.post(self.url, self.clear_body(), format='json')
        cleared = self.export()
        self.assertEqual(self.client.post('/api/import/', cleared, format='json').status_code, 200)
        restored = UserAnimeRecord.objects.get(user=self.user)
        self.assertEqual(restored.watch_history_base, 2)
        self.assertEqual(restored.watch_events.count(), 0)
        self.url = f'/api/records/{restored.pk}/watch-history/'
        self.assertEqual(self.command('note', note='new').status_code, 200)
        self.assertEqual(self.read()['version'], 3)
        self.assertEqual(self.client.post('/api/import/', old, format='json').status_code, 200)
        self.assertEqual(WatchEvent.objects.get().note, 'explicit old backup')
        bad = cleared.copy(); bad['records'][0]['watch_history']['base_revision'] = 3
        self.assertEqual(self.client.post('/api/import/', bad, format='json').status_code, 400)

    def test_filter_and_combined_undo_are_applied_before_pagination(self):
        self.command('note', note='one')
        self.command('undo', target_revision=1)
        self.command('transition', watch_status='watching')
        self.command('note', note='two')
        result = self.client.get(self.url, {'compact': 'true', 'category': 'notes', 'page_size': 1, 'page': 2})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['total_count'], 4)
        self.assertEqual(result.data['count'], 2)
        self.assertEqual(result.data['events'][0]['kind'], 'undo')
        self.assertEqual(result.data['events'][0]['undone_event']['note'], 'one')
        self.assertEqual(result.data['events'][0]['undone_event']['revision'], 1)
        progress = self.client.get(self.url, {'compact': 'true', 'category': 'progress'}).data
        self.assertEqual(progress['count'], 1)
        self.assertEqual(progress['events'][0]['kind'], 'transition')
        self.assertEqual(self.read()['count'], 4)  # Raw API/backup unchanged.
        self.assertEqual(self.client.get(self.url, {'category': 'invalid'}).status_code, 400)

    def preview(self, payload):
        # Exercise multipart parsing, matching the shipped file upload UI.
        file = SimpleUploadedFile('synthetic.json', json.dumps(payload).encode(), content_type='application/json')
        return self.client.post('/api/import/?preview=true', {'file': file}, format='multipart')

    def confirm(self, payload, token):
        file = SimpleUploadedFile('synthetic.json', json.dumps(payload).encode(), content_type='application/json')
        return self.client.post('/api/import/?confirm=true', {'file': file, 'confirmation_token': token}, format='multipart')

    def test_restore_preview_is_read_only_and_counts_complete_legacy_and_removed(self):
        self.command('note', note='restore me')
        payload = self.export()
        another = Anime.objects.create(chinese_title='removed')
        removed = UserAnimeRecord.objects.create(user=self.user, anime=another)
        before = list(UserAnimeRecord.objects.values())
        response = self.preview(payload)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['with_history'], 1)
        self.assertEqual(response.data['events_in_file'], 1)
        self.assertEqual(response.data['replaced_history'], 1)
        self.assertEqual(response.data['removed_count'], 1)
        self.assertEqual(response.data['removed_titles'], ['removed'])
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        legacy = json.loads(json.dumps(payload)); del legacy['records'][0]['watch_history']
        self.assertEqual(self.preview(legacy).data['preserved_history'], 1)
        self.assertEqual(self.confirm(payload, response.data['token']).status_code, 200)
        self.assertFalse(UserAnimeRecord.objects.filter(pk=removed.pk).exists())
        self.assertEqual(WatchEvent.objects.get().note, 'restore me')

    def test_restore_confirmation_is_bound_to_file_account_current_state_and_expiry(self):
        payload = self.export()
        token = self.preview(payload).data['token']
        self.command('note', note='new since preview')
        self.assertEqual(self.confirm(payload, token).status_code, 409)
        token = self.preview(payload).data['token']
        changed = json.loads(json.dumps(payload)); changed['records'][0]['review'] = 'changed file'
        self.assertEqual(self.confirm(changed, token).status_code, 409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.confirm(payload, token).status_code, 409)
        self.client.force_authenticate(self.user)
        with patch('django.core.signing.time.time', return_value=9999999999):
            self.assertEqual(self.confirm(payload, token).status_code, 409)
        self.assertEqual(self.client.post('/api/import/?confirm=true', payload, format='json').status_code, 400)
        self.assertEqual(WatchEvent.objects.get().note, 'new since preview')


class WatchClearConcurrencyTests(TransactionTestCase):
    def race(self, same_operation):
        if connection.vendor != 'postgresql': self.skipTest('PostgreSQL row locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user = get_user_model().objects.create_user(f'clear-race-{uuid4().hex}')
        anime = Anime.objects.create(chinese_title=f'合成清空并发{uuid4().hex}', airing_period='2020-1')
        record = UserAnimeRecord.objects.create(user=user, anime=anime)
        barrier, operation = Barrier(2), str(uuid4())
        def write(index):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                body = {'action': 'clear', 'version': 0, 'operation_id': operation, 'confirm_clear': True}
                if not same_operation and index:
                    body = {'action': 'note', 'version': 0, 'operation_id': str(uuid4()), 'note': 'racing write'}
                barrier.wait(timeout=10)
                response = client.post(f'/api/records/{record.pk}/watch-history/', body, format='json')
                return response.status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = sorted(pool.map(write, [0, 1]))
        self.assertEqual(codes, [200, 200] if same_operation else [200, 409])
        record.refresh_from_db()
        self.assertEqual(record.watch_revision, 1)
        self.assertEqual(record.watch_rounds.filter(is_current=True).count(), 1)
        if same_operation: self.assertEqual(record.watch_events.count(), 0)

    def test_repeated_clear_has_one_effect(self):
        self.race(True)

    def test_clear_and_old_version_write_have_one_winner(self):
        self.race(False)
