"""9A/9B bounded deployment GET audit; login is the only permitted POST.

No Django, .env, database, journal mutation or V2 selection. Reports contain
assertions/timings only, never passwords, tokens, cursors or source works.
"""
import argparse
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_recommendation_feedback_deployment import FeedbackDeploymentAudit
from scripts.verify_recommendation_deployment import digest, valid_evidence, ENVELOPE


class BrowseDeploymentAudit(FeedbackDeploymentAudit):
    def __init__(self, session=None):
        super().__init__(session)
        self.report['scope'] = '9a-browse-deployment-authorized-readonly'

    def authenticated_browse_checks(self, account):
        password = getpass.getpass('Authorized test account password (masked): ')
        response = self.request('/api/token/', authenticated=False, method='POST',
                                json={'username': account, 'password': password})
        password = None
        self.check('normal_login', response.status_code == 200)
        self.headers['Authorization'] = 'Bearer ' + response.json()['access']
        identity = self.request('/api/user/profile/')
        self.check('exact_authorized_identity', identity.status_code == 200
                   and identity.json().get('username', '').casefold() == account.casefold())
        paths = ('profile/', 'preferences/', 'feedback/hidden/')
        before = {name: digest(self.read('/api/recommendations/' + name)) for name in paths}
        legacy = self.read('/api/recommendations/', params={'page_size': 24})
        first = self.read('/api/recommendations/', params={'browse': 'true'})
        self.check('browse_contract', first.get('browse_contract_version') == 'recommendations.browse.v1'
            and first.get('control_policy_version') == 'browse-controls.v1'
            and first.get('algorithm_version') == 'content.v1' and first.get('style') == 'balanced')
        self.check('bounded_shape', first['count'] <= 120 and first['batch_size'] == 24
            and first['page_size'] == 12 and first['batch'] == first['page'] == 1)
        self.check('balanced_legacy_parity', first['count'] == legacy['count']
            and first['results'] == legacy['results'][:12])
        self.check('real_evidence', all(valid_evidence(row) for row in first['results']),
                   checked_rows=len(first['results']))
        ids = lambda page: [row['bangumi_id'] for row in page['results']]
        if first['next_page_cursor']:
            second = self.read('/api/recommendations/', params={'cursor': first['next_page_cursor']})
            repeat = self.read('/api/recommendations/', params={'cursor': first['next_page_cursor']})
            self.check('stable_cursor_retry', second['results'] == repeat['results']
                and second['snapshot_id'] == first['snapshot_id'] == repeat['snapshot_id']
                and second['snapshot_expires_at'] == first['snapshot_expires_at'])
            self.check('within_batch_no_duplicates', not set(ids(first)) & set(ids(second))
                and second['batch'] == 1 and second['page'] == 2
                and ids(first) + ids(second) == ids(legacy))
        else:
            self.report['limitations'].append('No second page in this account/condition.')
        if first['next_batch_cursor']:
            batch = self.read('/api/recommendations/', params={'cursor': first['next_batch_cursor']})
            self.check('next_batch_no_duplicates', batch['batch'] == 2 and batch['page'] == 1
                and not set(ids(batch)) & set(ids(legacy)) and batch['snapshot_id'] == first['snapshot_id'])
        if first['results']:
            row = first['results'][0]
            detail = self.read(f'/api/recommendations/{row["bangumi_id"]}/',
                               params={'evidence_token': row['evidence_token']})
            self.check('browse_detail_evidence_parity', all(row[key] == detail[key] for key in ENVELOPE))
        for style in ('close', 'open'):
            data = self.read('/api/recommendations/', params={'style': style})
            self.check('style_' + style, data.get('style') == style and data.get('algorithm_version') == 'content.v1'
                and all(valid_evidence(row) for row in data['results']))
        filtered = self.read('/api/recommendations/', params={'browse': 'true', 'year_from': 2020, 'year_to': 2026})
        self.check('year_filter', all(2020 <= int(row['season'][:4]) <= 2026 for row in filtered['results']))
        for name, params, status in (
            ('reversed_years', {'year_from': 2026, 'year_to': 2020}, 400),
            ('owner_override', {'browse': 'true', 'user_id': 1}, 400),
            ('algorithm_override', {'browse': 'true', 'algorithm_version': 'content.v2'}, 400),
            ('invalid_style', {'style': 'random'}, 400),
            ('invalid_cursor', {'cursor': 'invalid'}, 400),
        ):
            self.check(name, self.request('/api/recommendations/', params=params).status_code == status)
        if first['next_page_cursor']:
            self.check('changed_conditions_409', self.request('/api/recommendations/',
                params={'cursor': first['next_page_cursor'], 'style': 'open'}).status_code == 409)
        for name in paths:
            self.check('unchanged_' + name, before[name] == digest(self.read('/api/recommendations/' + name)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed', action='store_true')
    parser.add_argument('--expected-account', required=True)
    args = parser.parse_args(argv)
    if not args.deployment_confirmed:
        parser.error('Deployment must be confirmed before any network request.')
    audit = BrowseDeploymentAudit()
    try:
        audit.check('browse_requires_login', audit.request('/api/recommendations/', authenticated=False,
                    params={'browse': 'true'}).status_code == 401)
        audit.authenticated_browse_checks(args.expected_account)
        audit.report['result'] = 'passed_scoped_readonly'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            audit.report['failed_check'] = str(exc)
    finally:
        audit.close()
        output = ROOT / '.tools/deployment-9a'
        output.mkdir(parents=True, exist_ok=True)
        path = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as file:
            json.dump(audit.report, file, ensure_ascii=False, indent=2)
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'] == 'passed_scoped_readonly' else 1


if __name__ == '__main__':
    sys.exit(main())
