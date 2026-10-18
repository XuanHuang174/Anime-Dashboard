from io import BytesIO
import hmac
import logging

from django.db import DatabaseError
from django.utils.cache import patch_vary_headers

from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from .serializers import RecommendationPreferencePatchSerializer, RecommendationPreferenceSerializer, RecommendationProfileSerializer
from .services import patch_preferences, preference_contract, profile_contract
from .engine import (CONTRACT_VERSION, FEATURE_VERSION, RecommendationSnapshotChanged,
                     RecommendationUnavailable, rank_candidates, user_context, configured_algorithm)
from .evidence import EvidencePresenter, EVIDENCE_CONTRACT_VERSION, EVIDENCE_DISCLAIMER
from .identity import safe_catalog
from .serializers import RecommendationPublicAnimeSerializer, RecommendationListAnimeSerializer, RecommendationQuerySerializer
from .taxonomy import TAXONOMY_VERSION
from .serializers import IdentityReviewQuerySerializer, IdentityConfirmationSerializer
from .binding import bind_record
from .review import review_contract
from .want import add_want, WantConflict
from .serializers import RecommendationWantSerializer
from .serializers import EvidenceEnvelopeSerializer, RecommendationDetailQuerySerializer
from .feedback import FeedbackConflict, mutate_feedback, hidden_feedback
from .serializers import (FeedbackMutationSerializer, FeedbackUndoSerializer,
                          FeedbackHiddenQuerySerializer, FeedbackResultSerializer, FeedbackHiddenListSerializer)

logger = logging.getLogger(__name__)


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

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        # Personalized responses must not be replayed by a browser/CDN after a save.
        response['Cache-Control'] = 'private, no-store'
        patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response

    def handle_exception(self, exc):
        from .browsing import BrowseDisabled
        if isinstance(exc, BrowseDisabled):
            response = Response(exc.payload, status=exc.status_code)
            response.exception = True
            return response
        if isinstance(exc, FeedbackConflict):
            response = Response(exc.payload, status=exc.status_code)
            response.exception = True
            return response
        if isinstance(exc, DatabaseError):
            logger.warning('Recommendation database unavailable: %s', type(exc).__name__)
            exc = RecommendationUnavailable()
        return super().handle_exception(exc)

    def check_query_contract(self, request):
        if request.query_params:
            raise ValidationError('本接口不接受查询参数，只能读取或设置当前登录用户的数据。')


class RecommendationProfileView(OwnRecommendationAPIView):
    def get(self, request):
        self.check_query_contract(request)
        return Response(RecommendationProfileSerializer(profile_contract(request.user)).data)


class RecommendationCapabilitiesView(OwnRecommendationAPIView):
    def get(self, request):
        self.check_query_contract(request)
        from .deployment import capabilities
        return Response(capabilities())


class RecommendationFeedbackView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_write'
    action = 'hide'

    def post(self, request):
        self.check_query_contract(request)
        serializer = (FeedbackUndoSerializer if self.action == 'undo' else FeedbackMutationSerializer)(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = mutate_feedback(user=request.user, action=self.action, changes=serializer.validated_data)
        return Response(FeedbackResultSerializer(result).data)


class RecommendationFeedbackUndoView(RecommendationFeedbackView):
    action = 'undo'


class RecommendationHiddenFeedbackView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request):
        serializer = FeedbackHiddenQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(FeedbackHiddenListSerializer(hidden_feedback(request.user, serializer.validated_data)).data)


class RecommendationWantView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_write'

    def handle_exception(self, exc):
        if isinstance(exc, WantConflict):
            # APIException coerces nested bool/int details into strings. Preserve
            # this endpoint's explicit typed flags instead of returning "True".
            response = Response(exc.payload, status=exc.status_code)
            response.exception = True
            return response
        return super().handle_exception(exc)

    def post(self, request, bangumi_id):
        self.check_query_contract(request)
        if not isinstance(request.data, dict) or request.data:
            raise ValidationError('本接口只接受空 JSON 对象，不接受用户 ID、记录 ID、评分或长评。')
        result = add_want(user=request.user, bangumi_id=bangumi_id)
        return Response(RecommendationWantSerializer(result).data, status=201 if result['created'] else 200)


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


