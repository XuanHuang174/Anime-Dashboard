"""M3 audit tool permissions: mocked HTTP only, no credentials/DB/network."""
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from scripts.verify_recommendation_controlled_deployment import ControlledDeploymentAudit, main


class ControlledDeploymentGuards(SimpleTestCase):
    def test_invalid_configuration_never_constructs_client(self):
        for args in ([], ['--deployment-confirmed'], ['--expected-account', 'test.invalid'],
            ['--deployment-confirmed', '--expected-account', ' '],
            ['--deployment-confirmed', '--expected-account', 'test.invalid', '--adult-id', '-1'],
            ['--deployment-confirmed', '--expected-account', 'test.invalid', '--unknown-id', '2147483648'],
            ['--deployment-confirmed', '--expected-account', 'test.invalid', '--admin-view', 'enable']):
            with self.subTest(args=args), patch('scripts.verify_recommendation_controlled_deployment.ControlledDeploymentAudit') as client:
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit): main(args)
                client.assert_not_called()

    def test_business_mutations_and_cross_user_reads_block_before_transport(self):
        session = Mock(); audit = ControlledDeploymentAudit(session)
        for path, method in [('/api/recommendations/admin/control/', 'POST'),
            ('/api/recommendations/admin/applications/', 'GET'),
            ('/api/recommendations/admin/applications/1/', 'GET'),
            ('/api/recommendations/eligibility/apply/', 'POST'),
            ('/api/recommendations/2/want/', 'POST'),
            ('/api/recommendations/feedback/undo/', 'POST'),
            ('/api/recommendations/preferences/', 'PATCH'),
            ('/api/recommendations/', 'POST'), ('https://foreign.invalid/', 'GET')]:
            with self.subTest(path=path), self.assertRaises(ValueError): audit.request(path, method=method)
        session.request.assert_not_called()

    def test_frontend_never_receives_credentials_or_arbitrary_paths(self):
        session = Mock(); audit = ControlledDeploymentAudit(session)
        audit.headers['Authorization'] = 'Bearer SYNTHETIC_SECRET'
        for path in ('/assets/../secret.js', '//foreign.invalid/a.js', '/api/token/', '/assets/a.js?token=x'):
            with self.assertRaises(ValueError): audit.request(path, public=True, authenticated=False)
        with self.assertRaises(ValueError): audit.request('/assets/entry-hash.js', public=True)
        session.request.assert_not_called()
        audit.request('/assets/entry-hash.js', public=True, authenticated=False)
        args, kwargs = session.request.call_args
        self.assertEqual(args, ('GET', 'https://xh-anime.com/assets/entry-hash.js'))
        self.assertNotIn('Authorization', kwargs['headers']); self.assertFalse(kwargs['allow_redirects'])

    def test_only_normal_login_post_and_bounded_timeouts(self):
        session = Mock(); audit = ControlledDeploymentAudit(session)
        audit.request('/api/token/', method='POST', authenticated=False,
            json={'username': 'SYNTHETIC', 'password': 'FAKE'})
        args, kwargs = session.request.call_args
        self.assertEqual(args[0], 'POST'); self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], (10, 45)); self.assertNotIn('Authorization', kwargs['headers'])
        self.assertNotIn('FAKE', str(audit.report))
        session.reset_mock(); audit.report['requests'] = 40
        with self.assertRaises(ValueError): audit.request('/api/recommendations/context/')
        session.request.assert_not_called()

    def test_closed_superuser_state_required_and_only_get_used(self):
        for enabled in (False, True):
            session = Mock()
            denied = Mock(status_code=401)
            state = Mock(status_code=200, headers={'Cache-Control': 'private, no-store'})
            state.json.return_value = dict(runtime_enabled=False, enabled=enabled, effective_enabled=False, version=3)
            session.request.side_effect = [denied, state]
            audit = ControlledDeploymentAudit(session)
            with redirect_stderr(StringIO()):
                if enabled:
                    with self.assertRaises(AssertionError): audit.closed_admin_check('superuser')
                else: audit.closed_admin_check('superuser')
            self.assertTrue(all(call.args[0] == 'GET' for call in session.request.call_args_list))
            self.assertEqual(audit.report['checks']['both_global_gates_closed']['passed'], not enabled)

    def test_normal_account_denial_is_not_claimed_as_admin_state_inspection(self):
        session = Mock(); session.request.side_effect = [Mock(status_code=401), Mock(status_code=403)]
        audit = ControlledDeploymentAudit(session); audit.closed_admin_check('denied')
        self.assertNotIn('both_global_gates_closed', audit.report['checks'])
        self.assertFalse(audit.report['browser_tested']); self.assertFalse(audit.report['v2_enabled'])
