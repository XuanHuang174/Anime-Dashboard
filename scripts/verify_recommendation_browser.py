"""Normal authorized login + bounded deployed readonly browser audit.

Never imports Django or reads a database. Tokens stay in memory/stdin. No
business POST/PATCH/DELETE is allowed by the browser's request guard.
"""
import argparse
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import subprocess
import sys
import requests

ROOT = Path(__file__).resolve().parents[1]
BACK = 'https://anime-dashboard-api-dv0s.onrender.com'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-account', required=True)
    args = parser.parse_args()
    report = {'scope': '7c-deployed-readonly-ui', 'normal_login_only': True,
        'limitations': ['Only explicitly authorized account; no production journal/preference writes.',
                        'Live race/rollback/score preservation verified only in the isolated tests.']}
    try:
        account = getpass.getpass('Authorized account (masked): ')
        if account.casefold() != args.expected_account.casefold():
            raise ValueError('Account scope mismatch')
        password = getpass.getpass('Password (masked): ')
        with requests.Session() as session:
            response = session.post(BACK + '/api/token/', json={'username': account, 'password': password},
                headers={'Origin': 'https://xh-anime.com'}, timeout=(10, 45), allow_redirects=False)
            password = None
            response.raise_for_status()
            access = response.json()['access']
            owner = session.get(BACK + '/api/user/profile/', headers={'Authorization': 'Bearer ' + access},
                timeout=(10, 45), allow_redirects=False)
            if owner.status_code != 200 or owner.json().get('username', '').casefold() != args.expected_account.casefold():
                raise ValueError('Authenticated identity mismatch')
        completed = subprocess.run(['node', str(ROOT / 'frontend/scripts/verify-recommendation-readonly.mjs')],
            cwd=ROOT / 'frontend', input=json.dumps({'access': access}), capture_output=True, text=True,
            encoding='utf-8', timeout=240)
        access = None
        browser = json.loads(completed.stdout.strip().splitlines()[-1])
        report.update(result=browser['result'], browser=browser)
    except Exception as exc:
        report.update(result='failed_or_blocked', failure_type=type(exc).__name__)
    output = ROOT / '.tools/acceptance-7c-deployment'
    output.mkdir(parents=True, exist_ok=True)
    path = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with path.open('x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps({'result': report['result'], 'report_path': str(path)}, ensure_ascii=False))
    return 0 if report['result'].startswith('passed') else 1


if __name__ == '__main__':
    sys.exit(main())
