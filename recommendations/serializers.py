from collections.abc import Mapping
import re

from rest_framework import serializers

from .identity import public_cover_url, require_safe_anime
from .features import feature_tags
from .models import RecommendationAnime
from .taxonomy import AVAILABLE_TAGS, MAX_PREFERENCE_TAGS, canonical_tag, canonical_tags
from .profile_policy import PROFILE_SCORE_OPTIONS


class ProfileScoreField(serializers.DecimalField):
    def __init__(self, **kwargs):
        super().__init__(max_digits=3, decimal_places=1, min_value=6, max_value=10, **kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value not in PROFILE_SCORE_OPTIONS:
            raise serializers.ValidationError('画像取样门槛步长必须为 0.5。')
        return value


class ProfilePreviewQuerySerializer(serializers.Serializer):
    profile_min_score = ProfileScoreField()

    def to_internal_value(self, data):
        if set(data) - set(self.fields) or (hasattr(data, 'getlist') and
                any(len(data.getlist(key)) != 1 for key in data)):
            raise serializers.ValidationError({'non_field_errors': ['预览参数未知或重复；只能预览当前登录用户。']})
        if any(value == '' for value in data.values()):
            raise serializers.ValidationError({'profile_min_score': ['预览门槛不能为空。']})
        return super().to_internal_value(data)


class PreferenceTagList(serializers.ListField):
    child = serializers.CharField(max_length=50, allow_blank=False)

    def __init__(self, **kwargs):
        super().__init__(max_length=MAX_PREFERENCE_TAGS, **kwargs)

    def to_internal_value(self, data):
        if isinstance(data, list) and len(data) > MAX_PREFERENCE_TAGS:
            raise serializers.ValidationError('每次最多提交 20 个题材标签。')
        values = super().to_internal_value(data)
        if any(canonical_tag(tag) is None for tag in values):
            raise serializers.ValidationError('包含不支持的题材；请使用 available_tags 中的标签或已定义别名。')
        return canonical_tags(values)


class RecommendationPreferencePatchSerializer(serializers.Serializer):
    tags = PreferenceTagList(required=False)
    add_tags = PreferenceTagList(required=False)
    remove_tags = PreferenceTagList(required=False)
    expected_version = serializers.IntegerField(min_value=0, max_value=2_147_483_647, required=False)
    profile_min_score = ProfileScoreField(required=False)

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and set(data) - set(self.fields):
            # Do not accept forged owners, allow_nsfw switches or hidden fields.
            raise serializers.ValidationError({'non_field_errors': ['请求包含不支持的字段；所属用户只能由登录身份确定。']})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if not {'tags', 'add_tags', 'remove_tags', 'profile_min_score'}.intersection(attrs):
            raise serializers.ValidationError('请提交题材或画像取样门槛。')
        if 'profile_min_score' in attrs and 'expected_version' not in attrs:
            raise serializers.ValidationError({'expected_version': '修改画像门槛必须携带当前偏好版本。'})
        if 'tags' in attrs and {'add_tags', 'remove_tags'}.intersection(attrs):
            raise serializers.ValidationError('tags 整体设置不能与增量操作混用。')
        if set(attrs.get('add_tags', [])) & set(attrs.get('remove_tags', [])):
            raise serializers.ValidationError('同一标签不能同时添加和移除。')
        return attrs


class ProfileScoreControlSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(read_only=True)
    default = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    minimum = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    maximum = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    step = serializers.DecimalField(max_digits=2, decimal_places=1, read_only=True)
    choices = serializers.ListField(child=serializers.DecimalField(max_digits=3, decimal_places=1), read_only=True)


class RecommendationPreferenceSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    taxonomy_version = serializers.CharField(read_only=True)
    tags = serializers.ListField(child=serializers.CharField(), read_only=True)
    version = serializers.IntegerField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)
    available_tags = serializers.ListField(child=serializers.CharField(), read_only=True)
    profile_min_score = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    effective_profile_min_score = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    profile_policy_version = serializers.CharField(read_only=True)
    profile_min_score_control = ProfileScoreControlSerializer(read_only=True)
    available_tag_counts = serializers.DictField(child=serializers.IntegerField(min_value=0), read_only=True)
    option_order_version = serializers.CharField(read_only=True)
    option_order_status = serializers.CharField(read_only=True)
    option_catalog_revision = serializers.CharField(read_only=True)


