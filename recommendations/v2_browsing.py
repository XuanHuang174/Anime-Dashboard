"""D2 prepared browse/detail adapter. Deliberately absent from production URLs.

No HTTP algorithm override, setting, release approval or rollout switch is added.
Reuses the SAME batch service and D1 executor; D3 must approve any serving route.
"""
import hmac
import time
import uuid

from django.core import signing
from django.db import connection
from rest_framework.exceptions import NotAuthenticated, NotFound, ValidationError

from . import browsing, engine
from .browse_controls import canonical_options, OPTION_KEYS
from .evidence import EvidencePresenter
from .execution import recipe_descriptor
from .quality_readonly import readonly_snapshot

DETAIL_SALT = 'recommendations.browse.detail.v2'


def _recipe(serving_enabled=False):
    if serving_enabled:
        from .deployment import require_recipe
        return require_recipe()
    return recipe_descriptor()


def capabilities(*, serving_enabled=False):
    """Isolated adapter descriptor, NOT wired to public capabilities()."""
    return {**browsing.capabilities(), 'algorithm_version': 'content.v2',
            'browse_contract_version': browsing.PREPARED_CONTRACT,
            'execution_recipe': _recipe(serving_enabled)}


def browse_page(user, validated, supplied, *, metrics=None, serving_enabled=False):
    if not user.is_authenticated:
        raise NotAuthenticated()
    return browsing.browse_page(user, validated, supplied, metrics=metrics, _recipe=_recipe(serving_enabled))


def issue_detail_context(body, identity):
    # Only public controls, canonical work ID and opaque revisions. NO profile,
    # private tags/ratings, source work IDs, user IDs or whole result lists.
    value = signing.dumps({key: body[key] for key in ('execution_recipe', 'options', 'revision',
        'owner', 'expires', 'snapshot', 'rollout_revision')} | {'bangumi_id': identity},
        salt=DETAIL_SALT, compress=True)
    if len(value) > browsing.MAX_CURSOR_LENGTH:
        raise engine.RecommendationUnavailable('详情上下文超过安全大小限制。')
    return value


def _detail_scope(user, identity, ticket, recipe):
    if not isinstance(ticket, str) or not 1 <= len(ticket) <= browsing.MAX_CURSOR_LENGTH:
        raise ValidationError('详情上下文无效。')
    try:
        body = signing.loads(ticket, salt=DETAIL_SALT, max_age=browsing.SNAPSHOT_TTL_SECONDS)
    except signing.SignatureExpired:
        raise engine.RecommendationSnapshotChanged('详情上下文已过期，请重新加载推荐。') from None
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError('详情上下文无效或已被修改。') from None
    try:
        if (body['owner'] != browsing._token(browsing.PREPARED_SALT + '.owner', user.pk)
                or body['execution_recipe'] != recipe
                or type(body['bangumi_id']) is not int or body['bangumi_id'] != identity
                or type(body['expires']) is not int or body['expires'] <= int(time.time())
                or body['rollout_revision'] != browsing.rollout_revision()):
            raise engine.RecommendationSnapshotChanged()
        from .v2_controls import validate_options
        options = canonical_options(validate_options(body['options']))
        if set(body['options']) != set(OPTION_KEYS) or options != body['options']:
            raise ValueError()
        return body, options
    except (KeyError, TypeError, ValueError):
        raise engine.RecommendationSnapshotChanged('详情上下文不兼容，请重新加载。') from None


def detail(user, identity, validated, *, serving_enabled=False):
    """Strict card read, or intentional fresh scoped read after a known write.

    With evidence_token: revision AND evidence must match the card exactly.
    Without evidence_token: preserve signed identity/recipe/controls, read whole
    latest detail, issue a fresh context. Never recompute the recommendation list.
    The frontend only chooses fresh mode after a validated mutation acknowledgement.
    """
    if not user.is_authenticated:
        raise NotAuthenticated()
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise engine.RecommendationUnavailable('详情需要独立的PostgreSQL只读快照。')
    if engine.configured_algorithm() != 'content.v1' or not browsing.enabled():
        raise engine.RecommendationSnapshotChanged('准备服务已失效，请重新加载。')
    recipe = _recipe(serving_enabled)
    body, options = _detail_scope(user, identity, validated.get('detail_context'), recipe)
    expected = validated.get('evidence_token')
    with readonly_snapshot():
        context, revision, catalog = browsing.live_context(user, options, {}, recipe=recipe)
        if expected and not hmac.compare_digest(revision, body['revision']):
            raise engine.RecommendationSnapshotChanged('浏览依据已更新，请重新加载推荐。')
        anime = engine.safe_catalog().filter(bangumi_id=identity).first()
        if anime is None:
            raise NotFound('该作品不可用于推荐。')
        _, index = browsing.public_browse_index(catalog, {}, recipe=recipe)
        presenter = EvidencePresenter(user, context, index['weights'], 'content.v2', execution_scope={
            'recipe': recipe, 'browse_revision': revision})
        presenter.ensure_safe([anime])
        explanation = presenter.present(anime, engine.feature_tags(anime.tags, (anime.title_zh, anime.title_jp)))
        if expected and not hmac.compare_digest(expected, explanation['evidence_token']):
            raise engine.RecommendationSnapshotChanged('推荐依据已更新，请重新加载推荐。')
        if not expected:
            body = {**body, 'revision': revision, 'snapshot': uuid.uuid4().hex,
                    'expires': int(time.time()) + browsing.SNAPSHOT_TTL_SECONDS}
        from .serializers import RecommendationPublicAnimeSerializer, EvidenceEnvelopeSerializer
        return {**RecommendationPublicAnimeSerializer(anime).data,
                **EvidenceEnvelopeSerializer(explanation).data,
                'detail_context': issue_detail_context(body, identity),
                'content_rating': 'non_adult', 'watch_status': context['owned'].get(identity)}
