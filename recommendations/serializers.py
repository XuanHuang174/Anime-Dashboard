from collections.abc import Mapping

from rest_framework import serializers

from .identity import public_cover_url, require_safe_anime
from .features import feature_tags
from .models import RecommendationAnime
from .taxonomy import AVAILABLE_TAGS, MAX_PREFERENCE_TAGS, canonical_tag, canonical_tags


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

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and set(data) - set(self.fields):
            # Do not accept forged owners, allow_nsfw switches or hidden fields.
            raise serializers.ValidationError({'non_field_errors': ['请求包含不支持的字段；所属用户只能由登录身份确定。']})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if not {'tags', 'add_tags', 'remove_tags'}.intersection(attrs):
            raise serializers.ValidationError('请提交 tags 或 add_tags/remove_tags。')
        if 'tags' in attrs and {'add_tags', 'remove_tags'}.intersection(attrs):
            raise serializers.ValidationError('tags 整体设置不能与增量操作混用。')
        if set(attrs.get('add_tags', [])) & set(attrs.get('remove_tags', [])):
            raise serializers.ValidationError('同一标签不能同时添加和移除。')
        return attrs


class RecommendationPreferenceSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    taxonomy_version = serializers.CharField(read_only=True)
    tags = serializers.ListField(child=serializers.CharField(), read_only=True)
    version = serializers.IntegerField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)
    available_tags = serializers.ListField(child=serializers.CharField(), read_only=True)


class ProfileTagSerializer(serializers.Serializer):
    tag = serializers.CharField(read_only=True)
    sample_count = serializers.IntegerField(read_only=True)


class ProfilePolicySerializer(serializers.Serializer):
    watch_status = serializers.CharField(read_only=True)
    minimum_score = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
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


class RecommendationWantSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    bangumi_id = serializers.IntegerField(read_only=True)
    anime_id = serializers.IntegerField(read_only=True)
    record_id = serializers.IntegerField(read_only=True)
    watch_status = serializers.CharField(read_only=True)
    created = serializers.BooleanField(read_only=True)
    outcome = serializers.CharField(read_only=True)
    content_rating = serializers.CharField(read_only=True)


class RecommendationQuerySerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=('all', 'familiar', 'explore'), default='all')
    type = serializers.ChoiceField(choices=('all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'), default='all')
    min_score = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, default=0)
    hide_recorded = serializers.BooleanField(default=True)
    page = serializers.IntegerField(min_value=1, max_value=1000, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=24, default=12)
    state_token = serializers.RegexField(regex=r'^[a-f0-9]{64}$', required=False)

    def to_internal_value(self, data):
        if set(data) - set(self.fields) or any(len(data.getlist(key)) > 1 for key in data if hasattr(data, 'getlist')):
            raise serializers.ValidationError({'non_field_errors': ['查询包含未定义或重复的参数。']})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if attrs['page'] > 1 and 'state_token' not in attrs:
            raise serializers.ValidationError('加载下一页必须携带第一页的 state_token。')
        return attrs


class RecommendationListAnimeSerializer(RecommendationPublicAnimeSerializer):
    class Meta(RecommendationPublicAnimeSerializer.Meta):
        fields = tuple(field for field in RecommendationPublicAnimeSerializer.Meta.fields if field != 'synopsis')
        read_only_fields = fields


class EvidenceVersionsSerializer(serializers.Serializer):
    algorithm_version = serializers.CharField(read_only=True)
    ranking_policy_version = serializers.CharField(read_only=True)
    taxonomy_version = serializers.CharField(read_only=True)
    feature_version = serializers.CharField(read_only=True)
    feature_rule_version = serializers.CharField(read_only=True)
    selection_rule_version = serializers.CharField(read_only=True)
    profile_version = serializers.CharField(read_only=True)
    preference_version = serializers.IntegerField(read_only=True)
    catalog_feature_version = serializers.CharField(read_only=True)


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
    reason = serializers.CharField(read_only=True)
    evidence = RecommendationEvidenceSerializer(read_only=True, allow_null=True)
    evidence_context = EvidenceVersionsSerializer(read_only=True)
    evidence_token = serializers.CharField(read_only=True)
    evidence_disclaimer = serializers.CharField(read_only=True)


class RecommendationDetailQuerySerializer(serializers.Serializer):
    evidence_token = serializers.RegexField(regex=r'^[a-f0-9]{64}$', required=False)

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
