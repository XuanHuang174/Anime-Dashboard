"""V2-C bit-exact property tests and isolated local PostgreSQL guards."""
from copy import deepcopy
from io import StringIO
import json
import math
from pathlib import Path
from random import Random
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from anime.models import UserAnimeRecord
from recommendations import engine, ranking_v2, rerank_runtime
from recommendations.diagnostics import public_index
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import load_config
from recommendations.runtime_comparison import (REFERENCE_ARM, FAST_ARM, compare_runtime_sample,
    assert_equivalent, rank_arm, verify_runtime, benchmark_kernel)
from recommendations.tests import test_diagnostics as fixtures

CONFIG = Path(settings.BASE_DIR) / 'evaluation/v2a-recall.v1.json'


def row(identity, tags=('音乐',), value=.8):
    return {'bangumi_id': identity, 'tags': frozenset(tags), 'value': value,
            'evidence': {'kind': 'synthetic-real-overlap'}, 'reason': 'synthetic evidence'}


class RuntimeEquivalenceTests(SimpleTestCase):
    def test_declared_runtime_policy_is_verified_and_not_online(self):
        self.assertEqual(len(verify_runtime()), 64)
        self.assertFalse(rerank_runtime.runtime_contract()['online_enabled'])
        with patch.object(rerank_runtime, 'runtime_contract', return_value={'changed': True}):
            with self.assertRaisesMessage(ValueError, 'preregistration'):
                verify_runtime()

    def test_many_vectors_ties_limits_and_patterns_match_original_exactly_without_mutations(self):
        random = Random(445502)
        topics = ('音乐', '日常', '成长', '悬疑', '奇幻', '喜剧')
        for case in range(20):
            items = [row(identity, random.sample(topics, random.randint(1, 4)),
                         random.choice((.05, .5, .8, 1., random.random())))
                     for identity in range(1, 10 + case * 7)]
            random.shuffle(items)
            before = deepcopy(items)
            vector = {tag: random.uniform(.01, 1) for tag in topics[:1 + case % 4]}
            weights = {tag: random.uniform(1, 3) for tag in topics}
            for category in ('all', 'familiar', 'explore'):
                for limit in (0, 1, 9, 120):
                    expected = ranking_v2.rerank_candidates(items, vector, weights, category=category, limit=limit)
                    actual, stats = rerank_runtime.rerank_candidates(items, vector, weights, category=category, limit=limit)
                    self.assertEqual(actual, expected, (case, category, limit))
                    for a, b in zip(actual, expected):
                        self.assertIs(a, b); self.assertEqual(a['value'].hex(), b['value'].hex())
                    self.assertLessEqual(stats['pair_cache_entries'], rerank_runtime.PAIR_CACHE_LIMIT)
            self.assertEqual(items, before)

    def test_invalid_duplicate_and_unrelated_candidates_follow_frozen_filter(self):
        items = [row(1), row(1, value=.2), row(2), row(3, ('悬疑',)), row(4, value=float('nan')),
                 row(5, value=float('inf')), row(-1), row(True), {**row(6), 'evidence': None}]
        for category in ('all', 'familiar', 'explore'):
            expected = ranking_v2.rerank_candidates(items, {'音乐': 1}, {}, category=category)
            actual, _ = rerank_runtime.rerank_candidates(items, {'音乐': 1}, {}, category=category)
            self.assertEqual(actual, expected)

    def test_subtraction_rounding_tie_still_compares_every_work_not_just_pattern_head(self):
        lower = .05
        for _ in range(20):
            higher = math.nextafter(lower, math.inf)
            if lower - .25 == higher - .25:
                break
            lower = higher
        self.assertLess(lower, higher); self.assertEqual(lower - .25, higher - .25)
        items = [row(99, value=1.), row(2, value=higher), row(1, value=lower)]
        expected = ranking_v2.rerank_candidates(items, {'音乐': 1}, {}, category='all')
        actual, _ = rerank_runtime.rerank_candidates(items, {'音乐': 1}, {}, category='all')
        self.assertEqual([item['bangumi_id'] for item in actual], [99, 1, 2])
        self.assertEqual(actual, expected)

    def test_pattern_updates_and_pair_cache_really_avoid_repeated_work(self):
        items = [row(n, ('音乐', '成长') if n % 2 else ('音乐', '日常')) for n in range(1, 401)]
        actual, stats = rerank_runtime.rerank_candidates(items, {'音乐': 1}, {}, category='explore')
        expected = ranking_v2.rerank_candidates(items, {'音乐': 1}, {}, category='explore')
        self.assertEqual(actual, expected)
        self.assertLess(stats['prefix_state_updates'], 120 * 3)
        self.assertLessEqual(stats['weighted_overlap_calls'], 6)
        self.assertGreater(stats['pair_cache_hits'], 0)

    def test_bounded_cache_is_not_a_result_cap_or_shared_across_vectors(self):
        items = [row(n, ('音乐', f'feature{n}')) for n in range(1, 140)]
        with patch.object(rerank_runtime, 'PAIR_CACHE_LIMIT', 3):
            for vector in ({'音乐': 1}, {'音乐': .2, 'feature1': .8}):
                expected = ranking_v2.rerank_candidates(items, vector, {}, category='explore')
                actual, stats = rerank_runtime.rerank_candidates(items, vector, {}, category='explore')
                self.assertEqual(actual, expected); self.assertEqual(len(actual), 120)
                self.assertLessEqual(stats['pair_cache_entries'], 3)
        self.assertFalse(hasattr(rerank_runtime, 'memo'))

    def test_parity_guard_rejects_wrong_output_stage_or_sql(self):
        report = {'noninterference': {'output_hmac': 'x', 'presentation_hmac': 'y'},
                  'pipeline': {'same': True}, 'runs': {'control': {'sql_count': 3}, 'observed': {'sql_count': 3}}}
        assert_equivalent(report, deepcopy(report))
        for name in ('output', 'stage', 'sql'):
            other = deepcopy(report)
            if name == 'output':
                other['noninterference']['output_hmac'] = 'wrong'
            elif name == 'stage':
                other['pipeline']['same'] = False
            else:
                other['runs']['control']['sql_count'] = 2
            with self.assertRaises(ValueError):
                assert_equivalent(report, other)
        with self.assertRaisesMessage(ValueError, 'Unknown runtime arm'):
            rank_arm(None, {}, {}, {}, 'unknown', 'all')
        self.assertEqual(benchmark_kernel([], {}, {}, 'all')['status'], 'not_measured_empty_pool')


