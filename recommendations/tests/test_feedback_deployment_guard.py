"""Offline tests for future live-audit guard; absolutely no real login/network."""
from contextlib import redirect_stderr
from io import StringIO
from unittest import TestCase
from unittest.mock import Mock, patch

from scripts.verify_recommendation_feedback_deployment import FeedbackDeploymentAudit, main


class FeedbackDeploymentGuardTests(TestCase):
    def test_unconfirmed_deployment_stops_before_network_or_credentials(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')) as network:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(['--expected-account', 'synthetic@example.test'])
            network.assert_not_called()

    def test_all_business_mutations_and_foreign_paths_block_before_network(self):
        session = Mock()
        audit = FeedbackDeploymentAudit(session)
        for method, path in (('POST', '/api/recommendations/feedback/'),
                             ('POST', '/api/recommendations/feedback/undo/'),
                             ('POST', '/api/recommendations/701/want/'),
                             ('PATCH', '/api/recommendations/preferences/'),
                             ('DELETE', '/api/recommendations/feedback/hidden/'),
                             ('GET', '/api/user/other-users/'),
                             ('GET', 'https://foreign.test/api/token/')):
            with self.assertRaises(ValueError):
                audit.request(path, method=method)
        session.request.assert_not_called()

    def test_only_normal_login_can_post_and_tokens_cannot_go_to_frontend(self):
        session = Mock()
        audit = FeedbackDeploymentAudit(session)
        audit.request('/api/token/', method='POST', authenticated=False, json={'username': 'synthetic', 'password': 'fake'})
        self.assertEqual(session.request.call_count, 1)
        with self.assertRaises(ValueError):
            audit.request('/recommendations', public=True, authenticated=True)
        with self.assertRaises(ValueError):
            audit.request('/api/token/', public=True, authenticated=False, method='POST')
        self.assertEqual(session.request.call_count, 1)
