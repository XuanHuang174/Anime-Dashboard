import uuid

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import APIException

from .series_contract import KINDS
from .series_models import (SeriesState, SeriesNode, SeriesObservation, SeriesHead, SeriesAssertion, SeriesOverrideEvent)
from .series_store import revise_relation


def can_review(user):
    return bool(user.is_active and user.is_staff and user.has_perm('recommendations.review_series_relations'))


class RevisionForm(forms.Form):
    target_id = forms.IntegerField(label='目标 Bangumi ID', min_value=1, max_value=2147483647)
    action = forms.ChoiceField(label='动作', choices=(('replace', '确认/更正为以下关系'), ('suppress', '暂不展示此目标关系'), ('clear', '撤销本地修订，重新采用当前来源')))
    kind = forms.ChoiceField(label='目标相对来源的关系', required=False, choices=[('', '非更正动作不填写')] + [(row.key, row.label) for row in KINDS])
    reason = forms.CharField(label='核验原因', max_length=240)
    evidence_reference = forms.CharField(label='核验依据链接/记录编号', max_length=500)
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    operation_id = forms.UUIDField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(label='已核对两端身份、关系方向及依据；本操作不放行作品或改变私人手账')


class AuditOnlyAdmin(admin.ModelAdmin):
    actions = None
    def has_view_permission(self, request, obj=None): return can_review(request.user)
    def has_module_permission(self, request): return can_review(request.user)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(SeriesNode)
class SeriesNodeAdmin(AuditOnlyAdmin):
    list_display = ('bangumi_id', 'title', 'subject_type', 'nsfw', 'release_date', 'release_format', 'control_link')
    search_fields = ('=bangumi_id', 'title', 'original_title')
    readonly_fields = tuple(field.name for field in SeriesNode._meta.fields)

    def get_urls(self):
        return [path('<int:source_id>/revise/', self.admin_site.admin_view(self.revise_view),
                     name='series_relation_revise')] + super().get_urls()

    @admin.display(description='人工核验')
    def control_link(self, obj):
        return format_html('<a href="{}">核验/修订直接关系</a>', reverse('admin:series_relation_revise', args=(obj.pk,)))

    @method_decorator(never_cache)
    def revise_view(self, request, source_id):
        if not can_review(request.user):
            raise PermissionDenied
        source = self.get_object(request, str(source_id))
        if source is None:
            raise PermissionDenied
        revision = SeriesState.objects.filter(pk=1).values_list('revision', flat=True).first() or 0
        form = RevisionForm(request.POST if request.method == 'POST' else None,
            initial={'expected_revision': revision, 'operation_id': uuid.uuid4()})
        error = None
        if request.method == 'POST' and form.is_valid():
            values = form.cleaned_data
            try:
                revise_relation(actor=request.user, source_id=source_id,
                    **{key: values[key] for key in ('target_id', 'action', 'kind', 'reason',
                        'evidence_reference', 'operation_id', 'expected_revision')})
                self.message_user(request, '系列修订已存档；推荐和内容审核开关保持原样。', messages.SUCCESS)
                return HttpResponseRedirect(request.path)
            except APIException as exc:
                error = str(exc.detail)
            except DatabaseError:
                error = '数据库未能完成修订，可保留原表单重试。'
        return TemplateResponse(request, 'admin/recommendations/series_revision.html', {
            **self.admin_site.each_context(request), 'opts': self.model._meta, 'title': '核验系列直接关系',
            'source': source, 'form': form, 'error': error,
            'assertions': SeriesAssertion.objects.filter(observation__source_id=source_id)
                .select_related('observation', 'target').order_by('-observation_id', 'source_order')[:100],
            'events': SeriesOverrideEvent.objects.filter(source_id=source_id).select_related('actor')[:50]})


for model in (SeriesObservation, SeriesAssertion, SeriesOverrideEvent, SeriesHead, SeriesState):
    admin.site.register(model, type(f'{model.__name__}Admin', (AuditOnlyAdmin,), {
        'readonly_fields': tuple(field.name for field in model._meta.fields),
        'list_display': tuple(field.name for field in model._meta.fields if field.name in
                              ('id', 'source', 'target', 'snapshot_version', 'coverage', 'kind', 'action', 'actor', 'created_at', 'revision')),
    }))
