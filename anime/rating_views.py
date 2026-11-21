from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count
from django.utils.cache import patch_vary_headers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UserProfile, UserAnimeRecord
from .rating_config import normalized_rating_preferences, validate_rating_preferences


class RatingPreferencesView(APIView):
    permission_classes = (IsAuthenticated,)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response

    def payload(self, profile):
        histogram = list(UserAnimeRecord.objects.filter(user=profile.user).order_by('personal_score')
                         .values('personal_score').annotate(count=Count('pk')))
        return {'preferences': normalized_rating_preferences(profile.rating_preferences),
                'revision': profile.rating_revision, 'histogram': [
                    {'score': float(row['personal_score']) if row['personal_score'] is not None else None, 'count': row['count']}
                    for row in histogram]}

    def get(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        return Response(self.payload(profile))

    @transaction.atomic
    def patch(self, request):
        data = request.data
        if not isinstance(data, dict) or set(data) != {'preferences', 'revision'} or type(data['revision']) is not int or data['revision'] < 0:
            raise ValidationError('请提交完整配置及读取时的版本。')
        try:
            validate_rating_preferences(data['preferences'])
        except DjangoValidationError as error:
            raise ValidationError({'preferences': error.messages})
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        profile, _ = UserProfile.objects.select_for_update().get_or_create(user=request.user)
        if profile.rating_revision != data['revision']:
            return Response({'detail': '配置已在其他页面更新。请重新读取，再调整并保存；本页草稿仍保留。'}, status=409)
        profile.rating_preferences = data['preferences']; profile.rating_revision += 1
        profile.save(update_fields=['rating_preferences', 'rating_revision'])
        return Response(self.payload(profile))
