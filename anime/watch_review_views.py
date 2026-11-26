from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .watch_review import read_review


class WatchReviewView(APIView):
    permission_classes = (IsAuthenticated,)
    entries = False

    def get(self, request):
        return Response(read_review(request.user, request.query_params, request, entries=self.entries),
                        headers={'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'})

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        from django.utils.cache import patch_vary_headers
        patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response


class WatchReviewEntriesView(WatchReviewView):
    entries = True
