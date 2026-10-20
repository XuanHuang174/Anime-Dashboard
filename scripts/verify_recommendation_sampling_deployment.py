"""P3 bounded authorized read-only audit; only normal login may POST.

No Django initialization, .env/DB reads, flag changes or preference writes.
Disabled baseline is NEVER reported as enabled release acceptance.
"""
import argparse
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_recommendation_deployment import Audit, digest, valid_evidence, ENVELOPE

CHOICES = [f'{6+n*.5:.1f}' for n in range(9)]
VERSION = 'profile-sampling.v1'


def valid_preferences(data, enabled):
    control = data.get('profile_min_score_control', {})
    score = data.get('profile_min_score')
    return (data.get('contract_version') == 'recommendations.3a.v1'
        and data.get('profile_policy_version') == VERSION and score in CHOICES
        and type(data.get('version')) is int and data['version'] >= 0
        and control.get('enabled') is enabled and control.get('choices') == CHOICES
        and all(control.get(k) == v for k, v in {'default': '8.5', 'minimum': '6.0', 'maximum': '10.0', 'step': '0.5'}.items())
        and data.get('effective_profile_min_score') == (score if enabled else '8.5'))


def valid_policy(policy, prefs, *, preview_score=None):
    return (isinstance(policy, dict) and policy.get('profile_policy_version') == VERSION
        and policy.get('configured_minimum_score') == prefs['profile_min_score']
        and policy.get('minimum_score') == (preview_score or prefs['effective_profile_min_score'])
        and policy.get('customization_enabled') is prefs['profile_min_score_control']['enabled']
        and policy.get('source') == ('unsaved_preview' if preview_score else
            'user_setting' if prefs['profile_min_score_control']['enabled'] else 'server_default')
        and policy.get('watch_status') == 'completed' and policy.get('minimum_samples') == 3
        and policy.get('requires_safe_bangumi_identity') is True and policy.get('requires_usable_tags') is True)


