"""13F script tests only use isolated Django/fake HTTP; no live credentials."""
import io
from unittest.mock import patch, Mock
from urllib.parse import urlsplit
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from scripts import verify_credits_deployment as probe
from . import test_credits_http as prior


class CreditsDeploymentGuards(SimpleTestCase):
    def test_explicit_target_account_seed_scope_before_network_creation(self):
        for argv in (['--expect', 'public'], ['--deployment-confirmed', '--expect', 'enabled'],
            ['--deployment-confirmed', '--expect', 'enabled', '--expected-account', 'synthetic'],
            ['--deployment-confirmed', '--expect', 'public', '--scope', 'include_adult'],
            ['--deployment-confirmed', '--expect', 'enabled', '--expected-account', 'synthetic', '--seed-bangumi-id', '0']):
            with patch.object(probe, 'CreditsDeploymentAudit') as client, patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit): probe.main(argv)
                client.assert_not_called()

    def test_write_external_redirect_and_frontend_credentials_are_forbidden(self):
        session = Mock(); audit = probe.CreditsDeploymentAudit(session)
        audit.headers['Authorization'] = 'Bearer synthetic-only'
        for path, method in (('/api/recommendations/credits/entities/studio/1/items/1/want/', 'POST'),
                ('/api/recommendations/preferences/', 'PATCH'), ('/api/user/records/', 'GET'),
                ('https://other.invalid/', 'GET'), ('//other.invalid/', 'GET'), ('/assets/../private.js', 'GET')):
            with self.assertRaises(ValueError): audit.request(path, method=method)
        with self.assertRaises(ValueError): audit.request('/recommendations/credits', public=True)
        session.request.assert_not_called()
        session.request.return_value = Mock(status_code=200)
        with patch('sys.stdout', new=io.StringIO()): audit.request('/recommendations/credits', public=True, authenticated=False)
        self.assertNotIn('Authorization', session.request.call_args.kwargs['headers'])
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])
        self.assertFalse(audit.report['release_verified'])

    def test_bundle_requires_current_entry_and_credits_chunk_bytes(self):
        class Build:
            def __truediv__(self, path): return self
            def is_file(self): return True
            def read_bytes(self): return b'CreditsExplorer-synthetic.js'
        for content in (b'CreditsExplorer-synthetic.js', b'wrong-build'):
            audit = probe.CreditsDeploymentAudit(Mock())
            responses = [Mock(status_code=401)] * 4 + [Mock(status_code=200, text='<script src="/assets/index-synthetic.js"></script>'),
                Mock(status_code=200, text='CreditsExplorer-synthetic.js', content=content), Mock(status_code=200, content=content)]
            with patch.object(audit, 'request', side_effect=responses), patch('sys.stdout', new=io.StringIO()):
                if content == b'wrong-build':
                    with self.assertRaises(AssertionError): audit.public_checks(Build())
                    self.assertFalse(audit.report['frontend_bundle_verified'])
                else:
                    audit.public_checks(Build()); self.assertTrue(audit.report['frontend_bundle_verified'])
        self.assertFalse(audit.report['release_verified'])

    def test_http_budget_cannot_be_raised_to_fake_acceptance(self):
        for value in (0, 11, float('nan')):
            with self.assertRaises(ValueError): probe.CreditsDeploymentAudit(Mock(), response_budget=value)


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=[],
    RECOMMENDATION_CREDITS_STUDIO_ENABLED=True, RECOMMENDATION_CREDITS_VOICE_ACTOR_ENABLED=True,
    RECOMMENDATION_CREDITS_CANARY_USERS=[])
class CreditsDeploymentLocalWire(TransactionTestCase):
    setUp = prior.CreditsHTTPTests.setUp
    tearDown = prior.CreditsHTTPTests.tearDown
    publish = prior.CreditsHTTPTests.publish

    def adapter(self):
        client, owner = self.client, self.owner
        class Session:
            trust_env = True
            def request(inner, method, url, **options):
                if method != 'GET': raise AssertionError('No login or writes in local adapter')
                client.force_authenticate(owner if options.get('headers', {}).get('Authorization') else None)
                return client.get(urlsplit(url).path, options.get('params', {}))
            def close(inner): pass
        return Session()

    def test_actual_http_contracts_pass_enabled_readonly_audit_without_disclosing_account(self):
        audit = probe.CreditsDeploymentAudit(self.adapter())
        with patch.dict('os.environ', {'CREDITS_AUDIT_TOKEN': 'synthetic-only'}), patch('sys.stdout', new=io.StringIO()):
            audit.public_checks(); audit.authorize(self.owner.username); audit.authenticated_checks(True, 1)
        self.assertTrue(all(row['passed'] for row in audit.report['checks'].values()))
        self.assertFalse(audit.report['production_writes']); self.assertFalse(audit.report['release_verified'])
        self.assertLess(audit.report['requests'], 40)
        self.assertNotIn('synthetic-only', str(audit.report)); self.assertNotIn(self.owner.username, str(audit.report))

    def test_noncanary_and_independent_kind_use_actual_server_capabilities(self):
        audit = probe.CreditsDeploymentAudit(self.adapter()); audit.headers['Authorization'] = 'Bearer synthetic-only'
        with override_settings(RECOMMENDATION_CREDITS_CANARY_USERS=[self.other.username]), patch('sys.stdout', new=io.StringIO()):
            audit.authenticated_checks(False, 1)
        audit = probe.CreditsDeploymentAudit(self.adapter()); audit.headers['Authorization'] = 'Bearer synthetic-only'
        with override_settings(RECOMMENDATION_CREDITS_VOICE_ACTOR_ENABLED=False), patch('sys.stdout', new=io.StringIO()):
            audit.authenticated_checks(True, 1, kinds=('studio',))
