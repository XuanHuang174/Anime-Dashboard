from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from recommendations.features import feature_tags
from recommendations.models import RecommendationAnime
from recommendations.tag_expansion_review import (
    build_expansion_review, expansion_markdown, load_proposal, validate_proposal,
)
from recommendations.taxonomy import ALIASES, AVAILABLE_TAGS, canonical_tag


def proposal():
    return load_proposal(Path(settings.BASE_DIR) / 'evaluation/tag-expansion.20261020.proposal.json')


def row(identity, tags, **kwargs):
    return {'bangumi_id': identity, 'title_zh': f'公共样例{identity}', 'title_jp': '',
            'tags': tags, 'status_type': 'TV', **kwargs}


def analyze(rows, draft=None):
    return build_expansion_review(rows, {'total': len(rows), 'non_adult': len(rows),
                                        'adult': 0, 'unknown': 0}, draft or proposal())


class TagExpansionReviewTests(SimpleTestCase):
    def test_proposal_is_inactive_and_cannot_be_mistaken_for_approval(self):
        draft = proposal()
        self.assertEqual(len(draft['synonyms']), 9)
        self.assertEqual(len(draft['new_genres']), 8)
        for changed in ({'apply_to_production': True}, {'status': 'approved'},
                        {'baseline_taxonomy': 'genres.v2'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_proposal({**draft, **changed})

    def test_repeated_labels_and_aliases_count_once_per_work_combined_groups_deduplicate(self):
        data = analyze([row(1, '萌系,萌系,爱情'), row(2, '萌系,日常'),
                        row(3, '运动番,运动系'), row(4, '恋爱,爱情')])
        self.assertEqual(data['covered_before'], 2)
        self.assertEqual(data['simulation']['synonyms']['newly_eligible_works'], 2)
        self.assertEqual(data['simulation']['new_genres']['newly_eligible_works'], 1)
        self.assertEqual(data['simulation']['combined']['newly_eligible_works'], 2)
        self.assertEqual(data['simulation']['combined']['feature_enriched_works'], 3)
        romance = data['review']['synonyms'][0]
        self.assertEqual(romance['document_frequency'], 2)
        self.assertEqual(romance['target_feature_new_to_works'], 1)

    def test_raw_and_mapped_titles_are_exclusions_never_feature_sources(self):
        data = analyze([row(1, '萌系', title_zh='萌系'), row(2, '爱情', title_jp='恋爱'),
                        row(3, 'TV,神作', title_zh='萌系 战争 超能力 第三季')])
        self.assertEqual(data['simulation']['combined']['feature_enriched_works'], 0)

    def test_deferred_empty_tags_invalid_identity_do_not_enter_simulated_coverage(self):
        data = analyze([row(1, '萝卜,萌,SF,萌豚,实验'), row(2, ''), row(None, '萌系')])
        self.assertEqual(data['eligible_public_works'], 2)
        self.assertEqual(data['simulation']['combined']['covered_after_if_approved'], 0)
        self.assertIsNone(analyze([])['simulation']['combined']['coverage_fraction_if_approved'])

    def test_does_not_mutate_live_rules_rows_or_infer_related_works(self):
        rows = [row(1, '萌系'), row(2, 'TV', title_zh='公共样例1 第二季')]
        before_rows, before_aliases, before_available = deepcopy(rows), deepcopy(ALIASES), AVAILABLE_TAGS
        report = analyze(rows)
        self.assertEqual(report['simulation']['combined']['newly_eligible_works'], 1)
        self.assertEqual(rows, before_rows)
        self.assertEqual(ALIASES, before_aliases)
        self.assertEqual(AVAILABLE_TAGS, before_available)
        self.assertIsNone(canonical_tag('萌系', taxonomy_version='genres.v4'))
        self.assertEqual(feature_tags('萌系', taxonomy_version='genres.v4'), frozenset())
        self.assertEqual(feature_tags('萌系'), frozenset({'萌系'}))

    def test_duplicate_active_or_ambiguous_proposal_entries_rejected(self):
        for mutation in ('duplicate', 'active', 'target', 'deferred', 'implicit'):
            draft = proposal()
            if mutation == 'duplicate':
                draft['deferred'].append(deepcopy(draft['deferred'][0]))
            elif mutation == 'active':
                draft['synonyms'][0]['source'] = '搞笑'
            elif mutation == 'target':
                draft['synonyms'][0]['target'] = '不存在'
            elif mutation == 'deferred':
                draft['deferred'][0]['target'] = '科幻'
            else:
                draft['new_genres'][0]['target'] = '另一种题材'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_proposal(draft)

    def test_snapshot_and_proposal_fingerprints_are_reproducible_and_safety_samples_suppressed(self):
        rows = [row(1, '萌系'), row(2, '战争,里番', title_zh='DO_NOT_EXPORT_TITLE')]
        one, two = analyze(rows), analyze(rows)
        self.assertEqual(one['catalog_fingerprint'], two['catalog_fingerprint'])
        self.assertEqual(one['proposal_sha256'], two['proposal_sha256'])
        self.assertNotIn('DO_NOT_EXPORT_TITLE', json.dumps(one, ensure_ascii=False))
        self.assertIn('未启用', expansion_markdown(one))
        self.assertFalse(one['policy']['mapping_applied'])


class TagExpansionPostgresTests(TransactionTestCase):
    def test_command_public_only_readonly_transaction_and_output_cannot_activate_rules(self):
        self.assertEqual(connection.vendor, 'postgresql', 'Use independent PostgreSQL test settings')
        RecommendationAnime.objects.create(bangumi_id=1, title_zh='公共作品', tags='萌系', nsfw=False)
        RecommendationAnime.objects.create(bangumi_id=2, title_zh='ADULT_SECRET', tags='萌系', nsfw=True)
        RecommendationAnime.objects.create(bangumi_id=3, title_zh='UNKNOWN_SECRET', tags='萌系', nsfw=None)
        original = list(RecommendationAnime.objects.values())
        with tempfile.TemporaryDirectory() as directory:
            with (CaptureQueriesContext(connection) as queries,
                  patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')),
                  patch('recommendations.engine.cache.set', side_effect=AssertionError('No cache writes'))):
                call_command('audit_tag_expansion', output_dir=Path(directory), stdout=StringIO())
            content = (Path(directory) / 'review.json').read_text(encoding='utf-8')
            report = json.loads(content)
            self.assertEqual(report['eligible_public_works'], 1)
            self.assertEqual(report['scope']['adult'], 1)
            self.assertEqual(report['scope']['unknown'], 1)
            self.assertNotIn('ADULT_SECRET', content)
            self.assertNotIn('UNKNOWN_SECRET', content)
            sql = ' '.join(query['sql'] for query in queries).lower()
            self.assertIn('repeatable read, read only', sql)
            for forbidden in ('auth_user', 'useranimerecord', 'recommendationpreference',
                              'feedback', 'synopsis', 'cover_url', '"score"'):
                self.assertNotIn(forbidden, sql)
            self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))
                                 for query in queries))
            with self.assertRaises(CommandError):
                call_command('audit_tag_expansion', output_dir=Path(directory), stdout=StringIO())
            self.assertEqual((Path(directory) / 'review.json').read_text(encoding='utf-8'), content)
        self.assertEqual(list(RecommendationAnime.objects.values()), original)

    def test_command_rejects_sqlite_instead_of_falling_back(self):
        with patch.object(connection, 'vendor', 'sqlite'), self.assertRaises(CommandError):
            call_command('audit_tag_expansion', stdout=StringIO())
