import json
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch, PropertyMock
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection
from django.db import close_old_connections
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITransactionTestCase

from .models import Anime, UserAnimeRecord, WatchEvent, WatchRound
from .watch_history import execute
from .watch_review import SALT, LIMITS


class WatchReviewTests(APITransactionTestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = get_user_model().objects.create_user('review-owner')
        self.other = get_user_model().objects.create_user('review-other')
        self.client.force_authenticate(self.user)
        self.record = self.work('私人作品')
        self.url = '/api/watch-review/'
        self.entries = self.url + 'entries/'

    def work(self, title, **changes):
        anime = Anime.objects.create(chinese_title=title, japanese_title='Original', airing_period='2020-1',
            poster_url='https://media.xh-anime.com/original.jpg?a=1&b=2')
        return UserAnimeRecord.objects.create(user=self.user, anime=anime, personal_score=Decimal('9.0'),
            total_eps=12, review='私人综合长评不应作为当期随记', **changes)

    def send(self, action, record=None, **values):
        target = record or self.record
        target.refresh_from_db()
        return execute(target.pk, self.user, {'action': action, 'version': target.watch_revision,
            'operation_id': str(uuid4()), **values})

    def finish(self, record=None, started='2026-10-02', completed='2026-10-10'):
        self.send('transition', record, watch_status='watching', occurred_on=started)
        return self.send('transition', record, watch_status='completed', watched_eps=12, occurred_on=completed)

    def get(self, **params):
        response = self.client.get(self.url, params or {'month': '2026-10'})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def rows(self, **params):
        response = self.client.get(self.entries, {'month': '2026-10', **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_round_progress_correction_does_not_invent_review_activity_or_milestones(self):
        self.finish()
        before = self.get()
        self.send('update-round', round_number=1, watched_eps=8, total_eps=13)
        after = self.get()
        self.assertEqual(before['summary'], after['summary'])
        self.assertNotEqual(before['snapshot'], after['snapshot'])
        self.send('start-rewatch', started_on='2026-10-15')
        before = self.get()
        self.send('update-round', round_number=1, watched_eps=7, total_eps=14)
        self.assertEqual(before['summary'], self.get()['summary'])
        self.record.refresh_from_db(); self.assertEqual(self.record.watched_eps, 0)

    def test_empty_read_is_private_readonly_and_does_not_materialize_virtual_round(self):
        before = list(UserAnimeRecord.objects.values())
        response = self.client.get(self.url, {'month': '2026-10'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('private', response['Cache-Control']); self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Authorization', response['Vary'])
        self.assertEqual(response.data['summary']['completed_works'], 0)
        self.assertEqual(response.data['coverage']['undated']['works'], 0)
        self.assertFalse(WatchRound.objects.exists()); self.assertFalse(WatchEvent.objects.exists())
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)

    def test_month_defaults_to_shanghai_local_date_and_full_calendar_month(self):
        from django.utils import timezone
        with timezone.override('UTC'), patch('django.utils.timezone.now', return_value=datetime(2026, 10, 31, 18, tzinfo=dt_timezone.utc)):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200, response.data)
            period = response.data['period']
        self.assertEqual(period['start'], '2026-11-01'); self.assertEqual(period['end'], '2026-11-30')
        self.assertTrue(period['end_inclusive'])

    def test_one_work_two_completed_rounds_and_no_current_score_fallback(self):
        self.finish()
        self.send('start-rewatch', started_on='2026-10-15')
        self.record.refresh_from_db(); self.record.personal_score = Decimal('8.0'); self.record.save(update_fields=['personal_score'])
        self.send('transition', watch_status='completed', watched_eps=12, occurred_on='2026-10-20')
        summary = self.get()['summary']
        self.assertEqual((summary['completed_works'], summary['completed_rounds']), (1, 2))
        self.assertEqual((summary['first_completed_rounds'], summary['rewatch_completed_rounds']), (1, 1))
        self.assertEqual((summary['started_works'], summary['started_rounds']), (1, 2))
        self.assertEqual(summary['completed_round_rating']['average'], '8.50')
        self.send('update-round', round_number=1, rating=None)
        summary = self.get()['summary']
        self.assertEqual(summary['completed_round_rating']['count'], 1)
        self.assertEqual(summary['completed_round_rating']['missing_count'], 1)
        self.assertEqual(self.rows()['count'], 1)
        self.assertEqual(self.rows(section='completed')['count'], 2)

    def test_precision_containment_overlap_and_unknown_are_distinct(self):
        self.finish(started='2026', completed='2026-10')
        self.assertEqual(self.get()['summary']['completed_works'], 1)
        self.assertEqual(self.get()['summary']['started_works'], 0)
        self.assertEqual(self.get()['coverage']['uncertain']['facts'], 1)
        response = self.get(start='2026-10-10', end='2026-10-20')
        self.assertEqual(response['summary']['completed_works'], 0)
        self.assertEqual(response['coverage']['uncertain']['facts'], 2)
        data = self.rows(section='uncertain')
        self.assertEqual(data['results'][0]['date_precision'], 'year')
        self.assertEqual(data['results'][0]['actual_date'], '2026')
        self.assertEqual(self.get(start='2026-01-01', end='2026-12-31')['summary']['started_works'], 1)

    def test_boundaries_leap_month_and_no_invented_daily_dates(self):
        self.finish(started='2024-02', completed='2024-02-29')
        self.assertEqual(self.get(month='2024-02')['summary']['completed_works'], 1)
        self.assertEqual(self.get(start='2024-02-29', end='2024-02-29')['summary']['completed_works'], 1)
        self.assertEqual(self.get(start='2024-02-29', end='2024-02-29')['summary']['started_works'], 0)
        entries = self.rows(month='2024-02', section='started')['results']
        self.assertEqual(entries[0]['actual_date'], '2024-02')
        self.assertEqual(entries[0]['date_bounds'], {'start': '2024-02-01', 'end': '2024-02-29'})

    def test_cross_month_round_does_not_imply_viewing_in_intervening_months(self):
        self.finish(started='2026-03', completed='2026-11')
        self.assertEqual(self.get(month='2026-03')['summary']['started_works'], 1)
        self.assertEqual(self.get(month='2026-11')['summary']['completed_works'], 1)
        self.assertEqual(self.get(month='2026-06')['summary']['viewed_works'], 0)

    def test_unknown_legacy_completion_not_counted_by_created_or_recorded_time(self):
        self.record.watch_status = 'completed'; self.record.watched_eps = 12; self.record.save()
        data = self.get()
        self.assertEqual(data['summary']['completed_works'], 0)
        self.assertEqual(data['coverage']['undated']['works'], 1)
        self.assertEqual(self.rows(section='undated')['count'], 2)
        self.assertEqual(self.rows(section='undated')['results'][0]['date_precision'], 'unknown')

    def test_notes_and_memories_use_actual_date_without_inventing_completion(self):
        self.send('note', note='未填观看日期的随记')
        self.send('memory', note='十月往事', occurred_on='2026-10')
        summary = self.get()['summary']
        self.assertEqual(summary['notes'], 1); self.assertEqual(summary['memories'], 1)
        self.assertEqual(summary['completed_works'], 0); self.assertEqual(summary['viewed_works'], 0)
        self.assertEqual(summary['related_works'], 1)
        row = self.rows(section='notes')['results'][0]
        self.assertEqual(row['note'], '十月往事'); self.assertEqual(row['actual_date'], '2026-10')
        self.assertNotIn('operation_id', row); self.assertNotIn('before', row)

    def test_dated_thought_attached_to_transition_is_preserved_and_undo_removes_it(self):
        self.send('transition', watch_status='watching', occurred_on='2026-10-03', note='开始时的感想')
        self.assertEqual(self.get()['summary']['notes'], 1)
        row = self.rows(section='notes')['results'][0]
        self.assertEqual(row['note'], '开始时的感想'); self.assertEqual(row['source_kind'], 'transition')
        self.record.refresh_from_db(); self.send('undo', target_revision=self.record.watch_revision)
        self.assertEqual(self.get()['summary']['notes'], 0)

    def test_undo_excludes_note_and_voided_rewatch_without_reusing_round_numbers(self):
        self.finish()
        self.send('start-rewatch', started_on='2026-10-12')
        self.record.refresh_from_db(); self.send('undo', target_revision=self.record.watch_revision)
        self.send('start-rewatch', started_on='2026-10-14')
        self.assertEqual(self.rows(section='started')['count'], 2)
        self.assertEqual(self.get()['summary']['rewatch_started_rounds'], 1)
        self.send('note', note='随后撤销', occurred_on='2026-10-15')
        self.record.refresh_from_db(); self.send('undo', target_revision=self.record.watch_revision)
        self.assertEqual(self.get()['summary']['notes'], 0)

    def test_corrected_round_dates_and_ratings_move_milestones_not_old_event_snapshots(self):
        self.finish()
        old = self.get()['snapshot']
        self.send('update-round', round_number=1, started_on='2026-09-02', completed_on='2026-09-10', rating='7.0')
        self.assertEqual(self.get()['summary']['completed_rounds'], 0)
        self.assertEqual(self.get()['summary']['activity_facts'], 0)
        self.assertEqual(self.get(month='2026-09')['summary']['completed_round_rating']['average'], '7.00')
        self.assertEqual(self.client.get(self.entries, {'month':'2026-10', 'snapshot':old}).status_code, 409)

    def test_unfinished_round_with_old_completion_date_is_not_counted(self):
        self.finish()
        self.send('transition', watch_status='watching', watched_eps=4, occurred_on='2026-10-15')
        self.assertEqual(self.get()['summary']['completed_rounds'], 0)

    def test_explicit_positive_progress_and_pause_resume_but_no_episode_sum(self):
        self.send('transition', watch_status='watching', occurred_on='2026-09-30')
        self.send('transition', watched_eps=3, occurred_on='2026-10-02')
        self.send('transition', watched_eps=2, occurred_on='2026-10-03')
        self.send('transition', total_eps=13, occurred_on='2026-10-04')
        self.send('transition', watch_status='on_hold', occurred_on='2026-10-05')
        self.send('transition', watch_status='watching', occurred_on='2026-10-06')
        self.assertEqual(self.get()['summary']['activity_facts'], 3)
        self.assertEqual(self.get()['summary']['viewed_works'], 1)
        self.assertNotIn('watched_episodes', self.get()['summary'])

    def test_clear_removes_all_past_facts_and_preserves_current_private_record(self):
        self.finish(); self.send('note', note='被清空', occurred_on='2026-10')
        before = self.record.review
        self.send('clear', confirm_clear=True)
        self.assertEqual(self.get()['summary']['related_works'], 0)
        self.assertEqual(self.rows(section='notes')['count'], 0)
        self.record.refresh_from_db(); self.assertEqual(self.record.review, before)
        self.assertEqual(self.get()['coverage']['undated']['works'], 1)

    def test_counts_cover_full_scope_and_pagination_is_stable_and_guarded(self):
        for n in range(4): self.finish(self.work(f'作品 {n}'))
        first = self.rows(page_size=1)
        self.assertEqual(first['summary']['completed_works'], 4)
        self.assertEqual(first['count'], 4)
        seen = [first['results'][0]['work']['record_id']]
        for page in (2, 3, 4):
            result = self.rows(page=page, page_size=1, snapshot=first['snapshot'])
            seen.append(result['results'][0]['work']['record_id'])
            self.assertEqual(result['summary'], first['summary'])
        self.assertEqual(len(set(seen)), 4)
        self.assertEqual(self.client.get(self.entries, {'month':'2026-10', 'page':2}).status_code, 400)
        self.send('memory', note='并发新补录', occurred_on='2026-10')
        self.assertEqual(self.client.get(self.entries, {'month':'2026-10', 'page':2, 'snapshot':first['snapshot']}).status_code, 409)

    def test_snapshot_bound_to_owner_period_expiry_and_does_not_extend_on_page(self):
        self.finish(); token = self.get()['snapshot']
        self.assertEqual(self.rows(snapshot=token)['snapshot'], token)
        for params in ({'month':'2026-11', 'snapshot':token}, {'month':'2026-10', 'snapshot':token+'x'}):
            response = self.client.get(self.entries, params)
            self.assertEqual(response.status_code, 409); self.assertEqual(response.data['code'], 'watch_review_changed')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.entries, {'month':'2026-10','snapshot':token}).status_code, 409)
        self.client.force_authenticate(self.user)
        proof = signing.loads(token, salt=SALT)
        with patch('django.core.signing.time.time', return_value=1): expired = signing.dumps(proof, salt=SALT)
        self.assertEqual(self.client.get(self.entries, {'month':'2026-10','snapshot':expired}).status_code, 409)

    def test_authentication_unknown_duplicate_and_invalid_query_fields(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.client.force_authenticate(self.user)
        invalid = [{'month':'2026-13'}, {'month':'2026-1'}, {'start':'2026-01-01'},
            {'start':'2026-10-01','end':'2026-09-01'}, {'month':'2026-10','start':'2026-10-01','end':'2026-10-31'},
            {'start':'2026-02-30','end':'2026-03-01'}, {'start':'2025-01-01','end':'2026-01-02'},
            {'user_id':self.other.pk}, {'search':'作品'}, {'page_size':0}, {'record_id':self.record.pk}]
        for params in invalid:
            self.assertEqual(self.client.get(self.entries, params).status_code, 400, params)
        self.assertEqual(self.client.get(self.entries+'?month=2026-10&month=2026-11').status_code, 400)
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 405)

    def test_other_account_never_in_counts_or_results_and_same_title_not_merged(self):
        self.finish()
        second = self.work('另一个私人版本'); second.private_chinese_title = self.record.private_chinese_title; second.save(update_fields=['private_chinese_title'])
        self.finish(second)
        foreign = UserAnimeRecord.objects.create(user=self.other, anime=self.record.anime, watch_status='completed')
        WatchRound.objects.create(record=foreign, number=1, status='completed', started_on='2026-10', completed_on='2026-10')
        self.assertEqual(self.get()['summary']['completed_works'], 2)
        self.assertEqual({e['work']['record_id'] for e in self.rows()['results']}, {self.record.pk, second.pk})

    def test_current_metadata_label_cover_and_round_score_are_separate(self):
        self.finish()
        self.record.private_chinese_title = '当前私人名称'; self.record.personal_score = Decimal('2.0')
        self.record.save(update_fields=['private_chinese_title', 'personal_score'])
        result = self.rows(section='completed')['results'][0]
        self.assertEqual(result['rating'], '9.0'); self.assertEqual(result['work']['current_score'], '2.0')
        self.assertEqual(result['work']['title'], '当前私人名称')
        self.assertEqual(result['work']['poster_url'], 'https://media.xh-anime.com/original.jpg?a=1&b=2')
        self.assertEqual(result['work']['metadata_basis'], 'current_journal')

    def test_deletion_and_backup_restore_invalidate_tokens_and_recompute(self):
        self.finish(); token = self.get()['snapshot']
        backup = json.loads(self.client.get(reverse('anime_api:journal-export')).content)
        self.send('clear', confirm_clear=True)
        restored = self.client.post(reverse('anime_api:journal-import'), backup, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(self.get()['summary']['completed_works'], 1)
        self.assertEqual(self.client.get(self.entries, {'month':'2026-10','snapshot':token}).status_code, 409)
        UserAnimeRecord.objects.get(user=self.user).delete()
        self.assertEqual(self.get()['summary']['completed_works'], 0)

    def test_database_outage_and_bounds_fail_without_partial_statistics(self):
        from django.db import DatabaseError
        with patch('anime.watch_review.read_source', side_effect=DatabaseError('synthetic')):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 503)
        with patch.dict(LIMITS, records=0):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 503); self.assertNotIn('summary', response.data)

    def test_planned_dates_do_not_create_viewing_facts_or_invalidate_review(self):
        from .watch_plans import command as plan_command
        token = self.get()['snapshot']
        plan_command(self.user, self.record.pk, {'action':'enroll', 'version':0, 'operation_id':str(uuid4()),
            'priority':1, 'planned_for':'2026-10', 'note':'安排不等于观看'})
        response = self.rows(snapshot=token)
        self.assertEqual(response['summary']['viewed_works'], 0)
        self.assertEqual(response['coverage']['undated']['facts'], 0)
        self.assertFalse(WatchRound.objects.exists()); self.assertFalse(WatchEvent.objects.exists())

    def test_database_snapshot_rejects_accidental_writes(self):
        def attempt_write(user):
            UserAnimeRecord.objects.filter(pk=self.record.pk).update(review='不应写入')
            raise AssertionError('The database must reject a write in a review snapshot')
        with patch('anime.watch_review.read_source', side_effect=attempt_write):
            self.assertEqual(self.client.get(self.url).status_code, 503)
        self.record.refresh_from_db()
        self.assertEqual(self.record.review, '私人综合长评不应作为当期随记')

    def test_query_count_is_bounded_and_read_never_uses_write_queries(self):
        self.finish()
        self.rows()
        with CaptureQueriesContext(connection) as small: self.rows()
        for n in range(12): self.finish(self.work(f'额外作品 {n}'))
        with CaptureQueriesContext(connection) as large: result = self.rows()
        self.assertEqual(result['summary']['completed_works'], 13)
        self.assertLessEqual(len(large), len(small) + 1)
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE') for q in large))

    @skipUnless(connection.vendor == 'postgresql', 'Requires independent PostgreSQL MVCC connections')
    def test_concurrent_completion_cannot_mix_old_records_and_new_rounds_in_one_review(self):
        from .watch_review import bounded
        self.send('transition', watch_status='watching', occurred_on='2026-09-20')
        version = UserAnimeRecord.objects.get(pk=self.record.pk).watch_revision
        def writer():
            close_old_connections()
            try:
                return execute(self.record.pk, self.user, {'action':'transition', 'version':version,
                    'operation_id':str(uuid4()), 'watch_status':'completed', 'watched_eps':12, 'occurred_on':'2026-10-05'})
            finally: close_old_connections()
        fired = False
        with ThreadPoolExecutor(max_workers=1) as executor:
            def interleaved(query, kind):
                nonlocal fired
                result = bounded(query, kind)
                if kind == 'records' and not fired:
                    fired = True
                    executor.submit(writer).result(timeout=15)
                return result
            with patch('anime.watch_review.bounded', side_effect=interleaved):
                first = self.get()
        self.assertEqual(first['summary']['completed_works'], 0)
        self.assertEqual(self.get()['summary']['completed_works'], 1)
        self.assertEqual(self.client.get(self.entries, {'month':'2026-10', 'snapshot':first['snapshot']}).status_code, 409)

    def test_expiring_storage_urls_are_not_part_of_snapshot_fingerprint(self):
        from django.db.models.fields.files import FieldFile
        self.finish()
        self.record.private_poster_image.name = 'anime/posters/stable.jpg'
        self.record.save(update_fields=['private_poster_image'])
        with patch.object(FieldFile, 'url', new_callable=PropertyMock,
                          return_value='https://media.xh-anime.com/stable.jpg?v=first'):
            first = self.rows()
        with patch.object(FieldFile, 'url', new_callable=PropertyMock, return_value='https://media.xh-anime.com/stable.jpg?v=second'):
            second = self.rows(snapshot=first['snapshot'])
        self.assertEqual(first['snapshot'], second['snapshot'])
        self.assertNotEqual(first['results'][0]['work']['poster_url'], second['results'][0]['work']['poster_url'])
