from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from . import curations

HEADERS = {'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'}


class PrivateCurationView(APIView):
    permission_classes = (IsAuthenticated,)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        for key, value in HEADERS.items(): response[key] = value
        return response

    def get(self, request, list_id=None):
        return Response(curations.read(request.user, request.query_params, list_id, request), headers=HEADERS)

    def post(self, request, list_id=None):
        if list_id is not None or request.query_params: raise ValidationError('请使用片单操作接口并明确指定条目。')
        return Response(curations.command(request.user, request.data, request), headers=HEADERS)


class CurationDeletionImpactView(PrivateCurationView):
    permission_classes = (IsAuthenticated,)
    http_method_names = ('get', 'head', 'options')

    @transaction.atomic
    def get(self, request, record_id):
        if request.query_params: raise ValidationError('删除影响不接受筛选参数。')
        curations.lock_owner(request.user)
        return Response(curations.deletion_impact(request.user, record_id), headers=HEADERS)
