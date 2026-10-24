"""11C: own-record navigation and the actual Python -> JavaScript wire boundary."""
import json
import shutil
import subprocess
import time
from pathlib import Path

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection

from anime.models import Anime, UserAnimeRecord
from recommendations.matching import record_fingerprint, catalog_fingerprint
from recommendations.models import RecommendationRecordIdentity
from recommendations.resolution import record_row
from . import test_exploration_browsing as prior


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_EPOCH='11c-tests',
    RECOMMENDATION_V2_BROWSE_ENABLED=False, RECOMMENDATION_ALGORITHM_VERSION='content.v1')
class ExplorationFrontendTests(TransactionTestCase):
    setUp = prior.SingleWorkSnapshotTests.setUp
    catalog = prior.SingleWorkSnapshotTests.catalog
    record = prior.SingleWorkSnapshotTests.record
    start = prior.SingleWorkSnapshotTests.start
    details = prior.SingleWorkSnapshotTests.details

    def reference(self, record_id, **params):
        return self.client.get(f'/api/recommendations/explore/record/{record_id}/', params)

    def test_exact_own_record_resolves_external_id_without_scores_or_writes(self):
        record = self.record(10, personal_score=9)
        with CaptureQueriesContext(connection) as queries:
            response = self.reference(record.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({k: v for k, v in response.data.items() if k != 'content_scope_context'}, {
            'contract_version': 'recommendations.explore.record.v1', 'record_id': record.pk,
            'identity_state': 'linked', 'bangumi_id': 10})
        sql = '\n'.join(row['sql'].lower() for row in queries)
        for forbidden in ('personal_score', '"review"', 'custom_tags', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, sql)
        self.assertNotIn('PRIVATE_REVIEW_SENTINEL', json.dumps(response.data))
        self.client.force_authenticate(self.other)
        self.assertEqual(self.reference(record.pk).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.reference(record.pk).status_code, 401)

    def test_unlinked_record_requires_review_and_does_not_guess_from_pk_or_title(self):
        record = self.record(None)
        self.assertNotEqual(record.pk, 10)
        response = self.reference(record.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['bangumi_id'])
        self.assertEqual(response.data['identity_state'], 'unlinked')
        self.assertEqual(self.reference(record.pk, bangumi_id=10).status_code, 400)
        self.assertEqual(self.reference(record.pk, user_id=self.other.pk).status_code, 400)

    def test_valid_alias_and_stale_alias_follow_existing_identity_rules(self):
        record = self.record(None)
        target = self.catalog(300)
        link = RecommendationRecordIdentity.objects.create(record=record, catalog_anime=target, active=True,
            record_fingerprint=record_fingerprint(record_row(record)),
            catalog_fingerprint=catalog_fingerprint({'title_zh': target.title_zh, 'title_jp': target.title_jp,
                'season': target.season, 'status_type': target.status_type}), version=1)
        response = self.reference(record.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['bangumi_id'], 300)
        link.record_fingerprint = '0' * 64; link.save(update_fields=['record_fingerprint'])
        self.assertEqual(self.reference(record.pk).data['identity_state'], 'stale_link')
        self.assertIsNone(self.reference(record.pk).data['bangumi_id'])

    @override_settings(RECOMMENDATION_EXPLORATION_ENABLED=False)
    def test_disabled_capability_also_blocks_private_entry(self):
        self.assertEqual(self.reference(self.record(10).pk).status_code, 503)

    def verify_wire(self, first, full, next_page=None):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for 11C wire validation')
        root = Path(settings.BASE_DIR)
        result = subprocess.run([node, str(root / 'frontend/tests/fixtures/verifyExplorationWire.mjs')],
            input=json.dumps({'page': first, 'detail': full, 'next': next_page}), encoding='utf-8', capture_output=True, cwd=root, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(len(first['results'])))

    def test_actual_safe_list_and_strict_detail_are_accepted_by_frontend(self):
        first = self.start()
        self.assertEqual(first.status_code, 200, first.data)
        full = self.details(first.data['results'][0])
        self.assertEqual(full.status_code, 200, full.data)
        self.verify_wire(first.data, full.data)

    def test_resigned_context_in_a_later_second_does_not_break_frontend_paging(self):
        first = self.start()
        self.assertEqual(first.status_code, 200, first.data)
        time.sleep(1.05)  # TimestampSigner signs the SAME body with a newer timestamp.
        next_page = self.client.get('/api/recommendations/explore/', {'cursor': first.data['next_page_cursor']})
        self.assertEqual(next_page.status_code, 200, next_page.data)
        self.assertNotEqual(first.data['snapshot_context'], next_page.data['snapshot_context'])
        self.verify_wire(first.data, None, next_page.data)

    @override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True)
    def test_actual_permanent_adult_wire_and_own_reference_are_scoped(self):
        prior.AdultSingleWorkTests.approve(self)
        record = self.record(90)
        self.assertIsNone(self.reference(record.pk).data['bangumi_id'])
        self.assertEqual(self.reference(record.pk, content_scope='include_adult').data['bangumi_id'], 90)
        first = self.start(seed_bangumi_id=90, content_scope='include_adult')
        self.assertEqual(first.status_code, 200, first.data)
        full = self.details(first.data['results'][0], content_scope='include_adult')
        self.assertEqual(full.status_code, 200, full.data)
        self.verify_wire(first.data, full.data)
