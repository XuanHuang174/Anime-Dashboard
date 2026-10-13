"""4B read-only classification. HTTP failures and filtered results are NOT profile states."""

COLD_START_VERSION = 'recommendations.cold-start.v1'


def cold_start_contract(extracted, *, preference_tags, minimum):
    stats, diagnostics = extracted['record_stats'], extracted['diagnostics']
    eligible, usable = extracted['eligible_sample_count'], extracted['usable_sample_count']
    missing_tags = eligible - usable
    reasons = []
    if usable >= minimum:
        code = 'ready'
    elif stats['journal_record_count'] == 0:
        code = 'empty_journal'
    elif stats['rated_record_count'] == 0:
        code = 'all_unrated'
    elif diagnostics['high_score_record_count'] == 0:
        # A high rating in watching/plan is not a completed high-score sample.
        code = 'no_qualifying_high_scores'
    else:
        # Multiple blockers may coexist. Identity first, then genres, then quantity.
        if diagnostics['pending_identity_count']:
            reasons.append('pending_identity')
        if missing_tags:
            reasons.append('missing_genres')
        if 0 < usable < minimum:
            reasons.append('insufficient_samples')
        if diagnostics['unavailable_identity_count']:
            reasons.append('unavailable_samples')
        code = reasons[0] if reasons else 'insufficient_samples'
    if not reasons:
        reasons = [code]
    return {
        'contract_version': COLD_START_VERSION,
        'code': code, 'reasons': reasons, 'is_cold_start': usable < minimum,
        'recommendation_basis': ('personalized' if usable >= minimum
                                 else 'explicit_preferences' if preference_tags else 'none'),
        'minimum_samples': minimum, 'explicit_preference_count': len(preference_tags),
        'journal_record_count': stats['journal_record_count'],
        'rated_record_count': stats['rated_record_count'],
        'unrated_record_count': stats['unrated_record_count'],
        'high_score_record_count': diagnostics['high_score_record_count'],
        'eligible_sample_count': eligible, 'usable_sample_count': usable,
        'pending_identity_count': diagnostics['pending_identity_count'],
        'missing_genre_count': missing_tags,
        'unavailable_sample_count': diagnostics['unavailable_identity_count'],
    }
