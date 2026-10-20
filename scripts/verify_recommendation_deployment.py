"""6C bounded live GET audit. No Django setup, DB access, mutations or V2 switch.

Only normal /api/token/ login is POST. Credentials/tokens never enter reports.
Use --authenticated --expected-account with a human-authorized account only.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import requests

FRONT = 'https://xh-anime.com'
BACK = 'https://anime-dashboard-api-dv0s.onrender.com'
ENVELOPE = ('evidence', 'reason', 'evidence_token', 'evidence_context', 'evidence_disclaimer')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def valid_evidence(row, minimum_score=8.5):
    evidence, context = row.get('evidence') or {}, row.get('evidence_context') or {}
    source = evidence.get('source')
    tags = evidence.get('shared_tags')
    common = (row.get('content_rating') == 'non_adult'
        and evidence.get('contract_version') == 'recommendations.evidence.v1'
        and context.get('algorithm_version') == 'content.v1' and evidence.get('versions') == context
        and evidence.get('interpretation') == 'shared_genre_association'
        and evidence.get('is_probability') is False and evidence.get('is_exhaustive_ranking_explanation') is False
        and bool(re.fullmatch('[a-f0-9]{64}', row.get('evidence_token', '')))
        and isinstance(tags, list) and bool(tags) and all(isinstance(tag, str) for tag in tags)
        and len(tags) == len(set(tags)) and set(tags) <= set(row.get('tags', []))
        and isinstance(row.get('reason'), str) and bool(row['reason'].strip()))
    if not common:
        return False
    if evidence.get('kind') == 'explicit_preferences':
        return source is None
    if evidence.get('kind') != 'rated_work' or not isinstance(source, dict):
        return False
    try:
        return (set(source) == {'bangumi_id', 'title', 'personal_score'}
            and type(source['bangumi_id']) is int and source['bangumi_id'] > 0
            and isinstance(source['title'], str) and bool(source['title'].strip())
            and not isinstance(source['personal_score'], bool) and minimum_score <= float(source['personal_score']) <= 10)
    except (KeyError, ValueError, TypeError):
        return False


class Audit:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.headers = {'Origin': FRONT}
        self.report = {'scope': '6c-deployment-readonly', 'checks': {}, 'requests': 0,
            'write_endpoints_called': [], 'v2_enabled': False, 'browser_tested': False,
            'limitations': ['No live cross-user or data mutation tests.',
                'Source score provenance independently verified only by isolated tests, not by scanning private journals.',
                'Public timings are diagnostics, not V2 approval or a load test.']}

    def check(self, name, passed, **details):
        self.report['checks'][name] = {'passed': bool(passed), **details}
        print(json.dumps({'check': name, **self.report['checks'][name]}, ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(name)

    def request(self, path, *, public=False, authenticated=True, method='GET', **kwargs):
        if method != 'GET' and not (method == 'POST' and not public and path == '/api/token/'):
            raise ValueError('Only normal login POST is permitted')
        if not path.startswith('/') or path.startswith('//') or '://' in path:
            raise ValueError('Relative allowlisted service path required')
        if public and authenticated:
            raise ValueError('Credentials cannot be sent to frontend')
        self.report['requests'] += 1
        if self.report['requests'] > 40:
            raise ValueError('Bounded request budget exceeded')
        started = time.perf_counter()
        result = self.session.request(method, (FRONT if public else BACK) + path,
            headers=dict(self.headers) if authenticated else {'Origin': FRONT},
            timeout=(10, 45), allow_redirects=False, **kwargs)
        self.report.setdefault('request_seconds', []).append(round(time.perf_counter() - started, 3))
        return result

    def read(self, path, **kwargs):
        response = self.request(path, **kwargs)
        self.check(f'get_{self.report["requests"]}', response.status_code == 200, status=response.status_code)
        self.check(f'private_cache_{self.report["requests"]}',
            'private' in response.headers.get('Cache-Control', '') and 'no-store' in response.headers.get('Cache-Control', ''))
        return response.json()

    def public_checks(self, expected_dist=None):
        response = self.request('/recommendations', public=True, authenticated=False)
        self.check('frontend_route', response.status_code == 200)
        match = re.search(r'src="(/assets/[A-Za-z0-9_-]+\.js)"', response.text)
        self.check('entry_found', match is not None)
        entry = self.request(match[1], public=True, authenticated=False)
        self.check('entry_status', entry.status_code == 200)
        names = sorted(set(re.findall(r'Recommendations-[A-Za-z0-9_-]+\.(?:js|css)', entry.text)))
        self.check('recommendation_chunks_found', len(names) == 2)
        for name in names:
            response = self.request('/assets/' + name, public=True, authenticated=False)
            self.check('asset_available_' + name, response.status_code == 200)
            if expected_dist:
                local = expected_dist / 'assets' / name
                self.check('asset_matches_build_' + name, local.is_file()
                    and hashlib.sha256(local.read_bytes()).digest() == hashlib.sha256(response.content).digest())
        self.report['tested_build_hashes_checked'] = bool(expected_dist)
        for endpoint in ('', 'profile/', 'preferences/', '1/'):
            self.check('unauthenticated_' + (endpoint or 'list'), self.request('/api/recommendations/' + endpoint,
                       authenticated=False).status_code == 401)

    def authenticated_checks(self, expected_account, blocked_ids):
        account = getpass.getpass('Authorized account (masked): ')
        self.check('exact_authorized_account', account.casefold() == expected_account.casefold())
        password = getpass.getpass('Password (masked): ')
        response = self.request('/api/token/', authenticated=False, method='POST',
                                json={'username': account, 'password': password})
        password = None
        self.check('normal_login', response.status_code == 200)
        self.headers['Authorization'] = 'Bearer ' + response.json()['access']
        response = self.request('/api/user/profile/')
        self.check('server_identity', response.status_code == 200
                   and response.json().get('username', '').casefold() == expected_account.casefold())
        preferences = self.read('/api/recommendations/preferences/')
        profile = self.read('/api/recommendations/profile/')
        first = None
        for category in ('all', 'familiar', 'explore'):
            page = self.read('/api/recommendations/', params={'category': category, 'page_size': 3})
            self.check('default_v1_' + category, page.get('algorithm_version') == 'content.v1')
            self.check('6a_contract_' + category, page.get('evidence_contract_version') == 'recommendations.evidence.v1')
            self.check('profile_state_' + category, page.get('cold_start_state') == profile.get('cold_start_state'))
            rows = page['results']
            self.check('evidence_structure_' + category, all(valid_evidence(row)
                and row['evidence_context'] == page['evidence_context'] for row in rows), checked_rows=len(rows))
            if rows:
                row = rows[0]
                detail = self.read(f'/api/recommendations/{row["bangumi_id"]}/', params={'evidence_token': row['evidence_token']})
                self.check('list_detail_parity_' + category, all(row[key] == detail[key] for key in ENVELOPE))
            if category == 'all':
                first = page
        repeat = self.read('/api/recommendations/', params={'category': 'all', 'page_size': 3})
        self.check('repeat_stability', repeat == first)
        if first['next_page']:
            second = self.read('/api/recommendations/', params={'category': 'all', 'page_size': 3, 'page': 2,
                'state_token': first['state_token']})
            self.check('pagination_epoch', second['state_token'] == first['state_token']
                and second['evidence_context'] == first['evidence_context']
                and not ({row['bangumi_id'] for row in first['results']} & {row['bangumi_id'] for row in second['results']}))
        if first['results']:
            row = first['results'][0]
            path = f'/api/recommendations/{row["bangumi_id"]}/'
            token = row['evidence_token']
            forged = ('0' if token[0] != '0' else '1') + token[1:]
            failure = self.request(path, params={'evidence_token': forged})
            self.check('stale_proof_rejected', failure.status_code == 409
                and not any(key in failure.json() for key in ENVELOPE))
            for name, query in (('identity_override', {'user_id': 1}), ('algorithm_override', {'algorithm_version': 'content.v2'})):
                self.check(name + '_rejected', self.request(path, params=query).status_code == 400)
        for name, identity in blocked_ids.items():
            if identity is None:
                self.report['checks'][name + '_blocked'] = {'status': 'not_verified_no_supplied_fixture'}
            else:
                self.check(name + '_supplied_id_blocked', self.request(f'/api/recommendations/{identity}/').status_code == 404)
        self.check('preferences_unchanged', digest(preferences) == digest(self.read('/api/recommendations/preferences/')))
        self.check('profile_unchanged', digest(profile) == digest(self.read('/api/recommendations/profile/')))
        self.report['nonempty_evidence_checked'] = bool(first['results'])

    def close(self):
        self.headers.pop('Authorization', None)
        self.session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authenticated', action='store_true')
    parser.add_argument('--expected-account', help='Only the account explicitly authorized by its owner')
    parser.add_argument('--expected-dist', type=Path, help='Compare JS/CSS SHA256 to the tested local dist')
    parser.add_argument('--adult-id', type=int, help='Operator-confirmed PUBLIC adult fixture Bangumi ID; never a record ID')
    parser.add_argument('--unknown-id', type=int, help='Operator-confirmed PUBLIC unknown-rating fixture Bangumi ID')
    args = parser.parse_args(argv)
    if args.authenticated and not args.expected_account:
        parser.error('--authenticated requires --expected-account; never select an account automatically')
    if any(value is not None and value < 1 for value in (args.adult_id, args.unknown_id)):
        parser.error('Public fixture IDs must be positive')
    audit = Audit()
    try:
        audit.public_checks(args.expected_dist)
        if args.authenticated:
            audit.authenticated_checks(args.expected_account, {'adult': args.adult_id, 'unknown_rating': args.unknown_id})
        audit.report['result'] = 'passed_scoped_readonly' if args.authenticated else 'passed_public_only'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            audit.report['failed_check'] = str(exc)
        # Do not print exception bodies or request URLs containing evidence tokens.
    finally:
        audit.close()
        root = Path(__file__).resolve().parents[1] / '.tools/deployment-6c'
        root.mkdir(parents=True, exist_ok=True)
        path = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as output:
            json.dump(audit.report, output, ensure_ascii=False, indent=2)
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'].startswith('passed') else 1


if __name__ == '__main__':
    sys.exit(main())
