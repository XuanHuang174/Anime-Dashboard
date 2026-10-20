"""Current-input timing diagnostic, deliberately NOT a holdout evaluation."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from rest_framework.exceptions import APIException

from recommendations import browsing, engine, discovery_development as development
from recommendations.evidence import _token
from recommendations.execution import recipe_descriptor
from recommendations.exploration_audit import read_frozen_inputs
from recommendations.quality_readonly import readonly_snapshot
from recommendations.query_phases import public_server_probe
from recommendations.recall_diagnostics import output_digest
from recommendations.unused_validation import private_path
from recommendations.version_control import release_descriptor, sign_artifact


class Command(BaseCommand):
    help = 'Read-only current-input query timings; no target selection or quality claims.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        parser.add_argument('--prior-site-lock', required=True, type=Path)
        parser.add_argument('--output', required=True, type=Path)
        parser.add_argument('--optimized-public-read', action='store_true')

    def handle(self, *args, **options):
        if (not options['confirm_sitewide_readonly'] or connection.vendor != 'postgresql'
                or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Require authorization, PostgreSQL, V1 and V2 off')
        try:
            path = private_path(options['output'])
            if path.exists():
                raise ValueError('New diagnostic output path required')
            lock = read_frozen_inputs(options['prior_site_lock'])
            fingerprint = release_descriptor()['fingerprint']
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    # Predetermined first historical account by PK, not by hit
                    # outcome or latency. No whole-site private journal scan.
                    handles = {entry['account'] for entry in lock['entries']}
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only('pk', 'is_active')[:501])
                    if len(users) > 500:
                        raise ValueError('Bounded account lookup exceeded')
                    user = next((u for u in users if _token('recommendations.deployment.account.v1', u.pk) in handles), None)
                    if user is None:
                        raise ValueError('No original authorized account remains')
                    context = engine.user_context(user, evaluation_baseline=True); original = deepcopy(context)
                    revision = browsing.catalog_revision()
                    _, index = browsing.public_browse_index(revision, {}, recipe=recipe_descriptor())
                    server = public_server_probe()
                    arms = {}
                    for arm in development.DUAL_ARMS:
                        rows1, timing1 = development.run(user, context, index, 'all', arm, dual=True)
                        rows2, timing2 = development.run(user, context, index, 'all', arm, dual=True)
                        if output_digest(rows1) != output_digest(rows2):
                            raise ValueError('Repeated diagnostic results changed')
                        if context != original:
                            raise ValueError('Timing diagnostic changed private inputs')
                        arms[arm] = {'runs': [timing1, timing2], 'stable_order_and_evidence': True}
                    if options['optimized_public_read']:
                        from recommendations.public_manifest import PublicManifestReader
                        reader = PublicManifestReader()
                        measured = []
                        for _ in range(2):
                            rows, timing = development.run(user, context, index, 'all', 'candidate_v2', dual=True, public_reader=reader)
                            if output_digest(rows) != output_digest(rows2):
                                raise ValueError('Optimized public reads changed ranking/evidence')
                            measured.append(timing)
                        arms['optimized_candidate_v2'] = {'runs': measured, 'stable_order_and_evidence': True}
                    data = {'scope': 'current-input-timing-only-not-frozen-quality-evaluation',
                        'selection': 'first-original-frozen-account-by-pk-not-outcome',
                        'accounts_read': 1, 'targets_selected': 0, 'quality_evaluated': False,
                        'read_only': True, 'approval_issued': False, 'online_default': 'content.v1',
                        'catalog_unchanged': revision == lock['catalog_revision'],
                        'public_server_probe': server, 'arms': arms,
                        'deployment_performance': 'not_measured', 'private_identifiers_exported': False}
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed during diagnosis')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as file:
                json.dump(sign_artifact('discovery-query-profile', data), file, indent=2, allow_nan=False)
            self.stdout.write(f'Complete: {path}; diagnostic only; no quality or serving approval.')
        except (ValueError, OSError, KeyError, TypeError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Diagnostic stopped safely ({type(exc).__name__})') from None
