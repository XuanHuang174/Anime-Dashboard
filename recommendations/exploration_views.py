"""11A exposes selection/contracts only. Recommendation execution comes in 11B."""
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from .views import OwnRecommendationAPIView
from .exploration_catalog import search_catalog
from .exploration_contract import (ExplorationSearchQuerySerializer, contract_descriptor,
                                   require_enabled)


class ExplorationAPIView(OwnRecommendationAPIView):
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_enabled()


class ExplorationContractView(ExplorationAPIView):
    def get(self, request):
        self.check_query_contract(request)
        return Response(contract_descriptor())


class ExplorationCatalogView(ExplorationAPIView):
    def get(self, request):
        query = ExplorationSearchQuerySerializer(data=self.query_params(request))
        query.is_valid(raise_exception=True)
        return Response(search_catalog(query.validated_data['q'], request.content_scope))
