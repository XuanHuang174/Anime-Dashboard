"""The public fast path must be exactly the existing versioned tag projection."""
import random
from unittest import TestCase

from recommendations.features import extract_genres, feature_tags
from recommendations.taxonomy import AUDIT_TAXONOMY_VERSIONS, LEGACY_TAXONOMY_VERSION, genre_rules


class FeatureProjectionTests(TestCase):
    def test_every_vocabulary_alias_title_and_malformed_input_matches_full_extractor(self):
        for version in (None, LEGACY_TAXONOMY_VERSION, *AUDIT_TAXONOMY_VERSIONS):
            available, aliases = genre_rules(version)
            values = [*available, *aliases, '２０２６年１０月', '日本', '神作', '二刷', 'Unknown',
                      '', ' ', None, 3, {'日常': True}, 'ＳＣＩ－ＦＩ', ' 日常 ', '《百合》']
            cases = [None, 3, {}, {'tags': ['日常']}, [], (), *values, values,
                     ','.join(value for value in values if isinstance(value, str))]
            for raw in cases:
                for titles in ((), ('《日 常》', '百合'), ('ＳＣＩ－ＦＩ', 'Unknown'), ('', None, 3)):
                    with self.subTest(version=version, raw=raw, titles=titles):
                        self.assertEqual(feature_tags(raw, titles, taxonomy_version=version),
                            extract_genres(raw, titles=titles, base_source='shared_base_tags', taxonomy_version=version)['tags'])

    def test_deterministic_mixed_unicode_lists_and_comma_strings_keep_exact_projection(self):
        randomizer = random.Random(12026)
        values = ['日常', '喜剧', '搞笑', '真百', '科幻', 'ＳＣＩ－ＦＩ', '日本', '２００７',
                  '二刷', 'Unknown', '《日常》', ' ', '', None, 42]
        for _ in range(500):
            raw = randomizer.choices(values, k=randomizer.randrange(30))
            titles = randomizer.choices(values[:6], k=2)
            for value in (raw, ','.join(part for part in raw if isinstance(part, str))):
                self.assertEqual(feature_tags(value, titles),
                    extract_genres(value, titles=titles, base_source='shared_base_tags')['tags'])
