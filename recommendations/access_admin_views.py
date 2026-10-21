"""Module 3. Superuser-only, signed confirmation, same M1 mutation service."""
import uuid
from django.core import signing
from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle, ScopedRateThrottle
from .views import OwnRecommendationAPIView
from .models import RecommendationContentEligibility
from .eligibility import own_eligibility, mutate_eligibility, EligibilityConflict
from .serving_control import require_superuser, control_state, change_control

SALT = 'recommendations.module3.confirm.m2.v1'


class AccessAdminView(OwnRecommendationAPIView):
    throttle_classes = (UserRateThrottle, ScopedRateThrottle)
    throttle_scope = 'recommendation_write'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_superuser(request.user)


class AccessControlView(AccessAdminView):
    def get(self, request):
        self.check_query_contract(request)
        return Response(control_state(admin=True))

    def post(self, request):
        self.check_query_contract(request)
        return Response(change_control(request.user, request.data))


class AccessApplicationsView(AccessAdminView):
    def get(self, request):
        query = request.query_params
        if set(query) - {'status', 'page'} or any(len(query.getlist(k)) != 1 for k in query):
            raise ValidationError('包含未知或重复参数。')
        status = query.get('status', 'pending')
        if status not in ('pending', 'active', 'rejected', 'revoked', 'all'):
            raise ValidationError('未知审批状态。')
        try:
            page = int(query.get('page', '1'))
            if page < 1 or page > 100000:
                raise ValueError()
        except ValueError:
            raise ValidationError('分页参数无效。') from None
        rows = RecommendationContentEligibility.objects.filter(authorization_source='manual').select_related('user')
        if status != 'all':
            rows = rows.filter(status=status)
        count = rows.count()
        results = [{'user_id': r.user_id, 'username': r.user.get_username(), 'status': r.status,
            'version': r.eligibility_version, 'requested_at': r.requested_at, 'valid_until': r.expires_at}
            for r in rows.order_by('-requested_at', '-pk')[(page-1)*20:page*20]]
        return Response({'count': count, 'page': page, 'next_page': page+1 if page*20 < count else None, 'results': results})


class AccessApplicationView(AccessAdminView):
    def get(self, request, user_id):
        self.check_query_contract(request)
        subject = get_user_model().objects.filter(pk=user_id).first()
        if subject is None:
            raise NotFound()
        contract = own_eligibility(subject)
        ticket = signing.dumps({'actor': request.user.pk, 'subject': user_id,
            'version': contract['eligibility_version'], 'operation': str(uuid.uuid4())}, salt=SALT)
        return Response({'username': subject.get_username(), 'eligibility': contract, 'ticket': ticket})

    def post(self, request, user_id):
        self.check_query_contract(request)
        data = request.data
        if not isinstance(data, dict) or set(data) - {'ticket', 'action', 'reason', 'days', 'public_message', 'confirmed'}:
            raise ValidationError('审批字段无效。')
        if data.get('confirmed') is not True or data.get('action') not in ('approve', 'reject', 'revoke'):
            raise ValidationError('请确认本次审批操作。')
        try:
            ticket = signing.loads(data.get('ticket', ''), salt=SALT, max_age=900)
            if ticket['actor'] != request.user.pk or ticket['subject'] != user_id:
                raise ValueError()
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            raise EligibilityConflict('approval_ticket_invalid', '审批凭据已失效，请重新读取。') from None
        changes = {k: data[k] for k in ('reason', 'days', 'public_message') if k in data}
        changes.update(expected_version=ticket['version'], operation_id=ticket['operation'])
        return Response(mutate_eligibility(actor=request.user, subject_id=user_id, action=data['action'], changes=changes))