class ProfileTagSerializer(serializers.Serializer):
    tag = serializers.CharField(read_only=True)
    sample_count = serializers.IntegerField(read_only=True)


class ProfilePolicySerializer(serializers.Serializer):
    profile_policy_version = serializers.CharField(read_only=True)
    watch_status = serializers.CharField(read_only=True)
    minimum_score = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    configured_minimum_score = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    customization_enabled = serializers.BooleanField(read_only=True)
    source = serializers.CharField(read_only=True)
    minimum_samples = serializers.IntegerField(read_only=True)
    requires_safe_bangumi_identity = serializers.BooleanField(read_only=True)
    requires_usable_tags = serializers.BooleanField(read_only=True)


class FeatureSourceCountsSerializer(serializers.Serializer):
    private_base_tags = serializers.IntegerField(min_value=0, read_only=True)
    shared_base_tags = serializers.IntegerField(min_value=0, read_only=True)
    custom_tags = serializers.IntegerField(min_value=0, read_only=True)


class ExtractedGenreSerializer(ProfileTagSerializer):
    source_work_counts = FeatureSourceCountsSerializer(read_only=True)


class TrustedProfileSerializer(serializers.Serializer):
    eligible_work_count = serializers.IntegerField(min_value=0, read_only=True)
    usable_work_count = serializers.IntegerField(min_value=0, read_only=True)
    ready = serializers.BooleanField(read_only=True)
    tag_summary = ExtractedGenreSerializer(many=True, read_only=True)


class CommonTopicsSerializer(serializers.Serializer):
    work_count = serializers.IntegerField(min_value=0, read_only=True)
    feature_work_count = serializers.IntegerField(min_value=0, read_only=True)
    requires_confirmation = serializers.BooleanField(read_only=True)
    used_for_recommendation = serializers.BooleanField(read_only=True)
    tag_summary = ExtractedGenreSerializer(many=True, read_only=True)


class ProfileRecordStatsSerializer(serializers.Serializer):
    journal_record_count = serializers.IntegerField(min_value=0, read_only=True)
    rated_record_count = serializers.IntegerField(min_value=0, read_only=True)
    unrated_record_count = serializers.IntegerField(min_value=0, read_only=True)
    completed_record_count = serializers.IntegerField(min_value=0, read_only=True)
    safe_linked_work_count = serializers.IntegerField(min_value=0, read_only=True)


class IgnoredTagCountsSerializer(serializers.Serializer):
    invalid = serializers.IntegerField(min_value=0, read_only=True)
    year = serializers.IntegerField(min_value=0, read_only=True)
    country = serializers.IntegerField(min_value=0, read_only=True)
    management = serializers.IntegerField(min_value=0, read_only=True)
    work_title = serializers.IntegerField(min_value=0, read_only=True)
    unknown = serializers.IntegerField(min_value=0, read_only=True)


class PreferencePolicySerializer(serializers.Serializer):
    maximum_tags = serializers.IntegerField(read_only=True)
    suggested_initial_min = serializers.IntegerField(read_only=True)
    suggested_initial_max = serializers.IntegerField(read_only=True)
    maximum_observed_topics = serializers.IntegerField(read_only=True)
    explicit_preferences_are_hard_filters = serializers.BooleanField(read_only=True)
    common_topics_require_confirmation = serializers.BooleanField(read_only=True)


class ProfileExtractionSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    rule_version = serializers.CharField(read_only=True)
    taxonomy_version = serializers.CharField(read_only=True)
    explicit_preferences = RecommendationPreferenceSerializer(read_only=True)
    preference_policy = PreferencePolicySerializer(read_only=True)
    trusted_profile = TrustedProfileSerializer(read_only=True)
    common_topics = CommonTopicsSerializer(read_only=True)
    record_stats = ProfileRecordStatsSerializer(read_only=True)
    ignored_tag_counts = IgnoredTagCountsSerializer(read_only=True)


class ColdStartStateSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    code = serializers.CharField(read_only=True)
    reasons = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_cold_start = serializers.BooleanField(read_only=True)
    recommendation_basis = serializers.CharField(read_only=True)
    minimum_samples = serializers.IntegerField(min_value=1, read_only=True)
    explicit_preference_count = serializers.IntegerField(min_value=0, read_only=True)
    journal_record_count = serializers.IntegerField(min_value=0, read_only=True)
    rated_record_count = serializers.IntegerField(min_value=0, read_only=True)
    unrated_record_count = serializers.IntegerField(min_value=0, read_only=True)
    high_score_record_count = serializers.IntegerField(min_value=0, read_only=True)
    eligible_sample_count = serializers.IntegerField(min_value=0, read_only=True)
    usable_sample_count = serializers.IntegerField(min_value=0, read_only=True)
    pending_identity_count = serializers.IntegerField(min_value=0, read_only=True)
    missing_genre_count = serializers.IntegerField(min_value=0, read_only=True)
    unavailable_sample_count = serializers.IntegerField(min_value=0, read_only=True)


class RecommendationProfileSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    taxonomy_version = serializers.CharField(read_only=True)
    eligible_sample_count = serializers.IntegerField(read_only=True)
    usable_sample_count = serializers.IntegerField(min_value=0, read_only=True)
    cold_start = serializers.BooleanField(read_only=True)
    cold_start_state = ColdStartStateSerializer(read_only=True)
    has_usable_tags = serializers.BooleanField(read_only=True)
    readiness = serializers.CharField(read_only=True)
    tag_summary = ProfileTagSerializer(many=True, read_only=True)
    sample_policy = ProfilePolicySerializer(read_only=True)
    diagnostics = serializers.DictField(child=serializers.IntegerField(min_value=0), read_only=True)
    identity_service_ready = serializers.BooleanField(read_only=True)
    extraction = ProfileExtractionSerializer(read_only=True)


class ProfilePreviewSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    is_preview = serializers.BooleanField(read_only=True)
    saved = serializers.BooleanField(read_only=True)
    profile_min_score = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    preference_version = serializers.IntegerField(read_only=True)
    sample_policy = ProfilePolicySerializer(read_only=True)
    eligible_sample_count = serializers.IntegerField(read_only=True)
    usable_sample_count = serializers.IntegerField(read_only=True)
    cold_start = serializers.BooleanField(read_only=True)
    cold_start_state = ColdStartStateSerializer(read_only=True)
    readiness = serializers.CharField(read_only=True)
    identity_service_ready = serializers.BooleanField(read_only=True)
    diagnostics = serializers.DictField(child=serializers.IntegerField(min_value=0), read_only=True)


class RecommendationIdentitySerializer(serializers.Serializer):
    bangumi_id = serializers.IntegerField(read_only=True)
    anime_id = serializers.IntegerField(read_only=True, allow_null=True)
    record_id = serializers.IntegerField(read_only=True, allow_null=True)
    watch_status = serializers.CharField(read_only=True, allow_null=True)
    mapping_status = serializers.CharField(read_only=True)
    requires_identity_confirmation = serializers.BooleanField(read_only=True)
    journal_compatible = serializers.BooleanField(read_only=True)
    field_conflicts = serializers.ListField(child=serializers.CharField(), read_only=True)


