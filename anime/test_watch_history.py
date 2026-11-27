import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from .models import Anime, UserAnimeRecord, WatchRound, WatchEvent


class WatchHistoryTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('watch-owner')
        self.other = get_user_model().objects.create_user('watch-other')
        self.anime = Anime.objects.create(chinese_title='合成观看历程', airing_period='2020-1')
        self.record = UserAnimeRecord.objects.create(user=self.user, anime=self.anime, total_eps=12, personal_score=Decimal('9.0'))
        self.client.force_authenticate(self.user)
        self.url = f'/api/records/{self.record.pk}/watch-history/'

    def read(self):
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200, result.data)
        return result.data

    def command(self, action, **kwargs):
        return self.client.post(self.url, {'action': action, 'version': self.read()['version'], 'operation_id': str(uuid4()), **kwargs}, format='json')

    def export(self):
        result = self.client.get(reverse('anime_api:journal-export'))
        self.assertEqual(result.status_code, 200)
        return json.loads(result.content)

    def test_legacy_get_is_read_only_with_unknown_dates_and_private_cache(self):
        before = list(UserAnimeRecord.objects.values())
        response = self.client.get(self.url)
        row = response.data['rounds'][0]
        self.assertEqual(response.data['version'], 0)
        self.assertEqual(row['started_on'], '')
        self.assertEqual(row['completed_on'], '')
        self.assertEqual(response.data['events'], [])
        self.assertEqual(WatchRound.objects.count(), 0)
        self.assertEqual(WatchEvent.objects.count(), 0)
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertIn('no-store', response['Cache-Control'])

    def test_existing_update_details_and_quick_progress_record_atomic_events(self):
        response = self.client.patch(f'/api/records/{self.record.pk}/', {'watch_status': 'watching'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['watch_revision'], 1)
        response = self.client.patch(f'/api/records/{self.record.pk}/progress/', {'increment': 1}, format='json')
        self.assertEqual(response.status_code, 200)
        response = self.client.patch(f'/api/records/{self.record.pk}/details/', {'anime': {'studio': '合成公司'}, 'record': {'watched_eps': 3}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        history = self.read()
        self.assertEqual(history['version'], 3)
        self.assertEqual(history['rounds'][0]['watched_eps'], 3)
        self.assertTrue(all(not event['occurred_on'] for event in history['events']))
        self.assertEqual(history['rounds'][0]['started_on'], '')

    def test_metadata_score_and_tags_do_not_create_viewing_events(self):
        self.command('transition', watch_status='watching')
        self.client.patch(f'/api/records/{self.record.pk}/', {'review': '新长评', 'personal_score': '9.5', 'custom_tags': ['标签']}, format='json')
        self.assertEqual(self.read()['version'], 1)
        self.record.refresh_from_db()
        old = self.record.watch_revision
        self.record.private_studio = '编辑资料'; self.record.save(update_fields=['private_studio'])
        self.assertEqual(self.read()['version'], old)

    def test_dates_preserve_precision_and_invalid_intervals_roll_back(self):
        result = self.command('update-round', round_number=1, started_on='2020', completed_on='2020-02')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['rounds'][0]['started_on'], '2020')
        self.assertEqual(result.data['rounds'][0]['completed_on'], '2020-02')
        for value in ('2020-02-30', '2020-13', '2020-2', '0000', 'today'):
            version = self.read()['version']
            self.assertEqual(self.command('update-round', round_number=1, started_on=value).status_code, 400)
            self.assertEqual(self.read()['version'], version)
        self.assertEqual(self.command('update-round', round_number=1, started_on='2021').status_code, 400)

    def test_completion_snapshot_and_rewatch_preserve_first_round(self):
        self.command('transition', watch_status='watching', occurred_on='2020-01')
        result = self.command('transition', watch_status='completed', watched_eps=12, occurred_on='2020-02-29')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['rounds'][0]['rating'], '9.0')
        self.client.patch(f'/api/records/{self.record.pk}/', {'personal_score': '8.0'}, format='json')
        result = self.command('start-rewatch', started_on='2026-11', note='第二次观看')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['rewatch_count'], 1)
        self.assertEqual(result.data['completed_rounds'], 1)
        self.assertEqual(result.data['rounds'][0]['rating'], '9.0')
        self.assertEqual(result.data['rounds'][1]['watched_eps'], 0)
        self.assertEqual(UserAnimeRecord.objects.count(), 1)
        self.record.refresh_from_db()
        self.assertEqual(self.record.watch_status, 'watching')
        self.assertEqual(self.record.personal_score, Decimal('8.0'))
        self.assertEqual(self.command('start-rewatch').status_code, 409)

    def test_memory_and_past_round_correction_do_not_change_current_state_or_rating(self):
        before = self.client.get(f'/api/records/{self.record.pk}/').data
        result = self.command('memory', occurred_on='2019-03', note='以前看过几集，日期大致记得')
        self.assertEqual(result.status_code, 200, result.data)
        self.command('update-round', round_number=1, rating='7.0', note='第一轮感受')
        self.record.refresh_from_db()
        self.assertEqual(self.record.watch_status, before['watch_status'])
        self.assertEqual(self.record.watched_eps, before['watched_eps'])
        self.assertEqual(self.record.personal_score, Decimal('9.0'))
        self.assertEqual(self.record.updated_at, datetime.fromisoformat(before['updated_at']))

    def test_idempotency_stale_version_and_body_reuse(self):
        body = {'action': 'transition', 'watch_status': 'watching', 'version': 0, 'operation_id': str(uuid4())}
        first = self.client.post(self.url, body, format='json')
        second = self.client.post(self.url, body, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertTrue(second.data['replayed'])
        self.assertEqual(WatchEvent.objects.count(), 1)
        self.assertEqual(self.client.post(self.url, {**body, 'watch_status': 'completed'}, format='json').status_code, 409)
        self.assertEqual(self.client.post(self.url, {**body, 'operation_id': str(uuid4())}, format='json').status_code, 409)

    def test_command_acknowledgement_contains_current_owner_record_even_on_replay(self):
        body = {'action': 'transition', 'watch_status': 'watching', 'version': 0, 'operation_id': str(uuid4())}
        first = self.client.post(self.url, body, format='json')
        self.assertEqual(first.data['record']['watch_revision'], first.data['version'])
        self.assertEqual(first.data['record']['watch_status'], 'watching')
        self.command('transition', watched_eps=2)
        replayed = self.client.post(self.url, body, format='json')
        self.assertTrue(replayed.data['replayed'])
        self.assertEqual(replayed.data['record']['watched_eps'], 2)
        self.assertEqual(replayed.data['record']['watch_revision'], 2)
        self.assertEqual(replayed.data['record']['id'], self.record.pk)

    def test_undo_only_latest_does_not_overwrite_new_rating(self):
        self.command('transition', watch_status='completed', watched_eps=12)
        self.command('start-rewatch')
        self.client.patch(f'/api/records/{self.record.pk}/', {'personal_score': '8.5'}, format='json')
        result = self.command('undo', target_revision=2)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['rewatch_count'], 0)
        self.assertTrue(result.data['rounds'][1]['voided'])
        self.record.refresh_from_db()
        self.assertEqual(self.record.watch_status, 'completed')
        self.assertEqual(self.record.personal_score, Decimal('8.5'))
        self.assertEqual(self.command('undo', target_revision=1).status_code, 409)
        self.assertEqual(self.command('undo', target_revision=3).status_code, 409)
        self.command('start-rewatch')
        self.assertEqual(self.read()['rounds'][-1]['number'], 3)

    def test_event_failure_rolls_back_old_and_new_write_paths(self):
        with patch('anime.watch_history.append_event', side_effect=RuntimeError('synthetic ledger failure')):
            with self.assertRaises(RuntimeError): self.command('transition', watch_status='watching')
            with self.assertRaises(RuntimeError):
                self.client.patch(f'/api/records/{self.record.pk}/', {'watch_status': 'watching'}, format='json')
        self.record.refresh_from_db()
        self.assertEqual(self.record.watch_status, 'plan')
        self.assertEqual(WatchRound.objects.count(), 0)
        self.assertEqual(WatchEvent.objects.count(), 0)

    def test_backup_roundtrip_and_legacy_restore_preserve_history(self):
        self.command('transition', watch_status='completed', occurred_on='2020', watched_eps=12)
        self.command('start-rewatch', started_on='2026')
        self.command('note', note='重看随记')
        payload = self.export(); history = payload['records'][0]['watch_history']
        result = self.client.post('/api/import/', payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.export()['records'][0]['watch_history'], history)
        payload = self.export(); del payload['records'][0]['watch_history']
        result = self.client.post('/api/import/', payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.export()['records'][0]['watch_history'], history)
        payload['records'][0]['watched_eps'] = 2
        self.assertEqual(self.client.post('/api/import/', payload, format='json').status_code, 200)
        restored = self.export()['records'][0]['watch_history']
        self.assertEqual(restored['revision'], 4)
        self.assertEqual(restored['events'][-1]['source'], 'backup_restore')

    def test_malformed_backup_fails_without_mutating_records(self):
        self.command('transition', watch_status='watching')
        payload = self.export(); payload['records'][0]['watch_history']['revision'] = 999
        before = list(UserAnimeRecord.objects.values())
        self.assertEqual(self.client.post('/api/import/', payload, format='json').status_code, 400)
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)

    def test_legacy_backup_matches_exact_private_metadata_after_custom_title(self):
        self.command('note', note='保留这条私人随记')
        self.record.private_chinese_title = '我的私人作品名称'
        self.record.save(update_fields=['private_chinese_title'])
        payload = self.export(); history = payload['records'][0].pop('watch_history')
        self.assertEqual(self.client.post('/api/import/', payload, format='json').status_code, 200)
        restored = UserAnimeRecord.objects.get(user=self.user)
        self.assertNotEqual(restored.anime_id, self.anime.pk)
        self.assertEqual(self.export()['records'][0]['watch_history'], history)

    def test_ambiguous_legacy_match_is_rejected_without_losing_history(self):
        self.command('note', note='不能猜测归属的随记')
        other_anime = Anime.objects.create(chinese_title='另一条公共记录', airing_period='2020-1')
        other_record = UserAnimeRecord.objects.create(user=self.user, anime=other_anime)
        other_record.private_chinese_title = self.record.private_chinese_title
        other_record.save(update_fields=['private_chinese_title'])
        payload = self.export(); payload['records'] = payload['records'][:1]
        del payload['records'][0]['watch_history']
        before = list(UserAnimeRecord.objects.values())
        result = self.client.post('/api/import/', payload, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertIn('多个私人记录', str(result.data))
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertEqual(WatchEvent.objects.count(), 1)

    def test_invalid_commands_and_pagination_do_not_write_or_fake_events(self):
        for data in ({'action': 'note', 'note': ''}, {'action': 'memory', 'note': 'no date'},
                     {'action': 'transition', 'watch_status': 'plan'}, {'action': 'transition', 'watched_eps': 13},
                     {'action': 'update-round', 'round_number': 1}, {'action': 'note', 'note': 'x', 'user': self.other.pk}):
            result = self.client.post(self.url, {'version': 0, 'operation_id': str(uuid4()), **data}, format='json')
            self.assertEqual(result.status_code, 400, result.data)
        for params in ({'page': 0}, {'page_size': 101}, {'page': 'wrong'}):
            self.assertEqual(self.client.get(self.url, params).status_code, 400)
        self.assertEqual(WatchRound.objects.count(), 0)
        self.assertEqual(WatchEvent.objects.count(), 0)

    def test_observation_source_and_partial_save_use_only_persisted_values(self):
        from .watch_history import watch_observation
        with watch_observation('collection_import'):
            self.record.watch_status = 'watching'; self.record.save(update_fields=['watch_status'])
        self.assertEqual(self.read()['events'][0]['source'], 'collection_import')
        self.record.watch_status = 'completed'  # Intentionally excluded from this partial save.
        self.record.watched_eps = 1; self.record.save(update_fields=['watched_eps'])
        event = self.read()['events'][0]
        self.assertEqual(event['source'], 'record_update')
        self.assertEqual(event['after']['record']['watch_status'], 'watching')
        self.assertEqual(event['occurred_on'], '')

    def test_pagination_newest_first_and_correction_of_previous_round(self):
        self.command('transition', watch_status='completed')
        self.command('start-rewatch')
        self.command('update-round', round_number=1, started_on='2010', completed_on='2011', rating='8.5')
        self.record.refresh_from_db()
        self.assertEqual(self.record.watch_status, 'watching')
        self.assertEqual(self.record.watched_eps, 0)
        page = self.client.get(self.url, {'page': 2, 'page_size': 1}).data
        self.assertEqual(page['count'], 3)
        self.assertEqual(page['events'][0]['revision'], 2)
        self.assertEqual(page['rounds'][0]['completed_on'], '2011')
        self.assertEqual(self.command('undo', target_revision=3).status_code, 200)
        self.assertEqual(self.read()['rounds'][0]['completed_on'], '')

    def test_owner_isolation_public_payload_privacy_and_deletion(self):
        self.command('note', note='私人随记')
        profile = self.user.profile; profile.is_public = True; profile.public_status = 'approved'; profile.save()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'action': 'note', 'note': 'other', 'version': 0, 'operation_id': str(uuid4())}, format='json').status_code, 404)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        response = self.client.get(f'/api/public-journal/users/{self.user.username}/records/')
        self.assertNotIn('watch_revision', response.data['results'][0])
        self.assertNotIn('私人随记', json.dumps(response.data, ensure_ascii=False))
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.delete(f'/api/records/{self.record.pk}/').status_code, 204)
        self.assertEqual(WatchEvent.objects.count(), 0)
        self.assertEqual(WatchRound.objects.count(), 0)


    def test_current_round_progress_correction_preserves_status_dates_score_and_plan(self):
        from .models import WatchPlan
        from .watch_plans import command, read_detail
        from .watch_history import export_history, validate_backup
        for status in ('completed', 'on_hold', 'watching'):
            with self.subTest(status=status):
                self.assertEqual(self.command('transition', watch_status=status, watched_eps=0, total_eps=0).status_code, 200)
                if not WatchPlan.objects.filter(record=self.record).exists():
                    command(self.user, self.record.pk, {'action': 'enroll', 'version': read_detail(self.user, self.record.pk)['version'], 'operation_id': str(uuid4())})
                plan = WatchPlan.objects.get(record=self.record)
                self.record.refresh_from_db()
                self.command('update-round', round_number=1, completed_on='2020' if status == 'completed' else '')
                plan.refresh_from_db(); plan_before = (plan.state, plan.target_round_number, plan.purpose)
                origin = self.record.watch_rounds.get(number=1).rating_origin
                result = self.command('update-round', round_number=1, watched_eps=8, total_eps=12, rating='9.0')
                self.assertEqual(result.status_code, 200, result.data)
                self.record.refresh_from_db(); plan.refresh_from_db()
                row = result.data['rounds'][0]
                self.assertEqual((self.record.watch_status, self.record.watched_eps, self.record.total_eps), (status, 8, 12))
                self.assertEqual((row['status'], row['watched_eps'], row['total_eps']), (status, 8, 12))
                self.assertEqual(row['completed_on'], '2020' if status == 'completed' else '')
                self.assertEqual(self.record.personal_score, Decimal('9.0'))
                self.assertEqual(row['rating_origin'], origin)
                self.assertEqual((plan.state, plan.target_round_number, plan.purpose), plan_before)
                self.assertEqual(result.data['events'][0]['kind'], 'update-round')
                self.assertEqual(result.data['events'][0]['occurred_on'], '')
                validate_backup(export_history(self.record), {'watch_status': status, 'watched_eps': 8, 'total_eps': 12})

    def test_historical_progress_correction_is_separate_and_can_be_undone(self):
        self.command('transition', watch_status='completed', watched_eps=12)
        self.command('start-rewatch', started_on='2026-10')
        self.command('transition', watched_eps=3)
        response = self.command('update-round', round_number=1, watched_eps=8, total_eps=13)
        self.assertEqual(response.status_code, 200, response.data)
        self.record.refresh_from_db()
        self.assertEqual((self.record.watch_status, self.record.watched_eps, self.record.total_eps), ('watching', 3, 12))
        self.assertEqual(self.record.watch_rounds.get(number=1).watched_eps, 8)
        result = self.command('undo', target_revision=response.data['version'])
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.record.watch_rounds.get(number=1).watched_eps, 12)
        self.record.refresh_from_db(); self.assertEqual(self.record.watched_eps, 3)

    def test_progress_correction_validation_atomicity_idempotency_and_conflict(self):
        before = self.read()['version']
        response = self.command('update-round', round_number=1, watched_eps=13)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.read()['version'], before)
        self.assertFalse(self.record.watch_rounds.exists())
        for fields in ({'watched_eps': -1}, {'total_eps': -1}, {'watch_status': 'completed'}, {'occurred_on': '2026-11-01'}):
            self.assertEqual(self.command('update-round', round_number=1, **fields).status_code, 400)
        body = {'action': 'update-round', 'version': before, 'operation_id': str(uuid4()), 'round_number': 1, 'watched_eps': 8, 'total_eps': 0}
        response = self.client.post(self.url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        replay = self.client.post(self.url, body, format='json')
        self.assertEqual(replay.status_code, 200); self.assertTrue(replay.data['replayed'])
        stale = self.client.post(self.url, {**body, 'operation_id': str(uuid4())}, format='json')
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.command('undo', target_revision=response.data['version']).status_code, 200)
        self.record.refresh_from_db(); self.assertEqual((self.record.watched_eps, self.record.total_eps), (0, 12))