class SamplingDeploymentAudit(Audit):
    def __init__(self, session=None):
        super().__init__(session)
        self.report.update(scope='p3-authorized-sampling-readonly', enabled_release_verified=False,
            production_settings_changed=False, observations=[])
        self.report['limitations'].append('No production PATCH, journal scans, flag mutation or inferred server SQL metrics.')

    def request(self, path, **kwargs):
        method = kwargs.get('method', 'GET')
        if method != 'GET' and not (method == 'POST' and path == '/api/token/' and not kwargs.get('public')):
            raise ValueError('P3 audit rejects ALL business writes before network access')
        return super().request(path, **kwargs)

    def authenticated_checks(self, account, *, enabled):
        password = getpass.getpass('Authorized test account password (masked): ')
        response = self.request('/api/token/', authenticated=False, method='POST',
            json={'username': account, 'password': password})
        password = None
        self.check('normal_login', response.status_code == 200)
        self.headers['Authorization'] = 'Bearer ' + response.json()['access']
        identity = self.request('/api/user/profile/')
        self.check('exact_authorized_identity', identity.status_code == 200
            and identity.json().get('username', '').casefold() == account.casefold())
        prefs = self.read('/api/recommendations/preferences/')
        self.check('sampling_preferences_contract_and_expected_gate', valid_preferences(prefs, enabled))
        profile = self.read('/api/recommendations/profile/')
        self.check('effective_profile_policy', valid_policy(profile.get('sample_policy'), prefs))
        embedded = profile.get('extraction', {}).get('explicit_preferences', {})
        # The preferences endpoint additionally enriches/sorts public option counts;
        # that presentation metadata is not part of the saved private setting.
        self.check('profile_embedded_preferences', valid_preferences(embedded, enabled)
            and all(embedded.get(key) == prefs.get(key) for key in (
                'tags', 'version', 'profile_min_score', 'effective_profile_min_score', 'profile_policy_version')))
        for score in ('6.0', '8.5', '10.0'):
            preview = self.read('/api/recommendations/profile/preview/', params={'profile_min_score': score})
            count = preview.get('usable_sample_count')
            self.check('readonly_preview_' + score, preview.get('contract_version') == 'recommendations.profile-preview.v1'
                and preview.get('saved') is False and preview.get('is_preview') is True
                and preview.get('preference_version') == prefs['version'] and preview.get('profile_min_score') == score
                and valid_policy(preview.get('sample_policy'), prefs, preview_score=score)
                and type(count) is int and count >= 0 and preview.get('cold_start') == (count < 3))
            self.report['observations'].append({'preview_score': score, 'usable_samples': count,
                'pending_identity': preview.get('diagnostics', {}).get('pending_identity_count')})
        self.check('preview_login_required', self.request('/api/recommendations/profile/preview/',
            authenticated=False, params={'profile_min_score': '7.0'}).status_code == 401)
        for label, params in (('invalid_step', {'profile_min_score': '7.3'}),
                ('owner_override', {'profile_min_score': '7.0', 'user_id': 1}),
                ('duplicate_score', [('profile_min_score', '7.0'), ('profile_min_score', '8.0')])):
            self.check('preview_rejects_' + label,
                self.request('/api/recommendations/profile/preview/', params=params).status_code == 400)
        for category in ('all', 'familiar', 'explore'):
            page = self.read('/api/recommendations/', params={'browse': 'true', 'category': category})
            context = page.get('evidence_context', {})
            self.check('v1_and_policy_' + category, page.get('algorithm_version') == 'content.v1'
                and context.get('profile_min_score') == prefs['effective_profile_min_score']
                and context.get('profile_policy_version') == VERSION and context.get('preference_version') == prefs['version']
                and page.get('cold_start_state') == profile.get('cold_start_state'))
            rows = page['results']
            self.check('safe_real_evidence_' + category, all(valid_evidence(row, float(prefs['effective_profile_min_score']))
                and row.get('evidence_context') == context for row in rows), checked_rows=len(rows))
            self.report['observations'].append({'category': category, 'mode': page.get('mode'),
                'result_count': page.get('count'), 'checked_evidence_rows': len(rows)})
            if category == 'all' and rows:
                row = rows[0]
                detail = self.read(f"/api/recommendations/{row['bangumi_id']}/", params={'evidence_token': row['evidence_token']})
                self.check('list_detail_parity', all(row.get(key) == detail.get(key) for key in ENVELOPE))
                cursor = page.get('next_page_cursor')
                if cursor:
                    second = self.read('/api/recommendations/', params={'cursor': cursor})
                    repeat = self.read('/api/recommendations/', params={'cursor': cursor})
                    self.report['repeat_metadata_changes'] = sorted(key for key in set(second) | set(repeat)
                        if second.get(key) != repeat.get(key))
                    self.check('stable_cursor_policy', all(second.get(key) == repeat.get(key) for key in
                        ('results', 'snapshot_id', 'snapshot_expires_at', 'batch', 'page', 'count', 'evidence_context'))
                        and second['snapshot_id'] == page['snapshot_id'] and second['batch'] == page['batch']
                        and second['page'] == page['page'] + 1 and second['evidence_context'] == context
                        and not ({r['bangumi_id'] for r in rows} & {r['bangumi_id'] for r in second['results']}))
        self.check('preferences_unchanged_after_preview', digest(prefs) == digest(self.read('/api/recommendations/preferences/')))
        self.check('profile_unchanged_after_preview', digest(profile) == digest(self.read('/api/recommendations/profile/')))
        self.report['enabled_release_verified'] = enabled


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-confirmed', action='store_true')
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--expected-dist', type=Path)
    expected = parser.add_mutually_exclusive_group(required=True)
    expected.add_argument('--expect-enabled', action='store_true')
    expected.add_argument('--expect-disabled', action='store_true')
    args = parser.parse_args(argv)
    if not args.deployment_confirmed:
        parser.error('Deployment confirmation required; no network client created.')
    audit = SamplingDeploymentAudit()
    try:
        audit.public_checks(args.expected_dist)
        audit.authenticated_checks(args.expected_account, enabled=args.expect_enabled)
        audit.report['result'] = 'passed_enabled_readonly' if args.expect_enabled else 'passed_disabled_baseline_only'
    except Exception as exc:
        audit.report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            audit.report['failed_check'] = str(exc)
    finally:
        audit.close()
        output = ROOT / '.tools/deployment-sampling-p3'
        output.mkdir(parents=True, exist_ok=True)
        path = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        with path.open('x', encoding='utf-8') as file:
            json.dump(audit.report, file, ensure_ascii=False, indent=2)
        print(json.dumps({'result': audit.report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if audit.report['result'].startswith('passed_') else 1


if __name__ == '__main__':
    sys.exit(main())