class RecommendationPublicAnimeSerializer(serializers.ModelSerializer):
    """Future 3B detail contract; no raw tags, external images or source URL."""
    tags = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()

    class Meta:
        model = RecommendationAnime
        fields = ('bangumi_id', 'title_zh', 'title_jp', 'score', 'tags', 'cover_url',
                  'season', 'status_type', 'synopsis')
        read_only_fields = fields

    def to_representation(self, instance):
        require_safe_anime(instance)
        return super().to_representation(instance)

    def get_cover_url(self, instance):
        return public_cover_url(instance)

    def get_tags(self, instance):
        tags = feature_tags(instance.tags, (instance.title_zh, instance.title_jp))
        return [tag for tag in AVAILABLE_TAGS if tag in tags]


class ShowcasePublicationSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=('published', 'already_public', 'review_required'), read_only=True)
    code = serializers.CharField(read_only=True, required=False)
    detail = serializers.CharField(read_only=True)


class RecommendationWantSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    bangumi_id = serializers.IntegerField(read_only=True)
    anime_id = serializers.IntegerField(read_only=True)
    record_id = serializers.IntegerField(read_only=True)
    watch_status = serializers.CharField(read_only=True)
    created = serializers.BooleanField(read_only=True)
    outcome = serializers.CharField(read_only=True)
    content_rating = serializers.CharField(read_only=True)
    showcase_publication = ShowcasePublicationSerializer(read_only=True, required=False)


class RecommendationQuerySerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=('all', 'familiar', 'explore'), default='all')
    type = serializers.ChoiceField(choices=('all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'), default='all')
    min_score = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, default=0)
    max_score = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, default=10)
    hide_recorded = serializers.BooleanField(default=True)
    page = serializers.IntegerField(min_value=1, max_value=1000, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=24, default=12)
    state_token = serializers.RegexField(regex=r'^[a-f0-9]{64}$', required=False)
    # Opt-in contract; an old client without these controls keeps legacy paging.
    browse = serializers.BooleanField(default=False)
    style = serializers.ChoiceField(choices=('balanced', 'close', 'open'), default='balanced')
    year_from = serializers.IntegerField(min_value=1000, max_value=9999, default=None, allow_null=False)
    year_to = serializers.IntegerField(min_value=1000, max_value=9999, default=None, allow_null=False)
    cursor = serializers.CharField(max_length=8192, required=False, trim_whitespace=False)

    def to_internal_value(self, data):
        if set(data) - set(self.fields) or any(len(data.getlist(key)) > 1 for key in data if hasattr(data, 'getlist')):
            raise serializers.ValidationError({'non_field_errors': ['查询包含未定义或重复的参数。']})
        # HTML/query dicts otherwise treat an explicit empty input as a missing
        # field and silently apply its default. A supplied invalid value is 400.
        if any(value == '' for value in data.values()):
            raise serializers.ValidationError({'non_field_errors': ['查询参数不能是空字符串；不限条件请省略参数。']})
        for key in ('year_from', 'year_to'):
            if key in data and not re.fullmatch(r'[1-9]\d{3}', str(data[key])):
                raise serializers.ValidationError({key: '年份必须是四位整数。'})
        return super().to_internal_value(data)

    def validate(self, attrs):
        from .browsing import browse_requested
        if attrs['min_score'] > attrs['max_score']:
            raise serializers.ValidationError('最低 Bangumi 评分不能高于最高评分。')
        if browse_requested(attrs, self.initial_data):
            if 'browse' in self.initial_data and attrs['browse'] is False:
                raise serializers.ValidationError('不能同时关闭浏览控制并提供浏览参数。')
            if attrs['page'] != 1 or 'state_token' in attrs:
                raise serializers.ValidationError('批次浏览只接受服务端游标，不能混用旧分页参数。')
            if 'cursor' in attrs and 'page' in self.initial_data:
                raise serializers.ValidationError('游标已包含页位置，不接受额外页码。')
            if (attrs['year_from'] is not None and attrs['year_to'] is not None
                    and attrs['year_from'] > attrs['year_to']):
                raise serializers.ValidationError('年份起点不能大于终点。')
            return attrs
        if attrs['page'] > 1 and 'state_token' not in attrs:
            raise serializers.ValidationError('加载下一页必须携带第一页的 state_token。')
        return attrs