class WatchHistoryConcurrencyTests(TransactionTestCase):
    def test_legacy_progress_increments_keep_both_values_and_events(self):
        if connection.vendor != 'postgresql': self.skipTest('PostgreSQL row locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user = get_user_model().objects.create_user('watch-progress-race')
        anime = Anime.objects.create(chinese_title='合成进度并发', airing_period='2026-1')
        record = UserAnimeRecord.objects.create(user=user, anime=anime, watch_status='watching', total_eps=12)
        barrier = Barrier(2)
        def write(_):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                barrier.wait(timeout=10)
                return client.patch(f'/api/records/{record.pk}/progress/', {'increment': 1}, format='json').status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(write, [1, 2])), [200, 200])
        record.refresh_from_db()
        self.assertEqual(record.watched_eps, 2)
        self.assertEqual(record.watch_revision, 2)
        self.assertEqual(list(record.watch_events.values_list('after__record__watched_eps', flat=True)), [1, 2])

    def test_same_version_commands_have_one_winner(self):
        if connection.vendor != 'postgresql': self.skipTest('PostgreSQL row locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user = get_user_model().objects.create_user('watch-race')
        anime = Anime.objects.create(chinese_title='合成并发', airing_period='2026-1')
        record = UserAnimeRecord.objects.create(user=user, anime=anime)
        barrier = Barrier(2)
        def write(index):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                barrier.wait(timeout=10)
                return client.post(f'/api/records/{record.pk}/watch-history/', {'action': 'note', 'note': f'并发{index}', 'version': 0, 'operation_id': str(uuid4())}, format='json').status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(write, [1, 2])), [200, 409])
        self.assertEqual(WatchEvent.objects.count(), 1)
