"""Public rating ranges; ambiguous raw zero is not a confirmed low rating."""


def matches_score(score, options):
    upper = float(options.get('max_score', 10))
    return (score >= float(options.get('min_score', 0)) and score <= upper
            and (upper >= 10 or score > 0))


def score_query(query, options):
    query = query.filter(score__gte=options.get('min_score', 0))
    if float(options.get('max_score', 10)) < 10:
        query = query.filter(score__gt=0, score__lte=options['max_score'])
    return query
