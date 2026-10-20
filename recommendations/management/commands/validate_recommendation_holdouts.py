"""One sealed, target-disjoint supplementary validation; cannot approve serving."""
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

from recommendations import engine, browsing, unused_validation as validation
from recommendations.deployment_evaluation import cohort_entry
from recommendations.execution import recipe_descriptor as reference_recipe
from recommendations.exploration_candidate import recipe_descriptor, PROTECTED_VERSION
from recommendations.exploration_audit import read_frozen_inputs, verify_inputs
from recommendations.quality_readonly import readonly_snapshot
from recommendations.version_control import canonical_bytes, sign_artifact, read_artifact, release_descriptor


def test_proof(path):
    path = validation.private_path(path)
    data = read_artifact(path, 'unused-holdout-tests')
    if (data.get('status') != 'pass' or data.get('database') != 'local_isolated_postgresql'
            or data.get('skipped') != 0 or data.get('locks') != [6, 4, 6]
            or data.get('unused_validation_tests', 0) < 8):
        raise ValueError('Actual current isolated PostgreSQL validation/lock report required')
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


class Command(BaseCommand):
    help = 'Freeze unused pairs before ranking, then one read-only joint-holdout evaluation. Not a new-user blind test.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        parser.add_argument('--prior-site-lock', required=True, type=Path)
        parser.add_argument('--prior-history-lock', required=True, type=Path)
        phase = parser.add_mutually_exclusive_group(required=True)
        phase.add_argument('--freeze-only', action='store_true')
        phase.add_argument('--cohort-lock', type=Path)
        parser.add_argument('--test-report', type=Path)
        parser.add_argument('--output-dir', required=True, type=Path)

    def handle(self, *args, **options):
        if (not options['confirm_sitewide_readonly'] or connection.vendor != 'postgresql'
                or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Explicit authorization, PostgreSQL, V1 and V2-off required')
        try:
            config = validation.configuration()
            directory = validation.private_path(options['output_dir'])
            if directory.exists():
                raise ValueError('New output directory required; overwrite forbidden')
            site = read_frozen_inputs(options['prior_site_lock'])
            history = validation.read_history(options['prior_history_lock'])
            provenance = {'site_lock_sha256': hashlib.sha256(canonical_bytes(site)).hexdigest(),
                          'history_lock_sha256': hashlib.sha256(canonical_bytes(history)).hexdigest(),
                          'legacy_inventory': validation.legacy_inventory()}
            fingerprint = release_descriptor()['fingerprint']
            lock = None
            if options['cohort_lock']:
                lock = read_artifact(validation.private_path(options['cohort_lock']), 'unused-holdout-cohort')
                if not options['test_report']:
                    raise ValueError('Evaluation requires actual isolated test report')
                proof_sha = test_proof(options['test_report'])
            started = perf_counter()
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only('pk', 'is_active')[:config['maximum_accounts'] + 1])
                    if len(users) > config['maximum_accounts']:
                        raise ValueError('Account scope exceeds fixed budget')
                    contexts = [(user, engine.user_context(user, evaluation_baseline=True)) for user in users]
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
                    if len(selected) > config['maximum_evaluation_accounts']:
                        raise ValueError('Too many eligible accounts; no favorable truncation')
                    cohort = {'configuration': config, 'provenance': provenance, 'catalog_revision': revision,
                              'entries': entries, 'reference_recipe': reference_recipe(),
                              'candidate_recipe': recipe_descriptor(PROTECTED_VERSION), 'ranking_performed': False}
                    attempt = validation.attempt_path(cohort)
                    if attempt.exists():
                        raise ValueError('This target set is already consumed; no copy/refreeze/retry as independent validation')
                    if options['freeze_only']:
                        report = sign_artifact('unused-holdout-cohort', cohort)
                        filename = 'cohort.lock.json'
                    else:
                        if any(lock.get(k) != value for k, value in cohort.items()):
                            raise ValueError('Frozen input/protocol/source drift; no reselection')
                        # Exclusive consumption precedes the FIRST ranking. Failures
                        # retain this marker; no automatic re-freeze/retry or tuning.
                        attempt.parent.mkdir(parents=True, exist_ok=True)
                        with attempt.open('x', encoding='utf-8') as file:
                            json.dump(sign_artifact('unused-holdout-attempt', {
                                'cohort_sha256': hashlib.sha256(canonical_bytes(lock)).hexdigest(),
                                'test_report_sha256': proof_sha, 'automatic_retries': False}), file, indent=2)
                        _, index = browsing.public_browse_index(revision, {}, recipe=reference_recipe())
                        samples = []
                        for number, (user, context, targets) in enumerate(selected, 1):
                            self.stdout.write(f'sealed sample {number}/{len(selected)}: joint holdout, three fixed arms'); self.stdout.flush()
                            if perf_counter() - started > config['maximum_wall_seconds']:
                                raise ValueError('Time budget exceeded; attempt remains consumed')
                            samples.append(validation.evaluate(user, context, index, targets))
                        if perf_counter() - started > config['maximum_wall_seconds']:
                            raise ValueError('Time budget exceeded; no partial success')
                        body = {'configuration': config, 'candidate_recipe': recipe_descriptor(PROTECTED_VERSION),
                            'cohort_sha256': hashlib.sha256(canonical_bytes(lock)).hexdigest(), 'provenance': provenance,
                            'samples': samples, 'verdict': validation.verdict(samples, True), 'test_report_sha256': proof_sha,
                            'account_count': len(users), 'selection_counts': dict(Counter(e['status'] for e in entries)),
                            'read_only': True, 'no_review_reads': True, 'no_external_http': True,
                            'training_split': 'four-targets-jointly-removed-no-profile-or-explanation-cache',
                            'strict_new_user_blind_test': False, 'online_default': 'content.v1', 'approval_issued': False,
                            'seconds': round(perf_counter() - started, 3)}
                        report = sign_artifact('unused-holdout-evaluation', body)
                        filename = 'evaluation.json'
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed during validation; no complete report issued')
            directory.mkdir(parents=True)
            with (directory / filename).open('x', encoding='utf-8') as file:
                json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
            self.stdout.write(f'Complete: {directory}; selected={len(selected)}; no V2 approval.')
        except (ValueError, OSError, DatabaseError, APIException, KeyError, TypeError) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Stopped safely ({type(exc).__name__}); no target replacement or automatic retry.') from None
