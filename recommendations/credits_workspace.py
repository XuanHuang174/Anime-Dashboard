"""13E: source-bound details and lossless writes; never recommendation evidence."""
import time
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError
from . import credits_read as reads
from .credits_api_contract import TTL, CreditsChanged, require_access, rollout_revision
from .credits_models import CreditsState
from .content_scope import catalog_for_scope, lock_scope, ensure_scope
from .evidence import _token
from .serializers import RecommendationPublicAnimeSerializer
from .want import add_want

SALT = 'recommendations.credits.action.v1'


def binding(user, scope, session, kind, person):
    return _token(SALT, [user.pk, user.get_session_auth_hash(), session, scope, kind, person,
                        reads.revision(), reads.catalog_revision(), rollout_revision(user)])


def action_context(bound, target, expires):
    # Own journal revision is deliberately absent: a lost POST acknowledgement
    # can be retried after creation without invalidating its membership proof.
    return signing.dumps({'binding': bound, 'target': target, 'expires': expires}, salt=SALT, compress=True)


def member(user, scope, session, kind, person, target, context):
    require_access(user, kind)
    try:
        body = signing.loads(context, salt=SALT, max_age=TTL)
    except signing.SignatureExpired:
        raise CreditsChanged() from None
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError('作品操作上下文无效。') from None
    if (not isinstance(body, dict) or set(body) != {'binding', 'target', 'expires'}
            or type(body['target']) is not int or body['target'] != target
            or type(body['expires']) is not int or not time.time() < body['expires'] <= time.time() + TTL
            or body['binding'] != binding(user, scope, session, kind, person)):
        raise CreditsChanged()
    _, states, excluded = reads.live_revision(user, scope, session)
    if target in excluded:
        raise CreditsChanged()
    facts = reads.valid_facts(scope, kind).filter(source_person_id=person, observation__bangumi_id=target)
    if not facts.exists():
        raise NotFound('作品不在当前可见参与关系中。')
    return states


def detail(user, scope, session, kind, person, target, context):
    states = member(user, scope, session, kind, person, target, context)
    anime = catalog_for_scope(scope).get(bangumi_id=target)
    item = dict(RecommendationPublicAnimeSerializer(anime, context={'content_scope': scope}).data)
    item.update(watch_status=states.get(target), content_rating='adult' if anime.nsfw else 'non_adult')
    return {**reads.base('detail'), 'kind': kind, 'person_id': person, 'bangumi_id': target, 'item': item}


@transaction.atomic
def want(user, scope, session, kind, person, target, context):
    # Same owner-first order as journal writers. Source publications serialize on
    # CreditsState; scope/review and target rows are locked before qualification.
    if not get_user_model().objects.select_for_update().filter(pk=user.pk, is_active=True).exists():
        raise NotFound('账号不可用。')
    if not CreditsState.objects.select_for_update().filter(pk=1).exists():
        raise CreditsChanged()
    locked = lock_scope(user, scope)
    if not catalog_for_scope(locked).select_for_update().filter(bangumi_id=target).exists():
        raise NotFound('当前范围没有可展示的作品。')
    ensure_scope(user, scope)
    member(user, scope, session, kind, person, target, context)
    return add_want(user=user, bangumi_id=target, scope_context=scope)