class RecommendationListView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request):
        query = RecommendationQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        options = query.validated_data
        from .browsing import browse_requested
        from .deployment import browse_page
        if browse_requested(options, request.query_params):
            from .serializers import RecommendationBrowsePageSerializer
            return Response(RecommendationBrowsePageSerializer(
                browse_page(request.user, options, request.query_params)).data)
        context, ranked, token, weights = rank_candidates(request.user, options)
        if options['page'] > 1 and not hmac.compare_digest(token, options['state_token']):
            raise RecommendationSnapshotChanged()
        start = (options['page'] - 1) * options['page_size']
        page = ranked[start:start + options['page_size']]
        presenter = EvidencePresenter(request.user, context, dict(weights), context['algorithm_version'])
        presenter.ensure_safe([item['anime'] for item in page])
        results = []
        for item in page:
            data = dict(RecommendationListAnimeSerializer(item['anime']).data)
            data.update(EvidenceEnvelopeSerializer(presenter.present(item['anime'], item['tags'])).data)
            data.update(content_rating='non_adult', watch_status=context['owned'].get(item['anime'].bangumi_id))
            results.append(data)
        return Response({
            'contract_version': CONTRACT_VERSION, 'algorithm_version': context['algorithm_version'],
            'feature_version': FEATURE_VERSION, 'taxonomy_version': TAXONOMY_VERSION,
            'evidence_contract_version': EVIDENCE_CONTRACT_VERSION,
            'evidence_context': presenter.versions, 'evidence_disclaimer': EVIDENCE_DISCLAIMER,
            'mode': context['mode'], 'readiness': context['profile']['readiness'],
            'cold_start_state': context['profile']['cold_start_state'],
            'count': len(ranked), 'page': options['page'], 'page_size': options['page_size'],
            'state_token': token if token != 'cold' else None,
            'next_page': options['page'] + 1 if start + len(page) < len(ranked) else None,
            'results': results,
        })


class RecommendationDetailView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request, bangumi_id):
        query = RecommendationDetailQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        if 'detail_context' in query.validated_data:
            from .deployment import detail
            return Response(detail(request.user, bangumi_id, query.validated_data))
        anime = safe_catalog().filter(bangumi_id=bangumi_id).first()
        if anime is None:
            raise NotFound('该作品不可用于推荐。')
        context = user_context(request.user)
        from .engine import feature_tags, public_features
        algorithm = configured_algorithm()
        tags = feature_tags(anime.tags, (anime.title_zh, anime.title_jp))
        presenter = EvidencePresenter(request.user, context, public_features(algorithm_version=algorithm)['weights'], algorithm)
        presenter.ensure_safe([anime])
        explanation = presenter.present(anime, tags)
        expected = query.validated_data.get('evidence_token')
        if expected and not hmac.compare_digest(expected, explanation['evidence_token']):
            raise RecommendationSnapshotChanged('推荐依据已更新，请重新加载推荐后查看详情。')
        data = dict(RecommendationPublicAnimeSerializer(anime).data)
        data.update(EvidenceEnvelopeSerializer(explanation).data)
        data.update(content_rating='non_adult', watch_status=context['owned'].get(anime.bangumi_id))
        return Response(data)


class RecommendationIdentityReviewView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_identity'

    def get(self, request):
        serializer = IdentityReviewQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(review_contract(request.user, serializer.validated_data))

    def patch(self, request):
        self.check_query_contract(request)
        serializer = IdentityConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = bind_record(record_id=data['record_id'], user_id=request.user.pk, target_id=data['bangumi_id'],
                             source='user_confirmed', expected_version=data['expected_version'], ticket=data['ticket'])
        return Response(result)
