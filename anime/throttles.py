"""Independent, bounded budgets; no anonymous browsing budget on auth views."""
from django.core.cache import caches
from rest_framework.exceptions import APIException
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle, ScopedRateThrottle

from .client_identity import client_identity, private_digest, RateLimitStoreUnavailable


class IdentityThrottleMixin:
    cache = caches['throttle']
    cache_format = 'rl.v2.%(scope)s.%(ident)s'

    def get_ident(self, request):
        return client_identity(request)[0]

    def allow_request(self, request, view):
        self.request = request
        scope = self.scope or getattr(view, 'throttle_scope', None)
        request._rate_limit_active_budget = {
            'scope': scope, 'rate': getattr(self, 'rate', self.THROTTLE_RATES.get(scope)),
        }
        try:
            return super().allow_request(request, view)
        except APIException:
            raise
        except (ConnectionError, TimeoutError, OSError):
            raise RateLimitStoreUnavailable() from None

    def throttle_failure(self):
        failures = getattr(self.request, '_rate_limit_failures', [])
        failures.append({'scope': self.scope, 'rate': self.rate})
        self.request._rate_limit_failures = failures
        return super().throttle_failure()


class BudgetThrottle(IdentityThrottleMixin, SimpleRateThrottle):
    def __init__(self):
        # Scope depends on endpoint, authenticated user and HTTP method.
        pass

    def allow_request(self, request, view):
        self.scope = self.budget(request, view)
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        authenticated = request.user and request.user.is_authenticated
        ident = f'user:{request.user.pk}' if authenticated else f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': private_digest(ident)}


class ApiBurstThrottle(BudgetThrottle):
    def budget(self, request, view):
        actor = 'account' if request.user and request.user.is_authenticated else 'public'
        operation = 'read' if request.method in ('GET', 'HEAD', 'OPTIONS') else 'write'
        return f'{actor}_{operation}'


class ApiSustainedThrottle(ApiBurstThrottle):
    def budget(self, request, view):
        return super().budget(request, view) + '_hour'


class SensitiveAuthRateThrottle(BudgetThrottle):
    def budget(self, request, view):
        return view.auth_throttle_scope

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': private_digest(f'ip:{self.get_ident(request)}')}


class AuthSustainedThrottle(SensitiveAuthRateThrottle):
    def budget(self, request, view):
        return super().budget(request, view) + '_hour'


class LoginAccountThrottle(SensitiveAuthRateThrottle):
    def budget(self, request, view):
        return 'auth_login_account'

    def get_cache_key(self, request, view):
        # Another IP cannot exhaust this account's login budget.
        body = request.data if isinstance(request.data, dict) else {}
        username = str(body.get('username', '')).strip().casefold()[:254]
        ident = private_digest(f'{self.get_ident(request)}\0{username}')
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class AccountRateThrottle(IdentityThrottleMixin, UserRateThrottle):
    def get_cache_key(self, request, view):
        ident = f'user:{request.user.pk}' if request.user and request.user.is_authenticated else f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': private_digest(ident)}


class LoggedScopedRateThrottle(IdentityThrottleMixin, ScopedRateThrottle):
    def get_cache_key(self, request, view):
        return AccountRateThrottle.get_cache_key(self, request, view)
