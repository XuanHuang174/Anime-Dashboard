"""9C bounded authorized GET audit, never flips flags or writes business data.

Use --allow-pre9c only for a labelled pre-deployment baseline. A missing 9C
capability route is NOT a completed 9C deployment acceptance.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_recommendation_browse_deployment import BrowseDeploymentAudit


class BrowseClosureDeploymentAudit(BrowseDeploymentAudit):
    def __init__(self, session=None):
        super().__init__(session)
        self.report.update(scope='9c-authorized-deployment-readonly', observations=[],
                           server_sql_count=None, server_cache_hit_rate=None,
                           server_compute_ms=None, deployment_9c_verified=False)
        self.report['limitations'].append('HTTP wall time is not server SQL/CPU/cache telemetry; no flag mutation or production writes.')

    def read(self, path, **kwargs):
        data = super().read(path, **kwargs)
        if path == '/api/recommendations/':
            params = kwargs.get('params', {})
            rows = data.get('results', [])
            combos = {tuple(sorted(row['tags'])) for row in rows}
            self.report['observations'].append({
                'transport': 'browse' if data.get('browse_contract_version') else 'legacy',
                'style': data.get('style'), 'category': params.get('category', 'all'),
                'cursor_request': 'cursor' in params, 'year_filter': 'year_from' in params,
                'http_seconds': self.report['request_seconds'][-1],
                'algorithm_version': data.get('algorithm_version'), 'count': data.get('count'),
                'maximum_batches': (data['count'] + 23) // 24 if data.get('browse_contract_version') else None,
                'returned_rows': len(rows), 'top_genre_combination_count': len(combos)})
        return data

    def capability_checks(self, *, allow_pre9c=False):
        response = self.request('/api/recommendations/capabilities/')
        if response.status_code == 404 and allow_pre9c:
            self.report['limitations'].append('9C not deployed: capabilities returned 404. Current 9B baseline only; rollback deployment check pending.')
            return
        self.check('capabilities_deployed', response.status_code == 200)
        data = response.json()
        self.check('capabilities_v1', data.get('contract_version') == 'recommendations.capabilities.v1'
                   and data.get('algorithm_version') == 'content.v1' and data.get('browse_enabled') is True)
        self.check('capabilities_private', 'private' in response.headers.get('Cache-Control', '')
                   and 'no-store' in response.headers.get('Cache-Control', ''))
        self.check('capabilities_requires_login', self.request('/api/recommendations/capabilities/', authenticated=False).status_code == 401)
        self.report['deployment_9c_verified'] = True
        self.report['limitations'].append('Feature-off/re-enable tests run in isolation only; no production switch toggled.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed', action='store_true')
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--allow-pre9c', action='store_true')
    args = parser.parse_args(argv)
    if not args.deployment_confirmed:
        parser.error('Deployment confirmation required; no network client created.')
    audit = BrowseClosureDeploymentAudit()
    try:
        audit.check('browse_requires_login', audit.request('/api/recommendations/', authenticated=False,
                    params={'browse': 'true'}).status_code == 401)
        audit.authenticated_browse_checks(args.expected_account)
        audit.capability_checks(allow_pre9c=args.allow_pre9c)
        audit.report['result'] = 'passed_scoped_readonly' if audit.report['deployment_9c_verified'] else 'passed_pre9c_baseline_only'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            audit.report['failed_check'] = str(exc)
    finally:
        audit.close()
        output = ROOT / '.tools/deployment-9c'
        output.mkdir(parents=True, exist_ok=True)
        path = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as file:
            json.dump(audit.report, file, ensure_ascii=False, indent=2)
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'].startswith('passed_') else 1


if __name__ == '__main__':
    sys.exit(main())
