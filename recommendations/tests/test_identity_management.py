from uuid import uuid4
from unittest.mock import patch
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connection, close_old_connections
from django.core import signing
from django.test import TestCase, override_settings
from django.test import TransactionTestCase
from rest_framework.test import APITransactionTestCase
from rest_framework.exceptions import ValidationError
from anime.models import Anime, UserAnimeRecord
from anime.journal_sources import confirmed_source_id
from recommendations.binding import auto_link, IdentityConflict
from recommendations.content_scope import require_scope
from recommendations.identity import recommendation_identity
from recommendations.identity_management import (management_contract, correct_identity, export_correction, restore_correction, SALT)
from recommendations.models import RecommendationIdentityCorrection, RecommendationIdentityOverride
from recommendations.resolution import resolved_records
from .test_legacy_identity import LegacyFixtures


class ManagementFixtures(LegacyFixtures):
    def setUp(self):
        super().setUp()
        self.a = self.work(100)
        self.b = self.work(200, title_zh='正确作品', season='2026-07')
        self.entry = self.record(bgm_id=100)
        self.scope = require_scope(self.user)

    def detail(self, keyword='200'):
        return management_contract(self.user, {'record_id': self.entry.pk, 'page': 1, 'keyword': keyword}, self.scope)['results'][0]

    def payload(self, action='replace', item=None):
        item = item or self.detail()
        return {'record_id': self.entry.pk, 'version': item['version'], 'operation_id': uuid4(), 'action': action,
            'ticket': item['candidates'][0]['ticket'] if action == 'replace' else item['ticket'],
            **({'bangumi_id': 200} if action == 'replace' else {})}

    def apply(self, action='replace'):
        return correct_identity(self.user, self.payload(action), self.scope)


