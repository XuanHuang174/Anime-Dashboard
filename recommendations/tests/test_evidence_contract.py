"""6A public explanation parity without altering frozen ranking or real data."""
from copy import deepcopy
import json
import os
import subprocess
import sys
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase
from rest_framework.exceptions import PermissionDenied

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.evidence import EvidencePresenter
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_evaluation import evidence_errors


class EvidenceContractTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='evidence-owner')
        self.other = get_user_model().objects.create_user(username='evidence-other')
        self.client.force_authenticate(self.owner)
        for identity in (101, 202, 303):
            self.catalog(identity, score=1.0)
            shared = Anime.objects.create(bgm_id=identity, chinese_title=f'来源{identity}', base_tags=['音乐', '日常'])
            UserAnimeRecord.objects.create(user=self.owner, anime=shared, watch_status='completed',
                personal_score='9.0', review='PRIVATE_REVIEW_MUST_NOT_BE_QUERIED')
        for identity in range(1001, 1016):
            self.catalog(identity, tags='音乐,日常' if identity < 1010 else '音乐,成长')
        self.catalog(9001, nsfw=True)
        self.catalog(9002, nsfw=None)

    def catalog(self, identity, **changes):
        return RecommendationAnime.objects.create(bangumi_id=identity,
            **{'title_zh': f'作品{identity}', 'title_jp': 'Original', 'tags': '音乐,日常',
               'score': 7.5, 'nsfw': False, 'status_type': 'TV', **changes})

    def listed(self, **query):
        response = self.client.get('/api/recommendations/', query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def detail(self, row, **query):
        return self.client.get(f'/api/recommendations/{row["bangumi_id"]}/', query)

    def test_additive_typed_contract_and_all_categories_share_detail_evidence(self):
        for category in ('all', 'familiar', 'explore'):
            page = self.listed(category=category, page_size=2)
            for row in page['results']:
                evidence = row['evidence']
                self.assertEqual(evidence['contract_version'], 'recommendations.evidence.v1')
                self.assertEqual(evidence['kind'], 'rated_work')
                self.assertEqual(evidence['source']['personal_score'], '9.0')
                self.assertIn(evidence['source']['bangumi_id'], (101, 202, 303))
                self.assertEqual(set(evidence['source']), {'bangumi_id', 'title', 'personal_score'})
                self.assertFalse(evidence['is_probability'])
                self.assertFalse(evidence['is_exhaustive_ranking_explanation'])
                self.assertIn('不代表唯一排序原因', row['evidence_disclaimer'])
                versions = evidence['versions']
                self.assertEqual(versions, row['evidence_context'])
                self.assertEqual(versions['algorithm_version'], 'content.v1')
                self.assertEqual(versions['feature_rule_version'], engine.FEATURE_RULE_VERSION)
                self.assertEqual(versions['preference_version'], 0)
                for key in ('profile_version', 'catalog_feature_version'):
                    self.assertRegex(versions[key], '^[a-f0-9]{64}$')
                detail = self.detail(row, evidence_token=row['evidence_token'])
                self.assertEqual(detail.status_code, 200, detail.data)
                for key in ('evidence', 'reason', 'evidence_token', 'evidence_context', 'evidence_disclaimer'):
                    self.assertEqual(detail.data[key], row[key])
                self.assertEqual(evidence_errors({**row, 'tags': frozenset(row['tags'])}, engine.user_context(self.owner)), [])

    def test_explicit_and_absent_evidence_still_have_honest_versions(self):
        self.client.force_authenticate(self.other)
        missing = self.client.get('/api/recommendations/1001/').data
        self.assertIsNone(missing['evidence'])
        self.assertEqual(missing['reason'], '')
        self.assertRegex(missing['evidence_token'], '^[a-f0-9]{64}$')
        RecommendationPreference.objects.create(user=self.other, tags=['音乐'], version=3)
        row = self.listed()['results'][0]
        self.assertEqual(row['evidence']['kind'], 'explicit_preferences')
        self.assertIsNone(row['evidence']['source'])
        self.assertEqual(row['evidence']['shared_tag_sources'], {})
        self.assertEqual(row['evidence_context']['preference_version'], 3)
        self.assertNotIn('9.0', row['reason'])
        self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).data['evidence'], row['evidence'])

    def test_unchanged_requests_are_identical_and_metadata_never_enters_ranking(self):
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True, 'page_size': 24}
        context, ranked, _, weights = engine.rank_candidates(self.owner, options)
        before = deepcopy(ranked)
        presenter = EvidencePresenter(self.owner, context, weights, 'content.v1')
        for item in ranked:
            presenter.present(item['anime'], item['tags'])
        self.assertEqual([(r['value'], r['evidence'], r['reason']) for r in ranked],
                         [(r['value'], r['evidence'], r['reason']) for r in before])
        first, second = self.listed(page_size=24), self.listed(page_size=24)
        self.assertEqual(first, second)
        self.assertEqual([row['bangumi_id'] for row in first['results']], [row['anime'].bangumi_id for row in ranked])

    def test_rating_status_and_private_feature_changes_reject_old_card_without_leaking_new_reason(self):
        for field, value in (('personal_score', '9.3'), ('watch_status', 'on_hold'), ('private_base_tags', [])):
            row = self.listed()['results'][0]
            source = UserAnimeRecord.objects.filter(user=self.owner, anime__bgm_id=101)
            original = source.values(field).get()[field]
            source.update(**{field: value})
            if field == 'private_base_tags':
                source.update(metadata_initialized=True)
            response = self.detail(row, evidence_token=row['evidence_token'])
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('evidence', response.data)
            self.assertNotIn('PRIVATE_REVIEW', str(response.data))
            # A direct, fresh read remains allowed; no stale source is replayed.
            fresh = self.detail(row)
            self.assertEqual(fresh.status_code, 200)
            self.assertNotEqual(fresh.data['evidence_context']['profile_version'], row['evidence_context']['profile_version'])
            source.update(**{field: original}, metadata_initialized=False)

    def test_preference_revision_changes_even_if_the_displayed_source_is_unchanged(self):
        row = self.listed()['results'][0]
        RecommendationPreference.objects.create(user=self.owner, tags=['音乐'], version=1)
        self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).status_code, 409)
        fresh = self.detail(row).data
        self.assertEqual(row['evidence']['source'], fresh['evidence']['source'])
        self.assertNotEqual(row['evidence_token'], fresh['evidence_token'])

    def test_idf_and_selection_rule_revisions_invalidate_old_evidence_and_pagination(self):
        first = self.listed(page_size=2)
        row = first['results'][0]
        self.catalog(5001, tags='日常,成长')
        cache.clear()
        fresh = self.detail(row).data
        self.assertNotEqual(row['evidence_context']['catalog_feature_version'], fresh['evidence_context']['catalog_feature_version'])
        self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).status_code, 409)
        first = self.listed(page_size=2)
        with patch('recommendations.evidence.SELECTION_RULE_VERSION', 'association-evidence.next'):
            self.assertEqual(self.detail(first['results'][0], evidence_token=first['results'][0]['evidence_token']).status_code, 409)
            self.assertEqual(self.client.get('/api/recommendations/', {'page': 2, 'page_size': 2,
                             'state_token': first['state_token']}).status_code, 409)

    def test_service_refuses_foreign_or_unmarked_context_and_empty_list_has_version_metadata(self):
        context = engine.user_context(self.owner)
        with self.assertRaises(PermissionDenied) as error:
            EvidencePresenter(self.other, context, {}, 'content.v1')
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(PermissionDenied):
            EvidencePresenter(self.owner, {key: value for key, value in context.items() if key != '_owner_id'}, {}, 'content.v1')
        self.client.force_authenticate(self.other)
        cold = self.listed()
        self.assertEqual(cold['results'], [])
        self.assertEqual(cold['evidence_contract_version'], 'recommendations.evidence.v1')
        self.assertRegex(cold['evidence_context']['profile_version'], '^[a-f0-9]{64}$')

    def test_tokens_are_bound_to_authenticated_user_and_candidate_not_record_ids(self):
        row = self.listed()['results'][0]
        other_row = self.listed()['results'][1]
        self.assertEqual(self.detail(other_row, evidence_token=row['evidence_token']).status_code, 409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).status_code, 409)
        own = self.detail(row).data
        self.assertIsNone(own['evidence'])
        self.assertNotEqual(own['evidence_context']['profile_version'], row['evidence_context']['profile_version'])
        self.assertNotIn('来源', json.dumps(own, ensure_ascii=False))

    def test_candidate_changes_including_direct_update_invalidate_evidence(self):
        for changes in ({'score': 8.8}, {'tags': '音乐,成长'}, {'title_zh': '修改后的作品'}):
            row = self.listed()['results'][0]
            RecommendationAnime.objects.filter(bangumi_id=row['bangumi_id']).update(**changes)
            self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).status_code, 409)

    def test_authentication_query_validation_and_safety_fail_closed(self):
        row = self.listed()['results'][0]
        for params in ({'user_id': self.other.pk}, {'algorithm_version': 'content.v2'},
                       {'record_id': 1}, {'bgm_id': 101}, {'evidence_token': 'invalid'}):
            self.assertEqual(self.detail(row, **params).status_code, 400)
        token = row['evidence_token']
        self.assertEqual(self.client.get(f'/api/recommendations/1001/?evidence_token={token}&evidence_token={token}').status_code, 400)
        for identity in (9001, 9002):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/', {'evidence_token': token}).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.detail(row).status_code, 401)

    def test_detail_never_calls_the_ranking_engine_and_batch_safety_has_no_n_plus_one(self):
        self.listed()  # Warm only public features.
        row = {'bangumi_id': 1001}
        with (patch('recommendations.engine.rank_candidates', side_effect=AssertionError('full ranking')),
              patch('recommendations.views.rank_candidates', side_effect=AssertionError('full ranking'))):
            self.assertEqual(self.detail(row).status_code, 200)
        with CaptureQueriesContext(connection) as one:
            self.listed(page_size=1)
        with CaptureQueriesContext(connection) as many:
            self.listed(page_size=12)
        self.assertEqual(len(one), len(many))

    def test_gets_have_no_db_writes_or_review_queries_and_leave_all_records_intact(self):
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        preferences = list(RecommendationPreference.objects.values())
        with CaptureQueriesContext(connection) as queries:
            row = self.listed()['results'][0]
            self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).status_code, 200)
        sql = [query['sql'].strip().upper() for query in queries]
        self.assertFalse(any(statement.startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for statement in sql))
        self.assertFalse(any('"REVIEW"' in statement for statement in sql))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
        self.assertEqual(list(RecommendationPreference.objects.values()), preferences)

    def test_source_reclassified_during_explanation_is_rejected_before_serialization(self):
        original = EvidencePresenter.ensure_safe
        def flip(presenter, animes):
            RecommendationAnime.objects.filter(bangumi_id=101).update(nsfw=None)
            return original(presenter, animes)
        with patch.object(EvidencePresenter, 'ensure_safe', flip):
            response = self.client.get('/api/recommendations/1001/')
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('作品101', str(response.data))

    @override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v2')
    @patch('recommendations.version_control.require_v2_approval', return_value=None)
    def test_v2_detail_parity_and_v1_rollback_invalidate_old_evidence(self, _approval):
        # This isolated compatibility test is not a serving approval. PostgreSQL
        # correctly has no production V2 bypass; simulate approval in this method only.
        row = self.listed()['results'][0]
        self.assertEqual(row['evidence_context']['algorithm_version'], 'content.v2')
        self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).data['evidence'], row['evidence'])
        with patch('recommendations.ranking_v2.POLICY_VERSION', 'balanced-rerank.next'):
            self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).status_code, 409)
        with override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v1'):
            self.assertEqual(self.detail(row, evidence_token=row['evidence_token']).status_code, 409)
            self.assertEqual(self.detail(row).data['evidence_context']['algorithm_version'], 'content.v1')


