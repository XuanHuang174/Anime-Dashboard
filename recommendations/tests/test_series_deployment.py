"""Live acceptance code exercised against LOCAL synthetic Django responses only."""
import io
from urllib.parse import urlsplit
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from recommendations.series_store import publish_archive
from recommendations.tests import test_series_workspace as prior
from recommendations.tests.test_series_exploration import archive
from scripts import verify_series_deployment as probe


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=[],
    RECOMMENDATION_SERIES_ENABLED=True, RECOMMENDATION_SERIES_CANARY_USERS=[],
    RECOMMENDATION_SERIES_EXPLORATION_ENABLED=True, RECOMMENDATION_SERIES_REMINDERS_ENABLED=True)
class SeriesDeploymentTests(TransactionTestCase):
    setUp = prior.SeriesWorkspaceTests.setUp
    record = prior.SeriesWorkspaceTests.record

    def adapter(self):
        owner, client = self.owner, self.client
        class Session:
            trust_env = True
            def request(inner, method, url, **options):
                if method != 'GET': raise AssertionError('No live writes or logins in test')
                if options.get('headers', {}).get('Authorization'):
                    client.force_authenticate(owner)
                else: client.force_authenticate(None)
                return client.get(urlsplit(url).path, options.get('params', {}))
            def close(inner): pass
        return Session()

    def test_actual_local_payloads_pass_enabled_probe_and_wire_checks(self):
        publish_archive(archive([(1, 2, 3), (2, 1, 2)], version='dump-2026-10-21.000000Z'))
        self.record(identity=1, watch_status='completed')
        audit = probe.SeriesDeploymentAudit(self.adapter())
        with patch.dict('os.environ', {'SERIES_AUDIT_TOKEN': 'synthetic-only'}), patch('sys.stdout', new=io.StringIO()):
            audit.public_checks()
            audit.authorize(self.owner.username)
            audit.authenticated_checks(True, 1)
        self.assertTrue(all(row['passed'] for row in audit.report['checks'].values()))
        self.assertFalse(audit.report['production_writes'])
        self.assertLess(audit.report['requests'], 40)
        self.assertNotIn('synthetic-only', str(audit.report))
        self.assertNotIn(self.owner.username, str(audit.report))

    def test_noncanary_probe_preserves_old_modes_and_forbids_other_paths(self):
        audit = probe.SeriesDeploymentAudit(self.adapter())
        audit.headers['Authorization'] = 'Bearer synthetic-only'
        with override_settings(RECOMMENDATION_SERIES_CANARY_USERS=[self.other.username]), patch('sys.stdout', new=io.StringIO()):
            audit.authenticated_checks(False, 1)
        for path, method in (('/api/recommendations/series/1/items/2/want/', 'POST'), ('/api/user/account/', 'GET'),
                ('https://other.invalid', 'GET'), ('/api/recommendations/series/reminders/', 'PATCH')):
            with self.assertRaises(ValueError): audit.request(path, method=method)

    def test_explicit_target_and_account_required_before_network_client(self):
        for argv in (['--expect', 'public'], ['--deployment-confirmed', '--expect', 'enabled'],
                     ['--deployment-confirmed', '--expect', 'enabled', '--expected-account', 'synthetic']):
            with patch.object(probe, 'SeriesDeploymentAudit') as factory, patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit): probe.main(argv)
                factory.assert_not_called()
