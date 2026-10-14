from copy import deepcopy
from io import StringIO
import json
import hashlib
import math
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.db import connection, DatabaseError
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from anime.models import Anime, UserAnimeRecord
from recommendations.engine import build_feature_index, rank_candidates, rank_context, user_context
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_audit import audit_rows, catalog_snapshot, score_state, season_state
from recommendations.quality_evaluation import category_differences, evidence_errors, evaluate_user, top_metrics, without_seed
from recommendations.quality_readonly import readonly_snapshot
from recommendations.quality_report import write_reports


def catalog_row(identity=1, **changes):
    return {'bangumi_id': identity, 'title_zh': f'公开作品{identity}', 'title_jp': '', 'tags': '日常,音乐',
            'score': 8.0, 'season': '2026-04', 'status_type': 'TV', 'cover_url': '', **changes}


class QualityPrimitiveTests(SimpleTestCase):
    def test_zero_is_ambiguous_not_confidence_corrected_and_nonfinite_scores_report_safely(self):
        for value in (None, '9', True):
            self.assertEqual(score_state(value), 'invalid_type')
        for value in (float('nan'), float('inf'), -float('inf')):
            self.assertEqual(score_state(value), 'non_finite')
        self.assertEqual(score_state(-1), 'out_of_range')
        self.assertEqual(score_state(11), 'out_of_range')
        rows = [catalog_row(1, score=0), catalog_row(2, score=math.nan), catalog_row(3, score=10)]
        result = audit_rows(rows, reference_year=2026)
        self.assertEqual(result['score_states'], {'zero_ambiguous': 1, 'non_finite': 1, 'positive_public_rating': 1})
        self.assertFalse(result['rating_semantics']['confidence_correction_performed'])
        self.assertFalse(result['rating_semantics']['public_score_is_preference_probability'])
        json.dumps(result, allow_nan=False)

    def test_year_precision_invalid_dates_and_future_review_are_distinct(self):
        for value, state in (('', 'missing_year'), ('未定档', 'missing_year'), ('2026', 'year_only'),
            ('2026-01', 'quarter'), ('2026-4', 'quarter'), ('2026-03', 'non_quarter_month'),
            ('2024-02-29', 'dated'), ('2026-02-29', 'invalid_date'), ('2026-13', 'invalid_date'),
            ('year2026', 'invalid_format'), ('2050-01', 'year_needs_review')):
            self.assertEqual(season_state(value, reference_year=2026), state, value)

    def test_sparse_features_mapping_gaps_management_and_source_missing_are_separated_without_mutation(self):
        rows = [catalog_row(1, tags=''), catalog_row(2, tags='二刷,2026,日本'),
                catalog_row(3, tags='尚未定义题材'), catalog_row(4, tags='真百,轻百')]
        before = deepcopy(rows)
        data = audit_rows(rows, reference_year=2026)
        self.assertEqual(data['issue_counts']['raw_tags_missing'], 1)
        self.assertEqual(data['issue_counts']['only_non_genre_tags'], 1)
        self.assertEqual(data['issue_counts']['unmapped_tags_present'], 1)
        self.assertEqual(data['issue_counts']['sparse_genres_one'], 1)
        self.assertEqual(data['genre_document_frequency'], {'百合': 1})
        self.assertEqual(rows, before)
        self.assertNotIn('尚未定义题材', json.dumps(data, ensure_ascii=False))

    def test_exact_title_duplicates_do_not_establish_series_and_examples_are_bounded(self):
        rows = [catalog_row(i, title_zh='同名', title_jp='', tags='') for i in range(1, 20)]
        data = audit_rows(rows, reference_year=2026, example_limit=2)
        self.assertEqual(data['exact_duplicate_title_pair_groups'], 1)
        self.assertEqual(len(data['examples']), 2)
        self.assertNotIn('同名', json.dumps(data, ensure_ascii=False))
        self.assertEqual(data['catalog_fingerprint'], audit_rows(rows, reference_year=2026)['catalog_fingerprint'])

    def test_empty_results_are_not_zero_accuracy_or_identical_categories(self):
        context = {'mode': 'cold_start', 'seeds': [], 'preferences': {'tags': []}}
        metrics = top_metrics([], context, top_k=10)
        self.assertIsNone(metrics['topic_overlap_proxy_mean'])
        self.assertIsNone(metrics['pairwise_genre_jaccard_mean'])
        self.assertIsNone(metrics['evidence_valid_fraction'])
        differences = category_differences({'all': [], 'familiar': [], 'explore': []}, top_k=10)
        self.assertTrue(all(row['identical_order'] is None for row in differences.values()))


class QualityDatabaseTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='quality-owner-private')
        self.other = get_user_model().objects.create_user(username='quality-other-private')

    def seed(self, identity, *, user=None, score='9.0', tags=None, public_tags='日常,音乐', status='completed'):
        RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'作品{identity}', tags=public_tags, score=8, nsfw=False)
        shared = Anime.objects.create(bgm_id=identity, chinese_title=f'作品{identity}', base_tags=tags or ['日常', '音乐'])
        return UserAnimeRecord.objects.create(user=user or self.owner, anime=shared, watch_status=status,
                                             personal_score=score, review='PRIVATE_REVIEW_NEVER_QUERY_OR_EXPORT')

    def candidates(self):
        for identity in range(10, 25):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'作品{identity}', score=8, nsfw=False,
                tags='日常,音乐' if identity < 18 else '音乐,科幻')
        RecommendationAnime.objects.create(bangumi_id=90, title_zh='ADULT_SECRET_TITLE', tags='悬疑', nsfw=True)
        RecommendationAnime.objects.create(bangumi_id=91, title_zh='UNKNOWN_SECRET_TITLE', tags='魔法', nsfw=None)

    def index(self):
        return build_feature_index(catalog_snapshot()[1])

    def test_frozen_pre_refactor_v1_order_is_exact_and_shared_entry_matches_serving(self):
        for identity in (1, 2, 3):
            self.seed(identity)
        self.candidates()
        golden = {'all': [10, 18, 11, 12, 13, 14, 15, 19, 16, 17, 20, 21, 22, 23, 24],
                  'familiar': list(range(10, 25)), 'explore': list(range(18, 25))}
        context, index = user_context(self.owner), self.index()
        for category, expected in golden.items():
            options = {'category': category, 'type': 'all', 'min_score': 0, 'hide_recorded': True, 'page_size': 12}
            ranked = rank_context(context, options, index)[0]
            self.assertEqual([row['anime'].bangumi_id for row in ranked], expected)
            self.assertEqual([row['anime'].bangumi_id for row in rank_candidates(self.owner, options)[1]], expected)

    def test_evidence_checks_source_score_shared_tags_and_provenance_not_just_nonempty_text(self):
        for identity in (1, 2, 3):
            self.seed(identity)
        self.candidates()
        context = user_context(self.owner)
        item = rank_context(context, {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True}, self.index())[0][0]
        self.assertEqual(evidence_errors(item, context), [])
        for field, value in (('personal_score', '10.0'), ('bangumi_id', 9999), ('title', '虚构来源')):
            bad = deepcopy(item); bad['evidence']['source'][field] = value
            self.assertIn('source_not_a_trusted_personal_sample', evidence_errors(bad, context))
        bad = deepcopy(item); bad['evidence']['shared_tags'] = ['悬疑']
        self.assertTrue(evidence_errors(bad, context))
        bad = deepcopy(item); bad['evidence']['shared_tag_sources']['日常'] = ['review']
        self.assertIn('source_provenance_not_supported', evidence_errors(bad, context))
        bad = deepcopy(item); bad['reason'] = '虚构推荐理由'
        self.assertIn('rated_reason_not_supported', evidence_errors(bad, context))

    def test_holdout_removes_target_from_seeds_recorded_exclusion_and_cached_explanation_without_mutating(self):
        for identity in (1, 2, 3, 4):
            self.seed(identity)
        context = user_context(self.owner)
        context['explanation_seeds'] = list(context['seeds'])
        before = deepcopy(context)
        clone = without_seed(context, 1, minimum=3)
        self.assertNotIn(1, clone['owned'])
        self.assertNotIn(1, [seed['bangumi_id'] for seed in clone['seeds']])
        self.assertNotIn('explanation_seeds', clone)
        self.assertIsNone(without_seed(context, 1, minimum=4))
        self.assertEqual(context, before)

    def test_three_samples_baseline_ready_but_holdout_skips_and_never_lowers_policy(self):
        for identity in (1, 2, 3):
            self.seed(identity)
        self.candidates()
        result = evaluate_user(self.owner, self.index())
        self.assertEqual(result['mode'], 'personalized')
        self.assertEqual(result['holdout']['evaluated_targets'], 0)
        self.assertTrue(result['holdout']['status'].startswith('skipped_'))
        self.assertIsNone(result['holdout']['categories']['all']['hit_at_k'])

    def test_holdout_data_failures_stay_in_denominator_and_scores_are_never_edited(self):
        for identity in (1, 2, 3, 4):
            self.seed(identity, public_tags='' if identity == 1 else '日常,音乐')
        self.candidates()
        before = list(UserAnimeRecord.objects.values())
        result = evaluate_user(self.owner, self.index(), top_k=30, max_holdouts=4)
        self.assertEqual(result['holdout']['evaluated_targets'], 4)
        self.assertEqual(result['holdout']['targets_missing_catalog_genres'], 1)
        self.assertEqual(result['holdout']['categories']['all']['hit_at_k'], .75)
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)

    def test_explicit_only_and_other_owner_private_state_never_leak(self):
        self.seed(1, user=self.other)
        RecommendationPreference.objects.create(user=self.owner, tags=['科幻'])
        self.candidates()
        result = evaluate_user(self.owner, self.index())
        self.assertEqual(result['mode'], 'preference_based')
        self.assertEqual(result['usable_samples'], 0)
        self.assertTrue(all(row['evidence_error_count'] == 0 for row in result['categories'].values()))
        self.assertNotIn('personal_score', json.dumps(result))

    def test_synthetic_probes_are_explicitly_marked_and_not_a_fake_personalized_benchmark(self):
        self.candidates()
        with tempfile.TemporaryDirectory() as root:
            with patch('recommendations.quality_evaluation.user_context', side_effect=AssertionError('No private sample requested')):
                call_command('audit_recommendation_quality', include_probes=True, output_dir=Path(root), stdout=StringIO())
            report = json.loads((Path(root) / 'baseline.json').read_text(encoding='utf-8'))
            self.assertEqual(report['evaluation']['authorized_user_sample_count'], 0)
            self.assertEqual(report['evaluation']['synthetic_probe_count'], 3)
            self.assertFalse(report['privacy']['private_reports_when_samples_selected'])
            for probe in report['evaluation']['samples']:
                self.assertEqual(probe['sample_kind'], 'synthetic_preference_probe')
                self.assertEqual(probe['mode'], 'preference_based')
                self.assertEqual(probe['holdout']['status'], 'not_applicable_synthetic_probe')

    def test_baseline_fingerprint_repeatable_for_same_inputs_not_generated_time(self):
        self.candidates()
        with tempfile.TemporaryDirectory() as root:
            reports = []
            for folder in ('one', 'two'):
                path = Path(root) / folder
                call_command('audit_recommendation_quality', include_probes=True, output_dir=path, stdout=StringIO())
                reports.append(json.loads((path / 'baseline.json').read_text(encoding='utf-8')))
            self.assertEqual(reports[0]['baseline_fingerprint'], reports[1]['baseline_fingerprint'])

    def test_command_no_private_samples_by_default_and_no_cache_or_external_calls(self):
        self.seed(1); self.candidates()
        with tempfile.TemporaryDirectory() as root:
            with (patch('recommendations.quality_evaluation.user_context', side_effect=AssertionError('No private reads by default')),
                  patch('recommendations.engine.cache.set', side_effect=AssertionError('No cache writes')),
                  patch('requests.sessions.Session.request', side_effect=AssertionError('No external requests')),
                  CaptureQueriesContext(connection) as queries):
                call_command('audit_recommendation_quality', output_dir=Path(root), stdout=StringIO())
            data = json.loads((Path(root) / 'baseline.json').read_text(encoding='utf-8'))
            self.assertEqual(data['evaluation']['status'], 'not_requested')
            self.assertEqual(data['catalog']['scope']['adult'], 1)
            self.assertEqual(data['catalog']['scope']['unknown'], 1)
            self.assertFalse(any('anime_useranimerecord' in row['sql'] for row in queries))
            self.assertNotIn('ADULT_SECRET_TITLE', json.dumps(data))
            manifest = json.loads((Path(root) / 'manifest.json').read_text())
            self.assertTrue(manifest['complete'])
            for name, expected in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256((Path(root) / name).read_bytes()).hexdigest(), expected)
            with self.assertRaises(CommandError):
                call_command('audit_recommendation_quality', output_dir=Path(root), stdout=StringIO())

    def test_missing_ids_are_audited_but_never_enter_baseline_ranking(self):
        for identity in (1, 2, 3):
            self.seed(identity)
        RecommendationAnime.objects.create(title_zh='无身份条目', nsfw=False, tags='日常', score=8)
        with tempfile.TemporaryDirectory() as root:
            call_command('audit_recommendation_quality', user_id=[self.owner.pk], output_dir=Path(root), stdout=StringIO())
            report = json.loads((Path(root) / 'baseline.json').read_text(encoding='utf-8'))
            self.assertEqual(report['catalog']['scope']['safe_missing_identity'], 1)
            self.assertEqual(report['catalog']['issue_counts']['missing_identity'], 1)
            self.assertEqual(report['catalog']['ranking_eligible_identity_rows'], 3)

    def test_command_user_samples_are_readonly_no_review_queries_private_exports_or_record_changes(self):
        for identity in (1, 2, 3, 4):
            self.seed(identity)
        self.candidates()
        before = list(UserAnimeRecord.objects.values())
        with tempfile.TemporaryDirectory() as root:
            with (patch('requests.sessions.Session.request', side_effect=AssertionError('No external requests')),
                  patch('recommendations.engine.cache.set', side_effect=AssertionError('No cache writes')),
                  CaptureQueriesContext(connection) as queries):
                call_command('audit_recommendation_quality', user_id=[self.owner.pk], max_holdouts=2,
                             output_dir=Path(root), stdout=StringIO())
            data = json.loads((Path(root) / 'baseline.json').read_text(encoding='utf-8'))
            self.assertEqual(data['evaluation']['sample_count'], 1)
            self.assertEqual(data['evaluation']['samples'][0]['holdout']['evaluated_targets'], 2)
            self.assertFalse(any('"review"' in row['sql'] for row in queries))
            self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for row in queries))
            contents = ''.join(path.read_text(encoding='utf-8') for path in Path(root).iterdir())
            self.assertNotIn('PRIVATE_REVIEW_NEVER_QUERY_OR_EXPORT', contents)
            self.assertNotIn(self.owner.username, contents)
            self.assertNotIn('作品1', contents)
            self.assertEqual(list(UserAnimeRecord.objects.values()), before)
            self.assertEqual(RecommendationPreference.objects.count(), 0)

    def test_database_guard_rejects_accidental_writes_and_restores_connection(self):
        self.seed(1)
        with self.assertRaises(DatabaseError):
            with readonly_snapshot():
                RecommendationAnime.objects.filter(bangumi_id=1).update(score=1)
        self.assertEqual(RecommendationAnime.objects.get(bangumi_id=1).score, 8)
        # Normal test-fixture writes work again after the guarded scope.
        RecommendationAnime.objects.filter(bangumi_id=1).update(score=7)
        self.assertEqual(RecommendationAnime.objects.get(bangumi_id=1).score, 7)

    def test_cli_bounds_unknown_users_and_no_implicit_overwrite(self):
        for kwargs in ({'top_k': 31}, {'max_holdouts': 0}, {'user_id': [-1]},
                       {'user_id': list(range(1, 12))}, {'user_id': [99999]}, {'statement_timeout_ms': 1}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as root, self.assertRaises(CommandError):
                call_command('audit_recommendation_quality', output_dir=Path(root), stdout=StringIO(), **kwargs)
            self.assertEqual(RecommendationPreference.objects.count(), 0)
