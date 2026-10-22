"""M3 bounded CLOSED deployment audit. No DB, business writes or rollout commands.

Explicit deployment confirmation and authorized account are required. The only
POST is normal login. Superuser mode reads only the global control, not accounts
or applications. Manual approval is NOT age verification. No live open-mode test.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_recommendation_scope_deployment import ScopeDeploymentAudit
from scripts.verify_recommendation_deployment import Audit


class ControlledDeploymentAudit(ScopeDeploymentAudit):
    def __init__(self, session=None):
        super().__init__(session)
        self.report['scope'] = 'm3-closed-deployment-readonly'
        self.report['limitations'].extend([
            'No live grant, review, expiry, global switch or business write drill; isolated PostgreSQL only.',
            'No SQL count, server computation time, p95, age verification or global compliance claim.',
            'Browser interactions and build byte comparison remain separate acceptance items.'])

    def request(self, path, *, method='GET', public=False, authenticated=True, **kwargs):
        if public:
            if method != 'GET' or authenticated or not (path == '/recommendations'
                    or re.fullmatch(r'/assets/[A-Za-z0-9_-]+\.(?:js|css)', path)):
                raise ValueError('Only unauthenticated frontend GETs for exact static paths are allowed')
            return Audit.request(self, path, method=method, public=True, authenticated=False, **kwargs)
        if path == '/api/recommendations/admin/control/':
            if method != 'GET':
                raise ValueError('Module 3 control is read-only in this audit')
            return Audit.request(self, path, authenticated=authenticated, **kwargs)
        return super().request(path, method=method, authenticated=authenticated, **kwargs)

    def closed_admin_check(self, mode):
        self.check('admin_control_unauthenticated', self.request('/api/recommendations/admin/control/',
            authenticated=False).status_code == 401)
        if mode == 'denied':
            self.check('normal_account_admin_denied', self.request('/api/recommendations/admin/control/').status_code == 403)
        elif mode == 'superuser':
            state = self.read('/api/recommendations/admin/control/')
            self.check('both_global_gates_closed', all(state.get(k) is False
                for k in ('runtime_enabled', 'enabled', 'effective_enabled'))
                and type(state.get('version')) is int and state['version'] >= 0)
        else:
            raise ValueError('Unknown admin expectation')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed', action='store_true')
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--admin-view', choices=('denied', 'superuser'), default='denied')
    parser.add_argument('--adult-id', type=int, help='Operator-confirmed PUBLIC catalog ID, not a journal ID')
    parser.add_argument('--unknown-id', type=int)
    parser.add_argument('--expected-dist', type=Path, help='Optional matching local build, never guesses a release hash')
    args = parser.parse_args(argv)
    if not args.deployment_confirmed or not args.expected_account.strip():
        parser.error('Confirm deployment and provide one explicitly authorized account before any client or login.')
    if any(v is not None and not 0 < v <= 2147483647 for v in (args.adult_id, args.unknown_id)):
        parser.error('Public fixture IDs must be positive int32 values.')
    if args.expected_dist is not None and not (args.expected_dist / 'assets').is_dir():
        parser.error('Expected build assets directory does not exist.')
    audit = ControlledDeploymentAudit()
    try:
        audit.run(args.expected_account, {'adult': args.adult_id, 'unknown': args.unknown_id})
        audit.closed_admin_check(args.admin_view)
        if args.expected_dist is not None:
            audit.public_checks(args.expected_dist)
        else:
            audit.report['limitations'].append('Frontend asset bytes not checked: no matching local build supplied.')
        audit.report['result'] = 'passed_closed_readonly'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            audit.report['failed_check'] = str(exc)
    finally:
        audit.close()
        folder = ROOT / '.tools/deployment-m3'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as output:
            json.dump(audit.report, output, ensure_ascii=False, indent=2)
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'] == 'passed_closed_readonly' else 1


if __name__ == '__main__':
    sys.exit(main())
