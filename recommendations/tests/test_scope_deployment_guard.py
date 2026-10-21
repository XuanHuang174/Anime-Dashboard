"""Audit-script guard tests: no live network or credentials."""
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from scripts.verify_recommendation_scope_deployment import ScopeDeploymentAudit, main


class ScopeDeploymentGuardTests(SimpleTestCase):
    def test_unconfirmed_deployment_never_creates_client(self):
        with patch('scripts.verify_recommendation_scope_deployment.ScopeDeploymentAudit') as client:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(['--expected-account','synthetic@example.invalid'])
            client.assert_not_called()

    def test_all_business_writes_foreign_paths_and_frontend_requests_block_before_network(self):
        session=Mock(); audit=ScopeDeploymentAudit(session)
        for path,method in [('/api/recommendations/eligibility/revoke/','POST'),
            ('/api/recommendations/2/want/','POST'),('/api/recommendations/feedback/','POST'),
            ('/api/recommendations/feedback/undo/','POST'),('/api/recommendations/preferences/','PATCH'),
            ('/api/recommendations/eligibility/','POST'),('/api/recommendations/identities/batch/','POST'),
            ('/api/user/other/','GET'),('https://foreign.invalid/','GET')]:
            with self.assertRaises(ValueError): audit.request(path,method=method)
        with self.assertRaises(ValueError): audit.request('/api/token/',method='POST',public=True)
        session.request.assert_not_called()

    def test_only_normal_login_can_post_no_redirect_or_token_forwarding(self):
        session=Mock(); audit=ScopeDeploymentAudit(session)
        audit.request('/api/token/',method='POST',authenticated=False,json={'username':'synthetic','password':'fake'})
        args,kwargs=session.request.call_args
        self.assertEqual(args,('POST','https://anime-dashboard-api-dv0s.onrender.com/api/token/'))
        self.assertFalse(kwargs['allow_redirects']); self.assertNotIn('Authorization',kwargs['headers'])
