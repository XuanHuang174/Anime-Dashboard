from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPageNumberPagination(PageNumberPagination):
    """Project-wide pagination with a client-selectable, bounded page size."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class UserAnimeRecordPagination(StandardPageNumberPagination):
    """Allow an explicitly bounded, unpaginated response for private records."""

    all_page_size_value = "all"
    max_all_size = 2000

    def paginate_queryset(self, queryset, request, view=None):
        requested_size = request.query_params.get(self.page_size_query_param, "")
        self._all_mode = requested_size.strip().lower() == self.all_page_size_value

        if not self._all_mode:
            return super().paginate_queryset(queryset, request, view=view)

        # Read one extra row so callers can be told whether the hard safety cap
        # truncated the result without issuing a separate COUNT(*) query.
        records = list(queryset[: self.max_all_size + 1])
        self._all_was_truncated = len(records) > self.max_all_size
        return records[: self.max_all_size]

    def get_paginated_response(self, data):
        if not getattr(self, "_all_mode", False):
            return super().get_paginated_response(data)

        return Response(
            data,
            headers={
                "X-Result-Limit": str(self.max_all_size),
                "X-Result-Truncated": str(self._all_was_truncated).lower(),
            },
        )
