from django.db import DatabaseError
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from .views import OwnRecommendationAPIView
from .series_read import read_relations, require_enabled, verify_response
from .series_store import SeriesConflict


class SeriesRelationsView(OwnRecommendationAPIView):
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        from .series_rollout import require_access
        require_access(request.user)

    def handle_exception(self, exc):
        if isinstance(exc, SeriesConflict):
            response = Response({'code': 'series_snapshot_changed', 'detail': str(exc.detail)}, status=409)
            response.exception = True
            return response
        return super().handle_exception(exc)

    def get(self, request, bangumi_id):
        require_enabled()
        params = self.query_params(request)
        if set(params) - {'context'} or any(len(params.getlist(key)) != 1 or not params[key] for key in params):
            raise ValidationError('系列读取仅接受单个非空 context 参数。')
        return Response(read_relations(request.user, bangumi_id, request.content_scope, params.get('context')))

    def finalize_response(self, request, response, *args, **kwargs):
        if request.method == 'GET' and response.status_code < 400 and isinstance(response.data, dict):
            try:
                from .series_rollout import require_access
                require_access(request.user)
                if '_node_revision' in response.data:
                    verify_response(response.data)
            except (APIException, DatabaseError) as exc:
                response = self.handle_exception(exc)
        return super().finalize_response(request, response, *args, **kwargs)


def context_param(view, request, *, required=False):
    params = view.query_params(request)
    if (set(params) - {'context'} or any(len(params.getlist(key)) != 1 or not params[key] for key in params)
            or required and not params.get('context')):
        raise ValidationError('本接口仅接受单个非空 context 参数。')
    return params.get('context')


class SeriesWorkspaceView(SeriesRelationsView):
    def get(self, request, bangumi_id):
        from .series_workspace import workspace
        return Response(workspace(request.user, bangumi_id, request.content_scope, context_param(self, request)))


class SeriesDetailView(SeriesRelationsView):
    def get(self, request, bangumi_id, target_id):
        from .series_workspace import detail
        return Response(detail(request.user, bangumi_id, target_id, request.content_scope,
                               context_param(self, request, required=True)))


class SeriesWantView(SeriesRelationsView):
    throttle_scope = 'recommendation_write'

    def handle_exception(self, exc):
        from .want import WantConflict
        if isinstance(exc, WantConflict):
            response = Response(exc.payload, status=exc.status_code)
            response.exception = True
            return response
        return super().handle_exception(exc)

    def post(self, request, bangumi_id, target_id):
        from .series_workspace import want
        from .serializers import RecommendationWantSerializer
        context = context_param(self, request, required=True)
        if not isinstance(request.data, dict) or request.data:
            raise ValidationError('本接口只接受空 JSON 对象。')
        result = want(request.user, bangumi_id, target_id, request.content_scope, context)
        return Response(RecommendationWantSerializer(result).data, status=201 if result['created'] else 200)


class SeriesRecordView(SeriesRelationsView):
    def get(self, request, record_id):
        require_enabled()
        self.check_query_contract(request)
        from .series_workspace import record_reference
        return Response(record_reference(request.user, record_id, request.content_scope))


class SeriesAnnotationsView(SeriesRelationsView):
    def get(self, request):
        import re
        params = self.query_params(request)
        if (set(params) != {'ids'} or len(params.getlist('ids')) != 1
                or not re.fullmatch(r'[1-9]\d{0,9}(,[1-9]\d{0,9}){0,23}', params['ids'])):
            raise ValidationError('请提供 1–24 个不同的 Bangumi ID。')
        ids = [int(value) for value in params['ids'].split(',')]
        if len(set(ids)) != len(ids) or max(ids) > 2147483647:
            raise ValidationError('作品身份无效或重复。')
        from .series_exploration import annotations
        return Response(annotations(request.user, ids, request.content_scope))

    def finalize_response(self, request, response, *args, **kwargs):
        if response.status_code < 400 and isinstance(response.data, dict):
            try:
                from .series_exploration import verify
                from .browsing import catalog_revision
                verify(response.data, request.content_scope)
                if response.data['catalog_revision'] != catalog_revision():
                    raise SeriesConflict('目录资料已变化，请重新读取。')
            except (APIException, DatabaseError) as exc:
                response = self.handle_exception(exc)
        return super().finalize_response(request, response, *args, **kwargs)
