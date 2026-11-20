import os

from django.conf import settings
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .client_identity import client_identity, private_digest


class RateLimitStatusView(APIView):
    """Read-only, staff-only verification without exposing raw request headers."""
    permission_classes = (IsAdminUser,)

    def get(self, request):
        address, source, hops = client_identity(request)
        response = Response({
            'policy': 'rate-limits.v2', 'mode': settings.CLIENT_IP_MODE,
            'identity_source': source, 'trusted_hops': hops,
            'client_fingerprint': private_digest(address)[:20],
            'cache_backend': settings.CACHES['throttle']['BACKEND'],
            'worker_pid': os.getpid(),
            'rates': settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'],
        })
        response['Cache-Control'] = 'private, no-store'
        return response
