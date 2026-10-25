from django.db import DatabaseError
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from .views import OwnRecommendationAPIView
from .series_read import read_relations, require_enabled, verify_response


class SeriesRelationsView(OwnRecommendationAPIView):
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request, bangumi_id):
        require_enabled()
        params = self.query_params(request)
        if set(params) - {'context'} or any(len(params.getlist(key)) != 1 or not params[key] for key in params):
            raise ValidationError('系列读取仅接受单个非空 context 参数。')
        return Response(read_relations(request.user, bangumi_id, request.content_scope, params.get('context')))

    def finalize_response(self, request, response, *args, **kwargs):
        if response.status_code < 400 and isinstance(response.data, dict):
            try:
                require_enabled()
                verify_response(response.data)
            except (APIException, DatabaseError) as exc:
                response = self.handle_exception(exc)
        return super().finalize_response(request, response, *args, **kwargs)
