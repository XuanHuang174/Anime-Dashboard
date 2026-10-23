"""Protected exploration selection, single-work snapshots and scoped details."""
from django.db import DatabaseError
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from .views import OwnRecommendationAPIView
from .exploration_catalog import search_catalog
from .exploration_contract import (ExplorationSearchQuerySerializer, contract_descriptor,
    require_enabled, ExplorationQuerySerializer, ExplorationCursorQuerySerializer,
    ExplorationDetailQuerySerializer, ExplorationContextQuerySerializer, ExplorationSnapshotChanged)


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


class ExplorationSnapshotAPIView(ExplorationAPIView):
    def finalize_response(self, request, response, *args, **kwargs):
        # Outside the read-only snapshot: direct catalog UPDATE must not send an
        # old selected source or evidence, including zero-result/context replies.
        if response.status_code < 400 and isinstance(response.data, dict):
            expected = response.data.get('evidence_context', {}).get('catalog_feature_version')
            if expected:
                try:
                    from .browsing import catalog_revision
                    if expected != catalog_revision():
                        raise ExplorationSnapshotChanged('目录在读取期间已变化，请重新探索。')
                except (APIException, DatabaseError) as exc:
                    response = self.handle_exception(exc)
        return super().finalize_response(request, response, *args, **kwargs)


class ExplorationListView(ExplorationSnapshotAPIView):
    def get(self, request):
        params = self.query_params(request)
        serializer = ExplorationCursorQuerySerializer if 'cursor' in params else ExplorationQuerySerializer
        query = serializer(data=params)
        query.is_valid(raise_exception=True)
        from .exploration_browsing import browse_page
        return Response(browse_page(request.user, query.validated_data, scope_context=request.content_scope))


class ExplorationDetailView(ExplorationSnapshotAPIView):
    def get(self, request, bangumi_id):
        query = ExplorationDetailQuerySerializer(data=self.query_params(request))
        query.is_valid(raise_exception=True)
        from .exploration_browsing import detail
        return Response(detail(request.user, bangumi_id, query.validated_data, scope_context=request.content_scope))


class ExplorationContextView(ExplorationSnapshotAPIView):
    def get(self, request):
        query = ExplorationContextQuerySerializer(data=self.query_params(request))
        query.is_valid(raise_exception=True)
        from .exploration_browsing import context_probe
        return Response(context_probe(request.user, query.validated_data['snapshot_context'], scope_context=request.content_scope))