class RuntimePostgresTests(TransactionTestCase):
    setUp = fixtures.DiagnosticPostgresTests.setUp
    sample = fixtures.DiagnosticPostgresTests.sample

    def inputs(self):
        config, _ = load_config(CONFIG)
        _, rows = catalog_snapshot(); context, summary = self.sample()
        index, _ = public_index(rows)
        return config, {row['bangumi_id']: row for row in rows}, context, summary, index

    def test_full_engine_order_scores_evidence_pipeline_and_sql_equivalent_all_categories(self):
        from recommendations.recall_diagnostics import catalog_states
        config, catalog, context, summary, index = self.inputs()
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        original = deepcopy(context)
        with readonly_snapshot(), CaptureQueriesContext(connection) as queries:
            result = compare_runtime_sample(self.owner, context, summary, index, catalog, catalog_states(), config)
        self.assertEqual(result['runtime_equivalence'], 'pass'); self.assertEqual(context, original)
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
        self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        for category in config['categories']:
            bench = result['baseline'][FAST_ARM][category]['runs'][0]['kernel_benchmark']
            self.assertTrue(bench['same_hydrated_pool']); self.assertTrue(bench['output_equal'])

    def test_optimized_path_preserves_safe_live_fingerprint_and_current_tag_checks(self):
        from recommendations.models import RecommendationAnime
        config, _, context, _, index = self.inputs()
        RecommendationAnime.objects.filter(bangumi_id=7).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=8).update(nsfw=None)
        RecommendationAnime.objects.filter(bangumi_id=9).update(tags='悬疑')
        for category in config['categories']:
            with readonly_snapshot():
                left, ref = rank_arm(self.owner, context, index, config['baseline_filters'], REFERENCE_ARM, category)
                right, fast = rank_arm(self.owner, context, index, config['baseline_filters'], FAST_ARM, category)
            assert_equivalent(ref, fast)
            self.assertFalse({7, 8, 9} & {row['anime'].bangumi_id for row in right})
            self.assertEqual(left, right)
            stats = fast['runs']['control']['stage_trace']['runtime']
            self.assertGreater(stats['changed_public_rows'], 0)

    def test_default_paths_cannot_select_runtime_or_its_cache(self):
        config, _, context, _, index = self.inputs()
        with patch.object(rerank_runtime, 'rerank_candidates', side_effect=AssertionError('Offline only')):
            for version in ('content.v1', 'content.v2'):
                engine.rank_context(deepcopy(context), {**config['baseline_filters'], 'category': 'all'}, index, algorithm_version=version)
            engine.rank_recall_candidate(deepcopy(context), {**config['baseline_filters'], 'category': 'all'}, index)
            client = APIClient(); client.force_authenticate(self.owner)
            self.assertEqual(client.get('/api/recommendations/', {'runtime_version': rerank_runtime.VERSION}).status_code, 400)
            self.assertEqual(client.get('/api/recommendations/').status_code, 200)
        with self.assertRaises(engine.RecommendationUnavailable):
            engine.rank_recall_candidate(deepcopy(context), {**config['baseline_filters'], 'category': 'all'}, index, runtime_version='unknown')

    def test_optimized_runtime_cannot_bypass_source_safety(self):
        from recommendations.models import RecommendationAnime
        config, _, context, _, index = self.inputs()
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=True)
        with readonly_snapshot(), self.assertRaises(engine.RecommendationSnapshotChanged):
            rank_arm(self.owner, context, index, config['baseline_filters'], FAST_ARM, 'all')

    def test_command_requires_old_lock_is_readonly_private_and_never_grants_approval(self):
        directory = Path(settings.BASE_DIR) / '.tools/runtime-tests' / uuid4().hex
        common = dict(user_email=[self.owner.email, self.sparse.email], confirm_authorized_samples=True, stdout=StringIO())
        with self.assertRaisesMessage(CommandError, 'original cohort'):
            call_command('compare_recommendation_runtime', freeze_only=True, **common)
        call_command('audit_recommendation_recall', freeze_only=True, output_dir=directory / 'freeze', **common)
        with CaptureQueriesContext(connection) as queries:
            call_command('compare_recommendation_runtime', cohort_lock=directory / 'freeze/cohort.lock.json',
                         output_dir=directory / 'report', **common)
        text = (directory / 'report/recall-audit.json').read_text(encoding='utf-8')
        report = json.loads(text)
        self.assertFalse(report['approval_issued']); self.assertEqual(report['online_default'], 'content.v1')
        self.assertTrue(all(sample['runtime_equivalence'] == 'pass' for sample in report['samples']))
        for sentinel in (self.owner.email, self.sparse.email, 'PRIVATE_REVIEW', 'personal_score', '合成目录'):
            self.assertNotIn(sentinel, text)
        self.assertFalse(any('"review"' in q['sql'] or q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        self.assertFalse((directory / 'report/approval.json').exists())
