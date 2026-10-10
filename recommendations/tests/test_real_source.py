"""Optional real sample verification: only under the explicit in-memory settings."""
import io
import json
import os
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from recommendations.ingestion import normalize_row, upsert_anime
from recommendations.models import RecommendationAnime


class AdultMarkerTests(TestCase):
    def test_import_persists_marker_and_partial_updates_preserve_it(self):
        upsert_anime(*normalize_row({'bangumi_id': 42, 'title_zh': '标记测试', 'nsfw': True}))
        upsert_anime(*normalize_row({'bangumi_id': 42, 'score': 9}))
        self.assertTrue(RecommendationAnime.objects.get(bangumi_id=42).nsfw)
        upsert_anime(*normalize_row({'bangumi_id': 42, 'nsfw': False}))
        self.assertFalse(RecommendationAnime.objects.get(bangumi_id=42).nsfw)
        upsert_anime(*normalize_row({'bangumi_id': 43, 'nsfw': 'unknown'}))
        self.assertIsNone(RecommendationAnime.objects.get(bangumi_id=43).nsfw)


@skipUnless(os.environ.get('RECOMMENDATION_SAMPLE_PATH') and
            os.environ.get('DJANGO_SETTINGS_MODULE') == 'config.recommendation_test_settings',
            '真实样本验证需明确指定安全测试配置与样本文件')
class RealSourceTests(TestCase):
    def test_real_sample_reimport_is_idempotent_and_nsfw_retained(self):
        self.assertEqual(settings.DATABASES['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(settings.DATABASES['default']['NAME'], 'file:memorydb_default?mode=memory&cache=shared')
        path = Path(os.environ['RECOMMENDATION_SAMPLE_PATH'])
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(rows)
        for _ in range(2):
            call_command('import_anime', str(path), stdout=io.StringIO())
        self.assertEqual(RecommendationAnime.objects.count(), len(rows))
        for row in rows:
            stored = RecommendationAnime.objects.get(bangumi_id=row['bangumi_id'])
            self.assertEqual(stored.nsfw, row.get('nsfw'))
            self.assertEqual(stored.cover_url, row.get('cover_url', ''))
        print(f'真实样本导入及重复导入通过：{len(rows)} 条，仅 SQLite 内存测试库')
