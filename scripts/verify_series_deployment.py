"""12F bounded live GET checks. Only optional normal login can POST.

No Django/.env/DB access or business writes. Token/password, bodies, own record
statuses and signed contexts stay in memory; report contains check names/timings.
"""
import argparse
from datetime import datetime, timezone
import getpass
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


class SeriesDeploymentAudit(Audit):
    def __init__(self, session=None, response_budget=10):
        super().__init__(session)
        if not 0 < response_budget <= 45: raise ValueError('HTTP response budget must be 0–45 seconds')
        self.response_budget = response_budget
        self.session.trust_env = False
        self.report.update(scope='12f-authorized-readonly', browser_tested=False, production_writes=False,
            frontend_bundle_verified=False, v2_enabled=None, server_sql_count=None, server_memory_bytes=None)
        self.report['limitations'] = ['GET checks for the exact authorized account only; no production mutations or cross-user changes.',
            'HTTP latency is not server CPU/SQL/memory or a load test.',
            'Frontend contract validation does not verify deployed bundle hashes or replace browser acceptance.']

    def request(self, path, *, method='GET', **kwargs):
        allowed = path in ('/api/token/', '/api/user/profile/', '/api/recommendations/',
            '/api/recommendations/capabilities/', '/api/recommendations/preferences/', '/api/recommendations/feedback/hidden/',
            '/api/recommendations/explore/', '/api/recommendations/series/annotations/', '/api/recommendations/series/reminders/',
            '/api/recommendations/explore/series/', '/api/recommendations/explore/series/context/') or re.fullmatch(
                r'/api/recommendations/series/[1-9]\d*/workspace/', path)
        if not allowed or kwargs.get('public') or method != 'GET' and not (method == 'POST' and path == '/api/token/'):
            raise ValueError('Only fixed backend reads and normal login are allowed')
        response = super().request(path, method=method, **kwargs)
        seconds = self.report['request_seconds'][-1]
        self.check('http_budget_' + str(self.report['requests']), seconds <= self.response_budget, seconds=seconds, budget=self.response_budget)
        return response

    def authorize(self, account):
        token = os.environ.get('SERIES_AUDIT_TOKEN', '').strip()
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

    def public_checks(self):
        for path, params in (('/api/recommendations/series/1/workspace/', {}),
                ('/api/recommendations/series/annotations/', {'ids': '1'}), ('/api/recommendations/series/reminders/', {})):
            self.check('protected_' + str(self.report['requests'] + 1), self.request(path, params=params, authenticated=False).status_code == 401)

    def wire(self, kind, data, **fields):
        node = shutil.which('node')
        if not node: raise RuntimeError('Existing Node is required to validate real frontend contracts')
        result = subprocess.run([node, str(ROOT / 'frontend/tests/fixtures/verifySeriesReleaseWire.mjs')],
            input=json.dumps({'kind': kind, 'data': data, **fields}), encoding='utf-8', capture_output=True, cwd=ROOT, timeout=30)
        self.check('frontend_contract_' + str(self.report['requests']), result.returncode == 0)

    def authenticated_checks(self, expected, seed, adult_seed=None):
        before = {key: digest(self.read('/api/recommendations/' + key)) for key in ('preferences/', 'feedback/hidden/')}
        caps = self.read('/api/recommendations/capabilities/')
        self.report['deployed_algorithm_version'] = caps.get('algorithm_version')
        explore = caps.get('exploration', {})
        self.check('existing_three_modes_remain_open', [row.get('available') for row in explore.get('navigation', [])] == [True, True, True])
        self.check('series_capability_matches_account', explore.get('series_exploration', {}).get('enabled', False) is expected
            and explore.get('series_reminders', {}).get('enabled', False) is expected)
        if not expected:
            for path, params in (('/api/recommendations/series/1/workspace/', {}),
                    ('/api/recommendations/series/annotations/', {'ids': '1'}), ('/api/recommendations/series/reminders/', {}),
                    ('/api/recommendations/explore/series/', {'intent': 'similar', 'seed_bangumi_id': 1})):
                response = self.request(path, params=params)
                self.check('series_closed_' + str(self.report['requests']), response.status_code == 503 and response.json().get('code') in ('series_disabled', 'exploration_disabled'))
        else:
            self.scope_checks(seed, 'safe')
            if adult_seed: self.scope_checks(adult_seed, 'include_adult')
        # Original reads must remain usable under enabled AND withdrawn series gates.
        self.read('/api/recommendations/')
        if seed:
            legacy = self.read('/api/recommendations/explore/', params={'intent': 'similar', 'seed_bangumi_id': seed})
            self.wire('legacy', legacy)
        tags = self.read('/api/recommendations/explore/', params={'intent': 'tags', 'include_tags': '日常'})
        self.wire('legacy', tags)
        after = {key: digest(self.read('/api/recommendations/' + key)) for key in before}
        self.check('preferences_and_feedback_unchanged', before == after)
        self.check('capabilities_stable_during_audit', digest(self.read('/api/recommendations/capabilities/')) == digest(caps))

    def scope_checks(self, seed, scope):
        params = {'content_scope': scope} if scope == 'include_adult' else {}
        workspace = self.read(f'/api/recommendations/series/{seed}/workspace/', params=params)
        self.wire('workspace', workspace, source=seed, scope=scope)
        self.check('fresh_known_relations_' + scope, workspace.get('coverage') == 'observed' and bool(workspace.get('results')))
        ids = list(dict.fromkeys([seed] + [row['bangumi_id'] for row in workspace['results'][:23]]))
        annotations = self.read('/api/recommendations/series/annotations/', params={**params, 'ids': ','.join(map(str, ids))})
        self.wire('annotations', annotations, ids=ids, scope=scope)
        reminders = self.read('/api/recommendations/series/reminders/', params=params)
        self.wire('reminders', reminders, scope=scope)
        first = self.read('/api/recommendations/explore/series/', params={**params, 'intent': 'similar', 'seed_bangumi_id': seed, 'hide_recorded': False})
        self.wire('partition', first, scope=scope)
        self.check('partition_has_verified_related_' + scope, bool(first.get('series_context', {}).get('items')))
        context = self.read('/api/recommendations/explore/series/context/', params={**params, 'snapshot_context': first['snapshot_context']})
        self.check('partition_context_' + scope, context.get('valid') is True and context.get('snapshot_id') == first.get('snapshot_id')
            and context.get('series_context', {}).get('fingerprint') == first['series_context']['fingerprint'])
        if first['next_page_cursor']:
            following = self.read('/api/recommendations/explore/series/', params={**params, 'cursor': first['next_page_cursor']})
            self.wire('partition', following, scope=scope, previous=first)
        else:
            self.report['limitations'].append('No continuation cursor in one checked scope; paging covered only by isolated tests.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed', action='store_true')
    parser.add_argument('--expect', choices=('public', 'enabled', 'excluded'), required=True)
    parser.add_argument('--expected-account')
    parser.add_argument('--seed-bangumi-id', type=int)
    parser.add_argument('--adult-seed-id', type=int)
    parser.add_argument('--max-response-seconds', type=float, default=10)
    args = parser.parse_args(argv)
    if not args.deployment_confirmed: parser.error('Confirm the fixed deployment target before any network client is created.')
    if args.expect != 'public' and not args.expected_account: parser.error('An exact authorized account is required.')
    if args.expect == 'enabled' and not args.seed_bangumi_id: parser.error('Choose a known public seed with fresh verified direct relations.')
    if any(value is not None and not 1 <= value <= 2147483647 for value in (args.seed_bangumi_id, args.adult_seed_id)): parser.error('Invalid Bangumi ID.')
    if args.adult_seed_id and args.expect != 'enabled': parser.error('Adult verification is explicit enabled-scope acceptance only.')
    if not 0 < args.max_response_seconds <= 45: parser.error('HTTP response budget must be greater than zero and at most 45 seconds.')
    audit = SeriesDeploymentAudit(response_budget=args.max_response_seconds)
    try:
        audit.public_checks()
        if args.expect != 'public':
            audit.authorize(args.expected_account)
            audit.authenticated_checks(args.expect == 'enabled', args.seed_bangumi_id, args.adult_seed_id)
        audit.report['result'] = 'passed_public_baseline_only' if args.expect == 'public' else 'passed_series_readonly'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError): audit.report['failed_check'] = str(exc)
    finally:
        audit.close()
        folder = ROOT / '.tools/deployment-12f'; folder.mkdir(parents=True, exist_ok=True)
        path = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as file: json.dump(audit.report, file, ensure_ascii=False, indent=2)
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'].startswith('passed_') else 1


if __name__ == '__main__': sys.exit(main())
