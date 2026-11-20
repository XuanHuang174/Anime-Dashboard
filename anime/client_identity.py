"""Resolve only addresses supplied by a configured, trusted proxy chain."""
from functools import lru_cache
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import APIException


# Source: https://www.cloudflare.com/ips/ (verified 2026-11-20).
# Internal networks are trusted only in explicitly selected proxy mode.
RENDER_PROXY_CIDRS = (
    '127.0.0.0/8', '::1/128', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22', '2400:cb00::/32',
    '2606:4700::/32', '2803:f800::/32', '2405:b500::/32', '2405:8100::/32',
    '2a06:98c0::/29', '2c0f:f248::/32',
)


class ClientIdentityUnavailable(APIException):
    status_code = 503
    default_detail = '暂时无法确认访问来源，请稍后重试。'
    default_code = 'client_identity_unavailable'


class RateLimitStoreUnavailable(APIException):
    status_code = 503
    default_detail = '请求频率检查暂不可用，请稍后重试。'
    default_code = 'rate_limit_store_unavailable'


def private_digest(value):
    return salted_hmac('rate-limit.identity.v2', str(value), algorithm='sha256').hexdigest()


def _address(value):
    address = ip_address(value.strip())
    if '%' in str(address):
        raise ValueError('Scoped addresses are not accepted')
    return getattr(address, 'ipv4_mapped', None) or address


@lru_cache(maxsize=16)
def _networks(cidrs):
    return tuple(ip_network(value) for value in cidrs)


def client_identity(request):
    cached = getattr(request, '_resolved_client_identity', None)
    if cached is not None:
        return cached
    try:
        peer = _address(request.META.get('REMOTE_ADDR', ''))
        networks = _networks(tuple(settings.CLIENT_IP_TRUSTED_PROXY_CIDRS))
        trusted = lambda address: any(address in network for network in networks)
        if settings.CLIENT_IP_MODE == 'direct' or not trusted(peer):
            result = (str(peer), 'direct', 0)
        else:
            forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
            if not forwarded or len(forwarded) > 4096:
                raise ValueError('Missing or excessive forwarded chain')
            chain = forwarded.split(',')
            if len(chain) > 32:
                raise ValueError('Excessive proxy hops')
            # Ignore untrusted prefixes, including malformed spoofed values.
            for hops, value in enumerate(reversed(chain), 1):
                candidate = _address(value)
                if not trusted(candidate):
                    result = (str(candidate), 'trusted_proxy', hops)
                    break
            else:
                raise ValueError('No client beyond trusted proxies')
    except ValueError:
        # Never silently pool all visitors under a loopback address again.
        raise ClientIdentityUnavailable() from None
    request._resolved_client_identity = result
    return result
