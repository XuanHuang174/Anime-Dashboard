"""D1 complete V2 recipe, trusted prepared entry only; D2 serving not enabled."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.db import connection
from rest_framework.exceptions import NotAuthenticated, PermissionDenied

from . import engine, ranking_v2, recall_v2, rerank_runtime, v2_controls
from .version_control import canonical_bytes

CONTRACT = 'recommendations.execution.v1'


def recipe_descriptor(*, serving_enabled=False):
    policy = v2_controls.policy_contract()
    declared = json.loads((Path(settings.BASE_DIR) / 'evaluation/v2d-controls.v1.json').read_text(encoding='utf-8'))
    if declared != policy:
        raise ValueError('V2 control preregistration changed; no silent tuning')
    body = {'contract_version': CONTRACT, 'algorithm_version': 'content.v2',
            'ranking_policy_version': ranking_v2.POLICY_VERSION,
            'recall_policy_version': recall_v2.VERSION, 'runtime_version': rerank_runtime.VERSION,
            'control_policy_version': v2_controls.VERSION,
            'control_policy_sha256': hashlib.sha256(canonical_bytes(policy)).hexdigest(),
            'candidate_limit': engine.CANDIDATE_POOL, 'result_limit': engine.MAX_RESULTS,
            'batch_size': 24, 'default_page_size': 12, 'serving_enabled': serving_enabled}
    return {**body, 'recipe_revision': hashlib.sha256(canonical_bytes(body)).hexdigest()}


def execute_v2(user, options, index, *, context=None, observer=None, serving_enabled=False, exploration_policy_version=None,
               discovery_policy_version=None):
    """Own-user service; caller supplies fresh profile/index inside a snapshot.

    Does not mint cursors or feed any existing serving route. D2 must bind the
    complete recipe in list/detail/snapshot contracts before serving V2.
    """
    if not user.is_authenticated:
        raise NotAuthenticated()
    if discovery_policy_version is not None:
        from . import discovery_candidate, dual_discovery
        from .exploration_candidate import PROTECTED_VERSION
        if (serving_enabled or discovery_policy_version not in (discovery_candidate.VERSION, dual_discovery.VERSION)
                or exploration_policy_version != PROTECTED_VERSION):
            raise engine.RecommendationUnavailable('发现候选仅允许离线对照，不能上线。')
        discovery_candidate = dual_discovery if discovery_policy_version == dual_discovery.VERSION else discovery_candidate
        discovery_candidate.policy_contract()
    if exploration_policy_version is not None:
        from . import exploration_candidate
        if serving_enabled or exploration_policy_version not in (exploration_candidate.VERSION, exploration_candidate.DUAL_VERSION,
                                                                exploration_candidate.PROTECTED_VERSION):
            raise engine.RecommendationUnavailable('探索候选仅允许离线对照，不能上线。')
    options = v2_controls.validate_options(options)
    if connection.vendor != 'postgresql':
        raise engine.RecommendationUnavailable('V2执行需要PostgreSQL只读快照。')
    if not connection.in_atomic_block:
        from .quality_readonly import readonly_snapshot
        with readonly_snapshot():
            return execute_v2(user, options, index, context=context, observer=observer, serving_enabled=serving_enabled,
                              exploration_policy_version=exploration_policy_version,
                              discovery_policy_version=discovery_policy_version)
    with connection.cursor() as cursor:
        cursor.execute('SHOW transaction_read_only'); readonly = cursor.fetchone()[0]
        cursor.execute('SHOW transaction_isolation'); isolation = cursor.fetchone()[0]
    if readonly != 'on' or isolation != 'repeatable read':
        raise engine.RecommendationUnavailable('V2执行必须使用一致的数据库只读快照。')
    context = engine.user_context(user) if context is None else context
    if context.get('_owner_id') != user.pk:
        raise PermissionDenied('执行上下文不属于当前用户。')
    if serving_enabled:
        from .deployment import require_recipe
        recipe = require_recipe()
    else:
        recipe = recipe_descriptor()
        if exploration_policy_version is not None:
            recipe = exploration_candidate.recipe_descriptor(exploration_policy_version)
        if discovery_policy_version is not None:
            recipe = discovery_candidate.recipe_descriptor()
    if context.get('taxonomy_version', engine.vocabulary.TAXONOMY_VERSION) != index.get('taxonomy_version', engine.vocabulary.TAXONOMY_VERSION):
        raise engine.RecommendationUnavailable('画像与目录题材规则不一致。')
    prepared = deepcopy(context)
    ranked, weights = engine._rank_context_v2(prepared, options, index,
        recall_policy_version=recipe['recall_policy_version'], runtime_version=recipe['runtime_version'],
        browse_control_version=recipe['control_policy_version'], observer=observer,
        exploration_policy_version=exploration_policy_version, discovery_policy_version=discovery_policy_version)
    return ranked, weights, {**recipe, 'effective_conditions': options,
                             'trace': deepcopy(prepared.get('_ranking_trace', {}))}