class RecommendationListAnimeSerializer(RecommendationPublicAnimeSerializer):
    class Meta(RecommendationPublicAnimeSerializer.Meta):
        fields = tuple(field for field in RecommendationPublicAnimeSerializer.Meta.fields if field != 'synopsis')
        read_only_fields = fields


class EvidenceVersionsSerializer(serializers.Serializer):
    profile_policy_version = serializers.CharField(read_only=True)
    profile_min_score = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    recipe_revision = serializers.CharField(read_only=True)
    recall_policy_version = serializers.CharField(read_only=True)
    runtime_version = serializers.CharField(read_only=True)
    control_policy_version = serializers.CharField(read_only=True)
    browse_context_version = serializers.CharField(read_only=True)
    algorithm_version = serializers.CharField(read_only=True)
    ranking_policy_version = serializers.CharField(read_only=True)
    taxonomy_version = serializers.CharField(read_only=True)
    feature_version = serializers.CharField(read_only=True)
    feature_rule_version = serializers.CharField(read_only=True)
    selection_rule_version = serializers.CharField(read_only=True)
    profile_version = serializers.CharField(read_only=True)
    preference_version = serializers.IntegerField(read_only=True)
    feedback_version = serializers.IntegerField(read_only=True)
    catalog_feature_version = serializers.CharField(read_only=True)


class ExecutionRecipeSerializer(serializers.Serializer):
    """Prepared execution contract; D2 responses only, production stays V1."""
    contract_version = serializers.CharField(read_only=True)
    algorithm_version = serializers.CharField(read_only=True)
    ranking_policy_version = serializers.CharField(read_only=True)
    recall_policy_version = serializers.CharField(read_only=True)
    runtime_version = serializers.CharField(read_only=True)
    control_policy_version = serializers.CharField(read_only=True)
    control_policy_sha256 = serializers.CharField(read_only=True)
    recipe_revision = serializers.CharField(read_only=True)
    candidate_limit = serializers.IntegerField(read_only=True)
    result_limit = serializers.IntegerField(read_only=True)
    batch_size = serializers.IntegerField(read_only=True)
    default_page_size = serializers.IntegerField(read_only=True)
    serving_enabled = serializers.BooleanField(read_only=True)


class StrictFeedbackInteger(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail('invalid')
        return super().to_internal_value(data)


class FeedbackMutationSerializer(serializers.Serializer):
    bangumi_id = StrictFeedbackInteger(min_value=1, max_value=2147483647)
    expected_feedback_version = StrictFeedbackInteger(min_value=0, max_value=2147483647)
    operation_id = serializers.UUIDField()

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and set(data) - set(self.fields):
            raise serializers.ValidationError({'non_field_errors': ['包含不支持的字段；只允许修改当前用户的作品反馈。']})
        return super().to_internal_value(data)


class FeedbackUndoSerializer(FeedbackMutationSerializer):
    target_feedback_version = StrictFeedbackInteger(min_value=1, max_value=2147483647)


class FeedbackHiddenQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, max_value=1000000, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=24, default=12)
    feedback_version = serializers.IntegerField(min_value=0, max_value=2147483647, required=False)

    def to_internal_value(self, data):
        if set(data) - set(self.fields) or (hasattr(data, 'getlist') and
                any(len(data.getlist(key)) != 1 for key in data)):
            raise serializers.ValidationError({'non_field_errors': ['查询参数未知或重复。']})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if attrs['page'] > 1 and 'feedback_version' not in attrs:
            raise serializers.ValidationError('后续分页必须携带第一页的 feedback_version。')
        return attrs


