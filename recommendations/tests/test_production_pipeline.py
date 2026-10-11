import hashlib
import io
import json
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from recommendations.ingestion import normalize_row, upsert_anime
from recommendations.management.commands.run_recommendation_pipeline import load_verified_catalog, verify_database
from recommendations.models import RecommendationAnime


@override_settings(AWS_S3_CUSTOM_DOMAIN='media.xh-anime.com')
class ProductionPipelineTests(TestCase):
    def fixture(self, directory, rows):
        path = Path(directory) / 'source.jsonl'
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return path, digest

    def test_fingerprint_count_duplicate_unknown_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            path, digest = self.fixture(directory, [{'bangumi_id': 1, 'nsfw': False}])
            self.assertEqual(set(load_verified_catalog(path, digest, 1)), {1})
            for sha, count in [('0' * 64, 1), (digest, 2)]:
                with self.assertRaises(CommandError):
                    load_verified_catalog(path, sha, count)
            for rows in ([{'bangumi_id': 1}], [{'bangumi_id': 1, 'nsfw': False}] * 2):
                path, digest = self.fixture(directory, rows)
                with self.assertRaises(CommandError):
                    load_verified_catalog(path, digest, len(rows))
        self.assertEqual(RecommendationAnime.objects.count(), 0)

    def test_field_verification_and_adult_isolation(self):
        rows = [{'bangumi_id': 1, 'title_zh': '', 'title_jp': '原名', 'nsfw': False,
                 'cover_url': 'https://lain.bgm.tv/1.jpg'},
                {'bangumi_id': 2, 'title_zh': '文字', 'nsfw': True,
                 'cover_url': 'https://lain.bgm.tv/2.jpg'}]
        catalog = dict(normalize_row(row) for row in rows)
        for identity, values in catalog.items():
            upsert_anime(identity, values)
        stats, _ = verify_database(catalog, complete=True)
        self.assertEqual((stats['rows'], stats['adult'], stats['external']), (2, 1, 1))
        RecommendationAnime.objects.filter(bangumi_id=1).update(score=9)
        # An absent incremental score does not impose a comparison.
        verify_database(catalog, complete=True)
        RecommendationAnime.objects.filter(bangumi_id=2).update(cover_url='https://lain.bgm.tv/2.jpg')
        with self.assertRaises(CommandError):
            verify_database(catalog, complete=True)

    def test_missing_extra_ids_changed_fields_and_hosted_source_block(self):
        catalog = dict([normalize_row({'bangumi_id': 1, 'title_zh': '测试', 'score': 8,
                                      'nsfw': False, 'cover_url': 'https://lain.bgm.tv/1.jpg'})])
        with self.assertRaises(CommandError):
            verify_database(catalog, complete=True)
        upsert_anime(1, catalog[1])
        RecommendationAnime.objects.filter(bangumi_id=1).update(score=9)
        with self.assertRaises(CommandError):
            verify_database(catalog, complete=True)
        RecommendationAnime.objects.filter(bangumi_id=1).update(
            score=8, cover_url='https://media.xh-anime.com/covers/1.jpg', source_cover_url='https://lain.bgm.tv/changed.jpg')
        with self.assertRaises(CommandError):
            verify_database(catalog, complete=False)
        RecommendationAnime.objects.create(bangumi_id=3, title_zh='意外记录')
        with self.assertRaises(CommandError):
            verify_database(catalog, complete=False)

    def test_stop_marker_prevents_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self.fixture(directory, [{'bangumi_id': 1}])
            stop = Path(directory) / 'STOP'
            stop.touch()
            with self.assertRaises(CommandError):
                call_command('import_anime', str(path), stop_file=stop, stdout=io.StringIO())
        self.assertEqual(RecommendationAnime.objects.count(), 0)

    def test_no_production_authority_or_target_never_writes(self):
        for confirm in (False, True):
            with self.assertRaises(CommandError):
                call_command('run_recommendation_pipeline', 'unused.jsonl', expected_sha256='0' * 64,
                             expected_rows=1, run_dir='unused', confirm_production=confirm,
                             stdout=io.StringIO())
        self.assertEqual(RecommendationAnime.objects.count(), 0)

    @override_settings(DATABASE_URL='postgres://user@unit.neon.tech/test', R2_STORAGE_ENABLED=True,
                       AWS_STORAGE_BUCKET_NAME='xh-anime-media',
                       AWS_S3_ENDPOINT_URL='https://unit.r2.cloudflarestorage.com')
    def test_pipeline_order_progress_and_lock_without_real_network(self):
        # Target facade ONLY affects admission checks; ORM is the isolated test DB.
        target = Mock(vendor='postgresql', settings_dict={'HOST': 'unit.neon.tech'})
        client = Mock()
        client.get_paginator.return_value.paginate.return_value = [{'Contents': []}]
        with tempfile.TemporaryDirectory() as directory:
            path, digest = self.fixture(directory, [{'bangumi_id': 1, 'title_zh': '测试', 'nsfw': False}])
            run_dir = Path(directory) / 'run'
            with patch('recommendations.management.commands.run_recommendation_pipeline.connection', target), \
                    patch('recommendations.management.commands.run_recommendation_pipeline.make_r2_client', return_value=client), \
                    patch('recommendations.management.commands.run_recommendation_pipeline.resolve_proxy', return_value=None), \
                    patch('recommendations.management.commands.run_recommendation_pipeline.sync_covers', new=AsyncMock(return_value=Counter())) as sync:
                call_command('run_recommendation_pipeline', str(path), expected_sha256=digest,
                             expected_rows=1, run_dir=run_dir, confirm_production=True,
                             stdout=io.StringIO())
            self.assertEqual(RecommendationAnime.objects.count(), 1)
            self.assertEqual(json.loads((run_dir / 'progress.json').read_text(encoding='utf-8'))['phase'], 'completed')
            self.assertFalse((Path(directory) / 'production_pipeline.lock').exists())
            self.assertEqual(sync.await_count, 1)
            self.assertEqual(sync.call_args.kwargs['concurrency'], 2)
            client.close.assert_called_once()
