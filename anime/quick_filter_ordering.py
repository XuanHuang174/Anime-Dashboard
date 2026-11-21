"""Complete, owner-scoped quick-filter order with optimistic conflict detection."""
import hashlib
import json

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response


class OrderConflict(APIException):
    status_code = 409
    default_detail = '组合列表或顺序已变化，请重新读取顺序后再保存。条件草稿会保留。'


class OrderInput(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=10000)
    version = serializers.RegexField(r'^[0-9a-f]{64}$')

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != {'ids', 'version'}:
            raise serializers.ValidationError({'non_field_errors': ['请提交完整组合 ID 列表和顺序版本。']})
        return super().to_internal_value(data)


def order_version(rows):
    value = [(row.pk, row.sort_order) for row in rows]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


class QuickFilterOrderMixin:
    def _lock_order_scope(self):
        from .models import UserQuickFilter
        if self.get_queryset().model is UserQuickFilter:
            return get_user_model().objects.select_for_update().get(pk=self.request.user.pk)
        # The first administrator is a common lock anchor for all global-filter writers,
        # including creates when there are no existing featured filters to lock yet.
        return get_user_model().objects.select_for_update().filter(is_superuser=True).order_by('pk').first()

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        self._lock_order_scope()
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        from .models import UserQuickFilter
        highest = self.get_queryset().aggregate(value=Max('sort_order'))['value']
        values = {'sort_order': 0 if highest is None else highest + 1}
        if self.get_queryset().model is UserQuickFilter:
            values['user'] = self.request.user
        serializer.save(**values)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self._lock_order_scope()
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self._lock_order_scope()
        return super().destroy(request, *args, **kwargs)

    def _order_response(self, rows):
        return Response({'filters': self.get_serializer(rows, many=True).data, 'version': order_version(rows)},
                        headers={'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'})

    @action(detail=False, methods=('get', 'post'))
    def order(self, request):
        if request.method == 'GET':
            return self._order_response(list(self.get_queryset().order_by('sort_order', 'pk')))
        data = OrderInput(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            self._lock_order_scope()
            rows = list(self.get_queryset().select_for_update().order_by('sort_order', 'pk'))
            if data.validated_data['version'] != order_version(rows):
                raise OrderConflict()
            identities = data.validated_data['ids']
            if len(identities) != len(set(identities)) or set(identities) != {row.pk for row in rows}:
                raise serializers.ValidationError('请提交当前账号或管理范围内的完整组合，不得重复、遗漏或混入其他组合。')
            if identities == [row.pk for row in rows]:
                return self._order_response(rows)
            indexed = {row.pk: row for row in rows}
            ordered = [indexed[identity] for identity in identities]
            for position, row in enumerate(ordered):
                row.sort_order = position
            self.get_queryset().model.objects.bulk_update(ordered, ['sort_order'], batch_size=500)
            return self._order_response(ordered)
