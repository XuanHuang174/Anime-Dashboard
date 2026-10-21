"""Closed N1 contracts and public-only audit, never real account data."""
from dataclasses import FrozenInstanceError
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import NotAuthenticated, NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIClient

from anime.models import Anime, UserAnimeRecord
from recommendations.content_scope_contract import (
    ContentScopeDecisionSerializer, contract_descriptor, require_n1_scope, resolve_n1_scope)
from recommendations.content_scope_audit import audit_public_content_scope
from recommendations.identity import public_cover_url
from recommendations.models import RecommendationAnime


class ContentScopeContractTests(SimpleTestCase):
    def user(self, pk=1, **changes):
        return SimpleNamespace(pk=pk, is_authenticated=True, is_staff=False, is_superuser=False, **changes)

    def test_frozen_descriptor_and_no_mutable_shared_policy(self):
        expected = json.loads((Path(settings.BASE_DIR) / 'evaluation/content-scope.n1.v1.json').read_text(encoding='utf-8'))
        self.assertEqual(contract_descriptor(), expected)
        modified = contract_descriptor(); modified['selectable_scopes'].append('include_adult')
        self.assertEqual(contract_descriptor()['selectable_scopes'], ['safe'])

    @override_settings(RECOMMENDATION_ADULT_CONTENT_ENABLED=True)
    def test_no_setting_role_or_client_declaration_can_grant_access(self):
        for privileged in (False, True):
            user = self.user()
            user.is_staff = user.is_superuser = privileged
            user.age = 99; user.adult_confirmed = True
            decision = resolve_n1_scope(user, 'include_adult')
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.effective_scope, 'safe')
            self.assertEqual(decision.reason_code, 'adult_eligibility_unavailable')
            self.assertEqual(decision.eligibility_version, 0)
            with self.assertRaises(PermissionDenied) as denied:
                require_n1_scope(user, 'include_adult')
            self.assertEqual(denied.exception.status_code, 403)

    def test_binding_is_stable_user_and_requested_scope_isolated_without_private_fields(self):
        first = resolve_n1_scope(self.user())
        self.assertRegex(first.subject_binding, r'^[a-f0-9]{64}$')
        self.assertRegex(first.scope_revision, r'^[a-f0-9]{64}$')
        self.assertEqual(first, resolve_n1_scope(self.user()))
        other = resolve_n1_scope(self.user(2))
        self.assertNotEqual(first.subject_binding, other.subject_binding)
        self.assertNotEqual(first.scope_revision, other.scope_revision)
        self.assertNotEqual(first.scope_revision, resolve_n1_scope(self.user(), 'include_adult').scope_revision)
        self.assertTrue(first.allowed)
        self.assertEqual(require_n1_scope(self.user()), first)
        self.assertEqual(dict(ContentScopeDecisionSerializer(first).data), first.as_contract())
        self.assertNotIn('user_id', first.as_contract())
        with self.assertRaises(FrozenInstanceError):
            first.allowed = False

    def test_anonymous_unsaved_and_unknown_scope_fail_closed(self):
        for user in (None, SimpleNamespace(pk=1, is_authenticated=False), self.user(None)):
            with self.assertRaises(NotAuthenticated):
                resolve_n1_scope(user)
        for scope in ('nsfw', 'all', True, None, {'age':18}):
            with self.assertRaises(ValidationError):
                resolve_n1_scope(self.user(), scope)


@override_settings(AWS_S3_CUSTOM_DOMAIN='media.example.test')
class ContentScopeAuditTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='scope-owner')
        self.other = get_user_model().objects.create_user(username='scope-other', is_staff=True, is_superuser=True)
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        for identity, rating, tags, cover in (
                (1, False, '音乐', 'https://media.example.test/covers/1.jpg'),
                (2, True, '音乐', ''),
                (3, True, '二刷,日本,2026', 'https://external.example.test/image.jpg'),
                (4, None, '音乐', 'https://media.example.test/covers/4.jpg')):
            RecommendationAnime.objects.create(bangumi_id=identity, nsfw=rating, tags=tags, score=5.9,
                title_zh='PUBLIC_TITLE_SENTINEL' if identity != 3 else 'x' * 201,
                cover_url=cover, source_cover_url='https://source.example.test/RAW_URL_SENTINEL.jpg')
        shared = Anime.objects.create(bgm_id=2, chinese_title='PRIVATE_WORK_SENTINEL')
        for user in (self.owner, self.other):
            UserAnimeRecord.objects.create(user=user, anime=shared, personal_score=0,
                watch_status='completed', custom_tags=['PRIVATE_TAG_SENTINEL'], review='PRIVATE_REVIEW_SENTINEL')

    def test_aggregate_is_database_readonly_public_only_no_urls_titles_or_private_rows(self):
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with patch('requests.get', side_effect=AssertionError('No HTTP')), CaptureQueriesContext(connection) as queries:
            report = audit_public_content_scope()
        sql = '\n'.join(row['sql'].lower() for row in queries)
        self.assertIn('read only', sql)
        self.assertNotIn('anime_useranimerecord', sql)
        self.assertNotIn('auth_user', sql)
        self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for row in queries))
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        data = json.dumps(report)
        for secret in ('PRIVATE_', 'PUBLIC_TITLE_SENTINEL', 'RAW_URL_SENTINEL', 'https://'):
            self.assertNotIn(secret, data)
        self.assertEqual(report['catalog_counts']['non_adult']['with_identity_and_genres'], 1)
        adult = report['catalog_counts']['adult_flagged']
        self.assertEqual((adult['total'], adult['with_identity_and_genres'], adult['cover_empty']), (2, 1, 1))
        self.assertEqual(adult['title_over_journal_limit'], 1)
        self.assertEqual(report['catalog_counts']['unknown']['total'], 1)
        self.assertFalse(report['policy']['adult_access_enabled'])

    def test_audit_command_outputs_only_aggregate_contract(self):
        output = StringIO(); call_command('audit_recommendation_content_scope', stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report['audit_version'], 'content-scope-audit.n1.v1')
        self.assertTrue(report['database_read_only'])

    def test_unwired_scope_and_adult_unknown_direct_routes_remain_blocked(self):
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        for user in (self.owner, self.other):
            self.client.force_authenticate(user)
            for params in ({'content_scope':'include_adult'}, {'nsfw':'true'}, {'include_nsfw':'true'},
                           {'adult_confirmed':True}, {'age':99}, {'user_id':self.other.pk}):
                self.assertEqual(self.client.get('/api/recommendations/', params).status_code, 400)
            for identity in (2, 4):
                self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
                self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)
                with self.assertRaises(NotFound):
                    public_cover_url(RecommendationAnime.objects.get(bangumi_id=identity))
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_no_database_or_transaction_mode_fallback(self):
        with patch.object(connection, 'vendor', 'sqlite'), self.assertRaises(ValueError):
            audit_public_content_scope()
        with transaction.atomic(), self.assertRaises(ValueError):
            audit_public_content_scope()
