from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from .models import UserAnimeRecord
from .serializers import UserAnimeRecordSerializer

from . import watch_plans

CACHE_HEADERS = {'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'}


class WatchPlanDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, record_id):
        return Response(watch_plans.read_detail(request.user, record_id, request), headers=CACHE_HEADERS)

    @transaction.atomic
    def post(self, request, record_id):
        payload = watch_plans.command(request.user, record_id, request.data, request)
        record = UserAnimeRecord.objects.select_related('anime').prefetch_related('tag_preferences').get(pk=record_id, user=request.user)
        payload['canonical_record'] = UserAnimeRecordSerializer(record, context={'request': request, 'private_plans': True}).data
        return Response(payload, headers=CACHE_HEADERS)


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


class WatchPlanBatchView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        if request.query_params:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('批量操作须明确选择作品，不能直接提交筛选条件。')
        return Response(watch_plans.batch(request.user, request.data, request), headers=CACHE_HEADERS)
