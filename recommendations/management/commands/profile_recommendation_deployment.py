"""Shadow benchmark on the service host; V1/off stays unchanged, no HTTP writes."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from rest_framework.exceptions import APIException

from recommendations import browsing, v2_browsing
from recommendations.comparison import QueryCounter
from recommendations.deployment_evaluation import configuration
from recommendations.execution import recipe_descriptor
from recommendations.management.commands.evaluate_recommendation_deployment import private_path
from recommendations.quality_readonly import readonly_snapshot
from recommendations.serializers import RecommendationQuerySerializer
from recommendations.version_control import sign_artifact, release_descriptor


class Command(BaseCommand):
    help = 'D3 shadow service-process benchmark for 1–2 exact authorized accounts; not HTTP latency or load testing.'

    def add_arguments(self, parser):
        parser.add_argument('--user-email', action='append', default=[])
        parser.add_argument('--confirm-authorized-samples', action='store_true')
        parser.add_argument('--output-dir', required=True, type=Path)

    def handle(self, *args, **options):
        emails = options['user_email']
        if (not options['confirm_authorized_samples'] or not 1 <= len(set(emails)) == len(emails) <= 2
                or connection.vendor != 'postgresql' or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Exact authorized accounts, PostgreSQL and unchanged V1/off required.')
        try:
            directory = private_path(options['output_dir'])
            if directory.exists():
                raise ValueError('Use a new output directory')
            config, fingerprint = configuration(), release_descriptor()['fingerprint']
            with readonly_snapshot():
                users = list(get_user_model().objects.filter(email__in=emails, is_active=True).only('pk', 'email'))
                if len(users) != len(emails) or {user.email for user in users} != set(emails):
                    raise ValueError('Exact authorized account missing/ambiguous; scope not widened')
                initial = browsing.catalog_revision()
                profiles = {u.pk: browsing.live_context(u, {}, {})[1] for u in users}
            cases = []
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                for number, user in enumerate(users, 1):
                    for version, service in (('content.v1', browsing), ('content.v2', v2_browsing)):
                        for category, style in (('all', 'balanced'), ('all', 'balanced'), ('all', 'close'),
                                                ('all', 'open'), ('familiar', 'balanced'), ('explore', 'balanced')):
                            query = RecommendationQuerySerializer(data={'browse': True, 'category': category, 'style': style})
                            query.is_valid(raise_exception=True)
                            metrics, count = {}, QueryCounter()
                            started = perf_counter()
                            with connection.execute_wrapper(count):
                                page = service.browse_page(user, query.validated_data, query.initial_data, metrics=metrics)
                            cases.append({'sample': number, 'algorithm': version, 'category': category, 'style': style,
                                'wall_ms': round((perf_counter() - started) * 1000, 3), 'sql_count': count.count,
                                'public_cache': metrics.get('public_cache'), 'result_count': page['count']})
                        self.stdout.write(f'sample_{number:03d}: {version} shadow cases complete'); self.stdout.flush()
                with readonly_snapshot():
                    if initial != browsing.catalog_revision() or any(profiles[u.pk] != browsing.live_context(u, {}, {})[1] for u in users):
                        raise ValueError('Inputs changed; no complete performance receipt')
            deployed = bool(os.environ.get('RENDER_SERVICE_ID') and os.environ.get('RENDER_GIT_COMMIT'))
            report = sign_artifact('browse-performance', {'configuration': config, 'recipe': recipe_descriptor(),
                'execution_context': config['performance_scope'] if deployed else 'local-client-not-deployed-performance',
                'service_identity_hash': hashlib.sha256(os.environ['RENDER_SERVICE_ID'].encode()).hexdigest() if deployed else None,
                'git_commit': os.environ.get('RENDER_GIT_COMMIT') if deployed else None,
                'sample_count': len(users), 'cases': cases, 'read_only': True, 'no_review_reads': True,
                'online_default': 'content.v1', 'http_latency_measured': False})
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed during benchmark')
            directory.mkdir(parents=True)
            with (directory / 'performance.json').open('x', encoding='utf-8') as file:
                json.dump(report, file, ensure_ascii=False, indent=2)
            self.stdout.write(f'Complete: {directory}; deployed_process={deployed}; V1 unchanged; HTTP latency not measured.')
        except (ValueError, OSError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Performance stopped safely ({type(exc).__name__}).') from None
