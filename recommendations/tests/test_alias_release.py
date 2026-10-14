from collections import Counter
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from recommendations import engine
from recommendations.features import extract_genres, FEATURE_RULE_VERSION
from recommendations.models import RecommendationAnime, RecommendationPreference
from recommendations.quality_evaluation import evidence_errors
from recommendations.tag_coverage import (_coverage, analyze_public_tags, attach_release_comparison,
                                           coverage_markdown, write_coverage_reports)
from recommendations.tag_review_rules import GENRE_CANDIDATES, PENDING_ALIASES
from recommendations.taxonomy import (ALIASES, APPROVED_ALIASES, AVAILABLE_TAGS, TAXONOMY_VERSION,
                                      canonical_tag, validate_preference_tags)

EXPECTED_APPROVED = {'治愈系': '治愈', '治癒': '治愈', 'comedy': '喜剧', '日常系': '日常',
    '戀愛': '恋爱', 'music': '音乐', '戰鬥': '战斗', '旅行系': '旅行',
    '校園': '校园', '百合向': '百合', '職場': '职场'}


class AliasReleasePrimitiveTests(SimpleTestCase):
    def test_only_the_11_approved_aliases_retained_with_seven_approved_genres(self):
        self.assertEqual(APPROVED_ALIASES, EXPECTED_APPROVED)
        self.assertEqual(len(ALIASES), 21)  # 10 pre-existing + exactly 11 approved.
        self.assertEqual(len(AVAILABLE_TAGS), 33)
        self.assertEqual(TAXONOMY_VERSION, 'genres.v4')
        self.assertEqual(FEATURE_RULE_VERSION, 'journal-genres.v3')
        self.assertEqual(engine.ALGORITHM_VERSION, 'content.v1')
        for source, target in EXPECTED_APPROVED.items():
            self.assertEqual(canonical_tag(source), target)
        for source in (*PENDING_ALIASES, *GENRE_CANDIDATES):
            self.assertIsNone(canonical_tag(source), source)

    def test_nfkc_case_and_aliases_count_once_per_work_without_rewriting_inputs(self):
        base = ['MUSIC', 'ｍｕｓｉｃ', '音乐', 'Comedy', '喜剧', '治愈系', '治癒']
        custom = ['music', '喜剧', '治愈', '二刷', '神作', '未知', '2026', '日本']
        previous = deepcopy((base, custom))
        extracted = extract_genres(base, custom)
        self.assertEqual(extracted['tags'], {'音乐', '喜剧', '治愈'})
        for sources in extracted['sources'].values():
            self.assertEqual(sources, ('private_base_tags', 'custom_tags'))
        self.assertEqual((base, custom), previous)
        self.assertIsNone(canonical_tag('不喜欢MUSIC'))
        self.assertIsNone(canonical_tag('日常系2'))

    def test_source_or_mapped_target_equal_to_own_title_stays_excluded(self):
        for label, title in (('MUSIC', 'MUSIC'), ('MUSIC', '音乐'), ('日常系', '日常'), ('Comedy', '喜剧')):
            result = extract_genres([label], titles=(title,))
            self.assertFalse(result['tags'])
            self.assertEqual(result['ignored_counts']['work_title'], 1)
        self.assertFalse(extract_genres([], [])['tags'])

    def test_preference_payload_still_requires_canonical_choices_not_alias_writes(self):
        validate_preference_tags(['音乐', '喜剧'])
        for tags in (['MUSIC'], ['BL'], ['百合', '百合']):
            with self.assertRaises(ValidationError):
                validate_preference_tags(tags)


class AliasReleaseAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username='alias-owner')
        self.other = get_user_model().objects.create_user(username='alias-other')
        self.client.force_authenticate(self.owner)
        for identity in (1, 2, 3):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'来源作品{identity}',
                tags='MUSIC', score=8, nsfw=False)
            shared = Anime.objects.create(bgm_id=identity, chinese_title=f'来源作品{identity}', base_tags=['MUSIC'])
            UserAnimeRecord.objects.create(user=self.owner, anime=shared, watch_status='completed',
                personal_score='9.0', custom_tags=['music', '音乐'], review='PRIVATE_REVIEW_MUST_REMAIN_UNCHANGED')
        for identity, tags, rating in ((10, 'MUSIC', False), (11, '音乐', False), (12, 'MUSIC,日常', False),
                                       (90, 'MUSIC', True), (91, 'MUSIC', None), (92, '热血', False)):
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'候选作品{identity}',
                                                tags=tags, score=8, nsfw=rating)

    def test_profile_and_evidence_use_aliases_and_preserve_all_stored_rows(self):
        preference = RecommendationPreference.objects.create(user=self.owner, tags=['科幻'], version=7)
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        shared_before = list(Anime.objects.order_by('pk').values())
        catalog_before = list(RecommendationAnime.objects.order_by('pk').values())
        saved_preferences = RecommendationPreference.objects.filter(pk=preference.pk).values().get()
        with CaptureQueriesContext(connection) as queries:
            profile = self.client.get('/api/recommendations/profile/').data
            result = self.client.get('/api/recommendations/').data
        self.assertFalse(profile['cold_start'])
        self.assertEqual(profile['usable_sample_count'], 3)
        self.assertEqual(profile['tag_summary'], [{'tag': '音乐', 'sample_count': 3}])
        self.assertEqual(profile['extraction']['explicit_preferences']['tags'], ['科幻'])
        self.assertEqual(result['algorithm_version'], 'content.v1')
        self.assertEqual(result['taxonomy_version'], 'genres.v4')
        self.assertEqual({item['bangumi_id'] for item in result['results']}, {10, 11, 12})
        for item in result['results']:
            self.assertEqual(item['evidence']['feature_rule_version'], FEATURE_RULE_VERSION)
            self.assertEqual(item['evidence']['shared_tag_sources']['音乐'], ['private_base_tags', 'custom_tags'])
        context = engine.user_context(self.owner)
        options = {'category': 'all', 'type': 'all', 'min_score': 0, 'hide_recorded': True, 'page_size': 12}
        for item in engine.rank_candidates(self.owner, options)[1]:
            self.assertEqual(evidence_errors(item, context), [])
        self.assertFalse(any('"review"' in query['sql'] for query in queries))
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), before)
        self.assertEqual(list(Anime.objects.order_by('pk').values()), shared_before)
        self.assertEqual(list(RecommendationAnime.objects.order_by('pk').values()), catalog_before)
        self.assertEqual(RecommendationPreference.objects.filter(pk=preference.pk).values().get(), saved_preferences)

    def test_intentionally_empty_private_tags_do_not_fallback_to_new_catalog_aliases(self):
        UserAnimeRecord.objects.filter(user=self.owner).update(private_base_tags=[], custom_tags=[])
        profile = self.client.get('/api/recommendations/profile/').data
        self.assertEqual(profile['usable_sample_count'], 0)
        self.assertTrue(profile['cold_start'])
        self.assertEqual(profile['tag_summary'], [])
        self.assertEqual(self.client.get('/api/recommendations/').data['results'], [])

    def test_other_user_and_unsafe_details_cannot_reuse_owner_alias_profile(self):
        self.client.force_authenticate(self.other)
        profile = self.client.get('/api/recommendations/profile/').data
        self.assertEqual(profile['usable_sample_count'], 0)
        self.assertEqual(self.client.get('/api/recommendations/').data['results'], [])
        self.assertIsNone(self.client.get('/api/recommendations/10/').data['evidence'])
        for identity in (90, 91):
            self.assertEqual(self.client.get(f'/api/recommendations/{identity}/').status_code, 404)
            self.assertEqual(self.client.post(f'/api/recommendations/{identity}/want/', {}, format='json').status_code, 404)

    def test_old_cache_namespace_and_old_signature_are_not_reused(self):
        stale = {'signature': 'old-signature', 'features': [], 'weights': {}}
        cache.set('recommendations.public-features.v2', stale)
        index = engine.public_features()
        self.assertIn('音乐', next(value[1] for value in index['features'] if value[0] == 10))
        self.assertEqual(cache.get('recommendations.public-features.v2'), stale)
        self.assertEqual(index['signature'][-3:], (TAXONOMY_VERSION, engine.FEATURE_VERSION, FEATURE_RULE_VERSION))
        # Same data revision but missing extraction-rule version is still stale.
        cache.set(engine.CACHE_KEY, {**stale, 'signature': index['signature'][:-1]})
        self.assertTrue(engine.public_features()['features'])

    def test_version_change_invalidates_page_two_even_when_items_are_identical(self):
        for attribute, old_version in (('TAXONOMY_VERSION', 'genres.v2'),
            ('FEATURE_VERSION', 'genres-idf.v2'), ('FEATURE_RULE_VERSION', 'journal-genres.v1')):
            with self.subTest(attribute=attribute):
                with patch(f'recommendations.engine.{attribute}', old_version):
                    token = self.client.get('/api/recommendations/', {'page_size': 1}).data['state_token']
                stale = self.client.get('/api/recommendations/', {'page_size': 1, 'page': 2, 'state_token': token})
                self.assertEqual(stale.status_code, 409)
        first = self.client.get('/api/recommendations/', {'page_size': 1}).data
        self.assertEqual(self.client.get('/api/recommendations/', {
            'page_size': 1, 'page': 2, 'state_token': first['state_token']}).status_code, 200)


