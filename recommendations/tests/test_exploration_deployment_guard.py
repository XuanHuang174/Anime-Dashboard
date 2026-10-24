"""11F verifier isolation: no live requests, credentials or production changes."""
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from scripts.verify_recommendation_exploration_deployment import ExplorationDeploymentAudit, main
from . import test_exploration_browsing as prior


class ExplorationDeploymentGuardTests(SimpleTestCase):
    def test_required_target_account_and_seed_are_checked_before_client_creation(self):
        for args in (['--expect', 'public'], ['--deployment-confirmed', '--expect', 'enabled'],
                ['--deployment-confirmed', '--expect', 'enabled', '--expected-account', 'synthetic'],
                ['--deployment-confirmed', '--expect', 'enabled', '--expected-account', 'synthetic', '--seed-bangumi-id', '0']):
            with patch('scripts.verify_recommendation_exploration_deployment.ExplorationDeploymentAudit') as client:
                with self.assertRaises(SystemExit):
                    main(args)
                client.assert_not_called()

    def test_no_business_mutations_external_paths_or_frontend_credentials(self):
        session = Mock()
        audit = ExplorationDeploymentAudit(session=session)
        audit.headers['Authorization'] = 'Bearer synthetic-only'
        for method, path in [('POST', '/api/recommendations/1/want/'), ('PATCH', '/api/recommendations/preferences/'),
                ('POST', '/api/recommendations/feedback/'), ('GET', 'https://external.invalid/'),
                ('GET', '//external.invalid/'), ('GET', '/api/records/'), ('POST', '/api/recommendations/explore/')]:
            with self.assertRaises(ValueError):
                audit.request(path, method=method)
        session.request.assert_not_called()
        with self.assertRaises(ValueError):
            audit.request('/', public=True)
        session.request.assert_not_called()
        session.request.return_value = Mock(status_code=200)
        audit.request('/', public=True, authenticated=False)
        self.assertNotIn('Authorization', session.request.call_args.kwargs['headers'])
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])

    def test_public_success_and_partial_failures_never_mark_authenticated_release_verified(self):
        audit = ExplorationDeploymentAudit(session=Mock())
        with patch.object(audit, 'request', side_effect=[Mock(status_code=401)] * 3 + [Mock(status_code=200, text='<html>')]):
            audit.public_checks()
        self.assertFalse(audit.report['release_verified'])
        self.assertFalse(audit.report['frontend_bundle_verified'])
        for status in (404, 429, 503):
            with patch.object(audit, 'request', return_value=Mock(status_code=status)):
                with self.assertRaises(AssertionError):
                    audit.public_checks()
        self.assertFalse(audit.report['release_verified'])

    def test_old_closed_backend_is_not_mistaken_for_a_completed_11f_rollback(self):
        audit = ExplorationDeploymentAudit(session=Mock())
        old = {'exploration': {'contract_version': 'recommendations.explore.capabilities.v1', 'enabled': False,
            'catalog_search_enabled': False, 'query_execution_enabled': False, 'supported_intents': ['similar', 'tags'],
            'navigation': [{'available': True}, {'available': False}, {'available': False}]}}
        with patch.object(audit, 'read', side_effect=[{}, {}, {}, old]):
            with self.assertRaises(AssertionError):
                audit.authenticated_checks(False, None)
        self.assertFalse(audit.report['release_verified'])

    def test_deployed_frontend_requires_actual_build_hashes_not_just_a_200(self):
        class Build:
            def __truediv__(self, name):
                return self

            def is_file(self):
                return True

            def read_bytes(self):
                return b'synthetic-compiled-content'
        for content in (b'synthetic-compiled-content', b'wrong-build'):
            audit = ExplorationDeploymentAudit(session=Mock())
            responses = [Mock(status_code=401)] * 3 + [Mock(status_code=200, text='<html><script src="/assets/index-abc.js"></script>'),
                Mock(status_code=200, text='Recommendations-abc.js Recommendations-def.css'),
                Mock(status_code=200, content=content), Mock(status_code=200, content=content)]
            with patch.object(audit, 'request', side_effect=responses):
                if content == b'wrong-build':
                    with self.assertRaises(AssertionError):
                        audit.public_checks(Build())
                    self.assertFalse(audit.report['frontend_bundle_verified'])
                else:
                    audit.public_checks(Build())
                    self.assertTrue(audit.report['frontend_bundle_verified'])
            self.assertFalse(audit.report['release_verified'])


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=[],
                   RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
class ExplorationDeploymentLocalWireTests(TransactionTestCase):
    setUp = prior.SingleWorkSnapshotTests.setUp
    catalog = prior.SingleWorkSnapshotTests.catalog

    def test_actual_readonly_audit_against_synthetic_django_responses(self):
        import itertools
        import time
        from django.core import signing
        class LocalTransport:
            def __init__(transport, client):
                transport.client = client

            def request(transport, method, url, **kwargs):
                if method != 'GET':
                    raise AssertionError('No mutation or live network')
                from urllib.parse import urlsplit
                response = transport.client.get(urlsplit(url).path, kwargs.get('params', {}))
                return Mock(status_code=response.status_code, headers=response.headers, json=lambda: response.data)

            def close(transport):
                pass

        audit = ExplorationDeploymentAudit(session=LocalTransport(self.client))
        ticks = itertools.count(int(time.time()))
        # Deterministically exercise re-signed context bytes between requests.
        with patch.object(signing.TimestampSigner, 'timestamp', side_effect=lambda: signing.b62_encode(next(ticks))):
            audit.authenticated_checks(True, 1)
        self.assertTrue(audit.report['release_verified'])
        self.assertFalse(audit.report['frontend_bundle_verified'])
        self.assertLessEqual(audit.report['requests'], 40)
        self.assertEqual(audit.report['write_endpoints_called'], [])
