"""Loopback-only, read-only operational monitor; never resumes or writes DB rows.

python -m recommendations.progress_monitor --run-dir .tools/<run> --batch-id <uuid>
"""
import argparse
import json
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PROGRESS = re.compile(r'已处理 (\d+)/(\d+)')
LOG_NAME = re.compile(r'^apply-resume-[1-9]\d*\.err$')
PHASES = {
    'resuming': '正在扫描与关联', 'network_retry_wait': '网络异常，等待重试',
    'waiting_for_apply': '等待关联任务完成', 'verifying': '正在最终验收',
    'apply_complete_waiting_acceptance': '关联结束，等待验收',
    'complete': '关联与验收均已完成', 'needs_attention': '需要排查或人工确认',
}


def utcnow():
    return datetime.now(timezone.utc)


def read_report(path):
    try:
        if path.stat().st_size > 2_000_000:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def integer(value):
    return value if type(value) is int and value >= 0 else None


class ProgressMonitor:
    def __init__(self, root, batch_id):
        self.root, self.batch_id = Path(root), str(uuid.UUID(str(batch_id)))
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.database = {'committed_links': None, 'last_commit': None, 'sampled_at': None,
                         'error_kind': None, 'error_at': None}

    def snapshot(self):
        status = read_report(self.root / 'status.json')
        preflight = read_report(self.root / 'preflight.json')
        report = read_report(self.root / 'apply.json')
        files = [p for p in self.root.glob('apply-resume-*.err') if LOG_NAME.fullmatch(p.name)]
        suggested = status.get('progress_log', '')
        log = self.root / suggested if isinstance(suggested, str) and LOG_NAME.fullmatch(suggested) else None
        if log is None or not log.exists():
            log = max(files, key=lambda p: p.stat().st_mtime, default=None)
        if log is not None and not log.resolve().is_relative_to(self.root.resolve()):
            log = None
        scanned = total = last_progress = None
        events, command_failed = [], False
        if log:
            try:
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 16_384))
                    text = stream.read().decode('utf-8', errors='replace')
                matches = PROGRESS.findall(text)
                if matches:
                    scanned, total = map(int, matches[-1])
                    last_progress = datetime.fromtimestamp(log.stat().st_mtime, timezone.utc).isoformat()
                    events = [f'本轮已扫描 {a} / {b}' for a, b in matches[-6:]]
                command_failed = 'CommandError:' in text
            except OSError:
                pass
        with self.lock:
            database = dict(self.database)
        phase = status.get('state', 'unknown')
        if not isinstance(phase, str) or phase not in PHASES:
            phase = 'unknown'
        if command_failed and phase in ('resuming', 'waiting_for_apply'):
            phase = 'attempt_interrupted'
        if report.get('batch_id') == self.batch_id and report.get('mode') == 'apply':
            if phase not in ('complete', 'needs_attention', 'verifying'):
                phase = 'apply_complete_waiting_acceptance'
            total = sum(value for value in report.get('resume_counts', report.get('counts', {})).values()
                        if integer(value) is not None)
            scanned = total
        now = utcnow()
        last_activity = max((datetime.fromisoformat(value) for value in
                             (last_progress, database['last_commit']) if value), default=None)
        age = max(0, int((now-last_activity).total_seconds())) if last_activity else None
        stalled = (age is not None and age > 300 and phase not in
                   ('complete', 'needs_attention', 'apply_complete_waiting_acceptance', 'verifying'))
        counts = preflight.get('counts', {})
        return {
            'served_at': now.isoformat(), 'batch_id': self.batch_id,
            'phase': phase, 'phase_label': PHASES.get(phase, '本轮已中断' if command_failed else '正在读取状态'),
            'attempt': integer(status.get('resume_attempt')),
            'scanned': scanned, 'scan_total': total,
            'expected_links': integer(counts.get('strong_unique')),
            'needs_confirmation': integer(counts.get('needs_confirmation')),
            'no_safe_candidate': integer(counts.get('no_safe_candidate')),
            'last_scan_at': last_progress, 'last_activity_age_seconds': age,
            'no_recent_progress': stalled, 'events': events, 'database': database,
        }

    def poll_database(self):
        # Django/settings/credentials are loaded only in the dedicated polling thread.
        # HTTP handlers receive aggregate counts, never settings or journal payloads.
        from django.db import connection, transaction, close_old_connections
        from django.db.models import Count, Max
        from .models import RecommendationIdentityEvent
        try:
            close_old_connections()
            if connection.vendor != 'postgresql':
                raise RuntimeError('Production PostgreSQL is required')
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
                    cursor.execute('SET LOCAL statement_timeout = 10000')
                stats = RecommendationIdentityEvent.objects.filter(
                    batch_id=self.batch_id, action='linked').aggregate(
                        committed_links=Count('pk'), last_commit=Max('created_at'))
            with self.lock:
                self.database.update(committed_links=stats['committed_links'],
                    last_commit=stats['last_commit'].isoformat() if stats['last_commit'] else None,
                    sampled_at=utcnow().isoformat(), error_kind=None, error_at=None)
        except Exception as exc:
            # Retain the last successful count; failures NEVER become a false zero.
            with self.lock:
                self.database.update(error_kind=type(exc).__name__, error_at=utcnow().isoformat())
        finally:
            connection.close()

    def poll_forever(self, interval):
        import os
        os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
        try:
            import django
            django.setup()
            from django.conf import settings
            settings.DATABASES['default'].setdefault('OPTIONS', {})['connect_timeout'] = 10
        except Exception as exc:
            with self.lock:
                self.database.update(error_kind=type(exc).__name__, error_at=utcnow().isoformat())
            return
        while not self.stop.is_set():
            self.poll_database()
            try:
                temporary = self.root / 'live-progress.tmp'
                temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(self.root / 'live-progress.json')
            except OSError:
                pass
            self.stop.wait(interval)


def handler_for(monitor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, content, mime, nonce=None):
            payload = content.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            if nonce:
                self.send_header('Content-Security-Policy',
                    f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            port = self.server.server_port
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            origins = {'http://' + host for host in hosts}
            if (self.headers.get('Host') not in hosts or
                    self.headers.get('Origin') and self.headers['Origin'] not in origins):
                return self.respond(403, '{"error":"Local same-origin access only"}', 'application/json')
            path = urlsplit(self.path).path
            if path == '/api/progress':
                return self.respond(200, json.dumps(monitor.snapshot(), ensure_ascii=False), 'application/json')
            if path == '/':
                nonce = secrets.token_urlsafe(20)
                html = Path(__file__).with_suffix('.html').read_text(encoding='utf-8').replace('{{nonce}}', nonce)
                return self.respond(200, html, 'text/html', nonce)
            self.respond(404, '{"error":"Not found"}', 'application/json')
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--batch-id', required=True, type=uuid.UUID)
    parser.add_argument('--port', type=int, default=5185)
    parser.add_argument('--db-interval', type=int, default=30)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[1]
    root = args.run_dir.resolve()
    if not root.is_relative_to(workspace / '.tools') or not root.is_dir():
        parser.error('run-dir must be an existing private .tools directory in this workspace')
    if not 1 <= args.port <= 65535 or not 15 <= args.db_interval <= 300:
        parser.error('Invalid port or polling interval (15-300 seconds)')
    monitor = ProgressMonitor(root, args.batch_id)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(monitor))
    threading.Thread(target=monitor.poll_forever, args=(args.db_interval,), daemon=True).start()
    print(f'Read-only progress monitor: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