class EvidenceDeterminismTests(SimpleTestCase):
    def test_canonical_presentation_is_stable_across_hash_seeds_and_seed_input_order(self):
        code = '''
import django, json
django.setup()
from types import SimpleNamespace
from recommendations.evidence import EvidencePresenter
from recommendations.taxonomy import TAXONOMY_VERSION
tags = frozenset(['音乐', '日常', '成长', '治愈', '校园'])
seeds = [{'bangumi_id': identity, 'title': f'来源{identity}', 'score': 9.0,
          'tags': tags, 'feature_sources': {tag: ('private_base_tags',) for tag in tags}}
         for identity in (303, 101, 202)]
context = {'_owner_id': 123, 'profile': {'sample_policy': {'minimum_score': '8.5'}}, 'mode': 'personalized',
           'preferences': {'tags': [], 'version': 0}, 'owned': {}, 'seeds': seeds, 'taxonomy_version': TAXONOMY_VERSION}
anime = SimpleNamespace(bangumi_id=1001, score=7.5, status_type='TV', season='2026-04',
                        tags='日常,音乐,成长', title_zh='推荐作品', title_jp='Original')
weights = {tag: 1.23456789012345 + index / 10 for index, tag in enumerate(sorted(tags))}
outputs = []
for sequence in (seeds, list(reversed(seeds))):
    result = EvidencePresenter(SimpleNamespace(is_authenticated=True, pk=123), {**context, 'seeds': sequence}, weights, 'content.v1')
    outputs.append(result.present(anime, frozenset(['日常', '音乐', '成长'])))
assert outputs[0] == outputs[1]
print(json.dumps(outputs[0], ensure_ascii=True, sort_keys=True))
'''
        outputs = []
        for seed in ('1', '2', '12345'):
            process = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30,
                env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.recommendation_test_settings', 'PYTHONHASHSEED': seed})
            self.assertEqual(process.returncode, 0, process.stderr)
            outputs.append(process.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])
