"""Single-work signed confirmation only; never bulk-approves or renders covers."""
from uuid import uuid4

from django import forms
from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import APIException

from .content_review import can_review, reviewer_contract, mutate_review
from .eligibility_admin import _error_text
from .models import RecommendationContentReview as Review, RecommendationContentReviewEvent as Event

SALT='recommendations.content-review.admin.v1'


class ReviewForm(forms.Form):
    ticket=forms.CharField(widget=forms.HiddenInput,max_length=4096)
    action=forms.ChoiceField(label='单作品操作',choices=(('prepare','建立待审核'),('approve','审核通过（不自动开放推荐）'),
        ('reject','拒绝'),('revoke','撤销审核')))
    reason=forms.CharField(label='审核原因',max_length=240,help_text='仅审核记录；不要填写用户身份材料或私人手账。')
    evidence_reference=forms.RegexField(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$',max_length=128,required=False,
        label='内部依据编号',help_text='审核通过时必填。仅编号，不上传材料、不填外部链接；系统不会自动获取或核验材料。')
    days=forms.IntegerField(label='通过结论有效天数',min_value=1,max_value=30,initial=30)
    exclusions_confirmed=forms.BooleanField(required=False,label='已人工排除未成年人或年龄不明人物的性内容等禁止情形',
        help_text='审核通过时必选。此项是审核责任确认，不是自动内容识别或合法性证明。')
    confirmed=forms.BooleanField(label='已核对完整名称、季度、类型及当前资料，确认仅提交本作品的审核操作')

    def clean(self):
        values=super().clean()
        if values.get('action')=='approve':
            for key in ('evidence_reference','exclusions_confirmed'):
                if not values.get(key): self.add_error(key,'审核通过必须填写/明确确认。')
        return values


class ReadOnlyReviewAdmin(admin.ModelAdmin):
    actions=None

    def has_view_permission(self,request,obj=None): return can_review(request.user)
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False


@admin.register(Review)
class ContentReviewAdmin(ReadOnlyReviewAdmin):
    list_display=('anime','status','review_version','expires_at','updated_at','control_link')
    list_filter=('status',)
    search_fields=('=anime__bangumi_id',)
    readonly_fields=tuple(field.name for field in Review._meta.fields)
    change_list_template='admin/recommendations/content_review_change_list.html'

    def get_queryset(self,request): return super().get_queryset(request).select_related('anime')

    @admin.display(description='单作品核对')
    def control_link(self,obj):
        return format_html('<a href="{}?bangumi_id={}">读取与审核</a>',reverse('admin:recommendations_content_review_control'),obj.anime.bangumi_id)

    def changelist_view(self,request,extra_context=None):
        return super().changelist_view(request,{**(extra_context or {}),
            'review_control_url':reverse('admin:recommendations_content_review_control')})

    def get_urls(self):
        return [path('control/',self.admin_site.admin_view(self.control_view),
                     name='recommendations_content_review_control')]+super().get_urls()

    @method_decorator(never_cache)
    def control_view(self,request):
        if not can_review(request.user): raise PermissionDenied
        anime=decision=form=error=None
        try:
            if request.method=='POST':
                form=ReviewForm(request.POST)
                ticket=request.POST.get('ticket','')
                if len(ticket)>4096: raise signing.BadSignature()
                body=signing.loads(ticket,salt=SALT,max_age=900)
                if (not isinstance(body,dict) or set(body)!={'actor','work','version','catalog','policy','operation'}
                        or body['actor']!=request.user.pk or type(body['work']) is not int):
                    raise signing.BadSignature()
                anime,decision=reviewer_contract(request.user,body['work'])
                if form.is_valid():
                    data=form.cleaned_data
                    changes={'expected_version':body['version'],'expected_catalog_revision':body['catalog'],
                        'expected_policy_version':body['policy'],'operation_id':body['operation'],
                        'reason':data['reason'],'days':data['days'],'exclusions_confirmed':data['exclusions_confirmed']}
                    if data['evidence_reference']: changes['evidence_reference']=data['evidence_reference']
                    mutate_review(actor=request.user,bangumi_id=body['work'],action=data['action'],changes=changes)
                    self.message_user(request,'审核操作已确认；展示仍受总开关、用户授权和个人筛选约束。',messages.SUCCESS)
                    return HttpResponseRedirect(reverse('admin:recommendations_content_review_control')+f'?bangumi_id={anime.bangumi_id}')
            else:
                raw=request.GET.get('bangumi_id','')
                if raw:
                    if not raw.isascii() or not raw.isdecimal() or len(raw)>10: raise ValueError()
                    anime,decision=reviewer_contract(request.user,int(raw))
                    ticket=signing.dumps({'actor':request.user.pk,'work':anime.bangumi_id,
                        'version':decision['review_version'],'catalog':decision['catalog_revision'],
                        'policy':decision['policy_version'],'operation':str(uuid4())},salt=SALT)
                    form=ReviewForm(initial={'ticket':ticket})
        except (signing.BadSignature,ValueError): error='作品编号或审核凭据无效/过期，请重新读取。'
        except APIException as exc: error=_error_text(exc.detail)
        except DatabaseError: error='审核数据库尚未就绪，请先完成迁移。'
        return TemplateResponse(request,'admin/recommendations/content_review_control.html',{
            **self.admin_site.each_context(request),'opts':self.model._meta,'title':'推荐作品审核（独立放行台账）',
            'anime':anime,'decision':decision,'form':form,'error':error,
            'reload_url':reverse('admin:recommendations_content_review_control')})


@admin.register(Event)
class ContentReviewEventAdmin(ReadOnlyReviewAdmin):
    list_display=('review','actor','action','previous_version','applied_version','applied_release_version','created_at')
    readonly_fields=tuple(field.name for field in Event._meta.fields)
    list_filter=('action',)

    def get_queryset(self,request): return super().get_queryset(request).select_related('review','actor')
