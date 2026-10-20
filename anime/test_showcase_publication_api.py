"""Explicit approval closes legacy-hidden publication without relaxing auto rules."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord, UserAnimeTag
from anime.showcase_services import confirm_showcase_publication, publication_candidate
from anime.test_showcase_visibility import require_local_postgres
from recommendations.models import RecommendationAnime, RecommendationRecordIdentity


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL')
@override_settings(PUBLIC_SHOWCASE_USERNAME='XHcode')
class ShowcasePublicationAPITests(APITestCase):
    def setUp(self):
        require_local_postgres()
        self.owner = get_user_model().objects.create_user(username='XHcode')
        self.other = get_user_model().objects.create_user(username='private-owner')
        self.catalog = RecommendationAnime.objects.create(bangumi_id=331445, title_zh='少女手工',
            title_jp='Do It Yourself!!', season='2022-10', nsfw=False)
        self.shared = Anime.objects.create(bgm_id=331445, chinese_title='少女手工',
            japanese_title='Do It Yourself!!', airing_period='2022-10', is_active=False)
        self.record = UserAnimeRecord.objects.create(user=self.owner, anime=self.shared, watch_status='on_hold',
            personal_score=Decimal('0.0'), review='原长评\n' * 100, custom_tags=['私人管理标签'])
        UserAnimeRecord.objects.create(user=self.other, anime=self.shared, personal_score=Decimal('9.0'), review='其他用户长评')
        UserAnimeTag.objects.create(record=self.record, name='私人管理标签', color='blue')
        self.list_url = reverse('anime_api:user-anime-record-showcase-publication')
        self.post_url = reverse('anime_api:user-anime-record-publish-showcase', args=[self.record.pk])
        self.client.force_authenticate(self.owner)

    def ticket(self):
        return self.client.get(self.list_url).data['results'][0]['ticket']

    def confirm(self, ticket=None, **extra):
        return self.client.post(self.post_url, {'ticket': ticket or self.ticket(), 'confirm_publication': True, **extra}, format='json')

    def test_legacy_creation_notice_then_explicit_approval_and_public_search(self):
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        colors = list(UserAnimeTag.objects.order_by('pk').values())
        self.assertEqual(self.client.get(reverse('anime_api:showcase-list')).data['count'], 0)
        item = self.client.get(self.list_url).data['results'][0]
        self.assertEqual(item['anime_id'], self.shared.pk)
        self.assertEqual(item['bangumi_id'], 331445)
        self.assertTrue(item['requires_confirmation'])
        result = self.confirm(item['ticket'])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['status'], 'published')
        self.assertEqual(self.client.get(self.list_url).data['count'], 0)
        self.assertEqual(self.client.get(reverse('anime_api:showcase-list')).data['count'], 1)
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
        self.assertEqual(list(UserAnimeTag.objects.order_by('pk').values()), colors)

    def test_regular_user_cannot_list_or_publish_owner_even_with_valid_ticket(self):
        ticket = self.ticket()
        self.client.force_authenticate(self.other)
        result = self.client.get(self.list_url)
        self.assertEqual(result.data['can_publish'], False)
        self.assertEqual(result.data['results'], [])
        self.assertEqual(self.confirm(ticket).status_code, 403)
        self.shared.refresh_from_db(); self.assertFalse(self.shared.is_active)

    def test_login_required_and_strict_input(self):
        for params in ({'user_id': self.owner.pk}, {'page': 0}, {'page': '1.5'}):
            self.assertEqual(self.client.get(self.list_url, params).status_code, 400)
        self.assertEqual(self.client.get(self.list_url + '?page=1&page=2').status_code, 400)
        self.assertEqual(self.confirm(user_id=self.other.pk).status_code, 400)
        for payload in ({}, {'ticket': self.ticket(), 'confirm_publication': 1},
                        {'ticket': self.ticket(), 'confirm_publication': False}):
            self.assertEqual(self.client.post(self.post_url, payload, format='json').status_code, 400)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.list_url).status_code, 401)

    def test_read_contract_has_no_personal_fields_no_db_writes_no_shared_cache(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.list_url)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Authorization', response['Vary'])
        self.assertNotIn('review', str(response.data))
        self.assertNotIn('personal_score', str(response.data))
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for q in queries))
        self.assertFalse(any('"review"' in q['sql'] or '"personal_score"' in q['sql'] for q in queries))

    def test_ticket_tampering_record_swap_and_expiry_are_rejected(self):
        ticket = self.ticket()
        self.assertEqual(self.confirm(ticket + 'x').status_code, 409)
        other_record = UserAnimeRecord.objects.get(user=self.other)
        self.post_url = reverse('anime_api:user-anime-record-publish-showcase', args=[other_record.pk])
        self.assertEqual(self.confirm(ticket).status_code, 409)
        self.post_url = reverse('anime_api:user-anime-record-publish-showcase', args=[self.record.pk])
        import time
        with patch('django.core.signing.time.time', return_value=time.time() + 601):
            self.assertEqual(self.confirm(ticket).status_code, 409)

    def test_changed_metadata_or_private_record_invalidates_ticket(self):
        ticket = self.ticket()
        self.shared.description = '公共资料改动'
        self.shared.save()
        self.assertEqual(self.confirm(ticket).status_code, 409)
        ticket = self.ticket()
        self.record.review = '用户已自行更新长评'
        self.record.save()
        self.assertEqual(self.confirm(ticket).status_code, 409)

    def test_moderator_retirement_and_unrecognized_reason_remain_blocked(self):
        ticket = self.ticket()
        for reason in ('admin_retired', 'unrecognized_reason', ''):
            self.shared.hidden_reason = reason
            self.shared.save(update_fields=['hidden_reason'])
            self.assertEqual(self.confirm(ticket).status_code, 403)
            item = self.client.get(self.list_url).data['results'][0]
            self.assertIsNone(item['ticket'])
            self.assertFalse(item['requires_confirmation'])

    def test_adult_unknown_and_identity_conflicts_fail_closed(self):
        ticket = self.ticket()
        for rating in (True, None):
            self.catalog.nsfw = rating; self.catalog.save()
            self.assertEqual(self.confirm(ticket).status_code, 403)
        self.catalog.nsfw = False; self.catalog.save()
        other = RecommendationAnime.objects.create(bangumi_id=999, title_zh='不同作品', nsfw=False)
        RecommendationRecordIdentity.objects.create(record=self.record, catalog_anime=other, active=True)
        self.assertEqual(self.confirm(ticket).status_code, 409)

    def test_repeat_confirmation_is_write_free_and_cannot_create_records(self):
        ticket = self.ticket()
        self.assertEqual(self.confirm(ticket).status_code, 200)
        before = list(UserAnimeRecord.objects.values())
        with CaptureQueriesContext(connection) as queries:
            response = self.confirm(ticket)
        self.assertEqual(response.data['status'], 'already_public')
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for q in queries))
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)

    def test_missing_catalog_for_bangumi_identity_requires_manual_review(self):
        ticket = self.ticket()
        self.catalog.bangumi_id = 331446
        self.catalog.save(update_fields=['bangumi_id'])
        self.assertEqual(self.confirm(ticket).status_code, 403)
        self.shared.refresh_from_db(); self.assertFalse(self.shared.is_active)

    def test_explicit_manual_work_without_bangumi_does_not_require_catalog_identity_guess(self):
        self.shared.bgm_id = None
        self.shared.save(update_fields=['bgm_id'])
        self.assertEqual(self.confirm().status_code, 200)

    def test_only_selected_work_changes_not_other_hidden_entries(self):
        other_shared = Anime.objects.create(chinese_title='另一部历史隐藏作品', airing_period='2026-1', is_active=False)
        UserAnimeRecord.objects.create(user=self.owner, anime=other_shared)
        ticket = next(item['ticket'] for item in self.client.get(self.list_url).data['results'] if item['record_id'] == self.record.pk)
        self.assertEqual(self.confirm(ticket).status_code, 200)
        other_shared.refresh_from_db(); self.assertFalse(other_shared.is_active)

    def test_pending_pagination_stable_and_bounded(self):
        for index in range(13):
            shared = Anime.objects.create(chinese_title=f'分页{index}', airing_period='2026-1', is_active=False)
            UserAnimeRecord.objects.create(user=self.owner, anime=shared)
        first = self.client.get(self.list_url).data
        second = self.client.get(self.list_url, {'page': 2}).data
        self.assertEqual((first['count'], len(first['results']), first['next_page']), (14, 12, 2))
        self.assertFalse({r['record_id'] for r in first['results']} & {r['record_id'] for r in second['results']})


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL')
@override_settings(PUBLIC_SHOWCASE_USERNAME='XHcode')
class ShowcasePublicationConcurrencyTests(TransactionTestCase):
    def test_same_ticket_parallel_confirmation_has_one_update(self):
        require_local_postgres()
        owner = get_user_model().objects.create_user(username='XHcode')
        shared = Anime.objects.create(bgm_id=331445, chinese_title='少女手工', airing_period='2022-10', is_active=False)
        RecommendationAnime.objects.create(bangumi_id=331445, title_zh='少女手工', nsfw=False)
        record = UserAnimeRecord.objects.create(user=owner, anime=shared, personal_score=0, review='原长评')
        before = UserAnimeRecord.objects.filter(pk=record.pk).values().get()
        ticket = publication_candidate(record)['ticket']
        barrier = Barrier(4)
        def worker(_):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout='10s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                user = get_user_model().objects.get(pk=owner.pk)
                barrier.wait(timeout=10)
                return pid, confirm_showcase_publication(user=user, record_id=record.pk, ticket=ticket)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(4)))
        self.assertEqual(len({pid for pid, _ in results}), 4)
        self.assertEqual(sum(row['status']=='published' for _, row in results), 1)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=record.pk).values().get(), before)
