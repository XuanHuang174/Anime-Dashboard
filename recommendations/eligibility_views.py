"""Own-account application, status and stop. No public approval/renew endpoint."""
from rest_framework.response import Response
from anime.throttles import AccountRateThrottle as UserRateThrottle, LoggedScopedRateThrottle as ScopedRateThrottle

from .eligibility import mutate_eligibility, own_eligibility
from .views import OwnRecommendationAPIView


class RecommendationEligibilityView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def get(self, request):
        self.check_query_contract(request)
        return Response(own_eligibility(request.user))


class RecommendationEligibilityRevokeView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_write'

    def post(self, request):
        self.check_query_contract(request)
        return Response(mutate_eligibility(actor=request.user, subject_id=request.user.pk,
                                          action='self_revoke', changes=request.data))


class RecommendationEligibilityApplyView(RecommendationEligibilityRevokeView):
    def post(self, request):
        self.check_query_contract(request)
        return Response(mutate_eligibility(actor=request.user, subject_id=request.user.pk,
                                          action='apply', changes=request.data))
