from django.db import DatabaseError
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from .series_views import SeriesRelationsView
from . import series_reminders as reminders
from .quality_readonly import readonly_snapshot


class SeriesReminderPresentationView(SeriesRelationsView):
    def initial(self, request, *args, **kwargs):
        self.throttle_scope = 'recommendation_read' if request.method == 'GET' else 'recommendation_write'
        super().initial(request, *args, **kwargs)
        reminders.require_enabled(request.user)
        self.check_query_contract(request)

    def get(self, request):
        with readonly_snapshot():
            return Response(reminders.presentation(request.user))

    def patch(self, request):
        body = request.data
        if (not isinstance(body, dict) or set(body) != {'collapsed', 'expected_version'}
                or type(body['collapsed']) is not bool or type(body['expected_version']) is not int
                or not 0 <= body['expected_version'] <= 2147483647):
            raise ValidationError('展示设置参数无效。')
        return Response(reminders.set_presentation(request.user, request.content_scope, **body))


class SeriesRemindersView(SeriesRelationsView):
    def initial(self, request, *args, **kwargs):
        self.throttle_scope = 'recommendation_read' if request.method == 'GET' else 'recommendation_write'
        super().initial(request, *args, **kwargs)
        reminders.require_enabled(request.user)
        self.check_query_contract(request)

    def get(self, request):
        return Response(reminders.read(request.user, request.content_scope))

    def patch(self, request):
        body = request.data
        extra = {'collapse': {'collapsed'}, 'hide': {'bangumi_id'}, 'restore': set()}
        if (not isinstance(body, dict) or not isinstance(body.get('action'), str) or body['action'] not in extra
                or set(body) != {'action', 'context', 'expected_version'} | extra[body['action']]
                or not isinstance(body['context'], str) or not 1 <= len(body['context']) <= 4096
                or type(body['expected_version']) is not int or not 0 <= body['expected_version'] <= 2147483647
                or body['action'] == 'collapse' and type(body['collapsed']) is not bool
                or body['action'] == 'hide' and (type(body['bangumi_id']) is not int or not 1 <= body['bangumi_id'] <= 2147483647)):
            raise ValidationError('提醒操作参数无效。')
        return Response(reminders.mutate(request.user, request.content_scope, body))

    def finalize_response(self, request, response, *args, **kwargs):
        if request.method == 'GET' and response.status_code < 400:
            try:
                reminders.verify(request.user, request.content_scope, response.data)
            except (APIException, DatabaseError) as exc:
                response = self.handle_exception(exc)
        return super().finalize_response(request, response, *args, **kwargs)
