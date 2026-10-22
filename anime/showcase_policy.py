"""Publication content checks reuse the existing general-adult review ledger.

Public metadata is directly visible, without viewer-account grants or a new
opt-in. No content inference, media requests or private journal reads occur here.
"""
from django.db.models import Q, Subquery

UNSET = object()


def publication_content(shared, catalog=UNSET, *, allow_missing=False):
    from recommendations.adult_policy import PROHIBITED_IDENTITIES
    from recommendations.content_review import _decision
    from recommendations.models import RecommendationAnime
    if shared.bgm_id in PROHIBITED_IDENTITIES:
        return {'allowed': False, 'code': 'showcase_content_excluded',
            'detail': '该条目命中内容边界排除，不能公开；推荐审核通过记录不能覆盖此限制。', 'signature': None}
    if shared.bgm_id is None:
        return {'allowed': True, 'signature': None}
    if catalog is UNSET:
        catalog = RecommendationAnime.objects.select_related('content_review').filter(bangumi_id=shared.bgm_id).first()
    if catalog is None:
        if allow_missing:
            return {'allowed': True, 'signature': None}
        return {'allowed': False, 'code': 'showcase_catalog_missing',
            'detail': '尚无此 Bangumi ID 的确切目录资料，请先补齐目录并核对分级。', 'signature': None}
    if catalog.nsfw is False:
        return {'allowed': True, 'signature': None}
    if catalog.nsfw is None:
        return {'allowed': False, 'code': 'showcase_unknown_rating',
            'detail': '作品分级尚不明确，暂不能公开；不需要反复提交。', 'signature': None}
    decision = _decision(catalog, getattr(catalog, 'content_review', None))
    if not decision['review_valid']:
        excluded = decision['effective_status'] == 'content_excluded'
        return {'allowed': False,
            'code': 'showcase_content_excluded' if excluded else 'showcase_review_required',
            'detail': ('该条目命中内容边界排除，不能公开。' if excluded else
                'NSFW 作品需要当前有效的一般成人内容审核；请先在作品审核台账完成或更新核验，再重新读取此列表。'),
            'signature': None}
    return {'allowed': True, 'signature': {'rating': 'adult',
        'review_version': decision['review_version'], 'catalog_revision': decision['catalog_revision'],
        'policy_version': decision['policy_version'], 'never_expires': decision['never_expires'],
        'expires_at': decision['expires_at'].isoformat() if decision['expires_at'] else None}}


def filter_publication_content(queryset, identity_field='bgm_id'):
    """Current checks on every public list/detail/stat read, independent of login."""
    from recommendations.adult_policy import PROHIBITED_IDENTITIES
    from recommendations.content_scope import release_snapshot
    from recommendations.models import RecommendationAnime
    released, _, _ = release_snapshot()
    # Exclude NULL identities from NOT IN; one NULL must not hide the safe library.
    blocked = RecommendationAnime.objects.filter(bangumi_id__isnull=False).filter(
        Q(nsfw=True) | Q(nsfw__isnull=True)).exclude(bangumi_id__in=released).values('bangumi_id')
    return queryset.exclude(Q(**{identity_field + '__in': Subquery(blocked)})
        | Q(**{identity_field + '__in': PROHIBITED_IDENTITIES}))


class PublicContentResponseMixin:
    """Drop a serialized page if a returned work became unavailable meanwhile."""
    def finalize_response(self, request, response, *args, **kwargs):
        if request.method == 'GET' and response.status_code < 400 and isinstance(response.data, dict):
            from rest_framework.response import Response
            from .models import Anime
            rows = response.data.get('results', [response.data] if 'id' in response.data else [])
            ids = {row.get('anime', row).get('id') for row in rows if isinstance(row, dict)}
            ids.discard(None)
            if ids != set(filter_publication_content(Anime.objects.filter(pk__in=ids, is_active=True)).values_list('pk', flat=True)):
                response = Response({'code': 'showcase_content_changed',
                    'detail': '公共作品状态已变化，请重新读取。'}, status=409)
        return super().finalize_response(request, response, *args, **kwargs)
