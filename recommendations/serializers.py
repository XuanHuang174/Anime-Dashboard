from collections.abc import Mapping

from rest_framework import serializers

from .identity import public_cover_url, require_safe_anime
from .models import RecommendationAnime
from .taxonomy import MAX_PREFERENCE_TAGS, canonical_tag, canonical_tags


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


class RecommendationProfileSerializer(serializers.Serializer):
    contract_version = serializers.CharField(read_only=True)
    taxonomy_version = serializers.CharField(read_only=True)
    eligible_sample_count = serializers.IntegerField(read_only=True)
    cold_start = serializers.BooleanField(read_only=True)
    has_usable_tags = serializers.BooleanField(read_only=True)
    readiness = serializers.CharField(read_only=True)
    tag_summary = ProfileTagSerializer(many=True, read_only=True)
    sample_policy = ProfilePolicySerializer(read_only=True)


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
        return canonical_tags(instance.tags.split(','))


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
