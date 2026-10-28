"""13F fixed-target, bounded live GET acceptance. Normal login is the only POST.

Credentials, signed contexts, titles and own statuses stay in memory. This does
not perform business writes or assert that browser/server resource QA occurred.
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

KINDS = ('voice_actor', 'studio')
BASE = '/api/recommendations/credits/'


class CreditsDeploymentAudit(Audit):
    def __init__(self, session=None, response_budget=10):
        super().__init__(session)
        if not 0 < response_budget <= 10: raise ValueError('13F fixed HTTP budget must be at most 10 seconds')
        self.response_budget = response_budget
        self.session.trust_env = False
        self.report.update(scope='13f-authorized-readonly', production_writes=False, browser_tested=False,
            frontend_bundle_verified=False, release_verified=False, v2_enabled=None,
            server_sql_count=None, server_memory_bytes=None)
        self.report['limitations'] = ['Only the exact authorized account is checked; use separate canary and ordinary accounts.',
            'HTTP timing is not server SQL/CPU/memory or load testing.',
            'Read-only acceptance does not test real writes, resources, animation or browser return flow.',
            'Without --expected-dist, deployed frontend bytes are not verified.']

    def request(self, path, *, method='GET', **kwargs):
        public = kwargs.get('public', False)
        frontend = public and not kwargs.get('authenticated', True) and (
            path == '/recommendations/credits' or re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.(?:js|css)', path))
        allowed = path in ('/api/token/', '/api/user/profile/', '/api/recommendations/',
            '/api/recommendations/capabilities/', '/api/recommendations/preferences/', '/api/recommendations/feedback/hidden/',
            '/api/recommendations/explore/')
        allowed = allowed or path in (BASE + 'search/',) or re.fullmatch(
            re.escape(BASE) + r'(?:subjects/[1-9]\d*/|entities/(?:voice_actor|studio)/[1-9]\d*/(?:works/|items/[1-9]\d*/)?|record/[1-9]\d*/)', path)
        if not (frontend if public else allowed) or method != 'GET' and not (method == 'POST' and path == '/api/token/' and not public):
            raise ValueError('Only allowlisted reads and normal login are allowed')
        response = super().request(path, method=method, **kwargs)
        seconds = self.report['request_seconds'][-1]
        self.check('http_budget_' + str(self.report['requests']), seconds <= self.response_budget,
                   seconds=seconds, budget=self.response_budget)
        return response

    def authorize(self, account):
        token = os.environ.get('CREDITS_AUDIT_TOKEN', '').strip()
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

    def wire(self, operation, data, **fields):
        node = shutil.which('node')
        if not node: raise RuntimeError('Existing Node is required for actual frontend contract validation')
        result = subprocess.run([node, str(ROOT / 'frontend/tests/fixtures/verifyCreditsReleaseWire.mjs')],
            input=json.dumps({'operation': operation, 'data': data, **fields}), encoding='utf-8', capture_output=True,
            cwd=ROOT, timeout=30)
        self.check('frontend_contract_' + str(self.report['requests']), result.returncode == 0)

    def public_checks(self, expected_dist=None):
        for path in (BASE + 'search/', BASE + 'subjects/1/', BASE + 'entities/studio/1/works/', BASE + 'entities/studio/1/items/1/'):
            self.check('protected_' + str(self.report['requests'] + 1), self.request(path, authenticated=False).status_code == 401)
        if not expected_dist: return
        response = self.request('/recommendations/credits', public=True, authenticated=False)
        self.check('frontend_route', response.status_code == 200)
        entry = re.search(r'src="(/assets/[A-Za-z0-9_-]+\.js)"', response.text)
        self.check('frontend_entry_found', entry is not None)
        script = self.request(entry[1], public=True, authenticated=False)
        local = expected_dist / entry[1].lstrip('/')
        self.check('entry_matches_build', script.status_code == 200 and local.is_file()
            and hashlib.sha256(local.read_bytes()).digest() == hashlib.sha256(script.content).digest())
        names = sorted(set(re.findall(r'CreditsExplorer-[A-Za-z0-9_-]+\.js', script.text)))
        self.check('credits_lazy_chunk_found', len(names) == 1)
        payload = self.request('/assets/' + names[0], public=True, authenticated=False)
        local = expected_dist / 'assets' / names[0]
        self.check('credits_chunk_matches_build', payload.status_code == 200 and local.is_file()
            and hashlib.sha256(local.read_bytes()).digest() == hashlib.sha256(payload.content).digest())
        self.report['frontend_bundle_verified'] = True

    def authenticated_checks(self, enabled, source, kinds=KINDS, scope='safe'):
        before = {k: digest(self.read('/api/recommendations/' + k)) for k in ('preferences/', 'feedback/hidden/')}
        caps = self.read('/api/recommendations/capabilities/')
        self.wire('capabilities', caps.get('credits'))
        flags = caps['credits']
        self.check('credits_capability_matches_account', all(flags[k] is (enabled and k in kinds) for k in KINDS))
        self.check('existing_three_modes_remain_open', [r.get('available') for r in caps.get('exploration', {}).get('navigation', [])] == [True, True, True])
        params = {'content_scope': scope} if scope == 'include_adult' else {}
        if not enabled:
            for path, query in ((BASE + 'search/', {'kind': 'studio', 'q': 'person:1'}),
                    (BASE + 'subjects/1/', {}), (BASE + 'entities/studio/1/works/', {})):
                response = self.request(path, params=query)
                self.check('credits_closed_' + str(self.report['requests']), response.status_code == 503 and response.json().get('code') == 'credits_disabled')
        else:
            summary = self.read(BASE + f'subjects/{source}/', params=params)
            self.wire('summary', summary, source=source, scope=scope)
            adult_seen = False
            for kind in kinds:
                section = summary['sections'][kind]
                self.check(kind + '_seed_source_observed', section['status'] == 'observed' and bool(section['results']))
                person = section['results'][0]['person_id']
                prefix = BASE + f'entities/{kind}/{person}/'
                search = self.read(BASE + 'search/', params={**params, 'kind': kind, 'q': f'person:{person}'})
                self.wire('search', search, kind=kind, scope=scope)
                self.check(kind + '_exact_entity_search', any(r['person_id'] == person for r in search['results']))
                entity = self.read(prefix, params=params); self.wire('entity', entity, kind=kind, person=person, scope=scope)
                page = self.read(prefix + 'works/', params={**params, 'hide_recorded': 'false'})
                self.wire('works', page, kind=kind, person=person, scope=scope)
                self.check(kind + '_visible_works', bool(page['results']))
                row = next((r for r in page['results'] if r['content_rating'] == 'adult'), page['results'][0])
                detail = self.read(prefix + f'items/{row["bangumi_id"]}/', params={**params, 'context': row['action_context']})
                self.wire('detail', detail, kind=kind, person=person, row=row, scope=scope)
                adult_seen |= row['content_rating'] == 'adult'
                completed = self.read(prefix + 'works/', params={**params, 'view': 'completed', 'hide_recorded': 'false'})
                self.wire('works', completed, kind=kind, person=person, scope=scope)
                self.check(kind + '_completed_only', all(r['watch_status'] == 'completed' for r in completed['results']))
                if page['next_cursor']:
                    following = self.read(prefix + 'works/', params={**params, 'cursor': page['next_cursor']})
                    self.wire('works', following, kind=kind, person=person, scope=scope, previous=page)
                else: self.report['limitations'].append(kind + ': no continuation cursor in selected live entity.')
            if scope == 'include_adult': self.check('actual_adult_detail_checked', adult_seen)
        self.read('/api/recommendations/')
        tags = self.read('/api/recommendations/explore/', params={**params, 'intent': 'tags', 'include_tags': '日常'})
        self.wire('legacy', tags, scope=scope)
        if source:
            similar = self.read('/api/recommendations/explore/', params={**params, 'intent': 'similar', 'seed_bangumi_id': source})
            self.wire('legacy', similar, scope=scope)
        after = {k: digest(self.read('/api/recommendations/' + k)) for k in before}
        self.check('preferences_and_feedback_unchanged', before == after)
        self.check('capabilities_stable', digest(caps) == digest(self.read('/api/recommendations/capabilities/')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed', action='store_true')
    parser.add_argument('--expect', choices=('public', 'enabled', 'excluded'), required=True)
    parser.add_argument('--expected-account')
    parser.add_argument('--seed-bangumi-id', type=int)
    parser.add_argument('--kinds', choices=('both', 'studio', 'voice_actor'), default='both')
    parser.add_argument('--scope', choices=('safe', 'include_adult'), default='safe')
    parser.add_argument('--expected-dist', type=Path)
    args = parser.parse_args(argv)
    if not args.deployment_confirmed: parser.error('Confirm the fixed deployment target before creating a network client.')
    if args.expect != 'public' and not args.expected_account: parser.error('An exact authorized account is required.')
    if args.expect == 'enabled' and (args.seed_bangumi_id is None or not 0 < args.seed_bangumi_id <= 2147483647):
        parser.error('Choose a known catalog subject with verified participants.')
    if args.seed_bangumi_id is not None and not 0 < args.seed_bangumi_id <= 2147483647: parser.error('Invalid seed ID.')
    if args.scope != 'safe' and args.expect != 'enabled': parser.error('Adult scope requires explicit enabled-account verification.')
    if args.expected_dist and not args.expected_dist.is_dir(): parser.error('Expected build directory does not exist.')
    audit = CreditsDeploymentAudit()
    try:
        audit.public_checks(args.expected_dist)
        if args.expect != 'public':
            audit.authorize(args.expected_account)
            audit.authenticated_checks(args.expect == 'enabled', args.seed_bangumi_id,
                KINDS if args.kinds == 'both' else (args.kinds,), args.scope)
        audit.report['result'] = 'passed_public_baseline_only' if args.expect == 'public' else 'passed_credits_readonly'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError): audit.report['failed_check'] = str(exc)
    finally:
        audit.close()
        folder = ROOT / '.tools/deployment-13f'; folder.mkdir(parents=True, exist_ok=True)
        path = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as file: json.dump(audit.report, file, ensure_ascii=False, indent=2)
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'].startswith('passed_') else 1


if __name__ == '__main__': sys.exit(main())