class ManagementTests(ManagementFixtures, TestCase):
    def test_replace_preserves_journal_public_provenance_and_aligns_all_reads(self):
        before = list(UserAnimeRecord.objects.values())
        public = list(Anime.objects.values())
        self.apply()
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertEqual(list(Anime.objects.values()), public)
        self.assertEqual(resolved_records(self.user)[0]['resolved_bangumi_id'], 200)
        self.entry.refresh_from_db()
        self.assertEqual(confirmed_source_id(self.entry, private=True), 200)
        self.assertEqual(confirmed_source_id(self.entry), 100)
        self.assertEqual(recommendation_identity(user=self.user, bangumi_id=200)['record_id'], self.entry.pk)
        self.assertIsNone(recommendation_identity(user=self.user, bangumi_id=100)['record_id'])

    def test_unlink_masks_direct_and_automatic_fallback_and_undo_restores(self):
        self.apply('unlink')
        self.assertIsNone(resolved_records(self.user)[0]['resolved_bangumi_id'])
        self.entry.refresh_from_db()
        self.assertEqual(auto_link(self.entry)['status'], 'user_override')
        self.assertIsNone(confirmed_source_id(self.entry, private=True))
        self.apply('undo')
        self.assertEqual(resolved_records(self.user)[0]['resolved_bangumi_id'], 100)
        self.assertFalse(self.detail()['can_undo'])
        with self.assertRaises(IdentityConflict): self.apply('undo')

    def test_undo_after_two_changes_restores_previous_override_only(self):
        self.apply(); self.apply('unlink'); self.apply('undo')
        self.assertEqual(resolved_records(self.user)[0]['resolved_bangumi_id'], 200)
        self.assertEqual(RecommendationIdentityOverride.objects.get().version, 3)

    def test_retry_receipt_does_not_reapply_after_newer_changes(self):
        body = self.payload(); correct_identity(self.user, body, self.scope)
        self.apply('unlink')
        self.assertEqual(correct_identity(self.user, body, self.scope)['status'], 'already_applied')
        self.assertIsNone(resolved_records(self.user)[0]['resolved_bangumi_id'])
        self.assertEqual(RecommendationIdentityCorrection.objects.count(), 2)
        with self.assertRaises(IdentityConflict): correct_identity(self.user, {**body, 'action': 'unlink'}, self.scope)

    def test_other_account_and_stale_metadata_version_catalog_rejected(self):
        body = self.payload()
        from rest_framework.exceptions import NotFound
        with self.assertRaises(NotFound): correct_identity(self.other, body, require_scope(self.other))
        self.b.title_zh = '资料更改'; self.b.save(update_fields=['title_zh'])
        with self.assertRaises(IdentityConflict): correct_identity(self.user, body, self.scope)
        body = self.payload()
        self.entry.private_japanese_title = '新的私人标题'; self.entry.save(update_fields=['private_japanese_title'])
        with self.assertRaises(IdentityConflict): correct_identity(self.user, body, self.scope)
        self.assertFalse(RecommendationIdentityOverride.objects.exists())

    def test_expired_and_tampered_proofs_never_write_but_committed_retry_acks(self):
        body = self.payload()
        proof = signing.loads(body['ticket'], salt=SALT)
        with patch('django.core.signing.time.time', return_value=1):
            expired = signing.dumps(proof, salt=SALT)
        with self.assertRaises(IdentityConflict): correct_identity(self.user, {**body, 'ticket': expired}, self.scope)
        with self.assertRaises(IdentityConflict): correct_identity(self.user, {**body, 'ticket': body['ticket'] + 'x'}, self.scope)
        correct_identity(self.user, body, self.scope)
        with patch('recommendations.identity_management.signing.loads', side_effect=signing.SignatureExpired()):
            self.assertEqual(correct_identity(self.user, body, self.scope)['status'], 'already_applied')

    def test_adult_unknown_candidates_hidden_and_current_redacted(self):
        for rating in (True, None):
            self.b.nsfw = rating; self.b.save(update_fields=['nsfw'])
            self.assertEqual(self.detail()['candidates'], [])
        self.b.nsfw = False; self.b.save(update_fields=['nsfw']); self.apply()
        self.b.nsfw = True; self.b.save(update_fields=['nsfw'])
        item = self.detail()
        self.assertIsNone(item['current'])
        self.assertEqual(item['candidates'], [])

    def test_alias_corrections_and_metadata_drift_do_not_fall_back(self):
        self.entry.anime.bgm_id = None; self.entry.anime.save(update_fields=['bgm_id'])
        self.entry.refresh_from_db(); auto_link(self.entry)
        self.apply()
        self.entry.private_japanese_title = '改动'; self.entry.save(update_fields=['private_japanese_title'])
        self.assertEqual(resolved_records(self.user)[0]['identity_state'], 'stale_link')
        with self.assertRaises(IdentityConflict): self.apply('undo')

    def test_atomic_receipt_failure_rolls_back_correction(self):
        with patch('recommendations.identity_management.RecommendationIdentityCorrection.objects.create', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError): self.apply()
        self.assertFalse(RecommendationIdentityOverride.objects.exists())

    def test_undo_never_restores_restricted_original_in_safe_scope(self):
        self.apply()
        self.a.nsfw = True; self.a.save(update_fields=['nsfw'])
        with self.assertRaises(IdentityConflict): self.apply('undo')
        self.assertEqual(resolved_records(self.user)[0]['resolved_bangumi_id'], 200)

    def test_owner_signed_backup_restores_unlinked_and_replacement(self):
        for action in ('replace', 'unlink'):
            self.apply(action); self.entry.refresh_from_db()
            reference = export_correction(self.user, self.entry)
            imported = self.record(title=f'备份副本-{action}')
            imported.private_chinese_title = self.entry.effective_anime_metadata()['chinese_title']
            imported.save(update_fields=['private_chinese_title'])
            restore_correction(self.user, imported, reference)
            imported.refresh_from_db()
            self.assertEqual(confirmed_source_id(imported, private=True), 200 if action == 'replace' else None)
            with self.assertRaises(ValidationError): restore_correction(self.other, imported, reference)

    def test_backup_does_not_revalidate_a_stale_correction(self):
        self.apply()
        self.entry.private_japanese_title = '更改了原名'; self.entry.save(update_fields=['private_japanese_title'])
        self.entry.refresh_from_db()
        reference = export_correction(self.user, self.entry)
        restore_correction(self.user, self.entry, reference)
        self.assertEqual(resolved_records(self.user)[0]['identity_state'], 'stale_link')


