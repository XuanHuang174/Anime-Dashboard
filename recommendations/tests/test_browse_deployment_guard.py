"""Guard the deployment audit itself; never performs live network requests."""
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from scripts.verify_recommendation_browse_deployment import BrowseDeploymentAudit, main


class BrowseDeploymentGuardTests(SimpleTestCase):
    def test_unconfirmed_deployment_never_constructs_a_network_client(self):
        with patch('scripts.verify_recommendation_browse_deployment.BrowseDeploymentAudit') as audit:
            with self.assertRaises(SystemExit):
                main(['--expected-account', 'synthetic@example.invalid'])
        audit.assert_not_called()

    def test_business_writes_foreign_paths_and_frontend_credentials_are_rejected_before_network(self):
        session = Mock()
        audit = BrowseDeploymentAudit(session=session)
        for path, method in (('/api/recommendations/10/want/', 'POST'),
                             ('/api/recommendations/feedback/', 'POST'),
                             ('/api/recommendations/feedback/undo/', 'POST'),
                             ('/api/recommendations/preferences/', 'PATCH'),
                             ('https://example.invalid/', 'GET'), ('/api/anime/', 'GET')):
            with self.assertRaises(ValueError):
                audit.request(path, method=method)
        with self.assertRaises(ValueError):
            audit.request('/recommendations', public=True, authenticated=True)
        session.request.assert_not_called()

    def test_authenticated_cursor_get_uses_configured_backend_and_no_redirects(self):
        session = Mock()
        audit = BrowseDeploymentAudit(session=session)
        audit.request('/api/recommendations/', params={'cursor': 'synthetic-only'})
        args, kwargs = session.request.call_args
        self.assertEqual(args[0], 'GET')
        self.assertEqual(args[1], 'https://anime-dashboard-api-dv0s.onrender.com/api/recommendations/')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertNotIn('json', kwargs)
