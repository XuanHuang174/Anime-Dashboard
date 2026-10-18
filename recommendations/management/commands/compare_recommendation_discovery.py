"""Previously consumed targets only: a score-only development ablation."""
from collections import Counter
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

from recommendations import browsing, engine, unused_validation as validation, discovery_development as development
from recommendations.deployment_evaluation import cohort_entry
from recommendations.discovery_candidate import recipe_descriptor, policy_contract
from recommendations.execution import recipe_descriptor as reference_recipe
from recommendations.exploration_candidate import recipe_descriptor as protected_recipe, PROTECTED_VERSION
from recommendations.exploration_audit import read_frozen_inputs, verify_inputs
from recommendations.quality_readonly import readonly_snapshot
from recommendations.version_control import canonical_bytes, release_descriptor, sign_artifact


class Command(BaseCommand):
    help = 'Read-only revealed-target development; no independent validation or serving approval.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        parser.add_argument('--prior-site-lock', required=True, type=Path)
        parser.add_argument('--prior-history-lock', required=True, type=Path)
        parser.add_argument('--consumed-cohort', required=True, type=Path)
        parser.add_argument('--consumed-result', required=True, type=Path)
        parser.add_argument('--test-report', required=True, type=Path)
        parser.add_argument('--output-dir', required=True, type=Path)

    def handle(self, *args, **options):
        if (not options['confirm_sitewide_readonly'] or connection.vendor != 'postgresql'
                or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Require authorization, PostgreSQL, V1 and V2 off')
        try:
            from recommendations.management.commands.validate_recommendation_holdouts import test_proof
            from recommendations.version_control import read_artifact
            directory = validation.private_path(options['output_dir'])
            if directory.exists():
                raise ValueError('New development output directory required')
            proof_sha = test_proof(options['test_report'])
            proof = read_artifact(validation.private_path(options['test_report']), 'unused-holdout-tests')
            if proof.get('discovery_development_tests', 0) < 7:
                raise ValueError('Current discovery regression test attestation required')
            policy = policy_contract()
            site = read_frozen_inputs(options['prior_site_lock'])
            history = validation.read_history(options['prior_history_lock'])
            frozen, previous = development.consumed_inputs(options['consumed_cohort'], options['consumed_result'])
            provenance = {'site_lock_sha256': hashlib.sha256(canonical_bytes(site)).hexdigest(),
                'history_lock_sha256': hashlib.sha256(canonical_bytes(history)).hexdigest(),
                'legacy_inventory': validation.legacy_inventory()}
            if frozen['provenance'] != provenance:
                raise ValueError('Historical input archives changed; cannot reselect')
            fingerprint = release_descriptor()['fingerprint']
            config = validation.configuration()
            started = perf_counter()
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only('pk', 'is_active')[:config['maximum_accounts'] + 1])
                    if len(users) > config['maximum_accounts']:
                        raise ValueError('Account budget exceeded')
                    contexts = [(user, engine.user_context(user)) for user in users]
                    natural = [(u, c) for u, c in contexts if c['mode'] == 'personalized' and len(c['seeds']) >= 4]
                    revision = browsing.catalog_revision()
                    verify_inputs(site, entries=[cohort_entry(u, c) for u, c in natural],
                        catalog_revision=revision, active_account_count=len(users))
                    selected, entries = [], []
                    for user, context in contexts:
                        targets, entry = validation.select(context, site, history)
                        entries.append(entry)
                        if targets:
                            selected.append((user, context, targets))
                    if (entries != frozen['entries'] or revision != frozen['catalog_revision']
                            or len(selected) > config['maximum_evaluation_accounts']):
                        raise ValueError('Consumed cohort/catalog drifted; no reselection')
                    plan = {'purpose': 'revealed-development-only', 'independent_validation': False,
                        'candidate_recipe': recipe_descriptor(), 'policy': policy,
                        'prior_result_sha256': hashlib.sha256(canonical_bytes(previous)).hexdigest(),
                        'cohort_sha256': hashlib.sha256(canonical_bytes(frozen)).hexdigest(),
                        'arms': list(development.ARMS), 'targets': len(selected) * 4, 'test_report_sha256': proof_sha,
                        'approval_issued': False, 'complete': False}
                    # Persist a signed plan BEFORE ranking, not after seeing gains.
                    directory.mkdir(parents=True)
                    with (directory / 'development-plan.json').open('x', encoding='utf-8') as file:
                        json.dump(sign_artifact('discovery-development-plan', plan), file, indent=2)
                    _, index = browsing.public_browse_index(revision, {}, recipe=reference_recipe())
                    samples = []
                    for number, (user, context, targets) in enumerate(selected, 1):
                        self.stdout.write(f'development sample {number}/{len(selected)}: four fixed arms'); self.stdout.flush()
                        if perf_counter() - started > config['maximum_wall_seconds']:
                            raise ValueError('Time budget exceeded; no partial success')
                        samples.append(development.evaluate(user, context, index, targets))
                    if perf_counter() - started > config['maximum_wall_seconds']:
                        raise ValueError('Time budget exceeded; no partial success')
                    result = {**plan, 'complete': True, 'reference_recipe': reference_recipe(),
                        'protected_recipe': protected_recipe(PROTECTED_VERSION), 'samples': samples,
                        'verdict': development.report_verdict(samples), 'read_only': True,
                        'no_review_reads': True, 'no_external_http': True, 'online_default': 'content.v1',
                        'selection_counts': dict(Counter(e['status'] for e in entries)),
                        'seconds': round(perf_counter() - started, 3), 'deployment_performance': 'not_measured'}
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed; no completed development report')
            with (directory / 'comparison.json').open('x', encoding='utf-8') as file:
                json.dump(sign_artifact('discovery-development', result), file, ensure_ascii=False, indent=2, allow_nan=False)
            self.stdout.write(f'Complete: {directory}; development only; no V2 approval.')
        except (ValueError, OSError, KeyError, TypeError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Development stopped safely ({type(exc).__name__})') from None
