from copy import deepcopy
from io import StringIO
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from recommendations.engine import build_feature_index, candidate_score, rank_context
from recommendations.features import extract_genres, feature_tags
from recommendations.models import RecommendationAnime
from recommendations.tag_coverage import analyze_public_tags, coverage_markdown, review_document
from recommendations.tag_review_rules import classify_unknown, PENDING_ALIASES, validate_proposals
from recommendations.taxonomy import ALIASES, AVAILABLE_TAGS, TAXONOMY_VERSION, canonical_tag


def row(identity=1, **changes):
    return {'bangumi_id': identity, 'title_zh': f'公开作品{identity}', 'title_jp': '',
            'tags': '', 'status_type': 'TV', **changes}


def analyze(rows, **kwargs):
    return analyze_public_tags(rows, {'total': len(rows), 'non_adult': len(rows), 'adult': 0, 'unknown': 0}, **kwargs)


class TagCoveragePrimitiveTests(SimpleTestCase):
    def test_draft_proposals_do_not_activate_existing_taxonomy_or_preferences(self):
        before = deepcopy(ALIASES)
        validate_proposals()
        for source, target in PENDING_ALIASES.items():
            self.assertIn(target, AVAILABLE_TAGS)
            self.assertIsNone(canonical_tag(source))
            self.assertEqual(feature_tags(source), frozenset())
        self.assertEqual(ALIASES, before)
        self.assertEqual(TAXONOMY_VERSION, 'genres.v3')
        self.assertEqual(canonical_tag('搞笑'), '喜剧')

    def test_actual_coverage_unchanged_but_hypothesis_is_labelled_and_deduplicated(self):
        rows = [row(1, tags='SPORTS, sports,ｓｐｏｒｔｓ'), row(2, tags='运动,SPORTS'),
                row(3, tags='热血'), row(4, tags=''), row(5, tags='TV,2026,日本')]
        old = deepcopy(rows)
        result = analyze(rows)
        self.assertEqual(result['before']['with_effective_genres'], 1)
        self.assertEqual(result['after_active'], result['before'])
        self.assertEqual(result['draft_synonyms_simulation']['with_effective_genres'], 2)
        self.assertEqual(result['draft_impact']['recovered_zero_genre_works'], 1)
        self.assertEqual(result['draft_impact']['enriched_works'], 1)
        self.assertEqual(result['draft_impact']['new_genre_document_frequency'], {'运动': 1})
        sports = next(item for item in result['unknown_tags'] if item['normalized_key'] == 'sports')
        self.assertEqual(sports['document_frequency'], 2)
        self.assertEqual(sports['occurrences'], 4)
        self.assertEqual(sports['zero_genre_document_frequency'], 1)
        self.assertEqual(rows, old)
        self.assertFalse(result['policy']['mapping_applied'])
        self.assertNotIn('热血', result['draft_impact']['new_genre_document_frequency'])
        self.assertEqual(result['candidate_genre_observation']['zero_genre_works_with_candidate_labels'], 1)

    def test_empty_management_title_and_mapping_target_title_never_become_features(self):
        rows = [row(1, tags=''), row(2, tags='二刷,2026,日本,神作'),
                row(3, title_zh='SPORTS', tags='SPORTS'), row(4, title_zh='运动', tags='sports')]
        report = analyze(rows)
        self.assertEqual(report['before']['with_effective_genres'], 0)
        self.assertEqual(report['draft_synonyms_simulation']['with_effective_genres'], 0)
        self.assertEqual(report['baseline_zero_genre_breakdown']['raw_tags_missing'], 1)
        self.assertEqual(report['baseline_zero_genre_breakdown']['only_non_genre_tags'], 2)
        self.assertEqual(report['baseline_zero_genre_breakdown']['unmapped_tags_present'], 1)
        keys = {item['normalized_key'] for item in report['unknown_tags']}
        self.assertEqual(keys, {'sports'})
        self.assertEqual(extract_genres([], [])['tags'], frozenset())

    def test_three_classes_exact_labels_never_guess_from_names_or_cooccurrence(self):
        for value, category, target in (('音樂', 'pending_synonym', '音乐'),
            ('玄幻', 'candidate_genre', None), ('穿越', 'candidate_genre', None),
            ('魔法少女', 'candidate_genre', None), ('萝卜', 'excluded_or_unknown', None),
            ('SF', 'excluded_or_unknown', None), ('TV', 'excluded_or_unknown', None),
            ('SUNRISE', 'excluded_or_unknown', None), ('全新陌生词', 'excluded_or_unknown', None)):
            data = classify_unknown(value)
            self.assertEqual((data['category'], data['target']), (category, target))
        result = analyze([row(tags='全新陌生词,TV', title_zh='音乐魔法少女 第2季')])
        self.assertEqual(result['draft_synonyms_simulation']['with_effective_genres'], 0)
        self.assertEqual(result['unknown_categories'], {'excluded_or_unknown': 2})

    def test_unknown_frequencies_cover_all_works_and_zero_genre_subset_separately(self):
        data = analyze([row(1, tags='TV'), row(2, tags='日常,TV'), row(3, tags='TV,TV', status_type='WEB')])
        token = data['unknown_tags'][0]
        self.assertEqual(token['document_frequency'], 3)
        self.assertEqual(token['zero_genre_document_frequency'], 2)
        self.assertEqual(token['occurrences'], 4)
        self.assertEqual(token['type_distribution'], {'TV': 2, 'WEB': 1})
        self.assertEqual(data['per_type']['TV']['before']['with_effective_genres'], 1)

    def test_valid_identity_subscope_and_empty_catalog_not_fake_zero_coverage(self):
        result = analyze([row(None, tags='sports'), row(0, tags='sports'), row(1, tags='日常')])
        self.assertEqual(result['draft_synonyms_simulation']['with_effective_genres'], 3)
        self.assertEqual(result['valid_identity_coverage']['draft_synonyms_simulation']['works'], 1)
        empty = analyze([])
        self.assertIsNone(empty['before']['coverage_fraction'])
        self.assertEqual(empty['unknown_tags'], [])
        self.assertIn('不可计算', coverage_markdown(empty))

    def test_samples_bounded_and_safety_metadata_conflicts_are_not_exported_as_titles(self):
        report = analyze([row(i, tags='sports') for i in range(1, 12)], example_limit=2)
        self.assertEqual(len(report['unknown_tags'][0]['examples']), 2)
        self.assertEqual(len(report['unknown_tags'][0]['zero_genre_examples']), 2)
        self.assertEqual(analyze([row(tags='sports')], example_limit=0)['unknown_tags'][0]['examples'], [])
        data = analyze([row(tags='sports,里番', title_zh='SAFETY_REVIEW_TITLE')])
        self.assertEqual(data['safety_metadata_review_works'], 1)
        self.assertNotIn('SAFETY_REVIEW_TITLE', json.dumps(data, ensure_ascii=False))
        self.assertTrue(all(not item['examples'] for item in data['unknown_tags']))
        self.assertTrue(all(not item['zero_genre_examples'] for item in data['unknown_tags']))

    def test_problem_subset_samples_are_not_lost_when_first_examples_already_have_genres(self):
        data = analyze([row(1, tags='日常,TV'), row(2, tags='日常,TV'), row(3, tags='TV')], example_limit=1)
        token = data['unknown_tags'][0]
        self.assertEqual(token['examples'][0]['bangumi_id'], 1)
        self.assertEqual(token['zero_genre_examples'][0]['bangumi_id'], 3)

    def test_candidate_observation_counts_each_work_once_without_simulating_new_topics(self):
        data = analyze([row(1, tags='热血,穿越,热血'), row(2, tags='日常,热血')])
        self.assertEqual(data['candidate_genre_observation']['works_with_candidate_labels'], 2)
        self.assertEqual(data['candidate_genre_observation']['zero_genre_works_with_candidate_labels'], 1)
        self.assertEqual(data['draft_synonyms_simulation'], data['before'])

    def test_already_active_mappings_are_not_mislabeled_as_new_review_items(self):
        report = analyze([row(tags='搞笑,喜剧,真百,轻百')])
        self.assertEqual(report['before']['genre_count_histogram'], {'2': 1})
        self.assertEqual(report['draft_impact']['enriched_works'], 0)
        self.assertEqual(len(report['already_active_aliases']), 3)
        self.assertEqual(review_document(report)['pending_synonyms'], [])

    def test_fingerprint_stable_and_tracks_input_rules_and_sample_configuration(self):
        first, second = analyze([row(tags='music')]), analyze([row(tags='music')])
        self.assertEqual(first['report_fingerprint'], second['report_fingerprint'])
        self.assertNotEqual(first['report_fingerprint'], analyze([row(tags='music')], example_limit=0)['report_fingerprint'])
        self.assertNotEqual(first['report_fingerprint'], analyze([row(tags='Music')])['report_fingerprint'])

    def test_untrusted_public_markdown_is_escaped_not_rendered_as_markup(self):
        report = analyze([row(tags='<script>x</script>|[link](https://example.org)\n# fake')])
        text = coverage_markdown(report)
        self.assertNotIn('<script>', text)
        self.assertNotIn('\n# fake', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertIn('\\|', text)


class TagCoverageDatabaseTests(TransactionTestCase):
    def setUp(self):
        RecommendationAnime.objects.create(bangumi_id=1, title_zh='安全公共作品', tags='sports,TV', nsfw=False)
        RecommendationAnime.objects.create(bangumi_id=2, title_zh='ADULT_SECRET_TITLE', tags='ADULT_SECRET_TAG', nsfw=True)
        RecommendationAnime.objects.create(bangumi_id=3, title_zh='UNKNOWN_SECRET_TITLE', tags='UNKNOWN_SECRET_TAG', nsfw=None)

    def test_command_readonly_public_only_no_external_calls_cache_or_source_mutations(self):
        original = list(RecommendationAnime.objects.values())
        with tempfile.TemporaryDirectory() as root:
            with (CaptureQueriesContext(connection) as queries,
                patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP')),
                patch('recommendations.engine.cache.set', side_effect=AssertionError('No cache writes'))):
                call_command('audit_recommendation_tags', output_dir=Path(root), stdout=StringIO())
            coverage = json.loads((Path(root) / 'coverage.json').read_text(encoding='utf-8'))
            self.assertEqual(coverage['scope'], {'total': 3, 'non_adult': 1, 'adult': 1, 'unknown': 1})
            self.assertFalse(coverage['policy']['mapping_applied'])
            contents = ''.join(path.read_text(encoding='utf-8') for path in Path(root).iterdir())
            for secret in ('ADULT_SECRET', 'UNKNOWN_SECRET'):
                self.assertNotIn(secret, contents)
            sql = ' '.join(query['sql'] for query in queries).lower()
            for forbidden in ('anime_useranimerecord', 'recommendationpreference', 'auth_user', 'synopsis', 'review', 'cover_url', '"score"'):
                self.assertNotIn(forbidden, sql)
            self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for query in queries))
            manifest = json.loads((Path(root) / 'manifest.json').read_text())
            for name, digest in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256((Path(root) / name).read_bytes()).hexdigest(), digest)
            review = json.loads((Path(root) / 'mapping-review.json').read_text(encoding='utf-8'))
            self.assertEqual(review['status'], 'pending_human_review')
            self.assertEqual(review['pending_synonyms'][0]['decision'], 'pending_review')
            self.assertFalse(review['pending_synonyms'][0]['apply_to_production'])
        self.assertEqual(list(RecommendationAnime.objects.values()), original)

    def test_cli_bounds_no_overwrite_and_timeout_validation(self):
        for changes in ({'example_limit': -1}, {'example_limit': 6}, {'statement_timeout_ms': 1}):
            with tempfile.TemporaryDirectory() as root, self.assertRaises(CommandError):
                call_command('audit_recommendation_tags', output_dir=Path(root), stdout=StringIO(), **changes)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            call_command('audit_recommendation_tags', output_dir=path, stdout=StringIO())
            before = (path / 'manifest.json').read_bytes()
            with self.assertRaises(CommandError):
                call_command('audit_recommendation_tags', output_dir=path, stdout=StringIO())
            self.assertEqual((path / 'manifest.json').read_bytes(), before)

    def test_catalog_budget_fails_closed_without_partial_success_report(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('recommendations.tag_coverage.MAX_CATALOG_ROWS', 0), self.assertRaises(CommandError):
                call_command('audit_recommendation_tags', output_dir=Path(root), stdout=StringIO())
            self.assertFalse((Path(root) / 'manifest.json').exists())

    def test_report_edits_do_not_activate_draft_and_empty_genres_stay_out_of_v1_pool(self):
        # Even after offline simulation, V1 sees the original tags, not a repaired
        # candidate copy. No evidence can be manufactured from an empty feature set.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            call_command('audit_recommendation_tags', output_dir=path, stdout=StringIO())
            review = json.loads((path / 'mapping-review.json').read_text(encoding='utf-8'))
            review['pending_synonyms'][0]['decision'] = 'approved'
            review['pending_synonyms'][0]['apply_to_production'] = True
            self.assertIsNone(canonical_tag('sports'))
            index = build_feature_index([{'bangumi_id': 1, 'title_zh': '安全公共作品', 'title_jp': '',
                                         'tags': 'sports,TV', 'score': 8, 'status_type': 'TV'}])
            self.assertFalse(index['features'][0][1])
            self.assertIsNone(candidate_score(set(), 8, {'音乐': 1}, {'音乐': 1}, 'all'))
            context = {'mode': 'preference_based', 'seeds': [], 'preferences': {'tags': ['音乐']}, 'owned': {}}
            self.assertEqual(rank_context(context, {'hide_recorded': True, 'category': 'all', 'min_score': 0, 'type': 'all'}, index)[0], [])
            self.assertEqual(RecommendationAnime.objects.get(bangumi_id=1).tags, 'sports,TV')
