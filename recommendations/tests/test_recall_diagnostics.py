"""V2-A synthetic, local PostgreSQL only. Never reads real account data."""
import ast
from copy import deepcopy
from io import StringIO
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from anime.models import UserAnimeRecord
from recommendations import engine, ranking_v2
from recommendations.diagnostics import diagnose_sample
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.pipeline_trace import PipelineObserver
from recommendations.quality_audit import catalog_snapshot
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import (load_config, selected_targets, sample_binding,
    freeze_cohort, verify_cohort, catalog_states, census, destination, traced_rank, audit_sample)
from recommendations.tests import test_diagnostics as diagnostic_fixtures

CONFIG = Path(settings.BASE_DIR) / 'evaluation/v2a-recall.v1.json'


class RecallConfigurationTests(SimpleTestCase):
    def test_fixed_selection_keeps_original_four_and_predeclares_expansion_not_outcomes(self):
        config, _ = load_config(CONFIG)
        context = {'mode': 'personalized', 'seeds': [{'bangumi_id': n} for n in range(1, 30)]}
        self.assertEqual(selected_targets(context, config)['historical'], [1, 2, 3, 4])
        self.assertEqual(selected_targets(context, config)['expanded'], [1, 2, 3, 4, 5, 8, 11, 15, 18, 22, 25, 29])
        context['seeds'].reverse()
        self.assertEqual(selected_targets(context, config)['expanded'][-1], 29)
        for size in range(4, 30):
            context['seeds'] = [{'bangumi_id': n} for n in range(size)]
            selected = selected_targets(context, config)['expanded']
            self.assertEqual(len(selected), min(size, 12))
            self.assertEqual(len(selected), len(set(selected)))
        context['seeds'] = context['seeds'][:2]
        self.assertEqual(selected_targets(context, config), {'historical': [], 'expanded': []})

    def test_invalid_config_cannot_tune_k_targets_or_duplicate_keys(self):
        config, _ = load_config(CONFIG)
        for key, value in (('top_k', 20), ('historical_limit', 3), ('expanded_limit', 16), ('categories', ['all']), ('algorithms', ['content.v2'])):
            modified = {**config, key: value}
            with patch.object(Path, 'read_bytes', return_value=json.dumps(modified).encode()):
                with self.assertRaises(ValueError):
                    load_config(CONFIG)
        with patch.object(Path, 'read_bytes', return_value=b'{"top_k":10,"top_k":20}'):
            with self.assertRaises(ValueError):
                load_config(CONFIG)

    def test_observer_records_only_counts_and_ordinal_without_raw_identity_or_mutation(self):
        data = [11, 22, 33]; before = deepcopy(data)
        observer = PipelineObserver(22); observer.stage('pool', iter(data))
        self.assertEqual(observer.export()['stages']['pool'], {'observed': True, 'count': 3, 'target_position': 2})
        self.assertEqual(data, before)
        self.assertNotIn('22', json.dumps(observer.export()))
        self.assertIsNone(observer.export()['stages']['validated']['count'])
        self.assertEqual(observer.export(cold_start=True)['stages']['returned']['count'], 0)
        with self.assertRaises(ValueError):
            observer.stage('pool', [])

    def test_original_engine_ast_is_frozen_except_explicit_observation_hooks(self):
        reference = json.loads((Path(settings.BASE_DIR) / 'evaluation/v2a-engine-baseline.v1.json').read_text(encoding='utf-8'))
        class RemoveObservation(ast.NodeTransformer):
            def visit_If(self, node):
                if ast.dump(node.test, include_attributes=False) == ast.dump(ast.parse('observer is not None').body[0].value, include_attributes=False):
                    return None
                if ast.dump(node.test, include_attributes=False) == ast.dump(ast.parse('recall_policy_version is not None').body[0].value, include_attributes=False):
                    # V2-B is an explicit offline opt-in. Verify the DEFAULT
                    # else branch remains byte-for-AST identical to frozen A.
                    return [self.visit(statement) for statement in node.orelse]
                return self.generic_visit(node)
            def visit_arguments(self, node):
                pairs = [(arg, default) for arg, default in zip(node.kwonlyargs, node.kw_defaults)
                         if arg.arg not in ('observer', 'pipeline_observer', 'recall_policy_version')]
                node.kwonlyargs = [pair[0] for pair in pairs]; node.kw_defaults = [pair[1] for pair in pairs]
                return self.generic_visit(node)
            def visit_Call(self, node):
                node.keywords = [keyword for keyword in node.keywords if keyword.arg != 'observer']
                return self.generic_visit(node)
        tree = RemoveObservation().visit(ast.parse((Path(settings.BASE_DIR) / 'recommendations/engine.py').read_text(encoding='utf-8')))
        actual = {node.name: hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
                  for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in reference['engine_functions_ast_sha256']}
        self.assertEqual(actual, reference['engine_functions_ast_sha256'])
        policy_tree = ast.parse((Path(settings.BASE_DIR) / 'recommendations/ranking_v2.py').read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(ast.dump(policy_tree, include_attributes=False).encode()).hexdigest(), reference['ranking_v2_module_ast_sha256'])


