"""Server-only version isolation and signed, expiring release-check artifacts.

Operator safety gate, not a sandbox against an operator who owns SECRET_KEY/code.
No HTTP algorithm override, user-result cache, filesystem deletion or DB writes.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

ARTIFACT_VERSION = 'recommendations.release.v1'
GATE_NAMES = ('safety', 'evidence', 'relevance', 'categories', 'performance', 'rollback')
CHECK_MODULES = ('recommendations.tests.test_contracts_api', 'recommendations.tests.test_engine_api',
                 'recommendations.tests.test_ranking_v2', 'recommendations.tests.test_comparison')
SOURCE_PATHS = (
    'recommendations/public_fingerprint.py',
    'recommendations/engine.py', 'recommendations/ranking_v2.py', 'recommendations/version_control.py',
    'recommendations/taxonomy.py', 'recommendations/features.py', 'recommendations/extraction.py',
    'recommendations/services.py', 'recommendations/resolution.py', 'recommendations/identity.py',
    'recommendations/views.py', 'recommendations/want.py', 'recommendations/comparison.py',
    'recommendations/comparison_report.py', 'recommendations/quality_evaluation.py',
    'recommendations/quality_readonly.py', 'recommendations/management/commands/compare_recommendations.py',
    'recommendations/quality_audit.py', 'recommendations/matching.py', 'recommendations/normalization.py',
    'recommendations/cold_start.py', 'recommendations/serializers.py', 'recommendations/models.py', 'anime/models.py',
    'recommendations/management/commands/verify_recommendation_release.py',
    'config/settings.py', 'requirements.txt',
    *(module.replace('.', '/') + '.py' for module in CHECK_MODULES),
)


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def release_descriptor():
    from . import engine, ranking_v2, taxonomy, features
    sources = {name: hashlib.sha256((Path(settings.BASE_DIR) / name).read_text(
        encoding='utf-8').replace('\r\n', '\n').encode()).hexdigest() for name in SOURCE_PATHS}
    configuration = {'threshold': str(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD),
        'minimum_samples': settings.RECOMMENDATION_PROFILE_MIN_SAMPLES, 'pool': engine.CANDIDATE_POOL,
        'max_results': engine.MAX_RESULTS, 'max_catalog': engine.MAX_CATALOG_ROWS,
        'taxonomy': taxonomy.TAXONOMY_VERSION, 'features': engine.FEATURE_VERSION,
        'extraction': features.FEATURE_RULE_VERSION, 'v2_policy': ranking_v2.policy_contract()}
    body = {'sources': sources, 'configuration': configuration}
    return {**body, 'fingerprint': hashlib.sha256(canonical_bytes(body)).hexdigest()}


def sign_artifact(kind, payload, *, lifetime_days=7):
    now = datetime.now(timezone.utc)
    body = {**payload, 'artifact_version': ARTIFACT_VERSION, 'kind': kind,
            'release_fingerprint': release_descriptor()['fingerprint'],
            'issued_at': now.isoformat(), 'expires_at': (now + timedelta(days=lifetime_days)).isoformat()}
    signature = hmac.new(settings.SECRET_KEY.encode(), canonical_bytes(body), hashlib.sha256).hexdigest()
    return {**body, 'signature': signature}


def verify_artifact(value, kind):
    try:
        body = {key: item for key, item in value.items() if key != 'signature'}
        expected = hmac.new(settings.SECRET_KEY.encode(), canonical_bytes(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, value['signature']):
            raise ValueError()
        now = datetime.now(timezone.utc)
        issued, expires = (datetime.fromisoformat(value[key]) for key in ('issued_at', 'expires_at'))
        if (value['artifact_version'] != ARTIFACT_VERSION or value['kind'] != kind
                or value['release_fingerprint'] != release_descriptor()['fingerprint']
                or issued > now or expires <= now or expires - issued > timedelta(days=7)):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        raise ValueError('Invalid, stale or incompatible release artifact') from None
    return value


def read_artifact(path, kind):
    try:
        with Path(path).open('rb') as file:
            raw = file.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        return verify_artifact(json.loads(raw), kind)
    except (OSError, ValueError, TypeError):
        raise ValueError('Release artifact cannot be verified') from None


def require_v2_approval():
    if (getattr(settings, 'RECOMMENDATION_V2_TEST_BYPASS', False)
            and os.environ.get('DJANGO_SETTINGS_MODULE') == 'config.recommendation_test_settings'
            and settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'):
        return  # Never available through production environment variables.
    approval = read_artifact(settings.RECOMMENDATION_V2_APPROVAL_FILE, 'approval')
    if (approval.get('gates') != dict.fromkeys(GATE_NAMES, 'pass')
            or type(approval.get('sample_count')) is not int or approval['sample_count'] < 1):
        raise ValueError('All six gates and authorized samples are required')
    try:
        parent = Path(settings.RECOMMENDATION_V2_APPROVAL_FILE).parent
        manifest_raw = (parent / 'manifest.json').read_bytes()
        comparison_raw = (parent / 'comparison.json').read_bytes()
        approval_raw = Path(settings.RECOMMENDATION_V2_APPROVAL_FILE).read_bytes()
        if len(manifest_raw) > 16384 or len(comparison_raw) > 1024 * 1024:
            raise ValueError()
        manifest, report = json.loads(manifest_raw), json.loads(comparison_raw)
        if (manifest.get('complete') is not True or manifest.get('eligible_for_manual_switch') is not True
                or hashlib.sha256(approval_raw).hexdigest() != manifest['sha256']['approval.json']
                or hashlib.sha256(comparison_raw).hexdigest() != manifest['sha256']['comparison.json']
                or hashlib.sha256(canonical_bytes(report)).hexdigest() != approval['comparison_sha256']
                or report.get('eligible_for_manual_switch') is not True
                or report.get('test_attestation_verified') is not True
                or report['release']['fingerprint'] != approval['release_fingerprint']
                or {key: report['gates'][key]['status'] for key in GATE_NAMES} != approval['gates']
                or len(report['samples']) != approval['sample_count']
                or any(row.get('sample_kind') != 'explicitly_authorized_natural_account'
                       or type(row.get('usable_samples')) is not int or row['usable_samples'] < max(
                           4, settings.RECOMMENDATION_PROFILE_MIN_SAMPLES + 1) for row in report['samples'])):
            raise ValueError()
    except (ValueError, OSError, KeyError, TypeError):
        raise ValueError('Completed comparison manifest is required') from None


def cache_namespace(algorithm, taxonomy, feature, extraction):
    if algorithm not in ('content.v1', 'content.v2'):
        raise ValueError('Unsupported algorithm version')
    from .ranking_v2 import POLICY_VERSION
    suffix = f':{POLICY_VERSION}' if algorithm == 'content.v2' else ''
    return f'recommendations.public-features.v4:{algorithm}:{taxonomy}:{feature}:{extraction}{suffix}'


def clear_version_cache(algorithm):
    from . import engine
    key = cache_namespace(algorithm, engine.TAXONOMY_VERSION, engine.FEATURE_VERSION, engine.FEATURE_RULE_VERSION)
    cache.delete(key)  # One explicit public namespace, not cache.clear().


def rank_versions(context, options, index, *, versions=('content.v1', 'content.v2')):
    """Offline controller: cloned private contexts, fixed inputs, no global patch."""
    from .engine import rank_context
    return {version: rank_context(deepcopy(context), options, index, algorithm_version=version)[0]
            for version in versions}
