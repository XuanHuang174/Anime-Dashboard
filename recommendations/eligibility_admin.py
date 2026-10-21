"""Explicit single-account admin confirmation; no bulk grant or editable rows."""
from uuid import uuid4

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from rest_framework.exceptions import APIException

from .eligibility import can_manage, mutate_eligibility, own_eligibility
from .models import RecommendationContentEligibility as Eligibility, RecommendationEligibilityEvent as Event

ADMIN_SALT = 'recommendations.eligibility-admin.manual.m1.v1'


def _error_text(detail):
    if isinstance(detail, dict):
        if 'detail' in detail:
            return _error_text(detail['detail'])
        return '；'.join(_error_text(value) for key, value in detail.items() if key not in ('code', 'reload_required'))
    if isinstance(detail, (list, tuple)):
        return '；'.join(_error_text(value) for value in detail)
    return str(detail)


class EligibilityAdminForm(forms.Form):
    ticket = forms.CharField(widget=forms.HiddenInput, max_length=4096)
    action = forms.ChoiceField(label='操作', choices=(('approve', '批准待审批申请'),
        ('reject', '拒绝待审批申请'), ('revoke', '撤销访问授权')))
    days = forms.IntegerField(label='批准有效天数', min_value=1, max_value=30, initial=30,
                              help_text='默认 30 天，最多 30 天；不自动续期。')
    reason = forms.CharField(label='内部审批原因', max_length=200,
                            help_text='仅内部审计，不向申请人显示；不要填写证件或私人手账材料。')
    public_message = forms.CharField(label='给申请人的简短拒绝提示', max_length=200, required=False,
        help_text='仅拒绝时显示给本人；留空使用统一提示，不会公开内部审批原因。')
    confirmed = forms.BooleanField(label='我已核对目标账号、操作及期限，确认执行', required=True)


@admin.register(Eligibility)
class ContentEligibilityAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'authorization_source', 'requested_at', 'eligibility_version', 'expires_at', 'updated_at', 'control_link')
    list_filter = ('status', 'authorization_source')
    ordering = ('-requested_at', '-pk')
    search_fields = ('=user__id', 'user__username')
    readonly_fields = tuple(field.name for field in Eligibility._meta.fields)
    actions = None
    change_list_template = 'admin/recommendations/eligibility_change_list.html'

    def has_view_permission(self, request, obj=None):
        return can_manage(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'granted_by')

    @admin.display(description='安全操作')
    def control_link(self, obj):
        return format_html('<a href="{}?user_id={}">核对与管理</a>', reverse('admin:recommendations_eligibility_control'), obj.user_id)

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {**(extra_context or {}),
            'can_control_eligibility': can_manage(request.user),
            'eligibility_control_url': reverse('admin:recommendations_eligibility_control')})

    def get_urls(self):
        return [path('control/', self.admin_site.admin_view(self.control_view),
                     name='recommendations_eligibility_control')] + super().get_urls()

    @method_decorator(never_cache)
    def control_view(self, request):
        if not can_manage(request.user):
            raise PermissionDenied
        subject, status, form, error = None, None, None, None
        # Signed scope binds actor, subject, version and UUID. Never trust hidden IDs.
        try:
            if request.method == 'POST':
                form = EligibilityAdminForm(request.POST)
                ticket = request.POST.get('ticket', '')
                if len(ticket) > 4096:
                    raise signing.BadSignature()
                body = signing.loads(ticket, salt=ADMIN_SALT, max_age=900)
                if (not isinstance(body, dict) or set(body) != {'actor', 'subject', 'version', 'operation'}
                        or body['actor'] != request.user.pk or type(body['subject']) is not int or body['subject'] <= 0):
                    raise signing.BadSignature()
                subject = get_user_model().objects.get(pk=body['subject'])
                status = own_eligibility(subject)
                if form.is_valid():
                    result = mutate_eligibility(actor=request.user, subject_id=subject.pk, action=form.cleaned_data['action'], changes={
                        'expected_version': body['version'], 'operation_id': body['operation'],
                        'reason': form.cleaned_data['reason'], 'days': form.cleaned_data['days'],
                        'public_message': form.cleaned_data['public_message']})
                    message = ('该操作已处理，未重复执行；请核对当前最新资格状态。' if result['replayed'] else
                               '访问申请已更新。人工审批不等于年龄核验；仍须总开关开放及用户主动选择。' if result['changed'] else
                               '资格状态未变化，操作回执已记录；不会自动开启个人 NSFW 筛选。')
                    self.message_user(request, message, messages.SUCCESS)
                    return HttpResponseRedirect(reverse('admin:recommendations_eligibility_control') + f'?user_id={subject.pk}')
            else:
                raw = request.GET.get('user_id', '')
                if raw:
                    if not raw.isascii() or not raw.isdecimal() or len(raw) > 18:
                        raise ValueError()
                    subject = get_user_model().objects.get(pk=int(raw))
                    status = own_eligibility(subject)
                    ticket = signing.dumps({'actor': request.user.pk, 'subject': subject.pk,
                        'version': status['eligibility_version'], 'operation': str(uuid4())}, salt=ADMIN_SALT)
                    form = EligibilityAdminForm(initial={'ticket': ticket})
        except (signing.BadSignature, get_user_model().DoesNotExist, ValueError):
            error = '账号或操作凭据无效/已过期，请重新选择账号。'
        except APIException as exc:
            error = _error_text(exc.detail)
        except DatabaseError:
            error = '资格管理数据库尚未就绪，请完成迁移后重试。普通推荐不受影响。'
        return TemplateResponse(request, 'admin/recommendations/eligibility_control.html', {
            **self.admin_site.each_context(request), 'opts': self.model._meta, 'title': '受限内容访问申请审批',
            'subject': subject, 'qualification': status, 'form': form, 'error': error,
            'reload_url': reverse('admin:recommendations_eligibility_control')})


@admin.register(Event)
class ContentEligibilityEventAdmin(admin.ModelAdmin):
    list_display = ('eligibility', 'actor', 'action', 'previous_version', 'applied_version', 'changed', 'created_at')
    list_filter = ('action',)
    readonly_fields = tuple(field.name for field in Event._meta.fields)
    actions = None

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('eligibility', 'actor')

    def has_view_permission(self, request, obj=None):
        return can_manage(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
