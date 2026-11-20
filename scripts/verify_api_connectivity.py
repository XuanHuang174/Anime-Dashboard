"""Read-only DNS/HTTPS/CORS diagnostics. No login, tokens, writes or shell needed."""
import argparse
import json
import re
import socket
from urllib.parse import urlsplit

import requests


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError('Use an HTTPS origin only, without path, credentials or query.')
    return value.rstrip('/')


def verify(api_origin, frontend_origin, session=None):
    client = session or requests.Session()
    if session is None:
        # Tests the OS route after the user switches networks, not an env HTTP proxy.
        # OS/TUN/VPN routing and browser Secure DNS can still differ.
        client.trust_env = False
    report = {'api_origin': api_origin, 'frontend_origin': frontend_origin, 'checks': {},
              'writes': 0, 'login_attempts': 0, 'limitations': [
                  'OS DNS/HTTPS checks cannot reproduce browser Secure DNS, browser proxy rules or extensions.',
                  'Run once with proxy enabled and once disabled on the affected PC; phone results may differ.',
                  'This script does not modify DNS, deployment configuration, records or rate-limit counters.',
              ]}
    checks = report['checks']
    hostname = urlsplit(api_origin).hostname
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        checks['os_dns'] = {'passed': bool(addresses), 'answer_count': len({item[4][0] for item in addresses})}
    except OSError:
        checks['os_dns'] = {'passed': False, 'code': 'dns_resolution_failed'}
    headers = {'Origin': frontend_origin, 'Accept': 'application/json'}
    for name, method, path, extra in [
        ('health', 'GET', '/', {}),
        ('public_api', 'GET', '/api/showcase-profile/', {}),
        ('cors_preflight', 'OPTIONS', '/api/user/profile/', {
            'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'authorization'}),
    ]:
        try:
            response = client.request(method, api_origin + path, headers={**headers, **extra},
                                      timeout=(8, 35), allow_redirects=False)
            if name == 'cors_preflight':
                allowed = {item.strip().lower() for item in response.headers.get('Access-Control-Allow-Headers', '').split(',')}
                passed = (200 <= response.status_code < 300
                          and response.headers.get('Access-Control-Allow-Origin') == frontend_origin
                          and 'authorization' in allowed
                          and 'GET' in response.headers.get('Access-Control-Allow-Methods', '').split(', '))
            else:
                try:
                    data = response.json()
                except ValueError:
                    data = None
                passed = response.status_code == 200 and isinstance(data, dict)
                if name == 'public_api':
                    passed = passed and response.headers.get('Access-Control-Allow-Origin') == frontend_origin
            # No raw body, headers, cookies, IP addresses or user data in output.
            checks[name] = {'passed': bool(passed), 'status': response.status_code}
        except requests.RequestException as exc:
            checks[name] = {'passed': False, 'code': type(exc).__name__}
    try:
        page = client.get(frontend_origin + '/', timeout=(8, 35), allow_redirects=False)
        scripts = re.findall(r'<script[^>]+src=["\'](/assets/index-[^"\']+\.js)["\']', page.text)
        if page.status_code == 200 and scripts:
            bundle = client.get(frontend_origin + scripts[0], timeout=(8, 35), allow_redirects=False)
            checks['frontend_api_origin'] = {'passed': bundle.status_code == 200 and api_origin in bundle.text,
                                             'status': bundle.status_code}
        else:
            checks['frontend_api_origin'] = {'passed': False, 'code': 'bundle_not_found'}
    except requests.RequestException as exc:
        checks['frontend_api_origin'] = {'passed': False, 'code': type(exc).__name__}
    report['result'] = 'passed_connectivity_readonly' if all(item['passed'] for item in checks.values()) else 'failed_or_blocked'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-origin', type=origin, default='https://anime-dashboard-api-dv0s.onrender.com')
    parser.add_argument('--frontend-origin', type=origin, default='https://xh-anime.com')
    args = parser.parse_args()
    result = verify(args.api_origin, args.frontend_origin)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['result'] == 'passed_connectivity_readonly' else 1)
