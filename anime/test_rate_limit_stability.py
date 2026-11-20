"""Synthetic requests only: no production traffic or database required."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.test import SimpleTestCase, override_settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from .client_identity import client_identity, RENDER_PROXY_CIDRS, ClientIdentityUnavailable
from .rate_limit_views import RateLimitStatusView
from .throttles import ApiBurstThrottle, ApiSustainedThrottle, IdentityThrottleMixin, AccountRateThrottle, LoggedScopedRateThrottle
from .views import CustomTokenObtainPairView, CustomTokenRefreshView, UserRegistrationView, PasswordResetRequestView


class PublicProbe(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (ApiBurstThrottle, ApiSustainedThrottle)

    def get(self, request):
        return Response({'ok': True})

    post = get


class LoginProbe(PublicProbe):
    auth_throttle_scope = 'auth_login'
    throttle_classes = CustomTokenObtainPairView.throttle_classes


class RefreshProbe(PublicProbe):
    auth_throttle_scope = 'auth_refresh'
    throttle_classes = CustomTokenRefreshView.throttle_classes


class RegistrationProbe(PublicProbe):
    auth_throttle_scope = 'auth_register'
    throttle_classes = UserRegistrationView.throttle_classes


class EmailProbe(PublicProbe):
    auth_throttle_scope = 'auth_email'
    throttle_classes = PasswordResetRequestView.throttle_classes


class ScopedProbe(PublicProbe):
    throttle_scope = 'recommendation_read'
    throttle_classes = (AccountRateThrottle, LoggedScopedRateThrottle)


@override_settings(CLIENT_IP_MODE='trusted_proxy', CLIENT_IP_TRUSTED_PROXY_CIDRS=RENDER_PROXY_CIDRS)
class RateLimitStabilityTests(SimpleTestCase):
    def setUp(self):
        caches['throttle'].clear()
        self.addCleanup(caches['throttle'].clear)
        self.factory = APIRequestFactory()
        rates = dict(api_settings.DEFAULT_THROTTLE_RATES)
        rates.update(public_read='2/minute', public_read_hour='3/hour',
                     account_read='2/minute', account_write='2/minute', recommendation_read='2/minute')
        self.rate_patch = patch.object(SimpleRateThrottle, 'THROTTLE_RATES', rates)
        self.rate_patch.start()
        self.addCleanup(self.rate_patch.stop)

    def request(self, view=PublicProbe, *, ip='198.51.100.10', method='get', user=None, data=None, **headers):
        req = getattr(self.factory, method)(
            '/', data=data or {}, format='json', REMOTE_ADDR='127.0.0.1',
            HTTP_X_FORWARDED_FOR=f'{ip}, 172.64.0.10, 10.0.0.2', **headers)
        if user is not None:
            force_authenticate(req, user=SimpleNamespace(pk=user, is_authenticated=True, is_staff=False))
        return view.as_view()(req)

    def test_different_visitors_do_not_share_proxy_budget(self):
        self.assertEqual([self.request().status_code for _ in range(3)], [200, 200, 429])
        self.assertEqual(self.request(ip='203.0.113.20').status_code, 200)

    def test_recommendation_scope_initializes_lazily_and_keeps_account_isolation(self):
        self.assertEqual([self.request(ScopedProbe, user=1).status_code for _ in range(3)], [200, 200, 429])
        self.assertEqual(self.request(ScopedProbe, user=2).status_code, 200)

    def test_public_reads_cannot_lock_login_registration_email_or_refresh(self):
        for _ in range(3):
            self.request()
        for view in (LoginProbe, RegistrationProbe, EmailProbe, RefreshProbe):
            self.assertEqual(self.request(view, method='post').status_code, 200)

    def test_login_budget_does_not_lock_other_auth_endpoints(self):
        for _ in range(3):
            self.assertEqual(self.request(LoginProbe, method='post', data={'username': 'alice'}).status_code, 200)
        self.assertEqual(self.request(LoginProbe, method='post', data={'username': 'alice'}).status_code, 429)
        self.assertEqual(self.request(LoginProbe, method='post', data={'username': 'bob'}).status_code, 200)
        self.assertEqual(self.request(LoginProbe, method='post', ip='203.0.113.20', data={'username': 'alice'}).status_code, 200)
        for view in (RegistrationProbe, EmailProbe, RefreshProbe):
            self.assertEqual(self.request(view, method='post').status_code, 200)

    def test_registration_and_email_each_have_their_own_limit(self):
        for view in (RegistrationProbe, EmailProbe):
            self.assertEqual([self.request(view, method='post').status_code for _ in range(4)], [200, 200, 200, 429])
        self.assertEqual(self.request(LoginProbe, method='post').status_code, 200)

    def test_account_read_and_write_budgets_are_independent_of_ip_and_other_accounts(self):
        self.assertEqual([self.request(user=1).status_code for _ in range(3)], [200, 200, 429])
        self.assertEqual(self.request(user=2).status_code, 200)
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual([self.request(user=1, method='post').status_code for _ in range(3)], [200, 200, 429])

    def test_sustained_budget_survives_short_window_and_declines_do_not_extend_it(self):
        with patch.object(SimpleRateThrottle, 'timer', staticmethod(lambda: 10000)):
            self.request(); self.request()
        with patch.object(SimpleRateThrottle, 'timer', staticmethod(lambda: 10061)):
            self.assertEqual(self.request().status_code, 200)
            response = self.request()
            self.assertEqual(response.status_code, 429)
            self.assertLessEqual(int(response['Retry-After']), 3600)
        with patch.object(SimpleRateThrottle, 'timer', staticmethod(lambda: 13601)):
            self.assertEqual(self.request().status_code, 200)

    def test_429_contains_readable_wait_and_private_trace_without_secrets(self):
        self.request(); self.request()
        with self.assertLogs('anime.rate_limits', level='WARNING') as captured:
            response = self.request(HTTP_RNDR_ID='synthetic-request-123', HTTP_AUTHORIZATION='secret-token', HTTP_COOKIE='secret-cookie')
        self.assertEqual(response.data['code'], 'rate_limited')
        self.assertEqual(response.data['retry_after'], int(response['Retry-After']))
        self.assertEqual(response['X-Request-ID'], 'synthetic-request-123')
        self.assertIn('no-store', response['Cache-Control'])
        logged = captured.output[0]
        for secret in ('198.51.100.10', 'secret-token', 'secret-cookie'):
            self.assertNotIn(secret, logged)
        event = json.loads(logged.split(':', 2)[-1])
        self.assertEqual(event['budgets'][0]['scope'], 'public_read')
        self.assertEqual(event['identity_source'], 'trusted_proxy')

    def test_business_cache_clear_does_not_evict_production_throttle_cache(self):
        security = LocMemCache('rate-stability-synthetic-security', {})
        business = LocMemCache('rate-stability-synthetic-business', {})
        with patch.object(IdentityThrottleMixin, 'cache', security):
            self.request(); self.request()
            business.clear()
            self.assertEqual(self.request().status_code, 429)
        security.clear()

    def test_counter_outage_returns_503_and_does_not_run_a_write(self):
        with patch.object(IdentityThrottleMixin.cache, 'get', side_effect=ConnectionError('synthetic-secret-url')):
            response = self.request(method='post', user=1)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['code'], 'rate_limit_store_unavailable')
        self.assertNotIn('synthetic-secret-url', str(response.data))
        with patch.object(IdentityThrottleMixin.cache, 'get', side_effect=ConnectionError('synthetic')):
            with self.assertLogs('anime.rate_limits', level='WARNING') as captured:
                self.request(ScopedProbe, user=1)
        event = json.loads(captured.output[0].split(':', 2)[-1])
        self.assertEqual(event['budgets'][0]['scope'], 'user')

    def test_invalid_proxy_chain_is_explicit_503_with_no_shared_fallback(self):
        req = self.factory.get('/', REMOTE_ADDR='127.0.0.1')
        response = PublicProbe.as_view()(req)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['code'], 'client_identity_unavailable')

    def test_status_endpoint_requires_staff_and_omits_raw_addresses(self):
        self.assertIn(self.request(RateLimitStatusView).status_code, (401, 403))
        self.assertEqual(self.request(RateLimitStatusView, user=1).status_code, 403)
        req = self.factory.get('/', REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='198.51.100.10, 172.64.0.10')
        force_authenticate(req, user=SimpleNamespace(pk=1, is_authenticated=True, is_staff=True))
        response = RateLimitStatusView.as_view()(req)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['identity_source'], 'trusted_proxy')
        self.assertNotIn('198.51.100.10', json.dumps(response.data))


class ClientIdentityTests(SimpleTestCase):
    def request(self, peer='127.0.0.1', chain='198.51.100.10, 172.64.0.10'):
        return APIRequestFactory().get('/', REMOTE_ADDR=peer, HTTP_X_FORWARDED_FOR=chain)

    @override_settings(CLIENT_IP_MODE='direct', CLIENT_IP_TRUSTED_PROXY_CIDRS=[])
    def test_direct_mode_ignores_spoofed_headers(self):
        self.assertEqual(client_identity(self.request(peer='203.0.113.5'))[0], '203.0.113.5')

    @override_settings(CLIENT_IP_MODE='trusted_proxy', CLIENT_IP_TRUSTED_PROXY_CIDRS=RENDER_PROXY_CIDRS)
    def test_trusted_suffix_ignores_spoofed_or_malformed_left_prefix(self):
        for prefix in ('203.0.113.8', 'garbage', '127.0.0.1'):
            self.assertEqual(client_identity(self.request(chain=f'{prefix}, 198.51.100.10, 172.64.0.10'))[0], '198.51.100.10')
        self.assertEqual(client_identity(self.request(peer='203.0.113.5'))[0], '203.0.113.5')

    @override_settings(CLIENT_IP_MODE='trusted_proxy', CLIENT_IP_TRUSTED_PROXY_CIDRS=RENDER_PROXY_CIDRS)
    def test_ipv6_and_mapped_ipv4_are_canonical(self):
        self.assertEqual(client_identity(self.request(chain='2001:db8::1, 2606:4700::1'))[0], '2001:db8::1')
        self.assertEqual(client_identity(self.request(chain='::ffff:198.51.100.10'))[0], '198.51.100.10')

    @override_settings(CLIENT_IP_MODE='trusted_proxy', CLIENT_IP_TRUSTED_PROXY_CIDRS=RENDER_PROXY_CIDRS)
    def test_missing_malformed_or_all_trusted_chain_never_uses_shared_loopback(self):
        for chain in ('', 'garbage', '127.0.0.1, 10.0.0.1', '198.51.100.1%eth0', ','.join(['1.1.1.1'] * 33)):
            with self.assertRaises(ClientIdentityUnavailable):
                client_identity(self.request(chain=chain))


class DeploymentVerifierTests(SimpleTestCase):
    def session(self, login_status=200, spoof_changes=False):
        from unittest.mock import Mock
        session = Mock()

        def request(method, url, **kwargs):
            payload = {'rate_limit_policy': 'rate-limits.v2'}
            status = 200
            if url.endswith('/api/token/'):
                status, payload = login_status, {'access': 'synthetic-secret-access'}
            elif url.endswith('/api/admin/rate-limit-status/'):
                spoofed = 'X-Forwarded-For' in kwargs.get('headers', {})
                payload = {'identity_source': 'trusted_proxy', 'mode': 'trusted_proxy',
                           'client_fingerprint': 'different' if spoofed and spoof_changes else 'synthetic-fingerprint',
                           'trusted_hops': 3, 'cache_backend': 'locmem'}
            response = Mock(status_code=status, headers={'Cache-Control': 'private, no-store'})
            response.json.return_value = payload
            return response

        session.request.side_effect = request
        return session

    def test_bounded_staff_check_and_spoof_comparison_never_print_credentials(self):
        from scripts.verify_rate_limit_deployment import verify
        session = self.session()
        report = verify('https://example.com', 'synthetic-account', session=session, password_prompt=lambda _: 'synthetic-secret-password')
        self.assertEqual(report['result'], 'passed_staff_readonly')
        self.assertEqual(report['requests'], 5)
        self.assertNotIn('synthetic-secret', json.dumps(report))
        session.close.assert_called_once()

    def test_public_only_and_failed_login_stop_without_further_probes(self):
        from scripts.verify_rate_limit_deployment import verify
        public = verify('https://example.com', 'synthetic-account', True, session=self.session())
        self.assertEqual(public['result'], 'passed_public_only')
        self.assertEqual(public['requests'], 1)
        failed = verify('https://example.com', 'synthetic-account', session=self.session(429), password_prompt=lambda _: 'synthetic')
        self.assertEqual(failed['requests'], 2)
        self.assertEqual(failed['checks']['normal_login'], False)
        spoofed = verify('https://example.com', 'synthetic-account', session=self.session(spoof_changes=True), password_prompt=lambda _: 'synthetic')
        self.assertEqual(spoofed['result'], 'failed_or_blocked')
        self.assertEqual(spoofed['requests'], 4)
