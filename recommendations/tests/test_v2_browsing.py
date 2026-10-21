"""D2 synthetic local PostgreSQL only; no production V2 route or real writes."""
from copy import deepcopy
from uuid import uuid4
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import NotAuthenticated, NotFound, ValidationError

from anime.models import UserAnimeRecord
from recommendations import browsing, v2_browsing, engine
from recommendations.execution import execute_v2, recipe_descriptor
from recommendations.models import RecommendationAnime
from recommendations.quality_readonly import readonly_snapshot
from recommendations.feedback import mutate_feedback
from recommendations.services import patch_preferences
from recommendations.serializers import (RecommendationQuerySerializer, RecommendationBrowsePageSerializer,
    RecommendationDetailQuerySerializer)
from recommendations.tests import test_browsing as fixtures


class PreparedBrowseTests(TransactionTestCase):
    setUp = fixtures.BrowseAPITests.setUp
    catalog = fixtures.BrowseAPITests.catalog

    def page(self, user=None, **params):
        query = RecommendationQuerySerializer(data={'browse': True, **params})
        query.is_valid(raise_exception=True)
        return dict(RecommendationBrowsePageSerializer(v2_browsing.browse_page(
            user or self.owner, query.validated_data, query.initial_data)).data)

    def detail(self, row, user=None, **changes):
        return v2_browsing.detail(user or self.owner, row['bangumi_id'], {
            'detail_context': row['detail_context'], 'evidence_token': row['evidence_token'], **changes})

    def test_full_recipe_v2_order_and_fixed_batch_continuation_never_reranks(self):
        first = self.page()
        self.assertEqual(first['execution_recipe'], recipe_descriptor())
        self.assertEqual(first['browse_contract_version'], 'recommendations.browse.v2')
        self.assertEqual(first['evidence_context']['recipe_revision'], recipe_descriptor()['recipe_revision'])
        with readonly_snapshot():
            context, _, catalog = browsing.live_context(self.owner, first['effective_conditions'], {}, recipe=recipe_descriptor())
            _, index = browsing.public_browse_index(catalog, {}, recipe=recipe_descriptor())
            ranked, _, _ = execute_v2(self.owner, first['effective_conditions'], index, context=context)
        expected = [item['anime'].bangumi_id for item in ranked]
        page, identities = first, []
        with patch('recommendations.execution.execute_v2', side_effect=AssertionError('no rerank')):
            while True:
                identities.extend(row['bangumi_id'] for row in page['results'])
                target = page['next_page_cursor'] or page['next_batch_cursor']
                if not target:
                    break
                retried = self.page(cursor=target)
                page = self.page(cursor=target)
                # Django signs with the current timestamp on every retry;
                # semantic position/expiry/evidence, not serialized ticket bytes,
                # must be identical even when retries cross a clock second.
                for key in ('batch', 'page', 'snapshot_expires_at', 'count'):
                    self.assertEqual(retried[key], page[key])
                self.assertEqual([(r['bangumi_id'], r['evidence_token']) for r in retried['results']],
                                 [(r['bangumi_id'], r['evidence_token']) for r in page['results']])
                self.assertEqual(page['snapshot_id'], first['snapshot_id'])
        self.assertEqual(identities, expected)
        self.assertEqual(len(set(identities)), len(identities))

    def test_v1_remains_exact_and_cursors_are_never_cross_consumed(self):
        before = self.client.get('/api/recommendations/', {'browse': True}).data
        prepared = self.page()
        after = self.client.get('/api/recommendations/', {'browse': True}).data
        for key in ('results', 'algorithm_version', 'control_policy_version', 'evidence_context'):
            self.assertEqual(before[key], after[key])
        self.assertNotIn('execution_recipe', after)
        self.assertNotIn('detail_context', after['results'][0])
        self.assertEqual(self.client.get('/api/recommendations/', {'cursor': prepared['next_page_cursor']}).status_code, 409)
        with self.assertRaises(engine.RecommendationSnapshotChanged):
            self.page(cursor=before['next_page_cursor'])
        self.assertEqual(self.client.get('/api/recommendations/capabilities/').data['algorithm_version'], 'content.v1')
        self.assertFalse(v2_browsing.capabilities()['execution_recipe']['serving_enabled'])
        row = prepared['results'][0]
        self.assertEqual(self.client.get(f'/api/recommendations/{row["bangumi_id"]}/', {
            'detail_context': row['detail_context'], 'evidence_token': row['evidence_token']}).status_code, 409)

    def test_detail_exact_evidence_and_no_list_calculation_or_private_reads(self):
        row = self.page()['results'][0]
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with CaptureQueriesContext(connection) as queries, patch('recommendations.execution.execute_v2',
                side_effect=AssertionError('no list')), patch('recommendations.engine.rank_context', side_effect=AssertionError('no rank')):
            result = self.detail(row)
        for key in ('evidence_token', 'evidence_context', 'reason', 'evidence'):
            self.assertEqual(result[key], row[key])
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        ticket = signing.loads(row['detail_context'], salt=v2_browsing.DETAIL_SALT)
        self.assertEqual(set(ticket), {'execution_recipe', 'options', 'revision', 'owner', 'expires',
                                      'snapshot', 'rollout_revision', 'bangumi_id'})
        self.assertNotIn('PRIVATE_REVIEW', str(ticket))

    def test_cross_user_tamper_identity_expiry_and_recipe_changes_fail_closed(self):
        first = self.page(); row = first['results'][0]
        with self.assertRaises(NotAuthenticated):
            v2_browsing.detail(AnonymousUser(), row['bangumi_id'], {})
        with self.assertRaises(NotAuthenticated):
            v2_browsing.browse_page(AnonymousUser(), {}, {})
        with self.assertRaises(engine.RecommendationSnapshotChanged):
            self.page(user=self.other, cursor=first['next_page_cursor'])
        with self.assertRaises(engine.RecommendationSnapshotChanged):
            self.detail(row, user=self.other)
        with self.assertRaises(engine.RecommendationSnapshotChanged):
            v2_browsing.detail(self.owner, row['bangumi_id'] + 1, {'detail_context': row['detail_context']})
        with self.assertRaises(ValidationError):
            self.detail(row, detail_context=row['detail_context'] + 'x')
        for change in ({'expires': 1}, {'execution_recipe': {**recipe_descriptor(), 'runtime_version': 'changed'}}):
            ticket = signing.loads(row['detail_context'], salt=v2_browsing.DETAIL_SALT)
            ticket.update(change)
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.detail(row, detail_context=signing.dumps(ticket, salt=v2_browsing.DETAIL_SALT))
        for field in ('recipe_revision', 'recall_policy_version', 'runtime_version', 'control_policy_version'):
            body = signing.loads(first['next_page_cursor'], salt=browsing.PREPARED_SALT)
            body['execution_recipe'][field] = 'changed'
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.page(cursor=signing.dumps(body, salt=browsing.PREPARED_SALT))
        with patch('recommendations.rerank_runtime.VERSION', 'pattern-prefix-runtime.next'):
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.page(cursor=first['next_page_cursor'])
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.detail(row)

    def test_profile_preferences_feedback_catalog_options_and_rollout_invalidate(self):
        mutations = (
            lambda: UserAnimeRecord.objects.filter(pk=self.records[0].pk).update(personal_score='8.6'),
            lambda: patch_preferences(user=self.owner, changes={'tags': ['成长'], 'expected_version': 0}),
            lambda: mutate_feedback(user=self.owner, action='hide', changes={'bangumi_id': 149,
                'expected_feedback_version': 0, 'operation_id': str(uuid4())}),
            lambda: RecommendationAnime.objects.filter(bangumi_id=149).update(season='2020-01'),
            lambda: UserAnimeRecord.objects.filter(pk=self.records[0].pk).update(custom_tags=['音乐']),
        )
        for mutation in mutations:
            first = self.page(); row = first['results'][0]
            mutation()
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.page(cursor=first['next_page_cursor'])
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.detail(row)
        first = self.page()
        with self.assertRaises(engine.RecommendationSnapshotChanged):
            self.page(cursor=first['next_page_cursor'], style='open')
        with override_settings(RECOMMENDATION_BROWSE_EPOCH='d2-new'):
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.page(cursor=first['next_page_cursor'])
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.detail(first['results'][0])

    def test_known_mutation_refresh_reads_whole_current_detail_and_preserves_controls(self):
        row = self.page(style='close', year_from=2026)['results'][0]
        UserAnimeRecord.objects.filter(pk=self.records[0].pk).update(personal_score='8.5')
        with self.assertRaises(engine.RecommendationSnapshotChanged):
            self.detail(row)
        refreshed = self.detail(row, evidence_token=None)
        self.assertNotEqual(refreshed['evidence_token'], row['evidence_token'])
        self.assertNotEqual(refreshed['detail_context'], row['detail_context'])
        ticket = signing.loads(refreshed['detail_context'], salt=v2_browsing.DETAIL_SALT)
        self.assertEqual(ticket['options']['style'], 'close')
        self.assertEqual(ticket['options']['year_from'], 2026)
        self.assertEqual(self.detail(refreshed)['evidence_token'], refreshed['evidence_token'])

    def test_unsafe_reclassification_and_sources_block_old_and_fresh_details(self):
        for unsafe in (True, None):
            row = self.page()['results'][0]
            RecommendationAnime.objects.filter(bangumi_id=row['bangumi_id']).update(nsfw=unsafe)
            with self.assertRaises(engine.RecommendationSnapshotChanged):
                self.detail(row)
            with self.assertRaises(NotFound):
                self.detail(row, evidence_token=None)
        first = self.page()
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=True)
        with self.assertRaises(engine.RecommendationSnapshotChanged):
            self.page(cursor=first['next_page_cursor'])
        with self.assertRaises(engine.RecommendationSnapshotChanged):
            self.detail(first['results'][0])

    def test_public_cache_namespaces_isolate_recipe_but_never_contain_private_context(self):
        calls = []
        original = cache.set
        def record(key, value, *args, **kwargs):
            if key.startswith('recommendations.browse.public.'):
                calls.append((key, deepcopy(value)))
            return original(key, value, *args, **kwargs)
        with patch.object(cache, 'set', side_effect=record):
            self.client.get('/api/recommendations/', {'browse': True})
            self.page()
        self.assertTrue(any(key.startswith(browsing.PUBLIC_CACHE_NAMESPACE + ':') for key, _ in calls))
        self.assertTrue(any(key.startswith('recommendations.browse.public.v2:') for key, _ in calls))
        self.assertNotIn('PRIVATE_REVIEW', str(calls))
        self.assertTrue(all(set(value) == {'revision', 'rows', 'index'} for _, value in calls))

    def test_detail_query_is_strict_and_default_routes_never_invoke_prepared_adapter(self):
        for data in ({'detail_context': ''}, {'detail_context': 'a' * 8193}, {'algorithm_version': 'content.v2'}, {'user_id': 1}):
            self.assertFalse(RecommendationDetailQuerySerializer(data=data).is_valid())
        with patch('recommendations.v2_browsing.browse_page', side_effect=AssertionError('not production')), \
             patch('recommendations.v2_browsing.detail', side_effect=AssertionError('not production')):
            self.assertEqual(self.client.get('/api/recommendations/', {'browse': True}).status_code, 200)
            self.assertEqual(self.client.get('/api/recommendations/10/').status_code, 200)
