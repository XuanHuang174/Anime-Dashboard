"""N2-2 bounded deployment GET audit. Run only after confirmed deployment.

No Django, .env or DB imports. Login is the ONLY permitted POST. No qualification,
feedback, want, preferences or identity mutations; reports exclude credentials,
private titles, source ratings, result sets, scope revisions and cursor bodies.
"""
import argparse
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_recommendation_deployment import Audit, digest, ENVELOPE, valid_evidence


class ScopeDeploymentAudit(Audit):
    def __init__(self, session=None):
        super().__init__(session)
        self.report['scope'] = 'n2-2-closed-serving-readonly'

    def request(self, path, *, method='GET', public=False, **kwargs):
        allowed = path in ('/api/token/', '/api/user/profile/', '/api/recommendations/',
            '/api/recommendations/profile/', '/api/recommendations/preferences/',
            '/api/recommendations/eligibility/', '/api/recommendations/context/',
            '/api/recommendations/feedback/hidden/') or re.fullmatch(r'/api/recommendations/[1-9]\d*/', path)
        if public or not allowed:
            raise ValueError('Outside the closed-scope backend read-only allowlist')
        return super().request(path, method=method, public=False, **kwargs)

    def run(self, expected_account, blocked_ids):
        self.check('unauthenticated_qualification', self.request('/api/recommendations/eligibility/',
            authenticated=False).status_code == 401)
        account = getpass.getpass('Authorized account (masked): ')
        self.check('explicit_authorized_account', account.casefold() == expected_account.casefold())
        password = getpass.getpass('Password (masked): ')
        response = self.request('/api/token/', method='POST', authenticated=False,
                                json={'username': account, 'password': password})
        password = None
        self.check('normal_login', response.status_code == 200)
        self.headers['Authorization'] = 'Bearer ' + response.json()['access']
        response = self.request('/api/user/profile/')
        self.check('authenticated_identity', response.status_code == 200
            and response.json().get('username', '').casefold() == expected_account.casefold())
        prefs = self.read('/api/recommendations/preferences/')
        profile = self.read('/api/recommendations/profile/')
        hidden = self.read('/api/recommendations/feedback/hidden/')
        eligibility = self.read('/api/recommendations/eligibility/')
        self.check('manual_access_deployed', eligibility.get('contract_version') == 'recommendations.eligibility.manual.m2.v1'
            and eligibility.get('policy_version') == 'eligibility.manual.m2.v1'
            and eligibility.get('age_assurance_status') == 'not_performed'
            and eligibility.get('authorization_source') in ('none', 'legacy', 'manual')
            and type(eligibility.get('can_apply')) is bool
            and type(eligibility.get('manual_authorization_valid')) is bool)
        before = digest([prefs, profile, hidden, eligibility])
        self.check('adult_serving_closed', eligibility.get('adult_access_allowed') is False
            and eligibility.get('serving_integration') is False)
        first = self.read('/api/recommendations/', params={'browse':True,'page_size':3})
        scope = first.get('content_scope_context', {})
        self.check('closed_scope_deployed', scope.get('content_scope_contract_version') == 'recommendations.content-scope.n2-2.v1'
            and scope.get('content_scope_policy_version') == 'content-scope.controlled.m2.v1'
            and scope.get('content_release_policy_version') == 'release-review.ledger.closed.v1'
            and type(scope.get('content_review_version')) is int and scope['content_review_version'] >= 0
            and scope.get('content_scope') == 'safe' and scope.get('eligibility_version') == eligibility['eligibility_version']
            and bool(re.fullmatch('[a-f0-9]{64}', scope.get('scope_revision', ''))))
        self.check('v1_unchanged', first.get('algorithm_version') == 'content.v1')
        rows = first.get('results', [])
        self.check('safe_evidence', all(valid_evidence(row, minimum_score=float(prefs['effective_profile_min_score']))
            and all(row['evidence_context'].get(key) == value for key,value in scope.items()) for row in rows),
            checked_rows=len(rows))
        if rows:
            row = rows[0]
            detail = self.read(f'/api/recommendations/{row["bangumi_id"]}/', params={'evidence_token':row['evidence_token']})
            self.check('detail_evidence_parity', all(row[key] == detail[key] for key in ENVELOPE)
                and detail['content_scope_context'] == scope)
        if first.get('next_page_cursor'):
            continuation = self.read('/api/recommendations/', params={'cursor':first['next_page_cursor']})
            self.check('cursor_scope_isolation', continuation.get('snapshot_id') == first['snapshot_id']
                and continuation['content_scope_context'] == scope
                and not ({r['bangumi_id'] for r in rows} & {r['bangumi_id'] for r in continuation['results']}))
        for path in ('/api/recommendations/', '/api/recommendations/2147483647/',
                     '/api/recommendations/feedback/hidden/'):
            response = self.request(path, params={'content_scope':'include_adult'})
            self.check('explicit_closed_' + str(self.report['requests']), response.status_code == 403
                and response.json().get('code') == 'adult_scope_closed')
        for name,query in [('unknown_scope',{'content_scope':'all'}),('other_user',{'user_id':1}),
                           ('v2',{'algorithm_version':'content.v2'})]:
            self.check('reject_'+name,self.request('/api/recommendations/', params=query).status_code==400)
        for name,identity in blocked_ids.items():
            if identity is None:
                self.report['limitations'].append(name + ': no operator-confirmed PUBLIC fixture supplied; isolated tests only.')
            else:
                self.check(name + '_detail_blocked',self.request(f'/api/recommendations/{identity}/').status_code==404)
        after = [self.read('/api/recommendations/preferences/'),self.read('/api/recommendations/profile/'),
                 self.read('/api/recommendations/feedback/hidden/'),self.read('/api/recommendations/eligibility/')]
        self.check('readonly_state_stable', before == digest(after))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed',action='store_true')
    parser.add_argument('--expected-account',required=True,help='Explicitly authorized account only')
    parser.add_argument('--adult-id',type=int,help='Operator-confirmed public catalog ID, never a private record ID')
    parser.add_argument('--unknown-id',type=int)
    args=parser.parse_args(argv)
    if not args.deployment_confirmed:
        parser.error('Deployment must be confirmed before constructing a client or asking for credentials.')
    if any(value is not None and not 0 < value <= 2147483647 for value in (args.adult_id,args.unknown_id)):
        parser.error('Public fixture IDs must be positive int32 values.')
    audit=ScopeDeploymentAudit()
    try:
        audit.run(args.expected_account,{'adult':args.adult_id,'unknown':args.unknown_id})
        audit.report['result']='passed_scoped_readonly'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked',failure_type=type(exc).__name__)
        if isinstance(exc,AssertionError): audit.report['failed_check']=str(exc)
    finally:
        audit.close()
        folder=ROOT/'.tools/deployment-n2-2'; folder.mkdir(parents=True,exist_ok=True)
        path=folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        with path.open('x',encoding='utf-8') as output:
            json.dump(audit.report,output,ensure_ascii=False,indent=2)
        print(json.dumps({'result':audit.report['result'],'report_path':str(path)},ensure_ascii=False))
    return 0 if audit.report['result']=='passed_scoped_readonly' else 1


if __name__ == '__main__':
    sys.exit(main())