class FeedbackWorkSerializer(serializers.Serializer):
    bangumi_id = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    feedback_version = serializers.IntegerField(read_only=True)
    hidden_at = serializers.DateTimeField(read_only=True, allow_null=True)
    updated_at = serializers.DateTimeField(read_only=True)


class FeedbackResultSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    action = serializers.CharField(read_only=True)
    operation_id = serializers.UUIDField(read_only=True)
    replayed = serializers.BooleanField(read_only=True)
    changed = serializers.BooleanField(read_only=True)
    feedback_version = serializers.IntegerField(read_only=True)
    operation_feedback_version = serializers.IntegerField(read_only=True)
    feedback = FeedbackWorkSerializer(read_only=True)


class FeedbackHiddenWorkSerializer(FeedbackWorkSerializer):
    title_zh = serializers.CharField(read_only=True)
    title_jp = serializers.CharField(read_only=True)
    season = serializers.CharField(read_only=True)
    status_type = serializers.CharField(read_only=True)


class FeedbackHiddenListSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    feedback_version = serializers.IntegerField(read_only=True)
    count = serializers.IntegerField(read_only=True)
    page = serializers.IntegerField(read_only=True)
    page_size = serializers.IntegerField(read_only=True)
    next_page = serializers.IntegerField(read_only=True, allow_null=True)
    results = FeedbackHiddenWorkSerializer(many=True, read_only=True)


class EvidenceSourceSerializer(serializers.Serializer):
    bangumi_id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    personal_score = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)


class RecommendationEvidenceSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    kind = serializers.ChoiceField(choices=('rated_work', 'explicit_preferences'), read_only=True)
    source = EvidenceSourceSerializer(read_only=True, allow_null=True)
    shared_tags = serializers.ListField(child=serializers.CharField(), read_only=True)
    feature_rule_version = serializers.CharField(read_only=True)
    shared_tag_sources = serializers.DictField(child=serializers.ListField(child=serializers.CharField()), read_only=True)
    versions = EvidenceVersionsSerializer(read_only=True)
    interpretation = serializers.CharField(read_only=True)
    is_probability = serializers.BooleanField(read_only=True)
    is_exhaustive_ranking_explanation = serializers.BooleanField(read_only=True)


class EvidenceEnvelopeSerializer(serializers.Serializer):
    detail_context = serializers.CharField(read_only=True)
    reason = serializers.CharField(read_only=True)
    evidence = RecommendationEvidenceSerializer(read_only=True, allow_null=True)
    evidence_context = EvidenceVersionsSerializer(read_only=True)
    evidence_token = serializers.CharField(read_only=True)
    evidence_disclaimer = serializers.CharField(read_only=True)


class RecommendationBrowseResultSerializer(EvidenceEnvelopeSerializer):
    bangumi_id = serializers.IntegerField(read_only=True)
    title_zh = serializers.CharField(read_only=True)
    title_jp = serializers.CharField(read_only=True)
    score = serializers.FloatField(read_only=True, allow_null=True)
    tags = serializers.ListField(child=serializers.CharField(), read_only=True)
    cover_url = serializers.CharField(read_only=True)
    season = serializers.CharField(read_only=True)
    status_type = serializers.CharField(read_only=True)
    content_rating = serializers.ChoiceField(choices=('non_adult',), read_only=True)
    watch_status = serializers.CharField(read_only=True, allow_null=True)


