"""13C authenticated GET adapters, independent of series/exploration gates."""
from django.db import DatabaseError
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle, ScopedRateThrottle
from .views import OwnRecommendationAPIView
from . import credits_read as reads
from .credits_api_contract import (KINDS, StrictQuery, SearchQuery, WorksQuery, SummaryQuery,
    CreditsChanged, CreditsUnavailable, require_access, rollout_revision)
from .credits_contract import integer
from .evidence import _token


class CreditsView(OwnRecommendationAPIView):
    scoped = True
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_read'

    def handle_exception(self, exc):
        if isinstance(exc, DatabaseError):
            exc = CreditsUnavailable()
        return super().handle_exception(exc)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        kind = kwargs.get('kind')
        if kind is not None and kind not in KINDS:
            raise NotFound()
        for key in ('person_id', 'bangumi_id', 'record_id'):
            if key in kwargs:
                try:
                    integer(kwargs[key])
                except ValueError:
                    raise ValidationError('ID 范围无效。') from None
        require_access(request.user, kind)

    def query(self, request, serializer=StrictQuery):
        query = serializer(data=self.query_params(request))
        query.is_valid(raise_exception=True)
        return query.validated_data

    def execute(self, request, operation):
        def snapshot():
            self.guard = (reads.revision(), reads.catalog_revision(), rollout_revision(request.user))
            result = operation()
            result['content_scope_context'] = dict(request.content_scope)
            return result
        return Response(reads.read(snapshot))

    def finalize_response(self, request, response, *args, **kwargs):
        if response.status_code < 400 and hasattr(self, 'guard'):
            try:
                require_access(request.user, getattr(self, 'credits_kind', kwargs.get('kind')))
                if self.guard != (reads.revision(), reads.catalog_revision(), rollout_revision(request.user)):
                    raise CreditsChanged()
                if hasattr(self, 'private_guard') and self.private_guard() is False:
                    raise CreditsChanged()
            except (APIException, DatabaseError) as exc:
                response = self.handle_exception(exc)
        return super().finalize_response(request, response, *args, **kwargs)


class CreditsSearchView(CreditsView):
    def get(self, request):
        query = self.query(request, SearchQuery)
        self.credits_kind = query['kind']
        return self.execute(request, lambda: reads.search(request.user, request.content_scope, query['kind'], query['q']))


class CreditsSummaryView(CreditsView):
    def get(self, request, bangumi_id):
        query = self.query(request, SummaryQuery)
        return self.execute(request, lambda: reads.subject_summary(request.user, request.content_scope, bangumi_id,
            expanded=query['expanded'] == 'true'))


class CreditsRecordView(CreditsView):
    def get(self, request, record_id):
        self.query(request)
        def operation():
            data = reads.record_reference(request.user, request.content_scope, record_id)
            expected = dict(data)
            self.private_guard = lambda: reads.record_reference(request.user, request.content_scope, record_id) == expected
            return data
        return self.execute(request, operation)


class CreditsEntityView(CreditsView):
    def get(self, request, kind, person_id):
        self.query(request)
        return self.execute(request, lambda: reads.entity_header(request.user, request.content_scope, kind, person_id))


class CreditsWorksView(CreditsView):
    def get(self, request, kind, person_id):
        params = self.query_params(request)
        # Cursor alone inherits its canonical conditions; explicitly supplied
        # conditions must match exactly, never silently override signed filters.
        if set(params) == {'cursor'} and len(params.getlist('cursor')) == 1:
            body = reads.load_cursor(params['cursor'])
            params = {**{k: str(v).lower() if type(v) is bool else v for k, v in body['conditions'].items() if v is not None},
                      'cursor': params['cursor']}
        check = WorksQuery(data=params)
        check.is_valid(raise_exception=True)
        session = _token('recommendations.credits.session', [str(request.auth or ''),
            getattr(getattr(request, 'session', None), 'session_key', None)])
        def operation():
            data = reads.works(request.user, request.content_scope, kind, person_id, check.validated_data, session)
            expected = reads.load_cursor(data['context'])['revision']
            self.private_guard = lambda: reads.live_revision(request.user, request.content_scope, session)[0] == expected
            return data
        return self.execute(request, operation)
