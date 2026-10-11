import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from recommendations.ingestion import clean_score, clean_tags, normalize_row, upsert_anime
from recommendations.models import RecommendationAnime


class NormalizationTests(SimpleTestCase):
    def test_score_bad_values_and_bangumi_object(self):
        for value in ('', 'bad', None, float('nan'), float('inf'), -1, 11, True, []):
            with self.subTest(value=value):
                self.assertEqual(clean_score(value), 0.0)
        self.assertEqual(clean_score({'score': '8.5'}), 8.5)

    def test_tags_list_string_dicts_dedupe(self):
        for value in ('百合，日常,百合', ['百合', {'name': '日常'}, '', '百合']):
            self.assertEqual(clean_tags(value), '百合,日常')
        self.assertEqual(clean_tags(None), '')

    def test_default_and_season_mapping(self):
        identity, values = normalize_row({'bgm_id': '42', 'name': '原名', 'date': '2026-08-13'})
        self.assertEqual(identity, 42)
        self.assertEqual(values['season'], '2026-07')
        self.assertEqual(normalize_row({'bangumi_id': 42, 'season': 'FALL', 'seasonYear': 2026})[1]['season'], '2026-10')

    def test_ids_never_guess_namespace_or_accept_null(self):
        for value in (None, '', 0, -1, True, '1.2', 1.5, 2_147_483_648):
            with self.assertRaises(ValueError):
                normalize_row({'bangumi_id': value})
        with self.assertRaises(ValueError):
            normalize_row({'id': 42})
        self.assertEqual(normalize_row({'id': 42}, 'id')[0], 42)


