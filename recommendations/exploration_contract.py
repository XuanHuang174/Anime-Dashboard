"""11A: independent, read-only exploration contracts; no ranking/profile writes."""
import re
import hashlib
import json
import unicodedata

from django.conf import settings
from rest_framework import serializers

from .engine import RecommendationUnavailable, RecommendationSnapshotChanged
from .normalization import clean_id
from .taxonomy import AVAILABLE_TAGS, TAXONOMY_VERSION, canonical_tag

CAPABILITIES_VERSION = 'recommendations.explore.capabilities.v1'
QUERY_VERSION = 'recommendations.explore.query.v1'
CATALOG_VERSION = 'recommendations.explore.catalog.v1'
EVIDENCE_VERSION = 'recommendations.explore.evidence.v1'
RESULT_VERSION = 'recommendations.explore.v1'
OPTIONS_VERSION = 'recommendations.explore.options.v1'
ROLLOUT_VERSION = 'recommendations.explore.rollout.v1'
MAX_SEARCH_LENGTH = 120
SEARCH_LIMIT = 15
MAX_TAGS = 5
POLICIES = {'similar': 'similarity.v1', 'tags': 'tag-explore.v1'}
NAVIGATION = (
    {'id': 'personal', 'label': '个性化推荐', 'path': '/recommendations', 'available': True},
    {'id': 'similar', 'label': '单番相似', 'path': '/recommendations/similar', 'available': False},
    {'id': 'tags', 'label': '按标签找', 'path': '/recommendations/tags', 'available': False},
)


def canary_users():
    users = getattr(settings, 'RECOMMENDATION_EXPLORATION_CANARY_USERS', [])
    if not isinstance(users, (list, tuple)) or any(not isinstance(name, str) or not name.strip() for name in users):
        return None  # Invalid rollout configuration never becomes a full release.
    return tuple(sorted(set(users)))


def enabled(user=None):
    users = canary_users()
    return (getattr(settings, 'RECOMMENDATION_EXPLORATION_ENABLED', False) is True and users is not None
            and (not users or user is not None and user.is_authenticated and user.is_active and user.get_username() in users))


class ExplorationDisabled(RecommendationUnavailable):
    default_detail = {'code': 'exploration_disabled', 'detail': '探索功能暂未开放。'}


class ExplorationSnapshotChanged(RecommendationSnapshotChanged):
    def __init__(self, detail='探索依据已变化，请重新探索。'):
        super().__init__({'code': 'exploration_snapshot_changed', 'detail': detail})


def require_enabled(user=None):
    if not enabled(user):
        raise ExplorationDisabled()


def capabilities(user=None):
    available = enabled(user)
    from .series_exploration import enabled as series_enabled, POLICY
    return {'contract_version': CAPABILITIES_VERSION, 'rollout_contract_version': ROLLOUT_VERSION, 'enabled': available,
            'catalog_search_enabled': available, 'query_execution_enabled': available,
            'supported_intents': ['similar', 'tags'],
            **({'series_exploration': {'enabled': True, 'policy_version': POLICY}} if available and series_enabled() else {}),
            'options_contract_version': OPTIONS_VERSION,
            'query_contract_version': QUERY_VERSION, 'catalog_contract_version': CATALOG_VERSION,
            'evidence_contract_version': EVIDENCE_VERSION, 'result_contract_version': RESULT_VERSION,
            'navigation': [{**row, 'available': available if row['id'] in ('similar', 'tags') else row['available']} for row in NAVIGATION]}


class StrictQuerySerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if (set(data) - set(self.fields) or any(value == '' for value in data.values())
                or hasattr(data, 'getlist') and any(len(data.getlist(key)) != 1 for key in data)):
            raise serializers.ValidationError({'non_field_errors': ['查询包含未知、重复或空参数。']})
        return super().to_internal_value(data)


class StrictObjectSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != set(self.fields):
            raise serializers.ValidationError({'non_field_errors': ['探索对象字段不完整或包含未知字段。']})
        return super().to_internal_value(data)


def search_term(raw):
    # No remote fetching. Only these complete subject URLs represent Bangumi IDs.
    value = unicodedata.normalize('NFKC', raw).strip()
    if not value or len(value) > MAX_SEARCH_LENGTH or any(unicodedata.category(c).startswith('C') for c in value):
        raise serializers.ValidationError('搜索内容为空或包含控制字符。')
    match = re.fullmatch(r'https?://(?:bgm\.tv|bangumi\.tv|chii\.in)/subject/([1-9][0-9]*)/?', value)
    if match:
        value = match[1]
    elif '://' in value:
        raise serializers.ValidationError('仅支持完整的 Bangumi subject 链接。')
    if value.isdecimal():
        try:
            return {'kind': 'bangumi_id', 'value': str(clean_id(value))}
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None
    if len(value) < 2:
        raise serializers.ValidationError('作品名称至少输入两个字符。')
    return {'kind': 'title', 'value': value}


