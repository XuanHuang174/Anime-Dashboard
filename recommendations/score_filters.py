"""Public rating ranges; ambiguous raw zero is not a confirmed low rating."""


def matches_score(score, options):
    upper = float(options.get('max_score', 10))
    within_upper = score < upper if options.get('max_score_exclusive', False) else score <= upper
    return (score >= float(options.get('min_score', 0)) and within_upper
            and (upper >= 10 or score > 0))


def score_query(query, options):
    query = query.filter(score__gte=options.get('min_score', 0))
    if float(options.get('max_score', 10)) < 10:
        lookup = 'score__lt' if options.get('max_score_exclusive', False) else 'score__lte'
        query = query.filter(score__gt=0, **{lookup: options['max_score']})
    return query
