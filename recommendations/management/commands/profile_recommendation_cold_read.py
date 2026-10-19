"""Protected V2 IO-only experiment. No ranking tuning or serving changes."""
from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter, process_time
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, DatabaseError
from django.test import override_settings
from rest_framework.exceptions import APIException

from recommendations import browsing, engine, discovery_development as development
from recommendations.cold_public import SeededPublicReader, compact_catalog_revision, load_bundle, write_bundle, VERSION
from recommendations.comparison import QueryCounter
from recommendations.evidence import _token
from recommendations.execution import recipe_descriptor
from recommendations.exploration_audit import read_frozen_inputs
from recommendations.quality_readonly import readonly_snapshot
from recommendations.recall_diagnostics import output_digest
from recommendations.unused_validation import private_path
from recommendations.version_control import release_descriptor, sign_artifact, read_artifact


def measured(call):
    counter = QueryCounter(); started, cpu = perf_counter(), process_time()
    with connection.execute_wrapper(counter):
        result = call()
    return result, {'compute_ms': round((perf_counter() - started) * 1000, 3),
        'process_cpu_ms': round((process_time() - cpu) * 1000, 3), 'sql_queries': counter.count,
        'sql_execute_ms': round(counter.seconds * 1000, 3)}


class Command(BaseCommand):
    help = 'Authorized read-only cold bootstrap diagnostic; protected V2 only, no quality/release claim.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-sitewide-readonly', action='store_true')
        parser.add_argument('--prior-site-lock', required=True, type=Path)
        parser.add_argument('--test-report', required=True, type=Path)
        parser.add_argument('--output-dir', required=True, type=Path)

    def handle(self, *args, **options):
        if (not options['confirm_sitewide_readonly'] or connection.vendor != 'postgresql'
                or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
                or getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False)):
            raise CommandError('Require authorization, PostgreSQL, V1 and V2 off')
        try:
            root = private_path(options['output_dir'])
            if root.exists():
                raise ValueError('New output directory required')
            from .validate_recommendation_holdouts import test_proof
            test_proof(options['test_report'])
            proof = read_artifact(private_path(options['test_report']), 'unused-holdout-tests')
            if proof.get('cold_public_tests', 0) < 8:
                raise ValueError('Current isolated cold bootstrap proof required')
            lock = read_frozen_inputs(options['prior_site_lock'])
            fingerprint = release_descriptor()['fingerprint']
            with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')):
                with readonly_snapshot():
                    users = list(get_user_model().objects.filter(is_active=True).order_by('pk').only('pk', 'is_active')[:501])
                    if len(users) > 500:
                        raise ValueError('Account budget exceeded')
                    handles = {entry['account'] for entry in lock['entries']}
                    user = next((u for u in users if _token('recommendations.deployment.account.v1', u.pk) in handles), None)
                    if user is None:
                        raise ValueError('Original account unavailable')
                    context, profile_timing = measured(lambda: engine.user_context(user))
                    original = deepcopy(context)
                    self.stdout.write('Comparing exact catalog revision paths'); self.stdout.flush()
                    revision, full_revision = measured(browsing.catalog_revision)
                    compact, compact_revision = measured(compact_catalog_revision)
                    if compact != revision:
                        raise ValueError('Compact revision is not byte-equivalent')
                    with override_settings(RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED=False):
                        (public_rows, index), full_index = measured(lambda: browsing.public_browse_index(revision, {}, recipe=recipe_descriptor()))
                    baselines, results = {}, {}
                    for category in ('all', 'familiar', 'explore'):
                        self.stdout.write(f'Protected V2 parity: {category}'); self.stdout.flush()
                        rows, baseline = development.run(user, context, index, category, 'protected_v2')
                        baselines[category] = output_digest(rows)
                        seeded_rows, seeded = development.run(user, context, index, category, 'protected_v2', public_reader=SeededPublicReader(public_rows))
                        if output_digest(seeded_rows) != baselines[category]:
                            raise ValueError('Seeded public metadata changed ranking/evidence')
                        results[category] = {'baseline': baseline, 'seeded_first_call': seeded, 'identical': True}
                    bundle = root / 'public-catalog.json.gz'
                    _, prepare = measured(lambda: write_bundle(public_rows, bundle))
                    def bootstrap():
                        # New objects loaded only from the prepared file. This
                        # is a logical cold worker, NOT an OS/HTTP restart claim.
                        loaded = load_bundle(bundle)
                        loaded_index = engine.build_feature_index(loaded)
                        reader = SeededPublicReader(loaded)
                        if compact_catalog_revision() != revision:
                            raise engine.RecommendationSnapshotChanged()
                        ranked, timing = development.run(user, context, loaded_index, 'all', 'protected_v2', public_reader=reader)
                        if output_digest(ranked) != baselines['all']:
                            raise ValueError('Disk bootstrap changed ranking/evidence')
                        return loaded_index, reader, timing
                    (loaded_index, reader, first_engine), cold = measured(bootstrap)
                    warm = []
                    for _ in range(3):
                        rows, timing = development.run(user, context, loaded_index, 'all', 'protected_v2', public_reader=reader)
                        if output_digest(rows) != baselines['all']:
                            raise ValueError('Repeated bootstrap output changed')
                        warm.append(timing)
                    if context != original:
                        raise ValueError('Private context mutated')
                    data = {'scope': 'protected-v2-io-only', 'public_read_version': VERSION,
                        'online_default': 'content.v1', 'serving_approval': False, 'quality_evaluated': False,
                        'read_only': True, 'accounts_read': 1, 'targets_selected': 0,
                        'selection': 'first-original-frozen-account-by-pk-not-outcome',
                        'profile': profile_timing, 'full_catalog_revision': full_revision,
                        'compact_catalog_revision': compact_revision, 'revision_identical': True,
                        'live_public_index_build': full_index, 'category_parity': results,
                        'bundle_prepare': prepare, 'bundle_compressed_bytes': bundle.stat().st_size,
                        'disk_bootstrap': cold, 'disk_bootstrap_first_engine': first_engine, 'warm_engine': warm,
                        'cold_scope': 'fresh-objects-from-prepared-public-file-not-new-os-process',
                        'bundle_generation_excluded_from_request_but_reported': True,
                        'private_context_reused_and_profile_timing_reported_separately': True,
                        'deployment_http_performance': 'not_measured', 'no_external_models': True}
            if fingerprint != release_descriptor()['fingerprint']:
                raise ValueError('Code changed during diagnostic')
            root.mkdir(parents=True, exist_ok=True)
            with (root / 'profile.json').open('x', encoding='utf-8') as file:
                json.dump(sign_artifact('protected-v2-cold-profile', data), file, indent=2, allow_nan=False)
            self.stdout.write(f'Complete: {root}; protected V2 unchanged; no serving approval.')
        except (ValueError, OSError, KeyError, TypeError, DatabaseError, APIException) as exc:
            raise CommandError(str(exc) if type(exc) is ValueError else f'Cold diagnostic stopped safely ({type(exc).__name__})') from None