class ExplorationSearchQuerySerializer(StrictQuerySerializer):
    q = serializers.CharField(max_length=MAX_SEARCH_LENGTH, trim_whitespace=False)

    def validate_q(self, value):
        return search_term(value)


class BangumiIDField(serializers.Field):
    def to_internal_value(self, value):
        try:
            return clean_id(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None

    def to_representation(self, value):
        return clean_id(value)


class TopicSetField(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(max_length=250, **kwargs)

    def to_internal_value(self, data):
        raw = super().to_internal_value(data)
        values = unicodedata.normalize('NFKC', raw).split(',')
        tags = [canonical_tag(value) for value in values]
        if not tags or any(tag is None for tag in tags):
            raise serializers.ValidationError('题材必须来自标准词表或已批准的别名。')
        unique = set(tags)
        if len(unique) > MAX_TAGS:
            raise serializers.ValidationError('每组最多选择 5 个题材。')
        return [tag for tag in AVAILABLE_TAGS if tag in unique]

    def to_representation(self, value):
        return list(value)


class ExplorationQuerySerializer(StrictQuerySerializer):
    """Initial-query schema for disjoint single-work and tag exploration."""
    intent = serializers.ChoiceField(choices=tuple(POLICIES))
    seed_bangumi_id = BangumiIDField(required=False)
    include_tags = TopicSetField(required=False)
    exclude_tags = TopicSetField(required=False)
    match = serializers.ChoiceField(choices=('all', 'any'), required=False)
    sort = serializers.ChoiceField(choices=('relevance', 'score'), required=False)
    type = serializers.ChoiceField(choices=('all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'), default='all')
    min_score = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, default=0)
    max_score = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, default=10)
    max_score_exclusive = serializers.BooleanField(default=False)
    year_from = serializers.IntegerField(min_value=1000, max_value=9999, required=False)
    year_to = serializers.IntegerField(min_value=1000, max_value=9999, required=False)
    hide_recorded = serializers.BooleanField(default=True)
    page_size = serializers.ChoiceField(choices=(12,), default=12)

    def validate(self, attrs):
        for key in ('year_from', 'year_to'):
            if key in self.initial_data and not re.fullmatch(r'[1-9][0-9]{3}', str(self.initial_data[key])):
                raise serializers.ValidationError({key: '年份必须是四位整数。'})
        if attrs.get('year_from', 1000) > attrs.get('year_to', 9999):
            raise serializers.ValidationError('年份起点不能大于终点。')
        if attrs['min_score'] > attrs['max_score'] or attrs['max_score_exclusive'] and (
                'max_score' not in self.initial_data or attrs['max_score'] >= 10
                or attrs['min_score'] >= attrs['max_score']):
            raise serializers.ValidationError('公共评分区间无效。')
        if attrs['intent'] == 'similar':
            if 'seed_bangumi_id' not in attrs or set(attrs) & {'include_tags', 'exclude_tags', 'match', 'sort'}:
                raise serializers.ValidationError('单番相似仅接受一个参照作品，不接受标签模式参数。')
        else:
            if 'seed_bangumi_id' in attrs or not attrs.get('include_tags'):
                raise serializers.ValidationError('按标签找至少包含一个题材，不能混入参照作品。')
            attrs.setdefault('exclude_tags', [])
            attrs.setdefault('match', 'all')
            attrs.setdefault('sort', 'relevance')
            if set(attrs['include_tags']) & set(attrs['exclude_tags']):
                raise serializers.ValidationError('同一题材不能同时包含与排除。')
        return attrs


class ExplorationCursorQuerySerializer(StrictQuerySerializer):
    cursor = serializers.CharField(max_length=8192, trim_whitespace=False)


class ExplorationDetailQuerySerializer(StrictQuerySerializer):
    detail_context = serializers.CharField(max_length=8192, trim_whitespace=False)
    evidence_token = serializers.RegexField(r'\A[a-f0-9]{64}\Z', required=False)


class ExplorationContextQuerySerializer(StrictQuerySerializer):
    snapshot_context = serializers.CharField(max_length=8192, trim_whitespace=False)


