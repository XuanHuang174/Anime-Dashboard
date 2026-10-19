"""Public bootstrap IO parity on synthetic isolated PostgreSQL only."""
from copy import deepcopy
import gzip
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import transaction
from django.test import TransactionTestCase, override_settings

from anime.models import UserAnimeRecord
from recommendations import browsing, engine, cold_public as cold, discovery_development as development
from recommendations.execution import execute_v2
from recommendations.exploration_candidate import PROTECTED_VERSION
from recommendations.models import RecommendationAnime
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.version_control import sign_artifact, read_artifact
from recommendations.v2_controls import validate_options
from recommendations.tests import test_unused_validation as fixtures


class ColdPublicTests(TransactionTestCase):
    setUp = fixtures.UnusedPostgresTests.setUp

    def inputs(self):
        context = engine.user_context(self.owner)
        with override_settings(RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED=False):
            rows, index = browsing.public_browse_index(browsing.catalog_revision(), {})
        return context, rows, index

    def test_compact_revision_identical_and_detects_unsafe_metadata_unicode_changes(self):
        for changes in ({'title_zh': '合成🦊 ":\n', 'tags': '音乐,悬疑'}, {'nsfw': True}, {'nsfw': None}, {'score': 8.125}):
            RecommendationAnime.objects.filter(bangumi_id=16).update(**changes)
            with readonly_snapshot():
                self.assertEqual(cold.compact_catalog_revision(), browsing.catalog_revision())

    def test_seeded_first_call_matches_protected_all_categories_styles_without_full_scan(self):
        RecommendationAnime.objects.filter(bangumi_id__gte=15).update(season='2026-01', status_type='TV')
        context, rows, index = self.inputs()
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        for category in ('all', 'familiar', 'explore'):
            for style in ('balanced', 'close', 'open'):
                options = {'category': category, 'style': style, 'year_from': 2020, 'year_to': 2026}
                with readonly_snapshot():
                    baseline = execute_v2(self.owner, options, index, context=context, exploration_policy_version=PROTECTED_VERSION)[0]
                    reader = cold.SeededPublicReader(rows)
                    current, _, meta = execute_v2(self.owner, options, index, context=context,
                        exploration_policy_version=PROTECTED_VERSION, public_reader=reader)
                self.assertEqual(output_digest(baseline), output_digest(current))
                self.assertEqual(reader.last_read['transferred_rows'], 1)
                self.assertFalse(meta['serving_enabled'])
                self.assertEqual(meta['public_read_version'], cold.VERSION)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))

    def test_float_bits_unicode_and_negative_zero_match_database_digest(self):
        for score in (-0.0, 7.125, 1.0000000000000002):
            RecommendationAnime.objects.filter(bangumi_id=16).update(score=score, season='2026-01', status_type='TV', title_jp='\n🦊')
            context, rows, index = self.inputs()
            with readonly_snapshot():
                baseline = development.run(self.owner, context, index, 'all', 'protected_v2')[0]
                result, timing = development.run(self.owner, context, index, 'all', 'protected_v2', public_reader=cold.SeededPublicReader(rows))
            self.assertEqual(output_digest(baseline), output_digest(result))
            self.assertEqual(timing['sql_queries'], 4)

    def test_stale_seed_fails_closed_for_score_tags_season_type_and_safety(self):
        for changes in ({'score': 9.2}, {'tags': '科幻,音乐'}, {'season': '2026-07'},
                        {'status_type': 'OVA'}, {'nsfw': True}, {'nsfw': None}):
            RecommendationAnime.objects.filter(bangumi_id=16).update(score=7, tags='音乐,成长', season='', status_type='', nsfw=False)
            context, rows, index = self.inputs(); reader = cold.SeededPublicReader(rows)
            RecommendationAnime.objects.filter(bangumi_id=16).update(**changes)
            with readonly_snapshot(), self.assertRaises(engine.RecommendationSnapshotChanged):
                development.run(self.owner, context, index, 'all', 'protected_v2', public_reader=reader)

    def test_membership_swap_same_count_rejected(self):
        RecommendationAnime.objects.filter(bangumi_id=17).update(nsfw=True)
        context, rows, index = self.inputs(); reader = cold.SeededPublicReader(rows)
        RecommendationAnime.objects.filter(bangumi_id=16).update(nsfw=True)
        RecommendationAnime.objects.filter(bangumi_id=17).update(nsfw=False)
        with readonly_snapshot(), self.assertRaises(engine.RecommendationSnapshotChanged):
            development.run(self.owner, context, index, 'all', 'protected_v2', public_reader=reader)

    def test_filtered_scopes_bounded_and_strict_unknown_query_fails(self):
        RecommendationAnime.objects.filter(bangumi_id__gte=15).update(season='2026-04', status_type='TV')
        context, rows, index = self.inputs(); reader = cold.SeededPublicReader(rows)
        for score in (0, 1, 2, 3, 4, 5):
            options = {'min_score': score, 'type': 'TV', 'year_from': 2026, 'year_to': 2026}
            with readonly_snapshot():
                old = execute_v2(self.owner, options, index, context=context)[0]
                new = execute_v2(self.owner, options, index, context=context, public_reader=reader)[0]
            self.assertEqual(output_digest(old), output_digest(new))
        self.assertLessEqual(len(reader._scopes), 4)
        with readonly_snapshot(), self.assertRaises(engine.RecommendationSnapshotChanged):
            reader.read(engine.safe_catalog().filter(bangumi_id=16), validate_options({}), engine.public_feature_expression(), engine.MAX_CATALOG_ROWS)

    def test_empty_catalog_revision_and_seed_validated(self):
        RecommendationAnime.objects.update(nsfw=True)
        reader = cold.SeededPublicReader([])
        with readonly_snapshot():
            self.assertEqual(cold.compact_catalog_revision(), browsing.catalog_revision())
            self.assertEqual(reader.read(engine.safe_catalog(), validate_options({}), engine.public_feature_expression(), engine.MAX_CATALOG_ROWS), ([], {}))

    def test_writable_serving_invalid_rows_and_path_rejected(self):
        context, rows, index = self.inputs(); reader = cold.SeededPublicReader(rows)
        with transaction.atomic(), self.assertRaises(ValueError):
            reader.read(engine.safe_catalog(), validate_options({}), engine.public_feature_expression(), engine.MAX_CATALOG_ROWS)
        with transaction.atomic(), self.assertRaises(ValueError):
            cold.compact_catalog_revision()
        with self.assertRaises(engine.RecommendationUnavailable):
            execute_v2(self.owner, {}, index, context=context, serving_enabled=True, public_reader=reader)
        for invalid in ([rows[0], rows[0]], [{**rows[0], 'score': float('nan')}], [{**rows[0], 'review': 'PRIVATE'}]):
            with self.assertRaises(ValueError):
                cold.SeededPublicReader(invalid)
        with self.assertRaises(ValueError):
            cold.write_bundle(rows, self.root.parent.parent / 'outside-private.json.gz')

    def test_bundle_roundtrip_tamper_bounds_no_private_fields_or_overwrite(self):
        _, rows, _ = self.inputs(); path = self.root / 'public.json.gz'
        cold.write_bundle(rows, path)
        self.assertEqual(cold.load_bundle(path), rows)
        with self.assertRaises(FileExistsError):
            cold.write_bundle(rows, path)
        with gzip.open(path, 'rt', encoding='utf-8') as file:
            data = json.load(file)
        data['rows'][0]['score'] = 1.25
        tampered = self.root / 'tampered.json.gz'
        with gzip.open(tampered, 'wt', encoding='utf-8') as file:
            json.dump(data, file)
        with self.assertRaises(ValueError):
            cold.load_bundle(tampered)
        with patch.object(cold, 'MAX_BYTES', 10), self.assertRaises(ValueError):
            cold.load_bundle(path)
        self.assertNotIn('review', json.dumps(data))

    def test_unsafe_source_still_invalidates_context_with_fresh_bundle(self):
        context, _, _ = self.inputs()
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=True)
        _, fresh, index = self.inputs()
        with readonly_snapshot(), self.assertRaises(engine.RecommendationSnapshotChanged):
            development.run(self.owner, context, index, 'all', 'protected_v2', public_reader=cold.SeededPublicReader(fresh))

    def test_command_requires_authorization_and_preserves_records(self):
        proof = sign_artifact('unused-holdout-tests', {'status': 'pass', 'database': 'local_isolated_postgresql',
            'skipped': 0, 'locks': [6, 4, 6], 'unused_validation_tests': 9, 'cold_public_tests': 8})
        path = self.root / 'proof.json'; path.write_text(json.dumps(proof), encoding='utf-8')
        options = dict(prior_site_lock=self.site, test_report=path, output_dir=self.root / 'profile', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('profile_recommendation_cold_read', **options)
        before = list(UserAnimeRecord.objects.order_by('pk').values())
        call_command('profile_recommendation_cold_read', confirm_sitewide_readonly=True, **options)
        report = read_artifact(self.root / 'profile/profile.json', 'protected-v2-cold-profile')
        self.assertFalse(report['quality_evaluated']); self.assertFalse(report['serving_approval'])
        self.assertEqual(report['targets_selected'], 0); self.assertEqual(len(report['category_parity']), 3)
        self.assertEqual(report['disk_bootstrap_first_engine']['sql_queries'], 4)
        self.assertEqual(before, list(UserAnimeRecord.objects.order_by('pk').values()))
        with self.assertRaises(CommandError):
            call_command('profile_recommendation_cold_read', confirm_sitewide_readonly=True, **options)
