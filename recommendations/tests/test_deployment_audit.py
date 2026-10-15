"""Deployment script safety tests: mocked HTTP only, no accounts or sockets."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from scripts.verify_recommendation_deployment import Audit, BACK, FRONT, valid_evidence


class DeploymentAuditTests(SimpleTestCase):
    def test_write_endpoints_and_offsite_paths_are_rejected_before_network(self):
        session = Mock()
        audit = Audit(session)
        for method, path in (('PATCH', '/api/recommendations/preferences/'),
                             ('POST', '/api/recommendations/1/want/'), ('DELETE', '/api/records/1/'),
                             ('GET', '//other.example/api/'), ('GET', 'https://other.example/api/')):
            with self.assertRaises(ValueError):
                audit.request(path, method=method)
        with self.assertRaises(ValueError):
            audit.request('/recommendations', public=True)
        session.request.assert_not_called()

    def test_login_is_only_post_and_frontend_never_receives_credentials(self):
        session = Mock()
        audit = Audit(session)
        audit.headers['Authorization'] = 'Bearer synthetic-test-token'
        audit.request('/api/token/', method='POST', authenticated=False, json={'username': 'fixture', 'password': 'fixture'})
        self.assertEqual(session.request.call_args.args, ('POST', BACK + '/api/token/'))
        self.assertNotIn('Authorization', session.request.call_args.kwargs['headers'])
        audit.request('/recommendations', public=True, authenticated=False)
        self.assertEqual(session.request.call_args.args, ('GET', FRONT + '/recommendations'))
        self.assertNotIn('Authorization', session.request.call_args.kwargs['headers'])
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])
        audit.close()
        self.assertNotIn('Authorization', audit.headers)

    def test_budget_and_identity_mismatch_fail_without_selecting_any_other_user(self):
        audit = Audit(Mock())
        audit.report['requests'] = 40
        with self.assertRaises(ValueError):
            audit.request('/api/recommendations/')
        audit.session.request.assert_not_called()
        with patch('getpass.getpass', return_value='not-the-authorized-account'), patch('builtins.print'):
            with self.assertRaises(AssertionError):
                audit.authenticated_checks('authorized@example.test', {})
        audit.session.request.assert_not_called()

    def test_missing_or_unsafe_evidence_cannot_be_reported_as_valid(self):
        self.assertFalse(valid_evidence({}))
        self.assertFalse(valid_evidence({'content_rating': 'adult', 'evidence': {}}))
