"""8C deployment read-only audit, RUN ONLY after the operator confirms deployment.

No Django/.env/database imports. Only ordinary login may POST; business mutations
are denied before network access. Credentials use getpass, reports omit tokens.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_recommendation_deployment import Audit, digest, ENVELOPE


class FeedbackDeploymentAudit(Audit):
    def __init__(self, session=None):
        super().__init__(session)
        self.report.update(scope='8c-deployment-authorized-readonly', feedback_mutations=0,
                           local_write_tests='separate_isolated_postgresql_report')

    def request(self, path, *, method='GET', public=False, **kwargs):
        allowed = (path in ('/api/token/', '/api/user/profile/', '/api/recommendations/',
                           '/api/recommendations/profile/', '/api/recommendations/preferences/',
                           '/api/recommendations/feedback/', '/api/recommendations/feedback/undo/',
                           '/api/recommendations/feedback/hidden/')
                   or re.fullmatch(r'/api/recommendations/[1-9]\d*/', path))
        public_path = path == '/recommendations' or re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.(?:js|css)', path)
        if (public and (method != 'GET' or not public_path)) or (not public and not allowed):
            raise ValueError('Path is outside the deployment read-only allowlist')
        return super().request(path, method=method, public=public, **kwargs)

    def public_checks(self, expected_dist=None):
        super().public_checks(expected_dist)
        for name in ('feedback/', 'feedback/undo/', 'feedback/hidden/'):
            self.check('feedback_auth_' + name,
                       self.request('/api/recommendations/' + name, authenticated=False).status_code == 401)

    def authenticated_checks(self, expected_account, blocked_ids):
        super().authenticated_checks(expected_account, blocked_ids)
        first = self.read('/api/recommendations/feedback/hidden/')
        version = first.get('feedback_version')
        self.check('feedback_contract', first.get('contract_version') == 'recommendations.feedback.v1'
                   and type(version) is int and version >= 0 and type(first.get('count')) is int
                   and isinstance(first.get('results'), list), hidden_count=first.get('count'), feedback_version=version)
        rows = first['results']
        self.check('hidden_row_contract', all(type(row.get('bangumi_id')) is int
            and row['bangumi_id'] > 0 and row.get('is_active') is True and bool(row.get('hidden_at'))
            and type(row.get('feedback_version')) is int and 1 <= row['feedback_version'] <= version
            for row in rows), checked_rows=len(rows))
        if not rows:
            self.report['limitations'].append('Empty authorized hidden list: nonempty pagination verified only in isolation.')
        page = self.read('/api/recommendations/', params={'page_size': 3})
        self.check('feedback_epoch_in_list', page.get('evidence_context', {}).get('feedback_version') == version)
        if page['results']:
            row = page['results'][0]
            detail = self.read(f'/api/recommendations/{row["bangumi_id"]}/', params={'evidence_token': row['evidence_token']})
            self.check('feedback_epoch_in_detail', all(detail[key] == row[key] for key in ENVELOPE))
        for name, params, status in (
            ('owner_override', {'user_id': 1}, 400),
            ('page_without_epoch', {'page': 2}, 400),
            ('stale_hidden_epoch', {'feedback_version': version - 1 if version else 1}, 409),
        ):
            response = self.request('/api/recommendations/feedback/hidden/', params=params)
            self.check(name, response.status_code == status)
        self.check('feedback_unchanged', digest(first) == digest(self.read('/api/recommendations/feedback/hidden/')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed', action='store_true')
    parser.add_argument('--expected-account', required=True, help='Exactly the human-authorized account, not discovered automatically')
    parser.add_argument('--expected-dist', type=Path)
    parser.add_argument('--adult-id', type=int)
    parser.add_argument('--unknown-id', type=int)
    args = parser.parse_args(argv)
    if not args.deployment_confirmed:
        parser.error('Wait for confirmed deployment; no network request was made.')
    if any(value is not None and value < 1 for value in (args.adult_id, args.unknown_id)):
        parser.error('Public fixture IDs must be positive.')
    audit = FeedbackDeploymentAudit()
    try:
        audit.public_checks(args.expected_dist)
        audit.authenticated_checks(args.expected_account, {'adult': args.adult_id, 'unknown_rating': args.unknown_id})
        audit.report['result'] = 'passed_scoped_readonly'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            audit.report['failed_check'] = str(exc)
    finally:
        audit.close()
        output = ROOT / '.tools/deployment-8c'
        output.mkdir(parents=True, exist_ok=True)
        path = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as file:
            json.dump(audit.report, file, ensure_ascii=False, indent=2)
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'] == 'passed_scoped_readonly' else 1


if __name__ == '__main__':
    sys.exit(main())
