"""11F bounded live GET audit. Login is the only optional POST.

No Django/.env/database, flag changes or journal mutations. Credentials and
response bodies remain in memory; reports contain check names and timings only.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_recommendation_deployment import Audit, digest


class ExplorationDeploymentAudit(Audit):
    def __init__(self, session=None):
        super().__init__(session)
        self.report.update(scope='11f-authorized-readonly', release_verified=False, frontend_bundle_verified=False, v2_enabled=None,
            server_sql_count=None, server_cpu_ms=None, server_memory_bytes=None, server_cache_hit_rate=None)
        self.report['limitations'].append('HTTP latency is not server CPU/SQL/cache telemetry. No live writes or switch changes.')

    def request(self, path, *, method='GET', **kwargs):
        allowed = (path in ('/', '/api/token/', '/api/user/profile/', '/api/recommendations/',
            '/api/recommendations/capabilities/', '/api/recommendations/profile/',
            '/api/recommendations/preferences/', '/api/recommendations/feedback/hidden/')
            or re.fullmatch(r'/assets/[A-Za-z0-9_-]+\.(?:js|css)', path)
            or re.fullmatch(r'/api/recommendations/explore/(?:contract/|catalog/|options/|context/|[1-9]\d*/)?', path))
        if not allowed or method != 'GET' and not (method == 'POST' and path == '/api/token/'):
            raise ValueError('Only allowlisted reads and normal login are permitted')
        if path == '/api/token/' and method != 'POST':
            raise ValueError('Login endpoint only')
        return super().request(path, method=method, **kwargs)

    def public_checks(self, expected_dist=None):
        for path in ('/api/recommendations/capabilities/', '/api/recommendations/explore/', '/api/recommendations/explore/options/'):
            self.check('protected_' + path.rsplit('/', 2)[-2], self.request(path, authenticated=False).status_code == 401)
        front = self.request('/', public=True, authenticated=False)
        self.check('frontend_shell', front.status_code == 200 and '<html' in front.text.lower())
        if expected_dist:
            match = re.search(r'src="(/assets/[A-Za-z0-9_-]+\.js)"', front.text)
            self.check('frontend_entry_found', match is not None)
            entry = self.request(match[1], public=True, authenticated=False)
            self.check('frontend_entry_loaded', entry.status_code == 200)
            names = sorted(set(re.findall(r'Recommendations-[A-Za-z0-9_-]+\.(?:js|css)', entry.text)))
            self.check('frontend_exploration_chunks', len(names) == 2)
            for name in names:
                response = self.request('/assets/' + name, public=True, authenticated=False)
                local = expected_dist / 'assets' / name
                self.check('frontend_sha256_' + name, response.status_code == 200 and local.is_file()
                    and hashlib.sha256(local.read_bytes()).digest() == hashlib.sha256(response.content).digest())
            self.report['frontend_bundle_verified'] = True
        self.report['limitations'].append('Public shell/401 checks alone do not establish authenticated release or browser acceptance.')

    def authorize(self, account):
        token = os.environ.get('EXPLORATION_AUDIT_TOKEN', '').strip()
        if not token:
            password = getpass.getpass('Authorized account password (masked, never saved): ')
            response = self.request('/api/token/', authenticated=False, method='POST', json={'username': account, 'password': password})
            password = None
            self.check('normal_login', response.status_code == 200)
            token = response.json()['access']
        self.headers['Authorization'] = 'Bearer ' + token
        token = None
        identity = self.request('/api/user/profile/')
        self.check('exact_authorized_account', identity.status_code == 200 and identity.json().get('username') == account)

    def wire(self, kind, data):
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node is required for actual frontend contract verification')
        filename = 'verifyExplorationOptions.mjs' if kind == 'options' else 'verifyExplorationWire.mjs'
        result = subprocess.run([node, str(ROOT / 'frontend/tests/fixtures' / filename)],
            input=json.dumps(data), encoding='utf-8', capture_output=True, cwd=ROOT, timeout=30)
        self.check('frontend_wire_' + str(self.report['requests']), result.returncode == 0)

    def snapshot(self, params):
        first = self.read('/api/recommendations/explore/', params=params)
        self.wire('page', {'page': first})
        self.check('independent_' + params['intent'], first['intent'] == params['intent'] and first['query']['intent'] == params['intent'])
        if not first['results']:
            self.report['limitations'].append('An audited query had no results; its detail and continuation checks were unavailable.')
            return first
        row = first['results'][0]
        scope = {'content_scope': 'include_adult'} if params.get('content_scope') == 'include_adult' else {}
        full = self.read(f'/api/recommendations/explore/{row["bangumi_id"]}/', params={**scope,
            'detail_context': row['detail_context'], 'evidence_token': row['evidence_token']})
        self.wire('page', {'page': first, 'detail': full})
        context = self.read('/api/recommendations/explore/context/', params={**scope, 'snapshot_context': first['snapshot_context']})
        self.check('snapshot_context_' + params['intent'], context.get('valid') is True and context.get('intent') == first['intent']
            and context.get('snapshot_id') == first['snapshot_id'] and context.get('evidence_context') == first['evidence_context'])
        if first['next_page_cursor']:
            query = {**scope, 'cursor': first['next_page_cursor']}
            following = self.read('/api/recommendations/explore/', params=query)
            repeated = self.read('/api/recommendations/explore/', params=query)
            self.wire('page', {'page': first, 'next': following})
            self.wire('page', {'page': first, 'next': repeated})
            # TimestampSigner may re-sign identical detail bodies in another
            # second. Compare every public/evidence field, not signature bytes.
            stable = lambda rows: [{key: value for key, value in row.items() if key != 'detail_context'} for row in rows]
            self.check('cursor_retry_' + params['intent'], stable(following['results']) == stable(repeated['results'])
                and following['snapshot_id'] == repeated['snapshot_id']
                and following['snapshot_expires_at'] == repeated['snapshot_expires_at'])
        else:
            self.report['limitations'].append('An audited query had no second page; continuation was not verified for that query.')
        return first

    def authenticated_checks(self, expected, seed, adult_seed=None):
        self.report['expected_enabled'] = expected
        before = {path: digest(self.read('/api/recommendations/' + path)) for path in ('profile/', 'preferences/', 'feedback/hidden/')}
        caps = self.read('/api/recommendations/capabilities/')
        c = caps.get('exploration', {})
        self.check('deployed_capability', c.get('contract_version') == 'recommendations.explore.capabilities.v1'
            and c.get('rollout_contract_version') == 'recommendations.explore.rollout.v1'
            and c.get('enabled') is expected and c.get('catalog_search_enabled') is expected and c.get('query_execution_enabled') is expected
            and c.get('supported_intents') == ['similar', 'tags']
            and [row.get('available') for row in c.get('navigation', [])] == [True, expected, expected])
        self.check('legacy_v1_unaffected', caps.get('algorithm_version') == 'content.v1' and caps.get('browse_enabled') is True)
        self.read('/api/recommendations/', params={'browse': 'true', 'page_size': 12})
        if expected:
            contract = self.read('/api/recommendations/explore/contract/')
            self.check('contract_and_capability_agree', contract.get('capabilities') == c)
            options = self.read('/api/recommendations/explore/options/')
            self.wire('options', options)
            chosen = self.read('/api/recommendations/explore/catalog/', params={'q': str(seed)})
            self.check('exact_public_seed', len(chosen.get('results', [])) == 1
                and chosen['results'][0].get('bangumi_id') == seed and chosen['results'][0].get('can_seed') is True)
            self.snapshot({'intent': 'similar', 'seed_bangumi_id': seed})
            included = chosen['results'][0]['tags'][:2]
            base = {'intent': 'tags', 'include_tags': ','.join(included)}
            self.snapshot({**base, 'match': 'all', 'sort': 'relevance'})
            for sort in ('relevance', 'score'):
                data = self.read('/api/recommendations/explore/', params={**base, 'match': 'any', 'sort': sort})
                self.wire('page', {'page': data})
                self.check('server_sort_' + sort, data['query']['sort'] == sort and data['query']['match'] == 'any')
            excluded = next(tag for tag in contract['available_tags'] if tag not in included)
            data = self.read('/api/recommendations/explore/', params={**base, 'exclude_tags': excluded})
            self.wire('page', {'page': data})
            self.check('explicit_hard_exclusion', data['query']['exclude_tags'] == [excluded])
            if adult_seed:
                data = self.read('/api/recommendations/explore/', params={'intent': 'similar', 'seed_bangumi_id': adult_seed, 'content_scope': 'include_adult'})
                self.wire('page', {'page': data})
                self.check('explicit_adult_scope', data['content_scope_context']['content_scope'] == 'include_adult')
            else:
                self.report['limitations'].append('Live adult scope not requested; synthetic isolated coverage only.')
        else:
            for path, params in [('contract/', {}), ('options/', {}), ('catalog/', {'q': '1'}), ('', {'intent': 'tags', 'include_tags': '音乐'})]:
                response = self.request('/api/recommendations/explore/' + path, params=params)
                self.check('closed_' + (path or 'query'), response.status_code == 503 and response.json().get('code') == 'exploration_disabled')
        for path, fingerprint in before.items():
            self.check('unchanged_' + path, fingerprint == digest(self.read('/api/recommendations/' + path)))
        self.report['release_verified'] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed', action='store_true')
    parser.add_argument('--expect', choices=('public', 'enabled', 'disabled'), required=True)
    parser.add_argument('--expected-account')
    parser.add_argument('--seed-bangumi-id', type=int)
    parser.add_argument('--adult-seed-id', type=int)
    parser.add_argument('--expected-dist', type=Path, help='Compare deployed recommendation JS/CSS to this tested build')
    args = parser.parse_args(argv)
    if not args.deployment_confirmed:
        parser.error('Confirm the deployment target before any network client is created.')
    if args.expect != 'public' and not args.expected_account:
        parser.error('An exact authorized account is required for authenticated checks.')
    if args.expect == 'enabled' and args.seed_bangumi_id is None:
        parser.error('Enabled acceptance requires an operator-selected public Bangumi seed ID.')
    if any(value is not None and not 1 <= value <= 2147483647 for value in (args.seed_bangumi_id, args.adult_seed_id)):
        parser.error('Invalid public Bangumi ID.')
    if args.adult_seed_id and args.expect != 'enabled':
        parser.error('Adult scope is only an explicit part of enabled acceptance.')
    if args.expected_dist and not args.expected_dist.is_dir():
        parser.error('Expected build directory does not exist.')
    audit = ExplorationDeploymentAudit()
    try:
        audit.public_checks(args.expected_dist)
        if args.expect != 'public':
            audit.authorize(args.expected_account)
            audit.authenticated_checks(args.expect == 'enabled', args.seed_bangumi_id, args.adult_seed_id)
        audit.report['result'] = 'passed_public_baseline_only' if args.expect == 'public' else 'passed_scoped_readonly'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            audit.report['failed_check'] = str(exc)
    finally:
        audit.close()
        folder = ROOT / '.tools/deployment-11f'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        path.write_text(json.dumps(audit.report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'].startswith('passed_') else 1


if __name__ == '__main__':
    sys.exit(main())
