from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import time
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections, connection, OperationalError
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from recommendations.binding import candidate_ticket, bind_record
from recommendations.identity_batch import confirm_identity_batch
from recommendations.models import RecommendationRecordIdentity, RecommendationIdentityEvent
from recommendations.resolution import record_row
from .test_legacy_identity import LegacyFixtures


class BatchFixtures(LegacyFixtures):
    def prepare(self):
        self.first = self.record(title='旧作品一')
        self.second = self.record(title='旧作品二')
        self.a = self.work(100)
        self.b = self.work(200, title_zh='另一个季度')

    def item(self, record, target):
        return {'record_id': record.pk, 'bangumi_id': target.bangumi_id, 'expected_version': 0,
                'ticket': candidate_ticket(record_row(record), {key: getattr(target, key) for key in
                    ('pk', 'bangumi_id', 'title_zh', 'title_jp', 'season', 'status_type', 'nsfw')}), 'confirmed': True}

    def payload(self):
        return {'batch_id': str(uuid4()), 'items': [self.item(self.first, self.a), self.item(self.second, self.b)]}


class IdentityBatchAPITests(BatchFixtures, APITestCase):
    url = '/api/recommendations/identities/batch/'

    def setUp(self):
        super().setUp()
        self.prepare()
        self.client.force_authenticate(self.user)

    def send(self, body):
        return self.client.post(self.url, body, format='json')

    def test_explicit_batch_preserves_every_private_and_public_field_and_retries(self):
        before = list(type(self.first).objects.values().order_by('pk'))
        public = list(type(self.first.anime).objects.values().order_by('pk'))
        body = self.payload()
        response = self.send(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['success_count'], 2)
        self.assertEqual(response.data['batch_id'], body['batch_id'])
        self.assertEqual(self.send(body).data['success_count'], 2)
        self.assertEqual(RecommendationIdentityEvent.objects.count(), 2)
        self.assertEqual(list(type(self.first).objects.values().order_by('pk')), before)
        self.assertEqual(list(type(self.first.anime).objects.values().order_by('pk')), public)

    def test_auth_and_owner_isolation_including_forged_own_ticket(self):
        body = self.payload()
        self.client.force_authenticate(None)
        self.assertEqual(self.send(body).status_code, 401)
        self.client.force_authenticate(self.other)
        response = self.send(body)
        self.assertEqual(response.data['success_count'], 0)
        self.assertTrue(all(row['code'] == 'unavailable' for row in response.data['results']))
        self.assertFalse(RecommendationRecordIdentity.objects.exists())

    def test_failed_item_does_not_rollback_other_items(self):
        body = self.payload()
        self.second.private_japanese_title = '已被其他页面修改'
        self.second.save(update_fields=['private_japanese_title'])
        response = self.send(body)
        self.assertEqual(response.data['success_count'], 1)
        self.assertEqual(response.data['failed_count'], 1)
        self.assertEqual(response.data['results'][1]['code'], 'identity_changed')
        self.assertTrue(RecommendationRecordIdentity.objects.filter(record=self.first).exists())
        self.assertFalse(RecommendationRecordIdentity.objects.filter(record=self.second).exists())

    def test_unknown_adult_and_changed_target_are_never_bound(self):
        for nsfw in (True, None):
            body = self.payload()
            self.a.nsfw = nsfw; self.a.save(update_fields=['nsfw'])
            response = self.send(body)
            self.assertEqual(response.data['results'][0]['code'], 'unavailable')
        self.assertFalse(RecommendationRecordIdentity.objects.filter(record=self.first).exists())

    def test_strict_bounds_duplicate_records_and_unknown_fields_fail_before_any_write(self):
        body = self.payload()
        for bad in ({**body, 'items': []}, {**body, 'items': body['items'] * 11},
                    {**body, 'items': [body['items'][0]] * 2}, {**body, 'user_id': self.other.pk},
                    {**body, 'items': [{**body['items'][0], 'confirmed': False}]},
                    {**body, 'items': [{**body['items'][0], 'user_id': self.other.pk}]},
                    {**body, 'batch_id': 'invalid'}):
            self.assertEqual(self.send(bad).status_code, 400)
        self.assertEqual(self.client.post(self.url + '?user_id=1', body, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, ' ' * (64 * 1024 + 1), content_type='application/json').status_code, 400)
        self.assertFalse(RecommendationIdentityEvent.objects.exists())

    def test_expired_ticket_only_replays_own_exact_committed_batch(self):
        body = self.payload()
        self.assertEqual(self.send(body).data['success_count'], 2)
        with patch('django.core.signing.time.time', return_value=time() + 1000):
            self.assertEqual(self.send(body).data['success_count'], 2)
            self.assertEqual(self.send({**body, 'batch_id': str(uuid4())}).data['success_count'], 0)
        self.assertEqual(RecommendationIdentityEvent.objects.count(), 2)

    def test_expiry_cannot_create_new_links_or_replay_after_later_decision(self):
        body = self.payload()
        with patch('django.core.signing.time.time', return_value=time() + 1000):
            self.assertEqual(self.send(body).data['success_count'], 0)
        self.assertFalse(RecommendationRecordIdentity.objects.exists())
        self.send(body)
        link = RecommendationRecordIdentity.objects.get(record=self.first)
        link.active = False; link.version += 1; link.save(update_fields=['active', 'version'])
        with patch('django.core.signing.time.time', return_value=time() + 1000):
            self.assertEqual(self.send(body).data['results'][0]['outcome'], 'needs_review')
        link.refresh_from_db(); self.assertFalse(link.active)

    def test_database_failure_is_local_retryable_and_replay_creates_no_extra_event(self):
        body = self.payload()
        original = bind_record
        def flaky(**kwargs):
            if kwargs['record_id'] == self.second.pk:
                raise OperationalError('synthetic unavailable')
            return original(**kwargs)
        with patch('recommendations.identity_batch.bind_record', side_effect=flaky):
            response = self.send(body)
        self.assertEqual(response.data['results'][1]['outcome'], 'retryable')
        self.assertEqual(self.send(body).data['success_count'], 2)
        self.assertEqual(RecommendationIdentityEvent.objects.count(), 2)

    def test_same_bangumi_for_two_records_never_merges_or_deletes_records(self):
        body = self.payload(); body['items'][1] = self.item(self.second, self.a)
        self.assertEqual(self.send(body).data['success_count'], 2)
        self.assertEqual(type(self.first).objects.count(), 2)
        self.assertEqual(RecommendationRecordIdentity.objects.filter(catalog_anime=self.a).count(), 2)

    def test_full_twenty_item_batch_commits_each_explicit_mapping_once(self):
        records = [self.first, self.second] + [self.record(title=f'旧作品{i}') for i in range(18)]
        body = {'batch_id': str(uuid4()), 'items': [self.item(record, self.a) for record in records]}
        self.assertEqual(self.send(body).data['success_count'], 20)
        self.assertEqual(self.send(body).data['success_count'], 20)
        self.assertEqual(RecommendationRecordIdentity.objects.count(), 20)
        self.assertEqual(RecommendationIdentityEvent.objects.count(), 20)


