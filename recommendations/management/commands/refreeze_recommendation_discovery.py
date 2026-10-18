"""Authorized current-input development refresh; V1 and old archives untouched."""
import json
import hashlib
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from rest_framework.exceptions import APIException

from recommendations import browsing, discovery_development as development, unused_validation as validation
from recommendations import refrozen_development as refresh
from recommendations.dual_discovery import recipe_descriptor
from recommendations.execution import recipe_descriptor as reference_recipe
from recommendations.public_manifest import PublicManifestReader, VERSION as READ_VERSION
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.version_control import canonical_bytes, release_descriptor, read_artifact, sign_artifact


class Command(BaseCommand):
    help = 'Freeze/evaluate current inputs of identical consumed targets; development only.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        parser.add_argument('--consumed-cohort', required=True, type=Path)
        parser.add_argument('--consumed-result', required=True, type=Path)
        parser.add_argument('--test-report', required=True, type=Path)
        parser.add_argument('--freeze-only', action='store_true')
        parser.add_argument('--cohort-lock', type=Path)
        parser.add_argument('--output-dir', required=True, type=Path)

    def handle(self, *args, **options):
        if (not options['confirm_sitewide_readonly'] or connection.vendor != 'postgresql'
                or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Require authorization, PostgreSQL, V1 and V2 off')
        try:
            if options['freeze_only'] == bool(options['cohort_lock']):
                raise ValueError('Choose freeze-only OR evaluation with a new cohort lock')
            root = validation.private_path(options['output_dir'])
            if root.exists():
                raise ValueError('New output directory required')
            from .validate_recommendation_holdouts import test_proof
            proof_sha = test_proof(options['test_report'])
            proof = read_artifact(validation.private_path(options['test_report']), 'unused-holdout-tests')
            if proof.get('public_manifest_tests', 0) < 8:
                raise ValueError('Current isolated public manifest tests required')
            original, previous = development.consumed_inputs(options['consumed_cohort'], options['consumed_result'])
            fingerprint = release_descriptor()['fingerprint']
            started = perf_counter()
            config = validation.configuration()
            def progress(message):
                self.stdout.write(message); self.stdout.flush()
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only('pk', 'is_active')[:config['maximum_accounts'] + 1])
                    if len(users) > config['maximum_accounts']:
                        raise ValueError('Account budget exceeded')
                    entries, samples = refresh.bind_original(users, original, progress=progress)
                    revision = browsing.catalog_revision()
                    current = refresh.input_lock(original, previous, entries, revision)
                    if options['freeze_only']:
                        data = sign_artifact('discovery-refrozen-cohort', current)
                        filename = 'cohort.lock.json'
                    else:
                        frozen = read_artifact(validation.private_path(options['cohort_lock']), 'discovery-refrozen-cohort')
                        if any(frozen.get(key) != value for key, value in current.items()):
                            raise ValueError('Refrozen inputs drifted again; no silent refresh')
                        root.mkdir(parents=True)
                        plan = {**current, 'public_read_version': READ_VERSION, 'candidate_recipe': recipe_descriptor(),
                            'arms': list(development.DUAL_ARMS), 'targets': len(samples) * 4,
                            'test_report_sha256': proof_sha, 'complete': False,
                            'cohort_sha256': hashlib.sha256(canonical_bytes(frozen)).hexdigest()}
                        with (root / 'development-plan.json').open('x', encoding='utf-8') as file:
                            json.dump(sign_artifact('discovery-refrozen-plan', plan), file, indent=2)
                        _, index = browsing.public_browse_index(revision, {}, recipe=reference_recipe())
                        reader = PublicManifestReader()
                        # Natural parity on the first preselected account, all
                        # V2 arms/categories. Remaining safety/parity matrix is
                        # covered by the isolated test proof, not fabricated.
                        user, context, targets = samples[0]
                        training = validation.joint_training(context, targets)
                        parity = []
                        for arm in development.DUAL_ARMS[1:]:
                            for category in ('all', 'familiar', 'explore'):
                                progress(f'read parity {arm} {category}')
                                old = development.run(user, training, index, category, arm, dual=True)[0]
                                new = development.run(user, training, index, category, arm, dual=True, public_reader=reader)[0]
                                if output_digest(old) != output_digest(new):
                                    raise ValueError('Public read optimization changed ranking/evidence')
                                parity.append({'arm': arm, 'category': category, 'identical': True})
                        results = []
                        for number, (user, context, targets) in enumerate(samples, 1):
                            if perf_counter() - started > config['maximum_wall_seconds']:
                                raise ValueError('Original wall-time budget exceeded')
                            progress(f'development sample {number}/{len(samples)}: five fixed arms, live manifest')
                            results.append(development.evaluate(user, context, index, targets, dual=True, public_reader=reader))
                        if perf_counter() - started > config['maximum_wall_seconds']:
                            raise ValueError('Original wall-time budget exceeded')
                        data = sign_artifact('discovery-refrozen-development', {**plan, 'complete': True, 'ranking_performed': True,
                            'samples': results, 'verdict': development.report_verdict(results), 'read_only': True,
                            'natural_read_parity': parity, 'seconds': round(perf_counter() - started, 3),
                            'deployment_performance': 'not_measured', 'serving_approval': False})
                        filename = 'comparison.json'
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed; no completed artifact')
            root.mkdir(parents=True, exist_ok=True)
            with (root / filename).open('x', encoding='utf-8') as file:
                json.dump(data, file, indent=2, ensure_ascii=False, allow_nan=False)
            progress(f'Complete: {root / filename}; development only; no serving approval.')
        except (ValueError, OSError, KeyError, TypeError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Refresh stopped safely ({type(exc).__name__})') from None
