"""TXT/admin/batch regression. All catalog fixtures are synthetic local data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event as Signal
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, IntegrityError, transaction
from django.test import Client, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from anime.models import UserAnimeRecord
from recommendations import content_review as review, content_review_batch as batch
from recommendations.content_scope import release_snapshot, require_scope
from recommendations.models import (RecommendationAnime, RecommendationContentReview as Review,
    RecommendationContentReviewEvent as Event, RecommendationContentReviewState as State,
    RecommendationServingControl as Control)
from recommendations.tests.test_content_review import ReviewFixtures


def upload(text, name='ids.txt', encoding='utf-8'):
    return SimpleUploadedFile(name, text.encode(encoding), content_type='text/plain')


class TxtParserTests(SimpleTestCase):
    def test_download_format_bom_windows_newlines_and_duplicates(self):
        for encoding in ('utf-8', 'utf-8-sig', 'utf-16'):
            self.assertEqual(batch.parse_id_file(upload('92\r\n\r\n 90 \r\n92\r\n', encoding=encoding)),
                {'ids': [90, 92], 'duplicates': 1})

    def test_invalid_ids_and_urls_are_rejected_with_line_numbers(self):
        for value in ('0', '-1', '1.5', '90,92', '９０', '2147483648', 'https://bgm.tv/subject/90'):
            with self.assertRaisesRegex(ValueError, '2'):
                batch.parse_id_file(upload('90\n' + value))

    def test_file_size_encoding_extension_and_line_count_are_bounded(self):
        for file in (upload('90', name='ids.csv'), upload(''), upload('\n'.join(['90'] * 501)),
                SimpleUploadedFile('ids.txt', b'9' * (batch.MAX_FILE_BYTES + 1)),
                SimpleUploadedFile('ids.txt', b'\xff\xff')):
            with self.assertRaises(ValueError):
                batch.parse_id_file(file)
        self.assertEqual(len(batch.parse_id_file(upload('\n'.join(map(str, range(1, 501)))))['ids']), 500)


class BatchReviewTests(ReviewFixtures):
    def prepare(self, ids=None):
        return batch.prepare_batch(self.reviewer, ids or [90, 92])

    def changes(self, **overrides):
        return {'reason': 'Synthetic verified batch only', 'evidence_reference': 'synthetic-batch',
            'days': 0, 'exclusions_confirmed': True, 'general_adult_confirmed': True,
            'confirmed': True, **overrides}

    def submit(self, preview, **overrides):
        return batch.approve_batch(actor=self.reviewer, ticket=preview['ticket'], changes=self.changes(**overrides))

    def test_preview_never_writes_or_exposes_media_and_existing_valid_review_is_skipped(self):
        self.approve(work=92)
        before = list(Review.objects.values()), list(Event.objects.values()), State.objects.get().version
        with CaptureQueriesContext(connection) as queries:
            preview = self.prepare()
        self.assertEqual(preview['counts'], {'ready': 1, 'skip': 1, 'blocked': 0})
        self.assertNotIn('COVER_SENTINEL', str(preview))
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        self.submit(preview)
        self.assertEqual(list(Review.objects.filter(anime__bangumi_id=92).values()), before[0])
        self.assertEqual(Event.objects.filter(review__anime__bangumi_id=92).count(), 1)
        self.assertEqual(State.objects.get().version, before[2] + 1)

    def test_atomic_per_work_evidence_and_audit_without_opening_service_or_private_writes(self):
        before = list(UserAnimeRecord.objects.values())
        preview = self.prepare()
        result = self.submit(preview)
        self.assertEqual((result['approved'], result['skipped'], result['replayed']), (2, 0, False))
        self.assertEqual(release_snapshot()[0], {90, 92})
        self.assertEqual(set(Review.objects.values_list('evidence_reference', flat=True)),
            {'synthetic-batch.bgm.90', 'synthetic-batch.bgm.92'})
        self.assertTrue(all(Review.objects.values_list('never_expires', flat=True)))
        self.assertEqual(set(Event.objects.values_list('applied_release_version', flat=True)), {1, 2})
        self.assertEqual(State.objects.get().version, 2)
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertFalse(Control.objects.exists())

    def test_bounded_484_item_batch_does_not_make_per_work_database_round_trips(self):
        ids = list(range(100000, 100484))
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i,
            title_zh=f'Synthetic batch fixture {i}', nsfw=True, tags='音乐') for i in ids])
        preview = self.prepare(ids)
        with CaptureQueriesContext(connection) as queries:
            result = self.submit(preview)
        self.assertEqual(result['approved'], 484)
        self.assertEqual((Review.objects.count(), Event.objects.count(), State.objects.get().version), (484, 484, 484))
        self.assertLess(len(queries), 45, 'Batch approval regressed to per-work network round trips')
        self.assertEqual(batch.restore_preview(self.reviewer, self.prepare(ids)['ticket'])['counts']['skip'], 484)

    def test_confirmation_and_evidence_are_not_implied_by_upload(self):
        preview = self.prepare()
        for change in ({'confirmed': False}, {'confirmed': 1}, {'general_adult_confirmed': False},
                {'exclusions_confirmed': 'true'}, {'evidence_reference': 'https://invalid.example'},
                {'days': '0'}, {'days': -1}, {'days': 31}, {'reason': ''}):
            with self.assertRaises(ValidationError):
                self.submit(preview, **change)
        self.assertFalse(Review.objects.exists()); self.assertFalse(State.objects.exists())

    def test_missing_unknown_safe_risky_and_genreless_ids_block_the_whole_batch(self):
        for identity, values in ((93, {'nsfw': True, 'tags': '音乐,萝莉'}),
                (94, {'nsfw': True, 'tags': ''}), (184740, {'nsfw': True, 'tags': '音乐'})):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh='Synthetic boundary fixture', **values)
        for identity in (1, 91, 93, 94, 184740, 999999):
            preview = self.prepare(sorted([90, identity]))
            self.assertEqual(preview['counts']['blocked'], 1)
            self.assertFalse(preview['can_submit'])
            with self.assertRaises(signing.BadSignature):
                self.submit(preview)
        self.assertFalse(Review.objects.exists()); self.assertFalse(State.objects.exists())

    def test_changed_catalog_review_or_skip_expiry_rolls_back_all_other_items(self):
        preview = self.prepare()
        RecommendationAnime.objects.filter(bangumi_id=92).update(synopsis='Changed public fixture')
        with self.assertRaises(review.ReviewConflict):
            self.submit(preview)
        self.assertFalse(Review.objects.exists()); self.assertFalse(Event.objects.exists()); self.assertFalse(State.objects.exists())
        preview = self.prepare()
        self.approve(work=92)
        with self.assertRaises(review.ReviewConflict):
            self.submit(preview)
        self.assertFalse(Review.objects.filter(anime__bangumi_id=90).exists())
        preview = self.prepare()
        deadline = Review.objects.get(anime__bangumi_id=92).expires_at
        with patch('recommendations.content_review.timezone.now', return_value=deadline):
            with self.assertRaises(review.ReviewConflict):
                self.submit(preview)
        self.assertEqual(Event.objects.count(), 1)

    def test_duplicate_retry_never_reapproves_revoked_work_or_changes_expiry(self):
        preview = self.prepare()
        self.submit(preview, days=7)
        original = list(Review.objects.values())
        self.assertTrue(self.submit(preview, days=7)['replayed'])
        self.assertEqual(list(Review.objects.values()), original)
        self.change('revoke', version=1)
        self.assertTrue(self.submit(preview, days=7)['replayed'])
        self.assertEqual(Review.objects.get(anime__bangumi_id=90).status, 'revoked')
        self.assertEqual(Event.objects.count(), 3)
        with self.assertRaises(review.ReviewConflict):
            self.submit(preview, days=0)

    def test_permission_actor_policy_signature_and_ticket_expiry_are_checked(self):
        preview = self.prepare()
        with self.assertRaises(PermissionDenied):
            batch.prepare_batch(self.owner, [90])
        with self.assertRaises(signing.BadSignature):
            batch.approve_batch(actor=self.manager, ticket=preview['ticket'], changes=self.changes())
        with self.assertRaises(signing.BadSignature):
            batch.approve_batch(actor=self.reviewer, ticket=preview['ticket'] + 'x', changes=self.changes())
        body = signing.loads(preview['ticket'], salt=batch.SALT)
        with patch.object(signing.TimestampSigner, 'timestamp', return_value='1'):
            expired = signing.dumps(body, salt=batch.SALT, compress=True)
        with self.assertRaises(signing.BadSignature):
            batch.approve_batch(actor=self.reviewer, ticket=expired, changes=self.changes())
        with patch('recommendations.content_review.POLICY_VERSION', 'next-policy'):
            with self.assertRaises(signing.BadSignature):
                self.submit(preview)
        self.reviewer.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.submit(preview)
        self.assertFalse(Review.objects.exists())

    def test_audit_failure_and_version_overflow_cannot_leave_partial_approval(self):
        preview = self.prepare()
        with patch('recommendations.content_review_batch.Event.objects.bulk_create', side_effect=IntegrityError('synthetic audit failure')):
            with self.assertRaises(IntegrityError):
                self.submit(preview)
        self.assertFalse(Review.objects.exists()); self.assertFalse(State.objects.exists())
        State.objects.create(pk=1, version=review.MAX_VERSION - 1)
        with self.assertRaises(review.ReviewConflict):
            self.submit(preview)
        self.assertFalse(Review.objects.exists()); self.assertFalse(Event.objects.exists())

    def test_batch_changes_invalidate_existing_recommendation_context(self):
        scope = require_scope(self.owner)
        self.submit(self.prepare())
        self.assertNotEqual(scope, require_scope(self.owner))

    def test_mixed_legacy_and_new_reviews_keep_old_receipts_and_increment_versions(self):
        self.approve()
        row = Review.objects.get(anime=self.adult)
        old_receipt = list(Event.objects.values())
        Review.objects.filter(pk=row.pk).update(policy_version='release-review.ledger.closed.v1')
        preview = self.prepare()
        self.assertEqual(preview['counts']['ready'], 2)
        self.submit(preview)
        row.refresh_from_db()
        self.assertEqual(row.review_version, 2)
        self.assertTrue(row.never_expires)
        self.assertEqual(row.policy_version, review.POLICY_VERSION)
        self.assertEqual(list(Event.objects.filter(pk=old_receipt[0]['id']).values()), old_receipt)
        self.assertEqual(Event.objects.get(review=row, applied_version=2).previous_status, 'approved')
        self.assertEqual(State.objects.get().version, 3)

    def test_concurrent_duplicate_batches_have_one_receipt_per_work(self):
        preview = self.prepare()
        barrier = Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.submit(preview)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sum(r['replayed'] for r in results), 1)
        self.assertEqual((Review.objects.count(), Event.objects.count(), State.objects.get().version), (2, 2, 2))

    def test_overlapping_batches_do_not_deadlock_or_partially_approve(self):
        RecommendationAnime.objects.create(bangumi_id=93, title_zh='Synthetic overlapping fixture', nsfw=True, tags='音乐')
        previews = [self.prepare([90, 92]), self.prepare([92, 93])]
        barrier = Barrier(2)
        def run(preview):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    self.submit(preview)
                    return 'ok'
                except review.ReviewConflict:
                    return 'conflict'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, previews))
        self.assertEqual(sorted(results), ['conflict', 'ok'])
        self.assertEqual((Review.objects.count(), Event.objects.count(), State.objects.get().version), (2, 2, 2))

    def test_permission_revoked_while_waiting_for_catalog_lock_blocks_whole_batch(self):
        preview, started = self.prepare(), Signal()
        application = 'batch-review-permission-' + uuid4().hex
        def submit_waiting():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('application_name', %s, false)", [application])
                started.set()
                try:
                    self.submit(preview)
                    return 'unexpected-success'
                except PermissionDenied:
                    return 'denied'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                RecommendationAnime.objects.select_for_update().get(bangumi_id=90)
                future = pool.submit(submit_waiting)
                self.assertTrue(started.wait(10))
                waiting, deadline = False, monotonic() + 5
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock')", [application])
                        waiting = cursor.fetchone()[0]
                    if waiting:
                        break
                    sleep(.02)
                self.assertTrue(waiting, 'batch writer never reached the catalog lock wait')
                self.reviewer.user_permissions.clear()
            self.assertEqual(future.result(timeout=15), 'denied')
        self.assertFalse(Review.objects.exists()); self.assertFalse(Event.objects.exists()); self.assertFalse(State.objects.exists())


class BatchAdminTests(ReviewFixtures):
    def setUp(self):
        super().setUp()
        self.browser = Client()
        self.url = reverse('admin:recommendations_content_review_batch')
        self.browser.force_login(self.reviewer)

    def preview(self, text='90\n92\n92'):
        return self.browser.post(self.url, {'stage': 'preview', 'id_file': upload(text)})

    def confirmation(self, response):
        return {'stage': 'confirm', 'ticket': response.context['confirm_form']['ticket'].value(),
            'reason': 'Synthetic admin batch', 'evidence_reference': 'synthetic-admin', 'days': 0,
            'exclusions_confirmed': 'on', 'general_adult_confirmed': 'on', 'confirmed': 'on'}

    def test_navigation_upload_preview_and_confirm_flow(self):
        listing = self.browser.get(reverse('admin:recommendations_recommendationcontentreview_changelist'))
        self.assertContains(listing, self.url)
        response = self.preview()
        self.assertContains(response, '已合并 1 个重复 ID')
        self.assertContains(response, '可审核 2 部')
        self.assertContains(response, '未审核')
        self.assertNotContains(response, '<img')
        self.assertFalse(Review.objects.exists())
        self.assertEqual(response.context['confirm_form']['days'].value(), 0)
        payload = self.confirmation(response)
        self.assertContains(self.browser.post(self.url, payload, follow=True), '登记 2 部')
        self.assertEqual(self.browser.post(self.url, payload).status_code, 302)
        self.assertEqual((Review.objects.count(), Event.objects.count()), (2, 2))

    def test_invalid_confirmation_retains_ticket_and_fields_for_correction(self):
        response = self.preview()
        payload = self.confirmation(response)
        failure = self.browser.post(self.url, {**payload, 'confirmed': '', 'general_adult_confirmed': ''})
        self.assertContains(failure, '批量审核通过必须明确确认')
        self.assertEqual(failure.context['confirm_form']['ticket'].value(), payload['ticket'])
        self.assertEqual(failure.context['confirm_form']['reason'].value(), payload['reason'])
        self.assertIsNotNone(failure.context['preview'])
        self.assertFalse(Review.objects.exists())

    def test_blocked_and_invalid_uploads_offer_no_confirmation_and_get_never_writes(self):
        self.assertEqual(self.browser.get(self.url).status_code, 200)
        response = self.preview('90\n91')
        self.assertContains(response, '本批不能提交')
        self.assertIsNone(response.context['confirm_form'])
        self.assertContains(self.preview('90\nhttps://invalid.example'), '不是有效的正整数')
        self.assertFalse(Review.objects.exists()); self.assertFalse(State.objects.exists())

    def test_permissions_csrf_forged_expired_ticket_and_escaped_titles(self):
        RecommendationAnime.objects.filter(bangumi_id=90).update(title_zh='<script>synthetic-xss</script>')
        response = self.preview()
        self.assertContains(response, '&lt;script&gt;synthetic-xss&lt;/script&gt;')
        self.assertNotContains(response, '<script>synthetic-xss</script>')
        payload = self.confirmation(response)
        self.assertContains(self.browser.post(self.url, {**payload, 'ticket': 'forged'}), '凭据无效')
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.reviewer)
        self.assertEqual(strict.post(self.url, payload).status_code, 403)
        self.other.is_staff = True; self.other.save(update_fields=['is_staff'])
        self.browser.force_login(self.other)
        self.assertEqual(self.browser.get(self.url).status_code, 403)
        self.assertEqual(self.browser.post(self.url, payload).status_code, 403)
        self.assertFalse(Review.objects.exists())

    def test_changed_preview_requires_reupload_without_partial_write(self):
        payload = self.confirmation(self.preview())
        RecommendationAnime.objects.filter(bangumi_id=92).update(tags='音乐,成长')
        response = self.browser.post(self.url, payload)
        self.assertContains(response, '重新上传并预检')
        self.assertIsNone(response.context['confirm_form'])
        self.assertFalse(Review.objects.exists()); self.assertFalse(Event.objects.exists())
