from django.db import transaction, IntegrityError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from . import import_plans as plans


class PrivateImportView(APIView):
    permission_classes=(IsAuthenticated,)

    def finalize_response(self, request, response, *args, **kwargs):
        response=super().finalize_response(request,response,*args,**kwargs)
        response['Cache-Control']='private, no-store'
        response['Vary']='Authorization, Cookie'
        return response


class ImportPlanCollectionView(PrivateImportView):
    http_method_names=('post','options')

    def post(self, request):
        if request.query_params: raise ValidationError('创建方案不接受筛选参数。')
        return Response(plans.create_plan(request.user,request.data))


class ImportPlanDetailView(PrivateImportView):
    http_method_names=('get','head','options')

    @transaction.atomic
    def get(self,request,plan_id):
        plans.owner_lock(request.user)
        return Response(plans.read_plan(request.user,plan_id,request.query_params))


class ImportPlanScanView(PrivateImportView):
    http_method_names=('post','options')

    def post(self,request,plan_id):
        if request.query_params: raise ValidationError('读取步骤不接受筛选参数。')
        return Response(plans.scan_plan(request.user,plan_id,request.data))


class ImportPlanApplyView(PrivateImportView):
    http_method_names=('post','options')

    def post(self,request,plan_id):
        if request.query_params: raise ValidationError('应用取舍不接受筛选参数。')
        try: return Response(plans.apply_plan(request.user,plan_id,request.data))
        except IntegrityError: raise plans.PlanChanged('作品身份或数据约束已变化，本批已回滚，请重新核对。') from None


class JournalRestoreDiffView(PrivateImportView):
    """Field differences only. Existing restore confirmation remains authoritative."""
    http_method_names=('post','options')

    def post(self,request):
        from .journal_diff import restore_diff
        return Response(restore_diff(request))