class RecallPipelinePostgresTests(TransactionTestCase):
    setUp = diagnostic_fixtures.DiagnosticPostgresTests.setUp
    sample = diagnostic_fixtures.DiagnosticPostgresTests.sample

    def inputs(self):
        config, config_sha = load_config(CONFIG)
        _, rows = catalog_snapshot()
        rows = {row['bangumi_id']: row for row in rows}
        context, summary = self.sample()
        index, _ = __import__('recommendations.diagnostics', fromlist=['public_index']).public_index(list(rows.values()))
        return config, config_sha, rows, context, summary, index

    def test_all_categories_and_both_versions_have_equal_order_scores_evidence_and_query_counts(self):
        _, _, _, context, _, index = self.inputs()
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        for algorithm in ('content.v1', 'content.v2'):
            for category in ('all', 'familiar', 'explore'):
                with readonly_snapshot():
                    rows, report = traced_rank(self.owner, context, index, load_config(CONFIG)[0]['baseline_filters'], algorithm, category, target=7)
                self.assertTrue(all(report['noninterference'][key] for key in ('order_scores_evidence_equal', 'sql_count_equal', 'presentation_equal')))
                stages = report['pipeline']['stages']
                self.assertLessEqual(stages['pool']['count'], 400)
                self.assertLessEqual(stages['returned']['count'], 120)
                self.assertEqual(stages['top_10']['count'], min(10, len(rows)))
                self.assertTrue(all(stage['observed'] for stage in stages.values()))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)

    def test_noninterference_guard_rejects_changed_scores_reasons_and_order(self):
        config, _, _, context, _, index = self.inputs()
        original = engine.rank_context
        for change in ('score', 'reason', 'order'):
            def altered(*args, **kwargs):
                rows, weights = original(*args, **kwargs)
                if kwargs.get('pipeline_observer') is not None:
                    if change == 'order':
                        rows.reverse()
                    elif change == 'score':
                        rows[0]['value'] += .01
                    else:
                        rows[0]['reason'] += 'unsupported'
                return rows, weights
            with patch('recommendations.engine.rank_context', side_effect=altered), readonly_snapshot():
                with self.assertRaisesMessage(ValueError, 'Observer changed'):
                    traced_rank(self.owner, context, index, config['baseline_filters'], 'content.v2', 'all')

    def test_early_safety_filter_exclusion_and_genre_failure_reasons_are_separate(self):
        config, _, catalog, context, _, _ = self.inputs()
        states = catalog_states(); filters = config['baseline_filters']
        state_cases = {700: True, 701: None}; states.update(state_cases)
        self.assertEqual(census(context, filters, catalog, states, 700)['first_failure'], 'adult_filtered')
        self.assertEqual(census(context, filters, catalog, states, 701)['first_failure'], 'unknown_content_rating_filtered')
        self.assertEqual(census(context, filters, catalog, states, 999)['first_failure'], 'missing_catalog_identity')
        self.assertEqual(census(context, filters, catalog, states, 1)['first_failure'], 'training_seed_excluded')
        context['feedback']['hidden_ids'] = {7}
        self.assertEqual(census(context, filters, catalog, states, 7)['first_failure'], 'feedback_excluded')
        context['owned'][8] = 'plan'
        self.assertEqual(census(context, filters, catalog, states, 8)['first_failure'], 'recorded_excluded')
        for identity, tags, expected in ((9, '神作,二刷', 'no_effective_genres'), (10, '悬疑', 'no_real_preference_association')):
            catalog[identity]['tags'] = tags
            self.assertEqual(census(context, filters, catalog, states, identity)['first_failure'], expected)
        self.assertEqual(census(context, {**filters, 'min_score': 8}, catalog, states, 11)['first_failure'], 'public_filter_rejected')
        self.assertEqual(census(context, {**filters, 'type': 'TV'}, catalog, states, 11)['first_failure'], 'public_filter_rejected')
        with self.assertRaisesMessage(ValueError, 'Year filters'):
            census(context, {**filters, 'year_from': 2020}, catalog, states, 11)

    def test_target_in_pool_below_ten_and_outside_pool_or_return_limit_are_distinguished(self):
        config, _, catalog, context, _, index = self.inputs()
        logical = census(context, config['baseline_filters'], catalog, catalog_states(), 7)
        for positions, rank, expected in (({'eligible': 500}, None, 'outside_400_pool'),
                ({'eligible': 1, 'pool': 1}, None, 'current_hydration_or_filter_rejected'),
                ({'eligible': 1, 'pool': 1, 'hydrated': 1}, None, 'current_evidence_association_or_category_rejected'),
                ({'eligible': 1, 'pool': 1, 'hydrated': 1, 'validated': 1}, None, 'outside_returned_120'),
                ({'eligible': 1, 'pool': 1, 'hydrated': 1, 'validated': 1, 'returned': 1}, 11, 'returned_below_top_10'),
                ({'eligible': 1, 'pool': 1, 'hydrated': 1, 'validated': 1, 'returned': 1}, 1, 'hit_top_10')):
            observer = PipelineObserver(7)
            for stage in ('eligible', 'pool', 'hydrated', 'validated', 'returned'):
                observer.stage(stage, [0] * (positions.get(stage, 0) - 1) + ([7] if stage in positions else []))
            self.assertEqual(destination(logical, observer.export(), 'all', rank, True)['code'], expected)

    def test_actual_large_pool_and_hydration_failure_are_observed_not_guessed(self):
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=n, title_zh=f'合成{n}',
            score=9, tags='音乐,日常', nsfw=False) for n in range(100, 650)])
        RecommendationAnime.objects.filter(bangumi_id=7).update(score=10, tags='音乐,日常')
        config, _, catalog, context, _, index = self.inputs()
        filters = config['baseline_filters']
        with readonly_snapshot():
            _, report = traced_rank(self.owner, context, index, filters, 'content.v1', 'familiar', target=24)
        logical = census(context, filters, catalog, catalog_states(), 24)
        self.assertEqual(destination(logical, report['pipeline'], 'familiar', report['target_returned_rank'], True)['code'], 'outside_400_pool')
        original = engine._v2_item
        def fail_target_after_hydration(row, *args, **kwargs):
            if row['bangumi_id'] == 7 and kwargs.get('verified_tags') is None:
                return None
            return original(row, *args, **kwargs)
        with patch('recommendations.engine._v2_item', side_effect=fail_target_after_hydration), readonly_snapshot():
            _, report = traced_rank(self.owner, context, index, filters, 'content.v2', 'all', target=7)
        self.assertIsNotNone(report['pipeline']['stages']['pool']['target_position'])
        self.assertIsNotNone(report['pipeline']['stages']['hydrated']['target_position'])
        self.assertIsNone(report['pipeline']['stages']['validated']['target_position'])

    def test_historical_metrics_match_existing_diagnostic_and_sparse_is_insufficient_not_zero(self):
        config, _, catalog, context, summary, index = self.inputs()
        config = {**config, 'filter_probes': []}
        with readonly_snapshot():
            current = audit_sample(self.owner, context, summary, index, catalog, catalog_states(), config)
            previous = diagnose_sample(context, summary, index, top_k=10, repeats=2, max_holdouts=4)
        for algorithm in config['algorithms']:
            for category in config['categories']:
                now = current['holdout_sets']['historical']['metrics'][algorithm][category]
                old = previous['holdout']['metrics'][algorithm][category]
                self.assertEqual(now['hit_at_10'], old['hit_at_k'])
                self.assertEqual(now['mrr_at_10'], old['mrr_at_k'])
                self.assertEqual(now['binary_ndcg_at_10'], old['binary_ndcg_at_k'])
        RecommendationPreference.objects.create(user=self.sparse, tags=['音乐'])
        context, summary = self.sample(self.sparse)
        with readonly_snapshot():
            sparse = audit_sample(self.sparse, context, summary, index, catalog, catalog_states(), config)
        self.assertEqual(sparse['holdout_sets']['expanded']['status'], 'insufficient_evidence')
        self.assertIsNone(sparse['holdout_sets']['expanded']['metrics']['content.v2']['all']['hit_at_10'])

    def test_frozen_lock_binds_accounts_inputs_catalog_config_and_targets_with_no_raw_ids(self):
        config, sha, _, context, summary, _ = self.inputs()
        samples = [(self.owner, context, summary)]
        lock = freeze_cohort(samples, config, sha, 'catalog', 'release')
        self.assertFalse(lock['ranking_performed'])
        verify_cohort(lock, samples, config, sha, 'catalog')
        self.assertNotIn('bangumi_id', json.dumps(lock))
        self.assertNotIn(self.owner.email, json.dumps(lock))
        for bad_sha, bad_catalog in (('changed', 'catalog'), (sha, 'changed')):
            with self.assertRaises(ValueError):
                verify_cohort(lock, samples, config, bad_sha, bad_catalog)
        changed = deepcopy(context); changed['_owner_id'] = self.sparse.pk
        with self.assertRaises(ValueError):
            verify_cohort(lock, [(self.sparse, changed, summary)], config, sha, 'catalog')
        changed = deepcopy(context); changed['preferences']['tags'] = ['科幻']
        with self.assertRaises(ValueError):
            verify_cohort(lock, [(self.owner, changed, summary)], config, sha, 'catalog')

    def test_feedback_blocked_historical_target_stays_logged_not_replaced_or_unhidden(self):
        config, _, catalog, context, summary, index = self.inputs()
        context['feedback']['hidden_ids'] = {1}
        with readonly_snapshot():
            report = audit_sample(self.owner, context, summary, index, catalog, catalog_states(), {**config, 'filter_probes': []})
        group = report['holdout_sets']['historical']
        self.assertEqual(group['selected_count'], 4); self.assertEqual(group['paired_count'], 3)
        self.assertEqual(report['folds'][0]['arms']['content.v2']['all']['destination']['code'], 'feedback_excluded')
        self.assertEqual(context['feedback']['hidden_ids'], {1})

    def test_command_freeze_never_ranks_then_requires_lock_and_emits_private_nonapproval_reports(self):
        base = Path(settings.BASE_DIR) / '.tools/recall-test-reports' / uuid4().hex
        common = {'user_email': [self.owner.email, self.sparse.email], 'confirm_authorized_samples': True, 'stdout': StringIO()}
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        with patch('recommendations.engine.rank_context', side_effect=AssertionError('Freeze must not rank')):
            call_command('audit_recommendation_recall', freeze_only=True, output_dir=base / 'freeze', **common)
        with readonly_snapshot():
            with connection.cursor() as cursor:
                cursor.execute('SHOW transaction_read_only'); self.assertEqual(cursor.fetchone()[0], 'on')
        with CaptureQueriesContext(connection) as queries:
            call_command('audit_recommendation_recall', cohort_lock=base / 'freeze/cohort.lock.json', output_dir=base / 'audit', **common)
        text = (base / 'audit/recall-audit.json').read_text(encoding='utf-8')
        events = (base / 'audit/target-events.jsonl').read_text(encoding='utf-8')
        self.assertFalse(json.loads(text)['approval_issued'])
        self.assertFalse((base / 'audit/approval.json').exists())
        self.assertEqual(json.loads(text)['samples'][0]['filter_probes']['year_2020_2026']['status'], 'unsupported_in_engine_entry')
        for sentinel in (self.owner.email, self.sparse.email, 'PRIVATE_REVIEW', 'personal_score', '合成目录', 'source_title'):
            self.assertNotIn(sentinel, text + events)
        self.assertFalse(any('"review"' in query['sql'] or 'private_description' in query['sql'] for query in queries))
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for query in queries))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
        with self.assertRaises(CommandError):
            call_command('audit_recommendation_recall', freeze_only=True, output_dir=base / 'freeze', **common)

    def test_scope_v2_default_and_public_paths_fail_before_catalog_reads(self):
        for changes in ({}, {'user_email': [self.owner.email]},
                        {'user_email': [self.owner.email] * 2, 'confirm_authorized_samples': True}):
            with patch('recommendations.management.commands.audit_recommendation_recall.catalog_snapshot') as scan:
                with self.assertRaises(CommandError):
                    call_command('audit_recommendation_recall', freeze_only=True, stdout=StringIO(), **changes)
                scan.assert_not_called()
        with override_settings(RECOMMENDATION_ALGORITHM_VERSION='content.v2'):
            with self.assertRaises(CommandError):
                call_command('audit_recommendation_recall', freeze_only=True, user_email=[self.owner.email], confirm_authorized_samples=True, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('audit_recommendation_recall', freeze_only=True, user_email=[self.owner.email], confirm_authorized_samples=True,
                         output_dir=Path(settings.BASE_DIR) / 'static/recall', stdout=StringIO())

    def test_frozen_account_changes_stop_before_ranking_without_reselection(self):
        base = Path(settings.BASE_DIR) / '.tools/recall-test-reports' / uuid4().hex
        common = {'user_email': [self.owner.email], 'confirm_authorized_samples': True, 'stdout': StringIO()}
        call_command('audit_recommendation_recall', freeze_only=True, output_dir=base / 'freeze', **common)
        # Synthetic isolated DB only, never real account manipulation.
        UserAnimeRecord.objects.filter(user=self.owner).update(personal_score=8)
        with patch('recommendations.engine.rank_context', side_effect=AssertionError('No reselection')) as rank:
            with self.assertRaisesMessage(CommandError, 'Frozen cohort'):
                call_command('audit_recommendation_recall', cohort_lock=base / 'freeze/cohort.lock.json', output_dir=base / 'audit', **common)
        rank.assert_not_called()
        self.assertFalse((base / 'audit/manifest.json').exists())

    def test_http_cannot_enable_observer_or_override_target_or_algorithm(self):
        client = APIClient(); client.force_authenticate(self.owner)
        for key in ('pipeline_observer', '_diagnostic_target_id', 'cohort_lock', 'algorithm_version'):
            self.assertEqual(client.get('/api/recommendations/', {key: 'content.v2'}).status_code, 400)
        with patch.object(PipelineObserver, 'stage', side_effect=AssertionError('Observer must stay offline')):
            result = client.get('/api/recommendations/')
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('pipeline', result.data)

    def test_actual_category_rejection_and_unsafe_source_fail_closed_without_fake_results(self):
        config, _, catalog, context, _, index = self.inputs()
        with readonly_snapshot():
            _, run = traced_rank(self.owner, context, index, config['baseline_filters'], 'content.v2', 'explore', target=7)
        logical = census(context, config['baseline_filters'], catalog, catalog_states(), 7)
        self.assertEqual(destination(logical, run['pipeline'], 'explore', run['target_returned_rank'], True)['code'], 'explore_requires_new_topic')
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=True)
        with readonly_snapshot(), self.assertRaises(engine.RecommendationSnapshotChanged):
            traced_rank(self.owner, context, index, config['baseline_filters'], 'content.v2', 'all', target=7)
