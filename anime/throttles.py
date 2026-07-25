from rest_framework.throttling import SimpleRateThrottle


class SensitiveAuthRateThrottle(SimpleRateThrottle):
    """Share a strict per-IP budget across sensitive public auth endpoints."""

    scope = "sensitive_auth"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {
            "scope": self.scope,
            "ident": ident,
        }
