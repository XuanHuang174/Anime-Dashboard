"""Fixed-input three-arm diagnostic. Artifacts here can NEVER authorize serving."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path

from django.conf import settings

from . import engine
from .deployment_evaluation import configuration
from .execution import execute_v2, recipe_descriptor as reference_recipe
from .exploration_candidate import execute_dual_candidate, recipe_descriptor, DUAL_VERSION
from .recall_diagnostics import audit_sample, traced_rank, output_digest
from .pipeline_trace import PipelineObserver
from .quality_evaluation import without_seed
from .version_control import canonical_bytes, ARTIFACT_VERSION


def read_frozen_inputs(path):
    """Only an input-lock import, NOT relaxed release/test/approval validation.

    Source changes are expected in an experiment. Preserve original signature,
    source fingerprint and selection; separately check ALL live cohort inputs.
    Never re-sign an old receipt as if tests ran against today's code.
    """
    path = Path(path).resolve()
    if not path.is_relative_to((Path(settings.BASE_DIR) / '.tools').resolve()):
        raise ValueError('Frozen inputs must remain in private .tools')
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('Frozen input lock too large')
    value = json.loads(raw)
    body = {k: v for k, v in value.items() if k != 'signature'}
    expected = hmac.new(settings.SECRET_KEY.encode(), canonical_bytes(body), hashlib.sha256).hexdigest()
    issued, expires = (datetime.fromisoformat(value[k]) for k in ('issued_at', 'expires_at'))
    now = datetime.now(timezone.utc)
    if (not hmac.compare_digest(expected, value['signature']) or value['kind'] != 'browse-cohort'
            or value['artifact_version'] != ARTIFACT_VERSION or issued > now or expires <= now
            or expires - issued > timedelta(days=7) or value['recipe'] != reference_recipe()
            or value['configuration'] != configuration()):
        raise ValueError('Invalid/expired original input lock; no target reselection allowed')
    return value


def verify_inputs(lock, *, entries, catalog_revision, active_account_count):
    if (lock['entries'] != entries or lock['catalog_revision'] != catalog_revision
            or lock['active_account_count'] != active_account_count):
        raise ValueError('Frozen cohort/catalog/private inputs drifted; stopped, no reselection')


def trace_target(user, context, index, target, category, *, candidate_executor=execute_dual_candidate):
    clone = without_seed(context, target, minimum=settings.RECOMMENDATION_PROFILE_MIN_SAMPLES)
    if clone is None:
        raise ValueError('Frozen holdout lost training eligibility')
    from .v2_controls import validate_options
    options = validate_options({'category': category})
    result = {}
    for arm in ('current_v1', 'current_v2', 'candidate_v2'):
        observer = PipelineObserver(target)
        if arm == 'current_v1':
            rows, _ = engine.rank_context(deepcopy(clone), options, index, pipeline_observer=observer)
        else:
            executor = execute_v2 if arm == 'current_v2' else candidate_executor
            rows, _, _ = executor(user, options, index, context=clone, observer=observer)
        result[arm] = observer.export()
    return result


def audit_history(user, context, summary, index, catalog, states, config, progress=lambda text: None,
                  *, candidate_executor=execute_dual_candidate):
    local = deepcopy(config)
    local['algorithms'] = ['content.v1', 'reference_v2', 'candidate_v2']

    def ranker(owner, private, public, filters, arm, category, *, target=None):
        if arm == 'content.v1':
            return traced_rank(owner, private, public, filters, arm, category, target=target)
        executor = execute_v2 if arm == 'reference_v2' else candidate_executor
        return traced_rank(owner, private, public, filters, 'content.v2', category, target=target,
                           prepared_executor=executor)

    result = audit_sample(user, context, summary, index, catalog, states, local, progress, ranker=ranker)
    for category in ('all', 'familiar'):
        for left, right in zip(result['baseline']['reference_v2'][category]['runs'],
                               result['baseline']['candidate_v2'][category]['runs']):
            if left['noninterference']['output_hmac'] != right['noninterference']['output_hmac']:
                raise ValueError('Exploration experiment changed another category')
    result['other_categories_unchanged'] = True
    return result


def metadata():
    return {'contract_version': 'recommendations.exploration-history.v1',
            'candidate_recipe': recipe_descriptor(DUAL_VERSION), 'reference_recipe': reference_recipe(),
            'serving_changed': False, 'decision': 'offline-only-no-approval'}