class IdentityBatchConcurrencyTests(BatchFixtures, TransactionTestCase):
    def setUp(self):
        super().setUp()
        if settings.SETTINGS_MODULE != 'config.recommendation_pg_test_settings' or connection.vendor != 'postgresql':
            raise RuntimeError('Batch locking tests require independent local PostgreSQL, never SQLite/Neon.')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        self.prepare()

    def parallel(self, bodies):
        barrier = Barrier(len(bodies))
        def execute(body):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '20s'")
                barrier.wait(timeout=10)
                return confirm_identity_batch(self.user, body)
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=len(bodies)) as executor:
            return list(executor.map(execute, bodies))

    def test_eight_identical_batches_create_one_event_per_record(self):
        results = self.parallel([self.payload()] * 8)
        self.assertTrue(all(row['success_count'] == 2 for row in results))
        self.assertEqual(RecommendationIdentityEvent.objects.count(), 2)
        self.assertEqual(RecommendationRecordIdentity.objects.count(), 2)

    def test_reverse_order_batches_have_no_deadlock_or_duplicate_events(self):
        first = self.payload(); second = {**first, 'items': list(reversed(first['items']))}
        results = self.parallel([first, second])
        self.assertTrue(all(row['success_count'] == 2 for row in results))
        self.assertEqual(RecommendationIdentityEvent.objects.count(), 2)

    def test_conflicting_targets_same_record_only_one_wins(self):
        first = {'batch_id': str(uuid4()), 'items': [self.item(self.first, self.a)]}
        second = {'batch_id': str(uuid4()), 'items': [self.item(self.first, self.b)]}
        results = self.parallel([first, second])
        self.assertEqual(sum(row['success_count'] for row in results), 1)
        self.assertEqual(RecommendationIdentityEvent.objects.count(), 1)

    def test_one_batch_cannot_bind_another_users_records_even_while_racing(self):
        other_record = self.record(user=self.other)
        first = self.payload()
        second = {'batch_id': str(uuid4()), 'items': [self.item(other_record, self.a)]}
        results = self.parallel([first, second])
        self.assertEqual([row['success_count'] for row in results], [2, 0])
        self.assertFalse(RecommendationRecordIdentity.objects.filter(record=other_record).exists())