def query_fingerprint(validated_query):
    """Public normalized intent digest ONLY; not a user/scope authorization token."""
    canonical = ExplorationQuerySerializer(instance=validated_query).data
    return hashlib.sha256(json.dumps([QUERY_VERSION, canonical], sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class ExplorationEvidenceSourceSerializer(StrictObjectSerializer):
    bangumi_id = BangumiIDField()
    title = serializers.CharField(max_length=500)
    tags = serializers.ListField(child=serializers.ChoiceField(choices=AVAILABLE_TAGS), min_length=1, max_length=len(AVAILABLE_TAGS))


class ExplorationEvidenceContextSerializer(StrictObjectSerializer):
    query_fingerprint = serializers.RegexField(r'\A[a-f0-9]{64}\Z')
    catalog_feature_version = serializers.RegexField(r'\A[a-f0-9]{64}\Z')
    scope_revision = serializers.RegexField(r'\A[a-f0-9]{64}\Z')
    taxonomy_version = serializers.ChoiceField(choices=(TAXONOMY_VERSION,))
    ranking_policy_version = serializers.ChoiceField(choices=tuple(POLICIES.values()))


class ExplorationEvidenceSerializer(StrictObjectSerializer):
    """Validate provenance against the CALLER's query/candidate/current context.

    This is not a token issuer or authorization check. 11B/11D must first authenticate
    and load the scoped seed/candidate and sign its snapshot; no profile is used.
    """
    contract_version = serializers.ChoiceField(choices=(EVIDENCE_VERSION,))
    kind = serializers.ChoiceField(choices=('selected_work', 'selected_tags'))
    interpretation = serializers.ChoiceField(choices=('shared_genre_association',))
    is_probability = serializers.BooleanField()
    shared_tags = serializers.ListField(child=serializers.ChoiceField(choices=AVAILABLE_TAGS), min_length=1, max_length=len(AVAILABLE_TAGS))
    source = ExplorationEvidenceSourceSerializer(allow_null=True)
    versions = ExplorationEvidenceContextSerializer()

    def validate(self, attrs):
        query = self.context.get('query', {})
        versions = self.context.get('evidence_context')
        shared = attrs['shared_tags']
        candidate_tags = set(self.context.get('candidate_tags', ()))
        if (self.initial_data['is_probability'] is not False or attrs['versions'] != versions
                or type(self.context.get('candidate_id')) is not int or self.context['candidate_id'] <= 0
                or query.get('intent') not in POLICIES
                or attrs['versions']['ranking_policy_version'] != POLICIES[query['intent']]
                or len(set(shared)) != len(shared) or not set(shared) <= candidate_tags):
            raise serializers.ValidationError('探索依据与当前查询、题材或版本不一致。')
        source = attrs['source']
        if query['intent'] == 'similar':
            if (attrs['kind'] != 'selected_work' or not source
                    or source['bangumi_id'] != query.get('seed_bangumi_id')
                    or source['bangumi_id'] == self.context.get('candidate_id')
                    or source != self.context.get('selected_source')
                    or set(shared) != candidate_tags.intersection(source['tags'])):
                raise serializers.ValidationError('参照作品依据不一致。')
        else:
            included = set(query.get('include_tags', ()))
            if (attrs['kind'] != 'selected_tags' or source is not None
                    or set(shared) != candidate_tags.intersection(included)
                    or candidate_tags.intersection(query.get('exclude_tags', ()))
                    or query.get('match', 'all') == 'all' and not included <= candidate_tags):
                raise serializers.ValidationError('标签依据不满足本次匹配条件。')
        return attrs


def contract_descriptor(user=None):
    return {'contract_version': QUERY_VERSION, 'capabilities': capabilities(user),
            'taxonomy_version': TAXONOMY_VERSION, 'available_tags': list(AVAILABLE_TAGS),
            'search': {'contract_version': CATALOG_VERSION, 'limit': SEARCH_LIMIT,
                       'max_query_length': MAX_SEARCH_LENGTH, 'identity_namespace': 'bangumi'},
            'options': {'contract_version': OPTIONS_VERSION,
                        'count_definition': 'scoped_catalog_without_result_filters',
                        'result_filters_applied': False, 'requires_profile': False},
            'query': {'intents': dict(POLICIES), 'maximum_include_tags': MAX_TAGS,
                      'maximum_exclude_tags': MAX_TAGS, 'default_match': 'all',
                      'matches': ['all', 'any'], 'sorts': ['relevance', 'score'],
                      'fields': list(ExplorationQuerySerializer().fields),
                      'requires_profile': False, 'saves_preferences': False},
            'evidence': {'contract_version': EVIDENCE_VERSION,
                         'kinds': {'similar': 'selected_work', 'tags': 'selected_tags'},
                         'interpretation': 'shared_genre_association', 'is_probability': False,
                         'fields': list(ExplorationEvidenceSerializer().fields),
                         'source_fields': {'selected_work': ['bangumi_id', 'title', 'tags'],
                                           'selected_tags': []},
                         'required_context': ['query_fingerprint', 'catalog_feature_version',
                                              'scope_revision', 'taxonomy_version', 'ranking_policy_version'],
                         'private_score_source': False},
            'limits': {'catalog_rows': 50000, 'results': 120, 'batch_size': 24,
                       'page_size': 12, 'snapshot_ttl_seconds': 900}}
