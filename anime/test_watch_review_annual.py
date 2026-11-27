"""D1 annual contract and attribution against real isolated database reads."""
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.db import connection, close_old_connections, DatabaseError
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITransactionTestCase

from .models import Anime, UserAnimeRecord, WatchRound, WatchEvent
from .watch_history import execute
from .watch_review import SALT, LIMITS, read_source, build_facts, bounded


class AnnualWatchReviewTests(APITransactionTestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('annual-owner')
        self.other = get_user_model().objects.create_user('annual-other')
        self.client.force_authenticate(self.user)
        self.record = self.work('年度私人作品')
        self.url = '/api/watch-review/'
        self.entries = self.url + 'entries/'

    def work(self, title, **fields):
        anime = Anime.objects.create(chinese_title=title, airing_period='2020-1')
        return UserAnimeRecord.objects.create(user=self.user, anime=anime, total_eps=12,
                                              personal_score=Decimal('9.0'), **fields)

    def send(self, action, record=None, **fields):
        record = record or self.record
        record.refresh_from_db()
        return execute(record.pk, self.user, {'action': action, 'version': record.watch_revision,
                                              'operation_id': str(uuid4()), **fields})

    def finish(self, record=None, start='2026-03', end='2026-03-20'):
        self.send('transition', record, watch_status='watching', occurred_on=start)
        return self.send('transition', record, watch_status='completed', watched_eps=12, occurred_on=end)

    def get(self, **params):
        response = self.client.get(self.url, {'year': '2026', **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def rows(self, **params):
        response = self.client.get(self.entries, {'year': '2026', **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_empty_year_contract_is_readonly_and_keeps_twelve_zero_months(self):
        before = list(UserAnimeRecord.objects.values())
        response = self.client.get(self.url, {'year': '2026'})
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(data['contract_version'], 'journal.watch-review.v1')
        self.assertEqual(data['period'], {'mode': 'year', 'start': '2026-01-01', 'end': '2026-12-31',
                                          'timezone': 'Asia/Shanghai', 'end_inclusive': True})
        self.assertEqual(data['annual']['year'], '2026')
        self.assertEqual(data['annual']['basis'], 'effective_facts_by_actual_month')
        self.assertEqual([row['month'] for row in data['annual']['months']], [f'2026-{m:02d}' for m in range(1, 13)])
        for row in data['annual']['months']:
            self.assertEqual(row['facts'], 0)
            self.assertEqual(row['summary'], data['summary'])
        self.assertEqual(data['annual']['month_unknown']['facts'], 0)
        self.assertEqual(self.rows(section='month_unknown')['results'], [])
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertFalse(WatchRound.objects.exists()); self.assertFalse(WatchEvent.objects.exists())
        self.assertIn('private', response['Cache-Control']); self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Authorization', response['Vary'])

    def test_year_query_validation_and_existing_mode_compatibility(self):
        invalid = [{'year': y} for y in ('', '0', '0000', '0999', '202', '02026', '10000', '2026-01', '2026.0', 'year')]
        invalid += [{'year': '2026', 'month': '2026-03'}, {'year': '2026', 'start': '2026-01-01'},
                    {'year': '2026', 'start': '2026-01-01', 'end': '2026-12-31'}, {'year': '2026', 'user_id': self.other.pk},
                    {'year': '2026', 'month': ''}, {'year': '2026', 'start': '', 'end': ''},
                    {'year': '', 'month': '2026-03'}]
        for params in invalid:
            self.assertEqual(self.client.get(self.entries, params).status_code, 400, params)
        self.assertEqual(self.client.get(self.entries + '?year=2026&year=2025').status_code, 400)
        for params in ({'month': '2026-03'}, {'start': '2026-01-01', 'end': '2026-12-31'}, {}):
            response = self.client.get(self.url, params)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('annual', response.data)
            self.assertEqual(self.client.get(self.entries, {**params, 'section': 'month_unknown'}).status_code, 400)
        for year in ('1000', '9999'):
            self.assertEqual(self.get(year=year)['annual']['months'][-1]['month'], year + '-12')

    def test_leap_day_and_full_year_custom_have_equal_totals(self):
        self.finish(start='2024-02', end='2024-02-29')
        annual = self.get(year='2024')
        self.assertEqual(annual['annual']['months'][1]['summary']['completed_rounds'], 1)
        self.assertEqual(annual['period']['end'], '2024-12-31')
        custom = self.client.get(self.url, {'start': '2024-01-01', 'end': '2024-12-31'})
        self.assertEqual(custom.status_code, 200)
        self.assertEqual(custom.data['summary'], annual['summary'])
        self.assertNotIn('annual', custom.data)
        row = self.rows(year='2024', section='started')['results'][0]
        self.assertEqual(row['actual_date'], '2024-02')
        self.assertEqual(row['date_bounds'], {'start': '2024-02-01', 'end': '2024-02-29'})

    def test_annual_deduplication_and_round_weighted_rating_are_not_monthly_sums(self):
        self.finish(start='2026-01', end='2026-01')
        self.send('start-rewatch', started_on='2026-10')
        self.send('transition', watch_status='completed', watched_eps=12, occurred_on='2026-10')
        self.send('update-round', round_number=2, rating='3.0')
        second, third = self.work('十月另一作品'), self.work('十月缺少评分')
        self.finish(second, start='2026-10', end='2026-10'); self.send('update-round', second, round_number=1, rating='6.0')
        self.finish(third, start='2026-10', end='2026-10'); self.send('update-round', third, round_number=1, rating=None)
        data = self.get(); summary = data['summary']; months = data['annual']['months']
        self.assertEqual((summary['completed_works'], summary['completed_rounds']), (3, 4))
        self.assertEqual((summary['first_completed_rounds'], summary['rewatch_completed_rounds']), (3, 1))
        self.assertEqual(sum(row['summary']['completed_works'] for row in months), 4)
        self.assertEqual(summary['completed_round_rating'], {'average': '6.00', 'count': 3, 'missing_count': 1,
                                                              'basis': 'effective_completed_round'})
        self.assertEqual(months[0]['summary']['completed_round_rating']['average'], '9.00')
        self.assertEqual(months[9]['summary']['completed_round_rating']['average'], '4.50')
        self.assertEqual(self.rows()['count'], 3); self.assertEqual(self.rows(section='completed')['count'], 4)

    def test_year_precision_is_annual_definite_but_never_assigned_a_month(self):
        self.finish(start='2026', end='2026-03')
        second = self.work('只记得完成年份'); self.finish(second, start='2025', end='2026')
        self.work('旧记录没有日期', watch_status='completed')
        self.send('note', note='日期未知的随记')
        self.send('memory', note='只记得这一年', occurred_on='2026')
        data = self.get(); unknown = data['annual']['month_unknown']
        self.assertEqual((data['summary']['started_rounds'], data['summary']['completed_rounds'], data['summary']['notes']), (1, 2, 1))
        self.assertEqual((unknown['facts'], unknown['works']), (3, 2))
        self.assertEqual((unknown['summary']['started_rounds'], unknown['summary']['completed_rounds'], unknown['summary']['notes']), (1, 1, 1))
        self.assertEqual(sum(row['facts'] for row in data['annual']['months']), 1)
        self.assertEqual(data['annual']['months'][2]['summary']['completed_rounds'], 1)
        self.assertEqual(data['coverage']['undated'], {'facts': 3, 'works': 2})
        self.assertEqual(data['coverage']['undated_scope'], 'owner_all_history')
        rows = self.rows(section='month_unknown')
        self.assertEqual(rows['count'], unknown['facts']); self.assertEqual(rows['count_unit'], 'facts')
        self.assertTrue(all(r['date_precision'] == 'year' and r['actual_date'] == '2026' and r['attribution'] == 'definite' for r in rows['results']))
        self.assertEqual(self.client.get(self.url, {'month': '2026-03'}).data['summary']['started_rounds'], 0)

    def test_cross_year_milestones_do_not_fill_intervening_months(self):
        self.finish(start='2025-12-31', end='2026-01-01')
        self.finish(self.work('跨到下一年'), start='2026-11', end='2027-01')
        data = self.get(); months = data['annual']['months']
        self.assertEqual((data['summary']['started_rounds'], data['summary']['completed_rounds']), (1, 1))
        self.assertEqual(months[0]['summary']['completed_rounds'], 1)
        self.assertEqual(months[10]['summary']['started_rounds'], 1)
        self.assertEqual([m['facts'] for m in months[1:10]], [0] * 9)
        self.assertEqual(months[11]['facts'], 0)
        self.assertEqual(self.get(year='2025')['summary']['started_rounds'], 1)
        self.assertEqual(self.get(year='2027')['summary']['completed_rounds'], 1)

    def test_monthly_summaries_match_independent_month_queries_and_partition_fact_counts(self):
        self.finish(start='2026', end='2026-02')
        self.send('start-rewatch', started_on='2026-05')
        self.send('transition', watched_eps=4, occurred_on='2026-06-01', note='附带感想')
        self.send('transition', watch_status='on_hold', occurred_on='2026-07')
        self.send('transition', watch_status='watching', occurred_on='2026-08')
        self.send('transition', watch_status='completed', watched_eps=12, occurred_on='2026-10')
        self.send('memory', note='本年往事', occurred_on='2026')
        data = self.get(); months = data['annual']['months']; unknown = data['annual']['month_unknown']
        for row in months:
            monthly = self.client.get(self.url, {'month': row['month']})
            self.assertEqual(monthly.status_code, 200)
            self.assertEqual(row['summary'], monthly.data['summary'])
        self.assertEqual(sum(row['facts'] for row in months) + unknown['facts'], data['summary']['activity_facts'] + data['summary']['notes'])

    def test_date_corrections_repartition_and_invalidate_old_annual_snapshot(self):
        self.finish(); first = self.get()
        self.send('update-round', round_number=1, completed_on='2026')
        second = self.get()
        self.assertEqual(second['summary'], first['summary'])
        self.assertEqual(second['annual']['months'][2]['summary']['completed_rounds'], 0)
        self.assertEqual(second['annual']['month_unknown']['summary']['completed_rounds'], 1)
        self.assertEqual(self.client.get(self.entries, {'year': '2026', 'snapshot': first['snapshot']}).status_code, 409)
        self.send('update-round', round_number=1, started_on='2025', completed_on='2025', rating='7.0')
        self.assertEqual(self.get()['summary']['completed_rounds'], 0)
        self.assertEqual(self.get(year='2025')['annual']['month_unknown']['summary']['completed_rounds'], 1)
        self.assertEqual(self.get(year='2025')['summary']['completed_round_rating']['average'], '7.00')

    def test_undo_voided_rounds_unfinished_status_and_clear_recompute_distribution(self):
        self.finish(); self.send('start-rewatch', started_on='2026-05')
        self.record.refresh_from_db(); self.send('undo', target_revision=self.record.watch_revision)
        self.send('start-rewatch', started_on='2026-06')
        self.send('transition', watch_status='completed', watched_eps=12, occurred_on='2026-06')
        data = self.get()
        self.assertEqual(data['annual']['months'][4]['facts'], 0)
        self.assertEqual(data['summary']['rewatch_completed_rounds'], 1)
        self.send('note', occurred_on='2026', note='会撤销的随记')
        self.record.refresh_from_db(); self.send('undo', target_revision=self.record.watch_revision)
        self.assertEqual(self.get()['annual']['month_unknown']['facts'], 0)
        self.send('transition', watch_status='on_hold', occurred_on='2026-07')
        self.assertEqual(self.get()['annual']['months'][5]['summary']['completed_rounds'], 0)
        self.send('clear', confirm_clear=True)
        cleared = self.get()
        self.assertEqual(cleared['summary']['related_works'], 0)
        self.assertEqual(sum(m['facts'] for m in cleared['annual']['months']), 0)
        self.assertEqual(cleared['coverage']['undated']['works'], 1)

    def test_progress_correction_changes_snapshot_but_never_adds_annual_activity(self):
        self.finish(); before = self.get()
        self.send('update-round', round_number=1, watched_eps=8, total_eps=13)
        after = self.get()
        self.assertEqual(after['summary'], before['summary']); self.assertEqual(after['annual'], before['annual'])
        self.assertEqual(self.client.get(self.url, {'year': '2026', 'snapshot': before['snapshot']}).status_code, 409)

    def test_month_unknown_pagination_reuses_one_annual_snapshot_and_excludes_other_owner(self):
        for n in range(4): self.finish(self.work(f'年份记录 {n}'), start='2026', end='2026')
        foreign = UserAnimeRecord.objects.create(user=self.other, anime=self.record.anime, watch_status='completed')
        WatchRound.objects.create(record=foreign, number=1, status='completed', started_on='2026', completed_on='2026')
        first = self.rows(section='month_unknown', page_size=3)
        self.assertEqual(first['count'], 8)
        seen = []
        for page in range(1, 4):
            result = self.rows(section='month_unknown', page_size=3, page=page, snapshot=first['snapshot'])
            self.assertEqual(result['annual'], first['annual']); self.assertEqual(result['summary'], first['summary'])
            seen += [r['id'] for r in result['results']]
            self.assertTrue(all(r['record_id'] != foreign.pk for r in result['results']))
        self.assertEqual(len(set(seen)), 8)
        self.assertEqual(self.client.get(self.entries, {'year': '2026', 'page': 2}).status_code, 400)
        self.send('memory', note='新补录', occurred_on='2026')
        self.assertEqual(self.client.get(self.entries, {'year': '2026', 'section': 'month_unknown', 'page': 2, 'snapshot': first['snapshot']}).status_code, 409)

    def test_annual_tokens_are_bound_to_owner_year_mode_and_expiry(self):
        self.finish(); token = self.get()['snapshot']
        for params in ({'year': '2025'}, {'month': '2026-03'}, {'start': '2026-01-01', 'end': '2026-12-31'}):
            self.assertEqual(self.client.get(self.entries, {**params, 'snapshot': token}).status_code, 409)
        proof = signing.loads(token, salt=SALT)
        with patch('django.core.signing.time.time', return_value=1): expired = signing.dumps(proof, salt=SALT)
        self.assertEqual(self.client.get(self.url, {'year': '2026', 'snapshot': expired}).status_code, 409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.get()['summary']['completed_rounds'], 0)
        self.assertEqual(self.client.get(self.entries, {'year': '2026', 'snapshot': token}).status_code, 409)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url, {'year': '2026'}).status_code, 401)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(self.url, {'year': '2026'}, format='json').status_code, 405)

    def test_backup_restore_and_record_deletion_recompute_full_annual_report(self):
        self.finish(start='2026', end='2026-09')
        before = self.get(); backup = json.loads(self.client.get(reverse('anime_api:journal-export')).content)
        self.send('clear', confirm_clear=True)
        restored = self.client.post(reverse('anime_api:journal-import'), backup, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        result = self.get()
        self.assertEqual(result['summary'], before['summary']); self.assertEqual(result['annual'], before['annual'])
        self.assertEqual(self.client.get(self.entries, {'year': '2026', 'snapshot': before['snapshot']}).status_code, 409)
        UserAnimeRecord.objects.get(user=self.user).delete()
        self.assertEqual(self.get()['summary']['completed_rounds'], 0)

    def test_annual_distribution_uses_one_bounded_read_and_no_month_queries(self):
        self.finish(); self.get()
        with CaptureQueriesContext(connection) as monthly: self.client.get(self.url, {'month': '2026-03'})
        with patch('anime.watch_review.read_source', wraps=read_source) as source, patch('anime.watch_review.build_facts', wraps=build_facts) as facts:
            with CaptureQueriesContext(connection) as annual: self.get()
        self.assertEqual(source.call_count, 1); self.assertEqual(facts.call_count, 1)
        self.assertEqual(len(annual), len(monthly))
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in ('INSERT', 'UPDATE', 'DELETE') for q in annual))
        for n in range(12): self.finish(self.work(f'额外年度作品 {n}'))
        with CaptureQueriesContext(connection) as large: result = self.rows()
        self.assertEqual(result['summary']['completed_works'], 13)
        self.assertLessEqual(len(large), len(annual) + 1)

    def test_failure_never_returns_partial_annual_data_and_snapshot_rejects_writes(self):
        self.finish()
        for kind in LIMITS:
            with patch.dict(LIMITS, {kind: 0}):
                response = self.client.get(self.url, {'year': '2026'})
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('summary', response.data); self.assertNotIn('annual', response.data)
        with patch('anime.watch_review.read_source', side_effect=DatabaseError('synthetic')):
            self.assertEqual(self.client.get(self.url, {'year': '2026'}).status_code, 503)
        def accidental_write(*args):
            UserAnimeRecord.objects.filter(pk=self.record.pk).update(review='must not write')
            raise AssertionError('annual aggregation must remain in the read-only snapshot')
        with patch('anime.watch_review.annual_distribution', side_effect=accidental_write):
            self.assertEqual(self.client.get(self.url, {'year': '2026'}).status_code, 503)
        self.record.refresh_from_db(); self.assertNotEqual(self.record.review, 'must not write')

    def test_planned_dates_never_create_annual_or_month_unknown_facts(self):
        from .watch_plans import command
        before = self.get()
        command(self.user, self.record.pk, {'action': 'enroll', 'version': 0, 'operation_id': str(uuid4()),
                                            'planned_for': '2026', 'note': '计划不等于观看'})
        after = self.get(snapshot=before['snapshot'])
        self.assertEqual(after['annual'], before['annual'])
        self.assertEqual(after['summary']['viewed_works'], 0)
        self.assertEqual(after['annual']['month_unknown']['facts'], 0)

    @skipUnless(connection.vendor == 'postgresql', 'Requires independent PostgreSQL MVCC connections')
    def test_concurrent_completion_keeps_annual_totals_and_months_in_same_snapshot(self):
        self.send('transition', watch_status='watching', occurred_on='2026-03')
        self.record.refresh_from_db(); version = self.record.watch_revision
        def writer():
            close_old_connections()
            try:
                return execute(self.record.pk, self.user, {'action': 'transition', 'version': version,
                    'operation_id': str(uuid4()), 'watch_status': 'completed', 'watched_eps': 12, 'occurred_on': '2026-05'})
            finally: close_old_connections()
        fired = False
        with ThreadPoolExecutor(max_workers=1) as executor:
            def interleaved(query, kind):
                nonlocal fired
                result = bounded(query, kind)
                if kind == 'records' and not fired:
                    fired = True; executor.submit(writer).result(timeout=15)
                return result
            with patch('anime.watch_review.bounded', side_effect=interleaved): before = self.get()
        self.assertEqual(before['summary']['completed_rounds'], 0)
        self.assertEqual(before['annual']['months'][4]['summary']['completed_rounds'], 0)
        after = self.get()
        self.assertEqual(after['summary']['completed_rounds'], 1)
        self.assertEqual(after['annual']['months'][4]['summary']['completed_rounds'], 1)
        self.assertEqual(self.client.get(self.entries, {'year': '2026', 'snapshot': before['snapshot']}).status_code, 409)
