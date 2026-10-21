from io import BytesIO
import hmac
import logging

from django.db import DatabaseError
from django.utils.cache import patch_vary_headers

from rest_framework import permissions
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from .serializers import RecommendationPreferencePatchSerializer, RecommendationPreferenceSerializer, RecommendationProfileSerializer
from .services import patch_preferences, preference_contract, profile_contract
from .services import preview_profile
from .serializers import ProfilePreviewQuerySerializer, ProfilePreviewSerializer
from .preference_options import with_ordered_options
from .engine import (CONTRACT_VERSION, FEATURE_VERSION, RecommendationSnapshotChanged,
                     RecommendationUnavailable, rank_candidates, user_context, configured_algorithm)
from .evidence import EvidencePresenter, EVIDENCE_CONTRACT_VERSION, EVIDENCE_DISCLAIMER
from .identity import safe_catalog
from .serializers import RecommendationPublicAnimeSerializer, RecommendationListAnimeSerializer, RecommendationQuerySerializer
from .taxonomy import TAXONOMY_VERSION
from .serializers import IdentityReviewQuerySerializer, IdentityConfirmationSerializer, IdentityBatchSerializer
from .identity_batch import confirm_identity_batch
from .binding import bind_record
from .review import review_contract
from .want import add_want, WantConflict
from .serializers import RecommendationWantSerializer
from .serializers import EvidenceEnvelopeSerializer, RecommendationDetailQuerySerializer
from .feedback import FeedbackConflict, mutate_feedback, hidden_feedback
from .content_scope import scope_query, require_scope, ensure_scope, check_precondition
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
    scoped = False
    preload_scope = True

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.scoped:
            request.scope_query = scope_query(request.query_params)
            if self.preload_scope:
                request.content_scope = require_scope(request.user, request.query_params.get('content_scope', 'safe'))
                check_precondition(request.headers.get('X-Recommendation-Scope'), request.content_scope)

    def query_params(self, request):
        return getattr(request, 'scope_query', request.query_params)

    def check_throttles(self, request):
        try:
            return super().check_throttles(request)
        except APIException:
            raise  # Preserve genuine 429/Retry-After and other DRF decisions.
        except Exception as exc:
            # A shared limiter/cache outage must never silently allow writes.
            # Log type only, not backend URLs, credentials or private payloads.
            logger.warning('Recommendation limiter unavailable: %s', type(exc).__name__)
            raise RecommendationUnavailable('请求频率检查暂不可用，请稍后重试。') from None

    def finalize_response(self, request, response, *args, **kwargs):
        if self.scoped and hasattr(request, 'content_scope') and response.status_code < 400:
            # Outside the browse REPEATABLE READ snapshot: catch qualification
            # changes committed while a long GET was computing. Never turn an
            # already committed write acknowledgement into a misleading failure.
            if request.method == 'GET':
                try:
                    ensure_scope(request.user, request.content_scope)
                    from .content_scope import verify_response_catalog
                    verify_response_catalog(response.data, request.content_scope)
                except (APIException, DatabaseError) as exc:
                    response = self.handle_exception(exc)
            if response.status_code < 400 and isinstance(response.data, dict):
                response.data['content_scope_context'] = dict(request.content_scope)
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
        if self.query_params(request):
            raise ValidationError('本接口不接受查询参数，只能读取或设置当前登录用户的数据。')


class RecommendationProfileView(OwnRecommendationAPIView):
    def get(self, request):
        self.check_query_contract(request)
        return Response(RecommendationProfileSerializer(profile_contract(request.user)).data)


class RecommendationProfilePreviewView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request):
        query = ProfilePreviewQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(ProfilePreviewSerializer(preview_profile(
            request.user, query.validated_data['profile_min_score'])).data)


class RecommendationCapabilitiesView(OwnRecommendationAPIView):
    def get(self, request):
        self.check_query_contract(request)
        from .deployment import capabilities
        return Response(capabilities())


class RecommendationContextView(OwnRecommendationAPIView):
    scoped = True
    preload_scope = False
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request):
        self.check_query_contract(request)
        from .context_probe import context_contract
        data = context_contract(request.user, requested_scope=request.query_params.get('content_scope', 'safe'))
        request.content_scope = data.pop('content_scope_context')
        check_precondition(request.headers.get('X-Recommendation-Scope'), request.content_scope)
        return Response(data)


class RecommendationFeedbackView(OwnRecommendationAPIView):
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_write'
    action = 'hide'

    def post(self, request):
        self.check_query_contract(request)
        serializer = (FeedbackUndoSerializer if self.action == 'undo' else FeedbackMutationSerializer)(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = mutate_feedback(user=request.user, action=self.action, changes=serializer.validated_data,
                                 scope_context=request.content_scope)
        return Response(FeedbackResultSerializer(result).data)


class RecommendationFeedbackUndoView(RecommendationFeedbackView):
    action = 'undo'


class RecommendationHiddenFeedbackView(OwnRecommendationAPIView):
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request):
        serializer = FeedbackHiddenQuerySerializer(data=self.query_params(request))
        serializer.is_valid(raise_exception=True)
        return Response(FeedbackHiddenListSerializer(hidden_feedback(request.user, serializer.validated_data, scope_context=request.content_scope)).data)


