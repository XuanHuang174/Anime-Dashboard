"""6A request-local presentation contract; never feeds back into ranking scores.

The ranker's legacy float arithmetic remains frozen. Public explanations use its
existing evidence selector with sorted arithmetic, for both list and detail.
"""
from copy import deepcopy
import hashlib
import hmac
import json

from django.conf import settings

from .features import FEATURE_RULE_VERSION
from .identity import safe_catalog
from .taxonomy import TAXONOMY_VERSION
from .profile_policy import PROFILE_POLICY_VERSION, context_score

EVIDENCE_CONTRACT_VERSION = 'recommendations.evidence.v1'
SELECTION_RULE_VERSION = 'association-evidence.v1'
EVIDENCE_DISCLAIMER = '共有题材是关联证据，不代表唯一排序原因，也不代表你喜欢这部作品的概率。'


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      default=str, allow_nan=False).encode('utf-8')


def _token(domain, value):
    return hmac.new(settings.SECRET_KEY.encode(), domain.encode() + b'\0' + _bytes(value), hashlib.sha256).hexdigest()


class EvidencePresenter:
    """One authenticated user's immutable effective inputs; no shared private cache."""
    def __init__(self, user, context, weights, algorithm, *, execution_scope=None):
        from .engine import FEATURE_VERSION, ALGORITHM_VERSION, EXPERIMENTAL_ALGORITHM_VERSION
        from .ranking_v2 import POLICY_VERSION
        if algorithm not in (ALGORITHM_VERSION, EXPERIMENTAL_ALGORITHM_VERSION):
            raise ValueError('Unsupported evidence algorithm')
        if not user.is_authenticated:
            from rest_framework.exceptions import NotAuthenticated
            raise NotAuthenticated()
        if context.get('_owner_id') != user.pk:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('推荐画像不属于当前登录用户。')
        # Strip ranker's request-local memo; presentation never mutates it.
        self.context = deepcopy({key: value for key, value in context.items()
                                 if key in ('profile', 'preferences', 'seeds', 'owned', 'mode', 'taxonomy_version')})
        self.weights, self.owner = dict(weights), user.pk
        self.user = user
        self.feedback_version = context.get('feedback', {}).get('version')
        self.patterns = {}
        state = {'owner': user.pk, 'profile': self.context['profile'], 'mode': self.context['mode'],
                 'preferences': self.context['preferences'], 'owned': sorted(self.context['owned'].items()),
                 'seeds': [(seed['bangumi_id'], seed['title'], f'{seed["score"]:.1f}', sorted(seed['tags']),
                            {tag: sorted(origins) for tag, origins in seed['feature_sources'].items()})
                           for seed in sorted(self.context['seeds'], key=lambda row: row['bangumi_id'])]}
        self.versions = {
            'profile_policy_version': context['profile'].get('sample_policy', {}).get('profile_policy_version', PROFILE_POLICY_VERSION),
            'profile_min_score': f'{context_score(context):.1f}',
            'algorithm_version': algorithm,
            'ranking_policy_version': POLICY_VERSION if algorithm == EXPERIMENTAL_ALGORITHM_VERSION else 'frozen-v1',
            'taxonomy_version': context.get('taxonomy_version', TAXONOMY_VERSION),
            'feature_version': FEATURE_VERSION, 'feature_rule_version': FEATURE_RULE_VERSION,
            'selection_rule_version': SELECTION_RULE_VERSION,
            # An opaque content revision, NOT a persisted counter or public user ID.
            'profile_version': _token('recommendations.profile.evidence.v1', state),
            'preference_version': context['preferences']['version'],
            'feedback_version': self.feedback_version if self.feedback_version is not None else 0,
            'catalog_feature_version': hashlib.sha256(_bytes(self.weights)).hexdigest(),
        }
        if execution_scope is not None:
            from .execution import recipe_descriptor
            recipe = execution_scope['recipe']
            if recipe != recipe_descriptor(serving_enabled=recipe.get('serving_enabled', False)) or algorithm != recipe['algorithm_version']:
                raise ValueError('Evidence execution recipe mismatch')
            self.versions.update({key: recipe[key] for key in ('recipe_revision', 'recall_policy_version',
                'runtime_version', 'control_policy_version')})
            self.versions['browse_context_version'] = execution_scope['browse_revision']

    def ensure_safe(self, animes):
        """Batch source safety + independent feedback revision; no per-card queries."""
        from .engine import RecommendationSnapshotChanged
        if self.feedback_version is not None:
            from .feedback import ensure_version
            ensure_version(self.user, self.feedback_version)
        identities = {anime.bangumi_id for anime in animes}
        identities.update(seed['bangumi_id'] for seed in self.context['seeds'])
        if identities != set(safe_catalog().filter(bangumi_id__in=identities).values_list('bangumi_id', flat=True)):
            raise RecommendationSnapshotChanged('推荐作品或证据来源已变化，请重新加载推荐。')

    def present(self, anime, tags):
        from .engine import evidence_for
        from .public_fingerprint import public_feature_fingerprint
        tags = frozenset(tags)
        if tags not in self.patterns:
            evidence, reason, _ = evidence_for(tags, self.context, self.weights, deterministic=True)
            self.patterns[tags] = (evidence, reason)
        evidence, reason = deepcopy(self.patterns[tags])
        if evidence:
            evidence.update(contract_version=EVIDENCE_CONTRACT_VERSION, versions=dict(self.versions),
                            feature_rule_version=FEATURE_RULE_VERSION,
                            interpretation='shared_genre_association', is_probability=False,
                            is_exhaustive_ranking_explanation=False)
            evidence.setdefault('shared_tag_sources', {})
        # Bind the candidate's CURRENT feature input, identity and public score as
        # well as evidence/owner/version. Titles are not used to infer identity.
        state = {'owner': self.owner, 'versions': self.versions, 'contract': EVIDENCE_CONTRACT_VERSION,
                 'candidate': [anime.bangumi_id, anime.score, anime.status_type, anime.season,
                               public_feature_fingerprint({'tags': anime.tags, 'title_zh': anime.title_zh,
                                                           'title_jp': anime.title_jp})],
                 'tags': sorted(tags), 'evidence': evidence, 'reason': reason}
        return {'reason': reason, 'evidence': evidence, 'evidence_context': dict(self.versions),
                'evidence_token': _token('recommendations.candidate.evidence.v1', state),
                'evidence_disclaimer': EVIDENCE_DISCLAIMER}
