"""D3 fail-closed server dispatch. V1 rollback never needs an approval artifact."""
import hashlib
from pathlib import Path

from django.conf import settings

from . import browsing, engine, v2_browsing
from .execution import recipe_descriptor
from .version_control import read_artifact, GATE_NAMES, canonical_bytes


def selected_v2():
    return getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False) is True


def require_recipe():
    if (not selected_v2() or settings.RECOMMENDATION_ALGORITHM_VERSION != 'content.v1'
            or not browsing.enabled()):
        raise engine.RecommendationUnavailable('V2浏览未获启用；旧版引擎设置必须保持V1。')
    try:
        approval_path = Path(getattr(settings, 'RECOMMENDATION_V2_BROWSE_APPROVAL_FILE', ''))
        approval = read_artifact(approval_path, 'browse-approval')
        # Old 5B approvals and caller-written gate flags are not enough: follow
        # signed receipts and exact report bytes for THIS complete recipe.
        evaluation_path = approval_path.parent / 'evaluation.json'
        raw = evaluation_path.read_bytes()
        if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != approval['evaluation_sha256']:
            raise ValueError()
        evaluation = read_artifact(evaluation_path, 'browse-evaluation')
        tests = read_artifact(approval_path.parent / 'tests.json', 'browse-tests')
        performance = read_artifact(approval_path.parent / 'performance.json', 'browse-performance')
        from .deployment_evaluation import validate_tests, deployment_gates
        validate_tests(tests)
        gates = deployment_gates(evaluation, tests, performance)
        if (approval.get('recipe') != recipe_descriptor(serving_enabled=True)
                or evaluation.get('recipe') != recipe_descriptor()
                or approval.get('gates') != dict.fromkeys(GATE_NAMES, 'pass')
                or {key: value['status'] for key, value in gates.items()} != approval['gates']
                or approval.get('performance_sha256') != hashlib.sha256(canonical_bytes(performance)).hexdigest()
                or approval.get('tests_sha256') != hashlib.sha256(canonical_bytes(tests)).hexdigest()):
            raise ValueError()
    except (ValueError, OSError, KeyError, TypeError):
        raise engine.RecommendationUnavailable('V2浏览审批缺失、过期或不匹配，未切换算法。') from None
    return recipe_descriptor(serving_enabled=True)


def capabilities():
    return v2_browsing.capabilities(serving_enabled=True) if selected_v2() else browsing.capabilities()


def browse_page(user, validated, supplied, *, scope_context=None):
    if selected_v2():
        return v2_browsing.browse_page(user, validated, supplied, serving_enabled=True)
    return browsing.browse_page(user, validated, supplied, scope_context=scope_context)


def detail(user, identity, validated):
    if not selected_v2():
        raise engine.RecommendationSnapshotChanged('浏览版本已变化，请重新加载推荐。')
    return v2_browsing.detail(user, identity, validated, serving_enabled=True)
