"""Shared quick-filter semantics; each condition group is an AND restriction."""
import re
from datetime import date

# Match JavaScript String.trim, including imported BOM/Unicode whitespace.
REVIEW_WHITESPACE = ' \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'


def airing_year(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'([1-9][0-9]{3})(?:[-/](0?[1-9]|1[0-2])(?:[-/](0?[1-9]|[12]\d|3[01]))?|年(0?[1-9]|1[0-2])月)?', value.strip())
    if not match:
        return None
    if match[3]:
        try:
            date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return None
    return int(match[1])


def matches_year_range(period, year_from, year_to):
    if year_from is None and year_to is None:
        return True
    year = airing_year(period)
    return year is not None and (year_from is None or year >= year_from) and (year_to is None or year <= year_to)


def scalar_filters(queryset, config, score_field='personal_score'):
    low, high = config.score_min, config.score_max
    if config.score_state == 'unrated':
        queryset = queryset.filter(**{f'{score_field}__isnull': True})
    elif config.score_state == 'rated' or low is not None or high is not None:
        queryset = queryset.filter(**{f'{score_field}__isnull': False})
    if low is not None: queryset = queryset.filter(**{f'{score_field}__gte': low})
    if high is not None: queryset = queryset.filter(**{f'{score_field}__lte': high})
    if config.watch_statuses: queryset = queryset.filter(watch_status__in=config.watch_statuses)
    state, priority = getattr(config, 'plan_state', 'any'), getattr(config, 'plan_priority', None)
    if state == 'none': queryset = queryset.filter(watch_plan__isnull=True)
    elif state == 'active': queryset = queryset.filter(watch_plan__state__in=('pending', 'ongoing'))
    elif state != 'any': queryset = queryset.filter(watch_plan__state=state)
    if priority is not None: queryset = queryset.filter(watch_plan__priority=priority)
    return queryset


def matches_metadata(config, *, tags, title, japanese_title, period, review):
    if not matches_year_range(period, config.year_from, config.year_to): return False
    has_review = bool((review or '').strip(REVIEW_WHITESPACE))
    if config.review_state == 'with' and not has_review or config.review_state == 'without' and has_review: return False
    titles = [(title or '').upper(), (japanese_title or '').upper()]
    keyword_match = lambda word: any(word.upper() in value for value in titles)
    if any(tag in tags for tag in config.exclude_tags) or any(keyword_match(word) for word in config.exclude_title_keywords): return False
    matches = [tag in tags for tag in config.tags] + [keyword_match(word) for word in config.title_keywords]
    return all(matches) if config.match_mode == 'all' else not matches or any(matches)


def preview_config(data):
    from types import SimpleNamespace
    defaults = dict(tags=[], title_keywords=[], exclude_tags=[], exclude_title_keywords=[], year_from=None, year_to=None,
                    plan_state='any', plan_priority=None, match_mode='any', score_min=None, score_max=None, score_state='any', watch_statuses=[], review_state='any')
    return SimpleNamespace(**{**defaults, **data})
