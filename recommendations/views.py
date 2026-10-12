from io import BytesIO

from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RecommendationPreferencePatchSerializer, RecommendationPreferenceSerializer, RecommendationProfileSerializer
from .services import patch_preferences, preference_contract, profile_contract


class BoundedPreferenceJSONParser(JSONParser):
    """Small preference contract, not an unbounded arbitrary JSON upload."""

    def parse(self, stream, media_type=None, parser_context=None):
        data = stream.read(16 * 1024 + 1)
        if len(data) > 16 * 1024:
            raise ValidationError('偏好请求不能超过 16 KiB。')
        return super().parse(BytesIO(data), media_type=media_type, parser_context=parser_context)


class OwnRecommendationAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (BoundedPreferenceJSONParser,)

    def check_query_contract(self, request):
        if request.query_params:
            raise ValidationError('本接口不接受查询参数，只能读取或设置当前登录用户的数据。')


class RecommendationProfileView(OwnRecommendationAPIView):
    def get(self, request):
        self.check_query_contract(request)
        return Response(RecommendationProfileSerializer(profile_contract(request.user)).data)


class RecommendationPreferenceView(OwnRecommendationAPIView):
    def get(self, request):
        self.check_query_contract(request)
        return Response(RecommendationPreferenceSerializer(preference_contract(request.user)).data)

    def patch(self, request):
        self.check_query_contract(request)
        serializer = RecommendationPreferencePatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = patch_preferences(user=request.user, changes=serializer.validated_data)
        return Response(RecommendationPreferenceSerializer(result).data)