@override_settings(AWS_S3_CUSTOM_DOMAIN='media.xh-anime.com')
class ImportTests(TestCase):
    def run_import(self, rows, **options):
        # Temporary fixtures, never real source data or a production database.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'anime.json'
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8-sig')
            output = io.StringIO()
            call_command('import_anime', str(path), stdout=output, stderr=io.StringIO(), **options)
            return output.getvalue()

    def test_repeated_import_updates_not_duplicates_and_missing_preserves(self):
        self.run_import([{'bangumi_id': 42, 'title_zh': '测试', 'score': '8.5', 'tags': ['百合', '日常']}])
        self.run_import([{'bangumi_id': 42, 'score': '9.2', 'tags': '百合,治愈'}])
        self.assertEqual(RecommendationAnime.objects.count(), 1)
        anime = RecommendationAnime.objects.get()
        self.assertEqual((anime.title_zh, anime.score, anime.tags), ('测试', 9.2, '百合,治愈'))

    def test_cover_preserved_unless_source_changes(self):
        source = 'https://lain.bgm.tv/cover/a.png'
        anime = RecommendationAnime.objects.create(
            bangumi_id=42, title_zh='测试', cover_url='https://media.xh-anime.com/covers/42.jpg?v=abc',
            source_cover_url=source,
        )
        self.run_import([{'bangumi_id': 42, 'cover_url': source, 'score': 9}])
        anime.refresh_from_db()
        self.assertIn('media.xh-anime.com', anime.cover_url)
        new_source = 'https://lain.bgm.tv/cover/b.png'
        self.run_import([{'bangumi_id': 42, 'cover_url': new_source}])
        anime.refresh_from_db()
        self.assertEqual((anime.cover_url, anime.source_cover_url), (new_source, new_source))

    def test_adult_text_retained_without_public_cover_on_reimports(self):
        source = 'https://lain.bgm.tv/cover/adult.png'
        self.run_import([{'bangumi_id': 42, 'title_zh': '文字数据', 'synopsis': '保留简介',
                          'nsfw': True, 'cover_url': source}])
        self.run_import([{'bangumi_id': 42, 'score': 8.5, 'cover_url': source}])
        self.run_import([{'bangumi_id': 42, 'nsfw': None, 'cover_url': source}])
        anime = RecommendationAnime.objects.get()
        self.assertTrue(anime.nsfw)
        self.assertEqual((anime.title_zh, anime.synopsis, anime.score), ('文字数据', '保留简介', 8.5))
        self.assertEqual((anime.cover_url, anime.source_cover_url), ('', source))

    def test_marking_existing_hosted_record_adult_clears_link_not_object(self):
        anime = RecommendationAnime.objects.create(
            bangumi_id=42, title_zh='保留文字', nsfw=False,
            cover_url='https://media.xh-anime.com/covers/42.jpg',
            source_cover_url='https://lain.bgm.tv/a.png',
        )
        self.run_import([{'bangumi_id': 42, 'nsfw': True}])
        anime.refresh_from_db()
        self.assertEqual(anime.cover_url, '')
        self.assertEqual(anime.title_zh, '保留文字')
        self.assertEqual(anime.source_cover_url, 'https://lain.bgm.tv/a.png')

    def test_missing_title_and_bad_score_have_safe_defaults(self):
        self.run_import([{'bangumi_id': 1, 'score': 'oops'}, {'bangumi_id': 2, 'title_zh': '', 'name': '原名'}])
        self.assertEqual(RecommendationAnime.objects.get(bangumi_id=1).title_zh, '未命名番剧 1')
        self.assertEqual(RecommendationAnime.objects.get(bangumi_id=2).title_zh, '原名')

    def test_bad_rows_skipped_without_losing_valid_rows(self):
        with self.assertRaises(CommandError):
            self.run_import([{'bangumi_id': 1}, {}, {'bangumi_id': 2, 'title_zh': 'x' * 501}, {'bangumi_id': 3}])
        self.assertEqual(set(RecommendationAnime.objects.values_list('bangumi_id', flat=True)), {1, 3})

    def test_duplicates_batches_wrapper_and_dry_run(self):
        output = self.run_import({'data': [{'bangumi_id': 1}, {'bangumi_id': 1, 'score': 9}, {'bangumi_id': 2}]}, batch_size=1)
        self.assertIn('duplicate_ids', output)
        self.assertEqual(RecommendationAnime.objects.count(), 2)
        self.run_import([{'bangumi_id': 3}], dry_run=True)
        self.assertFalse(RecommendationAnime.objects.filter(bangumi_id=3).exists())

    def test_jsonl_bad_line_and_streaming(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.jsonl'
            path.write_text('{"bangumi_id": 1}\nnot-json\n{"bangumi_id": 2}\n', encoding='utf-8')
            call_command('import_anime', str(path), skip_invalid=True, stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(RecommendationAnime.objects.count(), 2)

    def test_database_failures_stop_and_rollback_batch(self):
        with patch('recommendations.management.commands.import_anime.upsert_anime', side_effect=DatabaseError):
            with self.assertRaises(CommandError):
                self.run_import([{'bangumi_id': 1}])
        self.assertEqual(RecommendationAnime.objects.count(), 0)

    def test_default_batch_boundary(self):
        output = self.run_import([{'bangumi_id': identity} for identity in range(1, 502)])
        self.assertEqual(RecommendationAnime.objects.count(), 501)
        self.assertIn('批次 2 完成', output)
        self.assertNotIn('批次 3 完成', output)

    def test_full_catalog_13001_rows_then_incremental_update(self):
        output = self.run_import([
            {'bangumi_id': identity, 'title_zh': f'测试番剧 {identity}', 'score': 8.5, 'tags': ['日常']}
            for identity in range(1, 13002)
        ])
        self.assertEqual(RecommendationAnime.objects.count(), 13001)
        self.assertIn('批次 27 完成', output)
        self.run_import([{'bangumi_id': 13001, 'score': 9.2, 'tags': '日常,治愈'}])
        self.assertEqual(RecommendationAnime.objects.count(), 13001)
        self.assertEqual(RecommendationAnime.objects.get(bangumi_id=13001).score, 9.2)

    def test_jsonl_oversized_record_does_not_discard_next_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.jsonl'
            path.write_text('x' * (2 * 1024 * 1024 + 1) + '\n{"bangumi_id": 2}\n', encoding='utf-8')
            call_command('import_anime', str(path), skip_invalid=True, stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(RecommendationAnime.objects.get().bangumi_id, 2)

    def test_unique_and_score_constraints(self):
        upsert_anime(1, {})
        for kwargs in ({'bangumi_id': 1}, {'bangumi_id': 2, 'score': 11}, {'bangumi_id': -1}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                RecommendationAnime.objects.create(title_zh='测试', **kwargs)