class ManagementAPITests(ManagementFixtures, APITransactionTestCase):
    url = '/api/recommendations/identities/manage/'

    def test_query_formats_and_linked_records_remain_accessible(self):
        self.client.force_authenticate(self.user)
        for keyword in ('正确', '200', 'https://bgm.tv/subject/200', 'https://bangumi.tv/subject/200/'):
            response = self.client.get(self.url, {'record_id': self.entry.pk, 'keyword': keyword})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['results'][0]['candidates'][0]['bangumi_id'], 200)
        response = self.client.get(self.url)
        self.assertEqual(response.data['count'], 1)

    def test_strict_owner_contract_and_conflict_codes(self):
        body = self.payload(); body['operation_id'] = str(body['operation_id'])
        self.assertEqual(self.client.post(self.url, body, format='json').status_code, 401)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(self.url, {**body, 'extra': True}, format='json').status_code, 400)
        response = self.client.post(self.url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        stale = {**body, 'operation_id': str(uuid4())}
        response = self.client.post(self.url, stale, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'identity_changed')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url, {'record_id': self.entry.pk}).status_code, 404)

    def test_complete_backup_and_legacy_backup_preserve_private_correction(self):
        import json
        from django.urls import reverse
        self.client.force_authenticate(self.user)
        self.apply()
        exported = self.client.get(reverse('anime_api:journal-export'))
        self.assertEqual(exported.status_code, 200, exported.data if hasattr(exported, 'data') else exported.content)
        backup = json.loads(exported.content)
        self.assertEqual(backup['records'][0]['anime']['identity_correction']['bangumi_id'], 200)
        restored = self.client.post(reverse('anime_api:journal-import'), backup, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(resolved_records(self.user)[0]['resolved_bangumi_id'], 200)
        backup['records'][0]['anime'].pop('identity_correction')
        restored = self.client.post(reverse('anime_api:journal-import'), backup, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(resolved_records(self.user)[0]['resolved_bangumi_id'], 200)

    def test_tampered_backup_rolls_back_replacement(self):
        import json
        from django.urls import reverse
        self.client.force_authenticate(self.user); self.apply()
        backup = json.loads(self.client.get(reverse('anime_api:journal-export')).content)
        backup['records'][0]['anime']['identity_correction']['bangumi_id'] = 100
        before = list(UserAnimeRecord.objects.values())
        response = self.client.post(reverse('anime_api:journal-import'), backup, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)

    def test_public_endpoints_ignore_private_override_even_when_owner_is_authenticated(self):
        self.client.force_authenticate(self.user)
        profile = self.user.profile; profile.is_public = True; profile.public_status = 'approved'; profile.save()
        self.apply()
        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            for path in ('/api/showcase/', f'/api/public-journal/users/{self.user.username}/records/'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.data)
                anime = response.data['results'][0]['anime']
                self.assertEqual(anime['confirmed_bangumi_id'], 100)
                self.assertEqual(anime['bangumi_url'], 'https://bgm.tv/subject/100')

    @skipUnless(connection.vendor == 'postgresql', 'Credits reads require PostgreSQL snapshots')
    @override_settings(RECOMMENDATION_SERIES_ENABLED=True, RECOMMENDATION_SERIES_CANARY_USERS=[],
                       RECOMMENDATION_CREDITS_STUDIO_ENABLED=True, RECOMMENDATION_CREDITS_CANARY_USERS=[])
    def test_series_and_credits_record_endpoints_use_correction_then_unlink(self):
        self.client.force_authenticate(self.user)
        for action, expected in [('replace', 200), ('unlink', None), ('undo', 200)]:
            self.apply(action)
            for path in ('series', 'credits'):
                response = self.client.get(f'/api/recommendations/{path}/record/{self.entry.pk}/')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['bangumi_id'], expected)
                self.assertEqual(response.data['identity_state'], 'linked' if expected else 'unlinked')


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated local PostgreSQL row locks')
class ManagementConcurrencyTests(ManagementFixtures, TransactionTestCase):
    def race(self, bodies):
        barrier = Barrier(len(bodies))
        def worker(body):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return correct_identity(self.user, body, self.scope)['status']
                except IdentityConflict:
                    return 'conflict'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=len(bodies)) as executor:
            return list(executor.map(worker, bodies))

    def test_same_operation_race_has_one_receipt_and_one_version(self):
        body = self.payload()
        results = self.race([body] * 6)
        self.assertEqual(results.count('applied'), 1)
        self.assertEqual(results.count('already_applied'), 5)
        self.assertEqual(RecommendationIdentityCorrection.objects.count(), 1)
        self.assertEqual(RecommendationIdentityOverride.objects.get().version, 1)

    def test_competing_corrections_never_overwrite_newer_version(self):
        results = self.race([self.payload(), self.payload('unlink')])
        self.assertEqual(results.count('applied'), 1)
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(RecommendationIdentityCorrection.objects.count(), 1)
