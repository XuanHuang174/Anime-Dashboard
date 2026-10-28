"""13C strict read contracts and independent, default-closed rollout."""
import re
from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import APIException

KINDS = ('voice_actor', 'studio')
TTL = 900
PAGE_SIZE = 12
SORT_VERSION = 'credits.catalog-period.v1'


class CreditsUnavailable(APIException):
    status_code = 503

    def __init__(self, detail='人物与制作资料暂不可用。', code='credits_data_unavailable'):
        super().__init__({'code': code, 'detail': detail})


class CreditsChanged(APIException):
    status_code = 409
    default_detail = {'code': 'credits_snapshot_changed', 'detail': '资料或浏览条件已变化，请重新读取。'}


def capabilities(user):
    users = getattr(settings, 'RECOMMENDATION_CREDITS_CANARY_USERS', [])
    valid = isinstance(users, (list, tuple)) and all(isinstance(v, str) and v and v == v.strip() for v in users)
    allowed = valid and user is not None and user.is_authenticated and user.is_active and (
        not users or user.get_username() in users)
    return {'contract_version': 'recommendations.credits.capabilities.v1', **{
        kind: bool(allowed and getattr(settings, 'RECOMMENDATION_CREDITS_' + kind.upper() + '_ENABLED', False) is True)
        for kind in KINDS}}


def require_access(user, kind=None):
    flags = capabilities(user)
    if not (flags.get(kind) if kind else any(flags[k] for k in KINDS)):
        raise CreditsUnavailable('人物与制作功能尚未对当前账号开放。', 'credits_disabled')


def rollout_revision(user):
    from .evidence import _token
    return _token('recommendations.credits.rollout.v1', [capabilities(user),
        getattr(settings, 'RECOMMENDATION_CREDITS_CANARY_USERS', []),
        str(getattr(settings, 'RECOMMENDATION_CREDITS_EPOCH', '1'))])


class StrictQuery(serializers.Serializer):
    def to_internal_value(self, data):
        if (set(data) - set(self.fields) or any(v == '' for v in data.values())
                or hasattr(data, 'getlist') and any(len(data.getlist(k)) != 1 for k in data)):
            raise serializers.ValidationError({'non_field_errors': ['查询参数未知、重复或为空。']})
        for key in ('hide_recorded', 'max_score_exclusive'):
            if key in data and data[key] not in ('true', 'false', True, False):
                raise serializers.ValidationError({key: '仅接受 true 或 false。'})
        for key in ('year_from', 'year_to'):
            if key in data and not re.fullmatch(r'[1-9]\d{3}', str(data[key])):
                raise serializers.ValidationError({key: '年份必须为四位整数。'})
        return super().to_internal_value(data)


class SearchQuery(StrictQuery):
    kind = serializers.ChoiceField(choices=KINDS)
    q = serializers.CharField(max_length=200, trim_whitespace=False)


class SummaryQuery(StrictQuery):
    expanded = serializers.ChoiceField(choices=('true', 'false'), default='false')


class ActionQuery(StrictQuery):
    context = serializers.CharField(max_length=4096, trim_whitespace=False)


class WorksQuery(StrictQuery):
    view = serializers.ChoiceField(choices=('all', 'completed'), default='all')
    sort = serializers.ChoiceField(choices=('score_desc', 'release_desc', 'release_asc'), default='score_desc')
    type = serializers.ChoiceField(choices=('all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'), default='all')
    year_from = serializers.IntegerField(min_value=1000, max_value=9999, default=None)
    year_to = serializers.IntegerField(min_value=1000, max_value=9999, default=None)
    min_score = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, default=0)
    max_score = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, default=10)
    max_score_exclusive = serializers.BooleanField(default=False)
    hide_recorded = serializers.BooleanField(default=True)
    cursor = serializers.CharField(max_length=4096, required=False, trim_whitespace=False)

    def validate(self, attrs):
        if attrs['view'] == 'completed' and attrs['hide_recorded']:
            raise serializers.ValidationError('我看过的视图必须显式设置 hide_recorded=false。')
        if attrs['min_score'] > attrs['max_score']:
            raise serializers.ValidationError('评分范围无效。')
        if attrs['max_score_exclusive'] and (attrs['max_score'] >= 10 or attrs['min_score'] >= attrs['max_score']):
            raise serializers.ValidationError('严格评分上限必须小于 10 且高于下限。')
        if attrs['year_from'] and attrs['year_to'] and attrs['year_from'] > attrs['year_to']:
            raise serializers.ValidationError('年份范围无效。')
        return attrs


def conditions(query):
    return {k: float(v) if k in ('min_score', 'max_score') else v for k, v in query.items() if k != 'cursor'}
