from decimal import Decimal
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import NotFound

from .binding import candidate_ticket
from .identity import safe_catalog
from .matching import catalog_matcher, IdentityCatalogUnavailable
from .resolution import resolved_records


def review_contract(user, options):
    rows = resolved_records(user)
    if not rows.schema_available:
        raise IdentityCatalogUnavailable('关联工具尚未完成部署；手账数据不受影响。')
    if 'record_id' in options:
        rows = [row for row in rows if row['pk'] == options['record_id']]
        if not rows or rows[0]['identity_state'] in ('unsafe_or_unknown', 'not_in_catalog'):
            raise NotFound('该记录无法进行推荐身份关联。')
    pending = [row for row in rows if row['identity_state'] in ('unlinked', 'stale_link', 'identity_conflict')
               and row['watch_status'] == 'completed' and row['personal_score'] is not None
               and row['personal_score'] >= Decimal(str(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD))]
    if 'record_id' in options and not pending:
        raise NotFound('该记录不在待核对列表中。')
    start = (options['page'] - 1) * options['page_size']
    selected = pending[start:start+options['page_size']]
    matcher = catalog_matcher(force=True) if selected and 'keyword' not in options else None
    results = []
    for row in selected:
        if row['anime__bgm_id']:
            status, candidates = 'needs_admin_review', []
        elif 'keyword' in options:
            keyword = options['keyword']
            candidates = list(safe_catalog().filter(Q(title_zh__icontains=keyword) | Q(title_jp__icontains=keyword))
                .order_by('-score', 'bangumi_id').values('pk', 'bangumi_id', 'title_zh', 'title_jp', 'season', 'status_type', 'nsfw')[:5])
            status = 'needs_confirmation' if candidates else 'no_safe_candidate'
        else:
            match = matcher.match(row['metadata'])
            status, candidates = match['status'], match['candidates']
        results.append({'record_id': row['pk'], 'title': row['metadata']['title_zh'],
                        'original_title': row['metadata']['title_jp'], 'season': row['metadata']['season'],
                        'identity_version': row['identity_version'], 'status': status,
                        'candidates': [{key: candidate[key] for key in ('bangumi_id', 'title_zh', 'title_jp', 'season', 'status_type')}
                                       | {'ticket': candidate_ticket(row, candidate), 'content_rating': 'non_adult'}
                                       for candidate in candidates]})
    return {'count': len(pending), 'page': options['page'],
            'next_page': options['page']+1 if start+len(selected) < len(pending) else None, 'results': results}
