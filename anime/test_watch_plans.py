import json
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient, APITestCase

from .models import Anime, UserAnimeRecord, WatchEvent, WatchPlan, WatchPlanOperation, WatchPlanQueue, WatchRound


class WatchPlanTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('plan-owner')
        self.other = get_user_model().objects.create_user('plan-other')
        self.client.force_authenticate(self.user)
        self.record = self.work('首部合成作品')

    def work(self, title, status='plan', user=None):
        anime = Anime.objects.create(chinese_title=title, airing_period='2020-1')
        return UserAnimeRecord.objects.create(user=user or self.user, anime=anime, total_eps=12, watch_status=status, personal_score=Decimal('9.1'), review='保留长评')

    def detail(self, record=None):
        response = self.client.get(f'/api/records/{(record or self.record).pk}/watch-plan/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def queue(self, **params):
        response = self.client.get('/api/watch-plans/', params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def plan(self, action, record=None, **fields):
        record = record or self.record
        body = {'action': action, 'version': self.detail(record)['version'], 'operation_id': str(uuid4()), **fields}
        return self.client.post(f'/api/records/{record.pk}/watch-plan/', body, format='json')

    def history(self, action, record=None, **fields):
        record = record or self.record; record.refresh_from_db()
        return self.client.post(f'/api/records/{record.pk}/watch-history/', {'action': action, 'version': record.watch_revision, 'operation_id': str(uuid4()), **fields}, format='json')

    def export(self):
        response = self.client.get('/api/export/')
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def ordered(self):
        return [row['record']['title'] for row in self.queue()['results']]

    def test_gets_and_old_records_never_create_plans_queue_rounds_or_events(self):
        before = list(UserAnimeRecord.objects.values())
        self.assertEqual(self.detail()['plan'], None)
        self.assertEqual(self.queue()['count'], 0)
        self.assertEqual(self.detail()['version'], 0)
        self.assertEqual(self.export()['records'][0]['watch_plan'], None)
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        for model in (WatchPlan, WatchPlanQueue, WatchRound, WatchEvent, WatchPlanOperation):
            self.assertEqual(model.objects.count(), 0)

    def test_enrollment_infers_purpose_without_starting_or_fabricating_dates(self):
        for status, purpose, phase in [('plan', 'first', 'pending'), ('watching', 'continue', 'ongoing'), ('on_hold', 'resume', 'pending'), ('completed', 'rewatch', 'pending')]:
            record = self.work(status, status)
            before = UserAnimeRecord.objects.values().get(pk=record.pk)
            response = self.plan('enroll', record, planned_for='2030-02', note='安排并非观看日期')
            self.assertEqual(response.status_code, 200, response.data)
            plan = response.data['plan']
            self.assertEqual((plan['priority'], plan['purpose'], plan['state']), (2, purpose, phase))
            self.assertEqual(plan['target_round_number'], None if purpose == 'rewatch' else 1)
            self.assertEqual(plan['planned_for'], '2030-02')
            self.assertEqual(UserAnimeRecord.objects.values().get(pk=record.pk), before)
        self.assertEqual(WatchRound.objects.count(), 0)
        self.assertEqual(WatchEvent.objects.count(), 0)

    def test_validation_and_action_owned_fields_are_strict_and_atomic(self):
        for fields in ({'priority': 0}, {'priority': 4}, {'priority': True}, {'priority': '1'}, {'planned_for': '2021-02-29'}, {'planned_for': '2020-2'}, {'note': 'x' * 501}, {'position': 0}, {'state': 'completed'}, {'user': self.other.pk}, {'watch_version': 0}):
            result = self.plan('enroll', **fields)
            self.assertEqual(result.status_code, 400, result.data)
        self.assertEqual(WatchPlanQueue.objects.count(), 0)
        self.assertEqual(WatchPlan.objects.count(), 0)
        self.assertEqual(self.plan('update', note='none').status_code, 400)
        self.assertEqual(self.plan('enroll', priority=1, planned_for='2000').status_code, 200)
        self.assertEqual(self.plan('enroll').status_code, 400)
        self.assertEqual(self.plan('update').status_code, 400)
        self.assertEqual(self.plan('update', priority=1).status_code, 400)
        self.assertEqual(self.plan('start').status_code, 400)
        self.assertEqual(self.detail()['record']['watch_status'], 'plan')

    def test_manual_queue_ignores_rating_and_updates_do_not_reorder(self):
        second = self.work('第二部'); third = self.work('第三部')
        self.plan('enroll'); self.plan('enroll', second); self.plan('enroll', third, priority=1)
        second.personal_score = 10; second.save()
        self.assertEqual(self.ordered(), ['第三部', '首部合成作品', '第二部'])
        position = self.detail()['plan']['position']
        self.plan('update', note='备注不改变顺序', planned_for='2031')
        self.assertEqual(self.detail()['plan']['position'], position)
        self.plan('update', priority=1)
        self.assertEqual(self.ordered(), ['第三部', '首部合成作品', '第二部'])
        planned = self.queue(sort='planned')
        self.assertEqual(planned['results'][0]['record']['id'], self.record.pk)
        self.assertEqual(self.ordered(), ['第三部', '首部合成作品', '第二部'])

    def test_reorder_requires_full_same_priority_queue_and_is_idempotent(self):
        second = self.work('第二部'); urgent = self.work('另一档')
        self.plan('enroll'); self.plan('enroll', second); self.plan('enroll', urgent, priority=1)
        version = self.queue()['version']
        body = {'version': version, 'operation_id': str(uuid4()), 'priority': 2, 'record_ids': [second.pk, self.record.pk]}
        result = self.client.post('/api/watch-plans/reorder/', body, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.ordered(), ['另一档', '第二部', '首部合成作品'])
        self.assertTrue(self.client.post('/api/watch-plans/reorder/', body, format='json').data['replayed'])
        for ids in ([second.pk], [second.pk, second.pk], [urgent.pk, self.record.pk]):
            bad = {**body, 'version': self.queue()['version'], 'operation_id': str(uuid4()), 'record_ids': ids}
            self.assertEqual(self.client.post('/api/watch-plans/reorder/', bad, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/watch-plans/reorder/?search=partial', body, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/watch-plans/reorder/', {**body, 'operation_id': str(uuid4())}, format='json').status_code, 409)

    def test_filters_and_pagination_cover_whole_queue_and_keep_stable_order(self):
        self.plan('enroll', priority=1)
        for index in range(5): self.plan('enroll', self.work(f'筛选{index}'), priority=2)
        page = self.queue(search='筛选', priority=2, page=2, page_size=2)
        self.assertEqual(page['count'], 5)
        self.assertEqual([row['record']['title'] for row in page['results']], ['筛选2', '筛选3'])
        self.assertEqual(page['counts']['pending'], 6)
        self.assertEqual(page['counts_scope'], 'owner_all_plans')
        for params in ({'priority': 4}, {'page_size': 101}, {'state': 'invalid'}, {'sort': 'score-desc'}, {'search': 'x'*101}, {'unknown': 'x'}):
            self.assertEqual(self.client.get('/api/watch-plans/', params).status_code, 400)

    def test_receipt_replay_returns_current_state_and_cannot_change_target_or_payload(self):
        body = {'action': 'enroll', 'version': 0, 'operation_id': str(uuid4()), 'note': '原备注'}
        url = f'/api/records/{self.record.pk}/watch-plan/'
        self.assertEqual(self.client.post(url, body, format='json').status_code, 200)
        self.plan('update', note='新备注')
        response = self.client.post(url, body, format='json')
        self.assertTrue(response.data['replayed'])
        self.assertEqual(response.data['plan']['note'], '新备注')
        self.assertEqual(response.data['version'], 2)
        self.assertEqual(self.client.post(url, {**body, 'note': 'different'}, format='json').status_code, 409)
        other = self.work('另一个目标')
        self.assertEqual(self.client.post(f'/api/records/{other.pk}/watch-plan/', body, format='json').status_code, 409)
        self.assertEqual(self.client.post(url, {**body, 'operation_id': str(uuid4())}, format='json').status_code, 409)

    def test_actual_start_uses_history_and_never_copies_planned_date(self):
        self.plan('enroll', planned_for='2030-12-01', note='周末再看')
        response = self.plan('start', watch_version=0)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['plan']['state'], 'ongoing')
        self.assertEqual(WatchRound.objects.get().started_on, '')
        self.assertEqual(WatchEvent.objects.get().kind, 'transition')
        self.assertEqual(response.data['plan']['planned_for'], '2030-12-01')
        self.record.refresh_from_db()
        self.assertEqual(self.record.personal_score, Decimal('9.1'))
        self.assertEqual(self.record.review, '保留长评')
        resumed = self.work('恢复', 'on_hold'); self.plan('enroll', resumed)
        self.assertEqual(self.plan('start', resumed, watch_version=0, occurred_on='2022-02').status_code, 200)
        self.assertEqual(resumed.watch_events.get().occurred_on, '2022-02')

    def test_start_replay_with_same_uuid_has_only_one_actual_event(self):
        self.plan('enroll')
        body = {'action': 'start', 'version': 1, 'watch_version': 0, 'operation_id': str(uuid4())}
        url = f'/api/records/{self.record.pk}/watch-plan/'
        self.assertEqual(self.client.post(url, body, format='json').status_code, 200)
        self.assertTrue(self.client.post(url, body, format='json').data['replayed'])
        self.assertEqual(WatchEvent.objects.count(), 1)
        self.assertEqual(self.plan('start', watch_version=1).status_code, 409)

    def test_old_edit_progress_completion_and_undo_reconcile_plan(self):
        self.plan('enroll')
        self.assertEqual(self.client.patch(f'/api/records/{self.record.pk}/', {'watch_status': 'watching'}, format='json').status_code, 200)
        self.assertEqual(self.detail()['plan']['state'], 'ongoing')
        version = self.detail()['version']
        self.assertEqual(self.client.patch(f'/api/records/{self.record.pk}/progress/', {'increment': 1}, format='json').status_code, 200)
        self.assertEqual(self.detail()['version'], version + 1)
        self.history('transition', watch_status='completed', watched_eps=12)
        self.assertEqual(self.detail()['plan']['state'], 'completed')
        self.history('undo', target_revision=3)
        self.assertEqual(self.detail()['plan']['state'], 'ongoing')

    def test_rewatch_intention_requires_explicit_round_and_undo_can_reopen_it(self):
        self.record.watch_status = 'completed'; self.record.save()
        self.plan('enroll')
        self.assertEqual(self.detail()['plan']['state'], 'pending')
        self.history('transition', watch_status='watching')
        self.assertEqual(self.detail()['plan']['target_round_number'], None)
        self.assertEqual(self.detail()['plan']['state'], 'pending')
        self.assertEqual(self.plan('start', watch_version=self.detail()['record']['watch_revision']).status_code, 409)
        self.history('transition', watch_status='completed')
        self.assertEqual(self.plan('start', watch_version=self.detail()['record']['watch_revision']).status_code, 200)
        target = self.detail()['plan']['target_round_number']
        self.assertEqual(target, 2)
        self.history('undo', target_revision=self.detail()['record']['watch_revision'])
        self.assertEqual(self.detail()['plan']['state'], 'pending')
        self.assertIsNone(self.detail()['plan']['target_round_number'])
        self.plan('start', watch_version=self.detail()['record']['watch_revision'])
        self.assertEqual(self.detail()['plan']['target_round_number'], 3)
        self.history('transition', watch_status='completed')
        self.assertEqual(self.detail()['plan']['state'], 'completed')
        self.history('start-rewatch')  # An unrelated later round is not this plan.
        self.assertEqual(self.detail()['plan']['state'], 'completed')

    def test_clear_history_preserves_arrangement_and_rebases_actual_round(self):
        self.record.watch_status = 'completed'; self.record.save()
        self.plan('enroll', priority=1, planned_for='2030', note='意图保留')
        self.plan('start', watch_version=self.detail()['record']['watch_revision'])
        response = self.history('clear', confirm_clear=True)
        self.assertEqual(response.status_code, 200, response.data)
        plan = self.detail()['plan']
        self.assertEqual((plan['note'], plan['planned_for'], plan['priority']), ('意图保留', '2030', 1))
        self.assertEqual((plan['state'], plan['target_round_number'], plan['history_base']), ('ongoing', 1, response.data['version']))
        self.assertEqual(WatchEvent.objects.count(), 0)

    def test_remove_reenroll_and_record_delete_preserve_history_and_invalidate_queue(self):
        self.plan('enroll'); self.plan('start', watch_version=0)
        self.assertEqual(self.plan('remove').status_code, 200)
        self.assertEqual(self.queue()['count'], 0)
        self.assertEqual(self.detail()['plan']['state'], 'archived')
        self.assertEqual(WatchEvent.objects.count(), 1)
        self.assertEqual(self.plan('enroll').data['plan']['purpose'], 'continue')
        version = self.detail()['version']
        self.assertEqual(self.client.delete(f'/api/records/{self.record.pk}/').status_code, 204)
        self.assertEqual(self.queue()['version'], version + 1)
        self.assertEqual(WatchPlan.objects.count(), 0)

    def test_start_and_metadata_failures_roll_back_ledger_queue_and_receipts(self):
        with patch('anime.watch_plans.WatchPlan.save', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError): self.plan('enroll')
        self.assertEqual(WatchPlanQueue.objects.count(), 0)
        self.plan('enroll')
        with patch('anime.watch_plans.WatchPlan.save', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError): self.plan('start', watch_version=0)
        self.assertEqual(self.detail()['version'], 1)
        self.assertEqual(self.detail()['record']['watch_revision'], 0)
        self.assertEqual(WatchEvent.objects.count(), 0)
        self.assertEqual(WatchRound.objects.count(), 0)
        self.assertEqual(WatchPlanOperation.objects.count(), 1)

    def test_owner_scope_and_public_payloads_never_expose_plan_content(self):
        self.plan('enroll', note='private-plan-secret', planned_for='2030')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.queue()['count'], 0)
        url = f'/api/records/{self.record.pk}/watch-plan/'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'action': 'remove', 'version': 1, 'operation_id': str(uuid4())}, format='json').status_code, 404)
        profile = self.user.profile; profile.is_public = True; profile.public_status = 'approved'; profile.save()
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/watch-plans/').status_code, (401, 403))
        public = self.client.get('/api/public-journal/users/plan-owner/records/').data
        self.assertNotIn('private-plan-secret', json.dumps(public))
        self.assertNotIn('watch_plan', json.dumps(public))
        self.client.force_authenticate(self.user)
        self.assertIn('no-store', self.client.get(url)['Cache-Control'])

    def test_full_backup_round_trip_order_precision_phase_and_owner(self):
        second = self.work('第二部'); self.plan('enroll', priority=1, planned_for='2030-02', note='保留计划备注')
        self.plan('enroll', second, priority=1)
        self.plan('start', watch_version=0, occurred_on='2020')
        before = self.export()
        self.assertEqual(before['records'][0]['watch_plan']['version'], 1)
        result = self.client.post('/api/import/', before, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        after = self.export()
        self.assertEqual([row['watch_plan'] for row in before['records']], [row['watch_plan'] for row in after['records']])
        self.assertEqual(self.ordered(), ['首部合成作品', '第二部'])
        self.assertTrue(all(plan.record.user_id == self.user.pk and plan.queue.user_id == self.user.pk for plan in WatchPlan.objects.select_related('record', 'queue')))

    def test_old_backup_omission_preserves_explicit_null_clears_and_missing_work_cascades(self):
        self.plan('enroll', note='保留')
        old = self.export(); del old['records'][0]['watch_plan']
        self.assertEqual(self.client.post('/api/import/', old, format='json').status_code, 200)
        self.assertEqual(WatchPlan.objects.get().note, '保留')
        current = self.export(); current['records'][0]['watch_plan'] = None
        self.assertEqual(self.client.post('/api/import/', current, format='json').status_code, 200)
        self.assertEqual(WatchPlan.objects.count(), 0)
        self.record = UserAnimeRecord.objects.get(user=self.user)
        self.plan('enroll')
        self.assertEqual(self.client.post('/api/import/', {'records': []}, format='json').status_code, 200)
        self.assertEqual(WatchPlan.objects.count(), 0)

    def test_invalid_backup_is_rejected_before_any_replacement(self):
        self.plan('enroll')
        payload = self.export()
        for changes in ({'priority': 9}, {'planned_for': '2023-02-29'}, {'target_round_number': 999}, {'queue_id': 999}, {'state': 'unknown'}, {'state': 'completed'}, {'history_base': 99999999999999999999}):
            bad = json.loads(json.dumps(payload)); bad['records'][0]['watch_plan'].update(changes)
            self.assertEqual(self.client.post('/api/import/', bad, format='json').status_code, 400)
            self.assertTrue(UserAnimeRecord.objects.filter(pk=self.record.pk).exists())
        self.assertEqual(WatchPlan.objects.count(), 1)

    def test_restore_preview_counts_plans_and_confirmation_rejects_plan_only_change(self):
        self.plan('enroll', note='原计划')
        payload = self.export()
        response = self.client.post('/api/import/?preview=true', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['plans_in_file'], 1)
        self.assertEqual(response.data['replaced_plans'], 1)
        self.plan('update', note='预览后改了计划')
        response = self.client.post('/api/import/?confirm=true', {**payload, 'confirmation_token': response.data['token']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(WatchPlan.objects.get().note, '预览后改了计划')

    def test_full_restore_preserves_reordered_positions_and_queue_version_never_rewinds(self):
        second = self.work('第二部'); third = self.work('第三部')
        for record in (self.record, second, third): self.plan('enroll', record)
        result = self.client.post('/api/watch-plans/reorder/', {'version': self.queue()['version'], 'operation_id': str(uuid4()), 'priority': 2, 'record_ids': [third.pk, self.record.pk, second.pk]}, format='json')
        self.assertEqual(result.status_code, 200)
        before = self.queue()['version']
        payload = self.export()
        self.assertEqual(self.client.post('/api/import/', payload, format='json').status_code, 200)
        self.assertGreater(self.queue()['version'], before)
        self.assertEqual(self.ordered(), ['第三部', '首部合成作品', '第二部'])

    def test_omitted_plan_rebases_when_history_is_explicitly_replaced_by_older_backup(self):
        self.record.watch_status = 'completed'; self.record.save()
        old = self.export()
        self.plan('enroll', note='准备重看保留')
        self.plan('start', watch_version=self.detail()['record']['watch_revision'])
        self.history('clear', confirm_clear=True)
        self.assertEqual(self.detail()['plan']['target_round_number'], 1)
        del old['records'][0]['watch_plan']
        self.assertEqual(self.client.post('/api/import/', old, format='json').status_code, 200)
        restored = WatchPlan.objects.get()
        self.assertEqual(restored.history_base, 0)
        self.assertEqual(restored.note, '准备重看保留')
        self.assertEqual(restored.target_round_number, 1)

    def test_other_account_backup_creates_only_target_owned_plans_without_database_ids(self):
        self.plan('enroll', note='允许通过自己的备份转移')
        payload = self.export()
        self.assertNotIn('queue_id', payload['records'][0]['watch_plan'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post('/api/import/', payload, format='json').status_code, 200)
        self.assertEqual(WatchPlan.objects.filter(record__user=self.user).count(), 1)
        self.assertEqual(WatchPlan.objects.filter(record__user=self.other, queue__user=self.other).count(), 1)

    def test_sparse_imported_position_does_not_overflow_when_appending(self):
        self.plan('enroll')
        WatchPlan.objects.filter(record=self.record).update(position=2147483646)
        second = self.work('追加作品')
        self.assertEqual(self.plan('enroll', second).status_code, 200)
        self.assertEqual(self.ordered(), ['首部合成作品', '追加作品'])
        self.assertLess(WatchPlan.objects.get(record=second).position, 2147483000)

    def test_restore_failure_rolls_back_original_records_plans_and_queue_version(self):
        self.plan('enroll', note='失败后仍须保留')
        payload, before = self.export(), self.detail()['version']
        with patch('anime.watch_plans.restore_plan', side_effect=RuntimeError('synthetic restore failure')):
            with self.assertLogs('anime.views', level='ERROR'):
                response = self.client.post('/api/import/', payload, format='json')
            self.assertEqual(response.status_code, 400)
        self.assertTrue(UserAnimeRecord.objects.filter(pk=self.record.pk).exists())
        self.assertEqual(self.detail()['version'], before)
        self.assertEqual(self.detail()['plan']['note'], '失败后仍须保留')

    def test_ambiguous_legacy_plan_match_is_rejected_before_replacement(self):
        self.plan('enroll')
        another = self.work('不同的公共作品')
        another.private_chinese_title = self.record.private_chinese_title
        another.save(update_fields=['private_chinese_title'])
        payload = self.export(); payload['records'] = payload['records'][:1]
        del payload['records'][0]['watch_plan']
        response = self.client.post('/api/import/?preview=true', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.user).count(), 2)

    def test_account_deletion_cascades_plans_queue_and_receipts_without_recreation(self):
        self.plan('enroll')
        self.user.delete()
        for model in (WatchPlan, WatchPlanQueue, WatchPlanOperation):
            self.assertEqual(model.objects.count(), 0)


class WatchPlanConcurrencyTests(TransactionTestCase):
    def race(self, mode):
        if connection.vendor != 'postgresql': self.skipTest('PostgreSQL row locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user = get_user_model().objects.create_user(f'plan-race-{uuid4().hex}')
        records = [UserAnimeRecord.objects.create(user=user, anime=Anime.objects.create(chinese_title=f'并发{i}-{uuid4().hex}', airing_period='2020-1')) for i in range(2)]
        client = APIClient(); client.force_authenticate(user)
        if mode != 'enroll':
            response = client.post(f'/api/records/{records[0].pk}/watch-plan/', {'action': 'enroll', 'version': 0, 'operation_id': str(uuid4())}, format='json')
            self.assertEqual(response.status_code, 200)
        if mode == 'reorder':
            self.assertEqual(client.post(f'/api/records/{records[1].pk}/watch-plan/', {'action': 'enroll', 'version': 1, 'operation_id': str(uuid4())}, format='json').status_code, 200)
        barrier, operation = Barrier(2), str(uuid4())
        def write(index):
            close_old_connections()
            try:
                api = APIClient(); api.force_authenticate(get_user_model().objects.get(pk=user.pk))
                if mode == 'enroll':
                    url = f'/api/records/{records[index].pk}/watch-plan/'
                    body = {'action': 'enroll', 'version': 0, 'operation_id': str(uuid4())}
                elif mode == 'start':
                    url = f'/api/records/{records[0].pk}/watch-plan/'
                    body = {'action': 'start', 'version': 1, 'watch_version': 0, 'operation_id': operation}
                elif mode == 'reorder' and not index:
                    url = '/api/watch-plans/reorder/'
                    body = {'version': 2, 'operation_id': str(uuid4()), 'priority': 2, 'record_ids': [records[1].pk, records[0].pk]}
                elif mode == 'reorder':
                    url = f'/api/records/{records[1].pk}/watch-history/'
                    body = {'action': 'transition', 'watch_status': 'watching', 'version': 0, 'operation_id': str(uuid4())}
                elif index:
                    url = f'/api/records/{records[0].pk}/watch-history/'
                    body = {'action': 'transition', 'watch_status': 'completed', 'version': 0, 'operation_id': str(uuid4())}
                else:
                    url = f'/api/records/{records[0].pk}/watch-plan/'
                    body = {'action': 'update', 'version': 1, 'operation_id': str(uuid4()), 'note': '并发备注'}
                barrier.wait(timeout=10)
                return api.post(url, body, format='json').status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = sorted(pool.map(write, [0, 1]))
        if mode == 'enroll':
            self.assertEqual(codes, [200, 409]); self.assertEqual(WatchPlan.objects.count(), 1)
        elif mode == 'start':
            self.assertEqual(codes, [200, 200]); self.assertEqual(WatchEvent.objects.count(), 1)
            self.assertEqual(WatchPlan.objects.get().state, 'ongoing')
        elif mode == 'metadata':
            self.assertIn(codes, ([200, 200], [200, 409]))
            self.assertEqual(WatchPlan.objects.get().state, 'completed')
        else:
            self.assertIn(codes, ([200, 200], [200, 409]))
            self.assertEqual(WatchPlan.objects.filter(queue__user=user).count(), 2)
            self.assertEqual(WatchPlan.objects.get(record=records[1]).state, 'ongoing')

    def test_two_initial_enrollments_use_one_queue_and_one_version_winner(self): self.race('enroll')
    def test_two_same_uuid_starts_have_one_real_viewing_event(self): self.race('start')
    def test_metadata_update_and_history_completion_never_overwrite_each_other(self): self.race('metadata')
    def test_full_queue_reorder_and_another_record_start_keep_lock_order(self): self.race('reorder')