class RecommendationBrowsePageSerializer(serializers.Serializer):
    execution_recipe = ExecutionRecipeSerializer(read_only=True)
    contract_version = serializers.CharField(read_only=True)
    browse_contract_version = serializers.CharField(read_only=True)
    algorithm_version = serializers.CharField(read_only=True)
    control_policy_version = serializers.CharField(read_only=True)
    style = serializers.CharField(read_only=True)
    effective_conditions = serializers.DictField(read_only=True)
    snapshot_id = serializers.CharField(read_only=True)
    snapshot_expires_at = serializers.IntegerField(read_only=True)
    batch = serializers.IntegerField(read_only=True)
    batch_size = serializers.IntegerField(read_only=True)
    batch_count = serializers.IntegerField(read_only=True)
    batch_hero_bangumi_id = serializers.IntegerField(read_only=True, allow_null=True)
    page = serializers.IntegerField(read_only=True)
    page_size = serializers.IntegerField(read_only=True)
    count = serializers.IntegerField(read_only=True)
    result_limit = serializers.IntegerField(read_only=True)
    next_page_cursor = serializers.CharField(read_only=True, allow_null=True)
    next_batch_cursor = serializers.CharField(read_only=True, allow_null=True)
    batch_exhausted = serializers.BooleanField(read_only=True)
    exhausted = serializers.BooleanField(read_only=True)
    mode = serializers.CharField(read_only=True)
    readiness = serializers.CharField(read_only=True)
    cold_start_state = ColdStartStateSerializer(read_only=True)
    feature_version = serializers.CharField(read_only=True)
    taxonomy_version = serializers.CharField(read_only=True)
    evidence_contract_version = serializers.CharField(read_only=True)
    evidence_context = EvidenceVersionsSerializer(read_only=True)
    evidence_disclaimer = serializers.CharField(read_only=True)
    results = RecommendationBrowseResultSerializer(many=True, read_only=True)


class RecommendationDetailQuerySerializer(serializers.Serializer):
    evidence_token = serializers.RegexField(regex=r'^[a-f0-9]{64}$', required=False)
    detail_context = serializers.CharField(max_length=8192, required=False, trim_whitespace=False, allow_blank=False)

    def to_internal_value(self, data):
        if set(data) - set(self.fields) or any(len(data.getlist(key)) != 1 for key in data if hasattr(data, 'getlist')):
            raise serializers.ValidationError({'non_field_errors': ['详情仅接受一枚 evidence_token，不接受用户或算法覆盖参数。']})
        return super().to_internal_value(data)


class IdentityReviewQuerySerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=('high-score', 'all'), default='high-score')
    page = serializers.IntegerField(min_value=1, max_value=1000, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=10, default=5)
    record_id = serializers.IntegerField(min_value=1, required=False)
    keyword = serializers.CharField(min_length=2, max_length=100, required=False)

    def to_internal_value(self, data):
        if set(data) - set(self.fields) or any(len(data.getlist(key)) != 1 for key in data if hasattr(data, 'getlist')):
            raise serializers.ValidationError({'non_field_errors': ['不支持的查询参数。']})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if 'keyword' in attrs and 'record_id' not in attrs:
            raise serializers.ValidationError('搜索候选须指定自己的 record_id。')
        return attrs


class IdentityConfirmationSerializer(serializers.Serializer):
    record_id = serializers.IntegerField(min_value=1)
    bangumi_id = serializers.IntegerField(min_value=1, max_value=2_147_483_647)
    expected_version = serializers.IntegerField(min_value=0, max_value=2_147_483_647)
    ticket = serializers.CharField(max_length=2048)
    confirmed = serializers.BooleanField()

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and set(data) - set(self.fields):
            raise serializers.ValidationError({'non_field_errors': ['不支持的字段。']})
        return super().to_internal_value(data)

    def validate_confirmed(self, value):
        if value is not True:
            raise serializers.ValidationError('须明确确认作品身份。')
        return value


class IdentityBatchSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField()
    items = IdentityConfirmationSerializer(many=True, allow_empty=False, max_length=20)

    def to_internal_value(self, data):
        if not isinstance(data, Mapping) or set(data) - set(self.fields):
            raise serializers.ValidationError({'non_field_errors': ['不支持的批量关联字段。']})
        return super().to_internal_value(data)

    def validate_items(self, items):
        if len({item['record_id'] for item in items}) != len(items):
            raise serializers.ValidationError('同一手账记录不能在一批中重复提交。')
        return items
