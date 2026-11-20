"""Bounded HTTPS checks; one normal login, then staff-only read-only probes.

Does not import Django/.env, save credentials, exercise rate limits, or write
business data. Never prints response bodies or tokens on a failed check.
"""
import argparse
import getpass
import json
from urllib.parse import urlsplit

import requests


def verify(base_url, account, public_only=False, session=None, password_prompt=getpass.getpass):
    parsed = urlsplit(base_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('An HTTPS origin without credentials, path or query is required')
    base_url = base_url.rstrip('/')
    session = session or requests.Session()
    report = {'scope': 'rate-limits.v2-readonly', 'checks': {}, 'requests': 0, 'business_writes': False}

    def request(method, path, **kwargs):
        report['requests'] += 1
        return session.request(method, base_url + path, timeout=(5, 30), allow_redirects=False, **kwargs)

    def check(name, passed):
        report['checks'][name] = bool(passed)
        if not passed:
            raise AssertionError(name)

    try:
        health = request('GET', '/')
        check('new_backend_deployed', health.status_code == 200 and health.json().get('rate_limit_policy') == 'rate-limits.v2')
        if public_only:
            report['result'] = 'passed_public_only'
            report['limitation'] = 'Health response does not verify client identity, authentication or frontend.'
            return report
        password = password_prompt('Account password (masked, never saved): ')
        login = request('POST', '/api/token/', json={'username': account, 'password': password})
        password = None
        check('normal_login', login.status_code == 200)
        token = login.json()['access']
        headers = {'Authorization': 'Bearer ' + token}
        baseline = request('GET', '/api/admin/rate-limit-status/', headers=headers)
        check('staff_status_access', baseline.status_code == 200)
        data = baseline.json()
        check('trusted_proxy_identity', data.get('identity_source') == 'trusted_proxy' and data.get('mode') == 'trusted_proxy')
        fingerprint = data.get('client_fingerprint')
        check('private_diagnostic', bool(fingerprint) and 'no-store' in baseline.headers.get('Cache-Control', ''))
        for number, spoofed in enumerate(('203.0.113.77', 'garbage, 198.51.100.99'), 1):
            response = request('GET', '/api/admin/rate-limit-status/', headers={**headers, 'X-Forwarded-For': spoofed})
            check(f'forwarded_spoof_ignored_{number}', response.status_code == 200 and response.json().get('client_fingerprint') == fingerprint)
        report.update(result='passed_staff_readonly', client_fingerprint=fingerprint,
                      identity_source=data['identity_source'], trusted_hops=data['trusted_hops'],
                      cache_backend=data['cache_backend'],
                      limitation='One network only; repeat from another network to compare fingerprints. Not a load test or frontend acceptance.')
    except (requests.RequestException, ValueError, KeyError, AssertionError) as exc:
        report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
    finally:
        session.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='https://anime-dashboard-api-dv0s.onrender.com')
    parser.add_argument('--account', default='XHcode')
    parser.add_argument('--public-only', action='store_true')
    args = parser.parse_args()
    result = verify(args.base_url, args.account, args.public_only)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(0 if result['result'].startswith('passed_') else 1)
