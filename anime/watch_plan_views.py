from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import watch_plans

CACHE_HEADERS = {'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'}


class WatchPlanDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, record_id):
        return Response(watch_plans.read_detail(request.user, record_id, request), headers=CACHE_HEADERS)

    def post(self, request, record_id):
        return Response(watch_plans.command(request.user, record_id, request.data, request), headers=CACHE_HEADERS)


class WatchPlanQueueView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(watch_plans.read_queue(request.user, request.query_params, request), headers=CACHE_HEADERS)


class WatchPlanReorderView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        if request.query_params:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('请提交未筛选的完整队列顺序。')
        return Response(watch_plans.reorder(request.user, request.data), headers=CACHE_HEADERS)
