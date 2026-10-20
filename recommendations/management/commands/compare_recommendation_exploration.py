"""Same original D3 17/68 cohort, not a new favorable subset."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from rest_framework.exceptions import APIException

from recommendations import browsing, engine
from recommendations.deployment_evaluation import configuration, cohort_entry, evaluate_sample
from recommendations.execution import recipe_descriptor as reference_recipe
from recommendations.exploration_candidate import (recipe_descriptor, execute_candidate, execute_dual_candidate,
    execute_protected_candidate, VERSION, DUAL_VERSION, PROTECTED_VERSION)
from recommendations.exploration_audit import read_frozen_inputs, verify_inputs, trace_target
from recommendations.management.commands.evaluate_recommendation_deployment import private_path
from recommendations.quality_readonly import readonly_snapshot
from recommendations.version_control import canonical_bytes, release_descriptor, sign_artifact


class Command(BaseCommand):
    help = 'Read-only fixed-cohort V1/reference V2/offline exploration candidate. Cannot issue approval.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        parser.add_argument('--cohort-lock', required=True, type=Path)
        parser.add_argument('--output-dir', required=True, type=Path)
        parser.add_argument('--policy-version', choices=(VERSION, DUAL_VERSION, PROTECTED_VERSION), default=PROTECTED_VERSION)

    def handle(self, *args, **options):
        if (not options['confirm_sitewide_readonly'] or connection.vendor != 'postgresql'
                or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Require authorization, PostgreSQL, unchanged V1 and V2 browse OFF')
        try:
            directory = private_path(options['output_dir'])
            if directory.exists():
                raise ValueError('New output directory required; no overwrite')
            lock = read_frozen_inputs(options['cohort_lock'])
            fingerprint, config = release_descriptor()['fingerprint'], configuration()
            version = options['policy_version']
            executor = {VERSION: execute_candidate, DUAL_VERSION: execute_dual_candidate,
                        PROTECTED_VERSION: execute_protected_candidate}[version]
            recipe_descriptor(version)  # Verify fixed policy before data reads/ranking.
            started = perf_counter()
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only('pk', 'is_active')[:config['maximum_accounts'] + 1])
                    if len(users) > config['maximum_accounts']:
                        raise ValueError('Account bound exceeded')
                    selected = []
                    for user in users:
                        context = engine.user_context(user, evaluation_baseline=True)
                        if context['mode'] == 'personalized' and len(context['seeds']) >= max(4, settings.RECOMMENDATION_PROFILE_MIN_SAMPLES + 1):
                            selected.append((user, context))
                    if len(selected) > config['maximum_eligible_accounts']:
                        raise ValueError('Eligible account bound exceeded')
                    revision = browsing.catalog_revision()
                    verify_inputs(lock, entries=[cohort_entry(u, c) for u, c in selected],
                                  catalog_revision=revision, active_account_count=len(users))
                    _, index = browsing.public_browse_index(revision, {}, recipe=reference_recipe())
                    samples, destinations = [], []
                    for ordinal, (user, context) in enumerate(selected, 1):
                        def progress(message):
                            self.stdout.write(f'sample_{ordinal:03d}/{len(selected)}: {message}'); self.stdout.flush()
                            if perf_counter() - started > config['maximum_wall_seconds']:
                                raise ValueError('Budget exceeded; no partial success')
                        progress('three fixed arms, same four targets')
                        samples.append(evaluate_sample(user, context, index, progress=progress, candidate_executor=executor))
                        # ALL frozen explore targets, not only the known lost hit.
                        for number, target in enumerate(sorted(s['bangumi_id'] for s in context['seeds'])[:4], 1):
                            destinations.append({'sample_ordinal': ordinal, 'target_ordinal': number,
                                'trace': trace_target(user, context, index, target, 'explore', candidate_executor=executor)})
                    body = {'reference_recipe': reference_recipe(), 'candidate_recipe': recipe_descriptor(version),
                        'configuration': config, 'samples': samples, 'explore_destinations': destinations,
                        'frozen_cohort_sha256': hashlib.sha256(canonical_bytes(lock)).hexdigest(),
                        'frozen_release_fingerprint': lock['release_fingerprint'],
                        'read_only': True, 'no_review_reads': True, 'no_external_http': True,
                        'online_default': 'content.v1', 'approval_issued': False,
                        'execution_context': 'local_neon_client_not_deployed_performance',
                        'seconds': round(perf_counter() - started, 3)}
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed during comparison; no complete receipt')
            directory.mkdir(parents=True)
            with (directory / 'comparison.json').open('x', encoding='utf-8') as file:
                json.dump(sign_artifact('exploration-comparison', body), file, ensure_ascii=False, indent=2, allow_nan=False)
            self.stdout.write(f'Complete: {directory}; samples={len(samples)}; V1 unchanged; no approval.')
        except (ValueError, OSError, KeyError, TypeError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Comparison stopped safely ({type(exc).__name__})') from None
