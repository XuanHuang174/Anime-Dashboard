"""One preregistered read-only trial; same consumed pairs, no release switch."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from django.test import override_settings
from rest_framework.exceptions import APIException

from recommendations import browsing, engine, completion_development as trial
from recommendations import discovery_development as history, refrozen_development as refresh
from recommendations import recall_completion as candidate, unused_validation as validation
from recommendations.cold_public import SeededPublicReader, compact_catalog_revision, load_bundle, write_bundle
from recommendations.quality_readonly import readonly_snapshot
from recommendations.version_control import canonical_bytes, release_descriptor, sign_artifact, read_artifact


class Command(BaseCommand):
    help = 'Freeze then compare ONE append-only recall trial on ORIGINAL consumed pairs. V1 stays online.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        parser.add_argument('--consumed-cohort', required=True, type=Path)
        parser.add_argument('--consumed-result', required=True, type=Path)
        parser.add_argument('--test-report', required=True, type=Path)
        phase = parser.add_mutually_exclusive_group(required=True)
        phase.add_argument('--freeze-only', action='store_true')
        phase.add_argument('--cohort-lock', type=Path)
        parser.add_argument('--output-dir', required=True, type=Path)

    def handle(self, *args, **options):
        if (not options['confirm_sitewide_readonly'] or connection.vendor != 'postgresql'
                or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Require explicit authorization, PostgreSQL, V1 and V2 off')
        try:
            root = validation.private_path(options['output_dir'])
            if root.exists():
                raise ValueError('New output directory required')
            from .validate_recommendation_holdouts import test_proof
            proof_sha = test_proof(options['test_report'])
            proof = read_artifact(validation.private_path(options['test_report']), 'unused-holdout-tests')
            if proof.get('recall_completion_tests', 0) < 8:
                raise ValueError('Current isolated recall completion proof required')
            original, previous = history.consumed_inputs(options['consumed_cohort'], options['consumed_result'])
            policy = candidate.policy_contract(); fingerprint = release_descriptor()['fingerprint']
            config = validation.configuration(); started = perf_counter()
            def progress(message):
                self.stdout.write(message); self.stdout.flush()
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only('pk', 'is_active')[:config['maximum_accounts'] + 1])
                    if len(users) > config['maximum_accounts']:
                        raise ValueError('Account budget exceeded')
                    entries, samples = refresh.bind_original(users, original, progress=progress)
                    revision = compact_catalog_revision()
                    current = {**refresh.input_lock(original, previous, entries, revision), 'policy': policy,
                        'candidate_recipe': candidate.recipe_descriptor(), 'test_report_sha256': proof_sha}
                    if options['freeze_only']:
                        with override_settings(RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED=False):
                            rows, _ = browsing.public_browse_index(revision, {})
                        write_bundle(rows, root / 'public-catalog.json.gz')
                        data = sign_artifact('recall-completion-cohort', current)
                        filename = 'cohort.lock.json'
                    else:
                        lock_path = validation.private_path(options['cohort_lock'])
                        lock = read_artifact(lock_path, 'recall-completion-cohort')
                        if any(lock.get(key) != value for key, value in current.items()):
                            raise ValueError('Fixed inputs/protocol drifted; no reselection or silent refresh')
                        # Marker ignores source fingerprint/output path; code edits
                        # cannot make the same revealed trial appear unused again.
                        marker_key = hashlib.sha256(canonical_bytes({key: current[key] for key in
                            ('entries', 'catalog_revision', 'policy')})).hexdigest()
                        marker = validation.private_path(Path('.tools/recall-completion-attempts') / (marker_key + '.json'))
                        marker.parent.mkdir(parents=True, exist_ok=True)
                        with marker.open('x', encoding='utf-8') as file:
                            json.dump(sign_artifact('recall-completion-attempt', {'cohort_sha256': hashlib.sha256(canonical_bytes(lock)).hexdigest(),
                                'automatic_retries': False, 'trial_budget': 1}), file, indent=2)
                        rows = load_bundle(lock_path.parent / 'public-catalog.json.gz')
                        index = engine.build_feature_index(rows); reader = SeededPublicReader(rows)
                        root.mkdir(parents=True)
                        with (root / 'development-plan.json').open('x', encoding='utf-8') as file:
                            json.dump(sign_artifact('recall-completion-plan', current), file, indent=2)
                        results = []
                        for number, (user, context, targets) in enumerate(samples, 1):
                            if perf_counter() - started > config['maximum_wall_seconds']:
                                raise ValueError('Original time budget exceeded; attempt remains consumed')
                            progress(f'trial sample {number}/{len(samples)}: V1 / protected / append-only; all categories twice')
                            result = trial.evaluate(user, context, index, targets, reader)
                            results.append(result)
                            progress('discovery Top10 hits: ' + ' / '.join(str(round(result['holdout'][arm]['all']['hit_at_k'] * 4)) for arm in trial.ARMS))
                        if perf_counter() - started > config['maximum_wall_seconds']:
                            raise ValueError('Original time budget exceeded; no partial-success report')
                        data = sign_artifact('recall-completion-comparison', {**current, 'ranking_performed': True,
                            'samples': results, 'summary': trial.summary(results), 'seconds': round(perf_counter() - started, 3),
                            'read_only': True, 'no_review_reads': True, 'no_external_http': True,
                            'approval_issued': False, 'complete': True})
                        filename = 'comparison.json'
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed; no completed artifact')
            root.mkdir(parents=True, exist_ok=True)
            with (root / filename).open('x', encoding='utf-8') as file:
                json.dump(data, file, indent=2, ensure_ascii=False, allow_nan=False)
            progress(f'Complete: {root / filename}; V1 unchanged; never serving approval.')
        except (ValueError, OSError, KeyError, TypeError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Trial stopped safely ({type(exc).__name__})') from None
