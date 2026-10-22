"""Conservative content boundary, not an automatic age/content classifier."""
import re
import unicodedata

from django.conf import settings

GENERAL_SCOPE_POLICY = 'content-scope.general-adult.v1'
SESSION_CONTRACT = 'recommendations.eligibility.session.v1'
SESSION_POLICY = 'eligibility.session.v1'

# Known exclusions are not overridden by an administrative approval.
# Only identities are retained; do not fetch, display or redistribute the media.
PROHIBITED_IDENTITIES = frozenset((184740, 500544))
RISK_TERMS = ('萝莉', '幼女', '幼儿', '儿童', '小学生', '中学生', '未成年',
              'loli', 'shota', 'ロリ', 'ショタ', '幼児', 'underage')


def self_service_enabled():
    return getattr(settings, 'RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED', False) is True


def general_adult_boundary(anime):
    """Negative guard ONLY. Passing does not certify adult-only content.

    A separate current review under the general-adult policy is also mandatory.
    No guesses from titles, descriptions, journal tags or reviews.
    """
    if (anime.nsfw is not True or type(anime.bangumi_id) is not int or anime.bangumi_id <= 0
            or anime.bangumi_id in PROHIBITED_IDENTITIES):
        return False
    tags = unicodedata.normalize('NFKC', anime.tags or '').casefold()
    return not any(term in tag for tag in re.split(r'[,，]', tags) for term in RISK_TERMS)
