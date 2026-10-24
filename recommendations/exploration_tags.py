"""11D: public tag counts and whole-catalog hard matching, no personal profile."""
import math
from collections import Counter

from . import engine
from .browse_controls import filter_index
from .browsing import catalog_revision, public_browse_index
from .content_scope import ensure_scope
from .features import FEATURE_RULE_VERSION
from .exploration_contract import OPTIONS_VERSION
from .quality_readonly import readonly_snapshot
from .taxonomy import AVAILABLE_TAGS, TAXONOMY_VERSION

COUNT_DEFINITION = 'scoped_catalog_without_result_filters'


def matches_tags(tags, query):
    included = frozenset(query['include_tags'])
    return (not tags.intersection(query['exclude_tags']) and bool(tags.intersection(included))
            and (query['match'] == 'any' or included <= tags))


def rank_tags(index, rows, query, excluded):
    """Filter the ENTIRE scoped catalog before sorting/capping at 120 results.

    ALL: cosine, public score, ID. ANY: weighted coverage, cosine, score, ID.
    Score sort: valid public score first, the same relevance tie-breaks, then ID.
    Zero means no public score and sorts behind scored works; never personal.
    """
    weights, included = index['weights'], frozenset(query['include_tags'])
    selected_norm = math.sqrt(sum(weights.get(tag, 1) ** 2 for tag in sorted(included)))
    selected_weight = sum(weights.get(tag, 1) for tag in sorted(included))
    filters = {**query, 'year_from': query.get('year_from'), 'year_to': query.get('year_to')}
    ranked = []
    for identity, tags, score, _ in filter_index(index, rows, filters)['features']:
        if identity in excluded or not matches_tags(tags, query):
            continue
        common = included & tags
        norm = math.sqrt(sum(weights.get(tag, 1) ** 2 for tag in sorted(tags)))
        cosine = sum(weights.get(tag, 1) ** 2 for tag in sorted(common)) / (selected_norm * norm)
        coverage = sum(weights.get(tag, 1) for tag in sorted(common)) / selected_weight
        relevance = (-coverage, -cosine) if query['match'] == 'any' else (-cosine,)
        valid_score = score if score > 0 else -1
        key = ((-valid_score, *relevance, identity) if query['sort'] == 'score'
               else (*relevance, -valid_score, identity))
        ranked.append((key, identity))
    ranked.sort(key=lambda item: item[0])
    return [identity for _, identity in ranked[:engine.MAX_RESULTS]], len(ranked)


def tag_options(user, scope_context, metrics=None):
    # Import only at request time: the shared snapshot pipeline calls rank_tags.
    from .exploration_browsing import _require_snapshot
    _require_snapshot()
    metrics = metrics if metrics is not None else {}
    with readonly_snapshot():
        scope = ensure_scope(user, scope_context)
        revision = catalog_revision()  # Live exact fingerprint, also enforces the 50k bound.
        rows, index = public_browse_index(revision, metrics, scope_context=scope)
        counts = Counter(tag for _, tags, _, _ in index['features'] for tag in tags)
        return {'contract_version': OPTIONS_VERSION, 'taxonomy_version': TAXONOMY_VERSION,
                'feature_rule_version': FEATURE_RULE_VERSION, 'catalog_feature_version': revision,
                'count_definition': COUNT_DEFINITION, 'catalog_count': len(rows),
                'count_description': '当前内容范围的目录数量，未叠加本次结果筛选；不扣除已有记录或隐藏反馈。',
                'exclusion_description': '排除按目录已有题材标注筛选，不保证作品绝无相关内容。',
                'tags': [{'tag': tag, 'count': counts[tag]} for tag in AVAILABLE_TAGS]}
