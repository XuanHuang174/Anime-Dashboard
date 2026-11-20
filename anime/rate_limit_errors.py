import json
import logging
import math
import re
from uuid import uuid4

from rest_framework.exceptions import Throttled
from rest_framework.views import exception_handler

from .client_identity import ClientIdentityUnavailable, RateLimitStoreUnavailable, client_identity, private_digest

logger = logging.getLogger('anime.rate_limits')


def rate_limit_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None or not isinstance(exc, (Throttled, ClientIdentityUnavailable, RateLimitStoreUnavailable)):
        return response
    request = context['request']
    supplied_id = request.META.get('HTTP_RNDR_ID', '')
    request_id = supplied_id if re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', supplied_id) else uuid4().hex
    code = 'rate_limited' if isinstance(exc, Throttled) else exc.default_code
    event = {'event': code,
             'request_id': request_id, 'method': request.method,
             'view': type(context['view']).__name__, 'status': response.status_code,
             'budgets': getattr(request, '_rate_limit_failures', [])}
    if not event['budgets'] and hasattr(request, '_rate_limit_active_budget'):
        event['budgets'] = [request._rate_limit_active_budget]
    try:
        address, source, hops = client_identity(request)
        event.update(client=private_digest(address)[:20], identity_source=source, trusted_hops=hops)
    except ClientIdentityUnavailable:
        event['identity_source'] = 'unresolved'
    if request.user and request.user.is_authenticated:
        event['account'] = private_digest(f'user:{request.user.pk}')[:20]
    if isinstance(exc, Throttled):
        wait = max(1, math.ceil(exc.wait or 1))
        response.data.update(code='rate_limited', retry_after=wait, request_id=request_id)
        response['Retry-After'] = str(wait)
        event['retry_after'] = wait
    else:
        response.data.update(code=code, request_id=request_id)
        response['Retry-After'] = '30'
    response['X-Request-ID'] = request_id
    response['Cache-Control'] = 'private, no-store'
    # No raw IP, path/query, account name, body, cookies or tokens.
    logger.warning(json.dumps(event, ensure_ascii=True, separators=(',', ':')))
    return response