class RecommendationWantView(OwnRecommendationAPIView):
    scoped = True
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
        result = add_want(user=request.user, bangumi_id=bangumi_id, scope_context=request.content_scope)
        return Response(RecommendationWantSerializer(result).data, status=201 if result['created'] else 200)


class RecommendationPreferenceView(OwnRecommendationAPIView):
    def get(self, request):
        self.check_query_contract(request)
        return Response(RecommendationPreferenceSerializer(with_ordered_options(preference_contract(request.user))).data)

    def patch(self, request):
        self.check_query_contract(request)
        serializer = RecommendationPreferencePatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = patch_preferences(user=request.user, changes=serializer.validated_data)
        return Response(RecommendationPreferenceSerializer(with_ordered_options(result)).data)


class RecommendationListView(OwnRecommendationAPIView):
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request):
        query = RecommendationQuerySerializer(data=self.query_params(request))
        query.is_valid(raise_exception=True)
        options = query.validated_data
        from .browsing import browse_requested
        from .deployment import browse_page
        if browse_requested(options, self.query_params(request)):
            from .serializers import RecommendationBrowsePageSerializer
            return Response(RecommendationBrowsePageSerializer(
                browse_page(request.user, options, self.query_params(request), scope_context=request.content_scope)).data)
        context, ranked, token, weights = rank_candidates(request.user, options, scope_context=request.content_scope)
        if options['page'] > 1 and not hmac.compare_digest(token, options['state_token']):
            raise RecommendationSnapshotChanged()
        start = (options['page'] - 1) * options['page_size']
        page = ranked[start:start + options['page_size']]
        presenter = EvidencePresenter(request.user, context, dict(weights), context['algorithm_version'])
        presenter.ensure_safe([item['anime'] for item in page])
        results = []
        for item in page:
            data = dict(RecommendationListAnimeSerializer(item['anime'], context={'content_scope': request.content_scope, 'validated_adult_ids': {r['anime'].bangumi_id for r in page if r['anime'].nsfw is True}}).data)
            data.update(EvidenceEnvelopeSerializer(presenter.present(item['anime'], item['tags'])).data)
            data.update(content_rating='adult' if item['anime'].nsfw else 'non_adult', watch_status=context['owned'].get(item['anime'].bangumi_id))
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
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request, bangumi_id):
        query = RecommendationDetailQuerySerializer(data=self.query_params(request))
        query.is_valid(raise_exception=True)
        if 'detail_context' in query.validated_data:
            from .deployment import detail
            return Response(detail(request.user, bangumi_id, query.validated_data))
        from .content_scope import catalog_for_scope
        anime = catalog_for_scope(request.content_scope).filter(bangumi_id=bangumi_id).first()
        if anime is None:
            raise NotFound('该作品不可用于推荐。')
        context = user_context(request.user, scope_context=request.content_scope)
        from .engine import feature_tags, public_features
        algorithm = configured_algorithm()
        tags = feature_tags(anime.tags, (anime.title_zh, anime.title_jp))
        presenter = EvidencePresenter(request.user, context, public_features(algorithm_version=algorithm, scope_context=request.content_scope)['weights'], algorithm)
        presenter.ensure_safe([anime])
        explanation = presenter.present(anime, tags)
        expected = query.validated_data.get('evidence_token')
        if expected and not hmac.compare_digest(expected, explanation['evidence_token']):
            raise RecommendationSnapshotChanged('推荐依据已更新，请重新加载推荐后查看详情。')
        data = dict(RecommendationPublicAnimeSerializer(anime, context={'content_scope': request.content_scope}).data)
        data.update(EvidenceEnvelopeSerializer(explanation).data)
        data.update(content_rating='adult' if anime.nsfw else 'non_adult', watch_status=context['owned'].get(anime.bangumi_id))
        return Response(data)


class RecommendationIdentityReviewView(OwnRecommendationAPIView):
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_identity'

    def get(self, request):
        serializer = IdentityReviewQuerySerializer(data=self.query_params(request))
        serializer.is_valid(raise_exception=True)
        return Response(review_contract(request.user, serializer.validated_data, scope_context=request.content_scope))

    def patch(self, request):
        self.check_query_contract(request)
        serializer = IdentityConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = bind_record(record_id=data['record_id'], user_id=request.user.pk, target_id=data['bangumi_id'],
                             source='user_confirmed', expected_version=data['expected_version'], ticket=data['ticket'], scope_context=request.content_scope)
        return Response(result)


class BoundedIdentityBatchParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        data = stream.read(64 * 1024 + 1)
        if len(data) > 64 * 1024:
            raise ValidationError('批量关联请求不能超过 64 KiB。')
        return super().parse(BytesIO(data), media_type=media_type, parser_context=parser_context)


class RecommendationIdentityBatchView(OwnRecommendationAPIView):
    scoped = True
    parser_classes = (BoundedIdentityBatchParser,)
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_identity'

    def post(self, request):
        self.check_query_contract(request)
        serializer = IdentityBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(confirm_identity_batch(request.user, serializer.validated_data, scope_context=request.content_scope))