def comparison_fixture():
    rows = [{'bangumi_id': 1, 'title_zh': '公开作品', 'title_jp': '', 'tags': 'MUSIC', 'status_type': 'TV'}]
    return analyze_public_tags(rows, {'total': 1, 'non_adult': 1, 'adult': 0, 'unknown': 0})


def preapproval_fixture(current):
    # Controlled old-report fixture for comparison validation, not production
    # measurements. Actual deployment verification uses the preserved 5B-1 run.
    old = deepcopy(current)
    old['versions']['active_taxonomy'] = 'genres.v2'
    old['before'] = _coverage(Counter({0: 1}))
    old['after_active'] = deepcopy(old['before'])
    return old


class AliasReleaseReportTests(SimpleTestCase):
    def test_actual_comparison_requires_verified_manifest_and_same_catalog_snapshot(self):
        current = comparison_fixture()
        with tempfile.TemporaryDirectory() as root:
            write_coverage_reports(preapproval_fixture(current), root)
            attach_release_comparison(current, root)
        comparison = current['approved_release_comparison']
        self.assertEqual(comparison['additional_covered_works'], 1)
        self.assertTrue(comparison['identical_catalog_snapshot'])
        self.assertIn('同目录实际前后对比', coverage_markdown(current))
        self.assertFalse(current['policy']['mapping_applied'])

    def test_changed_catalog_bad_checksum_or_inconsistent_baseline_rejected(self):
        for change in ('different_catalog', 'bad_digest', 'bad_histogram', 'wrong_version'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                current = comparison_fixture()
                old = preapproval_fixture(current)
                if change == 'different_catalog':
                    old['catalog_fingerprint'] = 'different'
                if change == 'wrong_version':
                    old['versions']['active_taxonomy'] = 'genres.v4'
                if change == 'bad_histogram':
                    old['before']['works'] = 99
                    old['after_active']['works'] = 99
                write_coverage_reports(old, root)
                if change == 'bad_digest':
                    manifest = Path(root) / 'manifest.json'
                    # Test-local fixture mutation only; no database or source writes.
                    with manifest.open('w', encoding='utf-8') as file:
                        json.dump({'complete': True, 'contract_version': current['contract_version'],
                                   'sha256': {'coverage.json': 'wrong'}}, file)
                with self.assertRaises(ValueError):
                    attach_release_comparison(current, root)
                self.assertNotIn('approved_release_comparison', current)


class AliasReleaseCommandTests(TransactionTestCase):
    def test_baseline_mismatch_produces_no_completed_output(self):
        with tempfile.TemporaryDirectory() as root:
            base, output = Path(root) / 'base', Path(root) / 'new'
            write_coverage_reports(preapproval_fixture(comparison_fixture()), base)
            with self.assertRaises(CommandError):
                call_command('audit_recommendation_tags', compare_before_dir=base, output_dir=output, stdout=StringIO())
            self.assertFalse((output / 'manifest.json').exists())
