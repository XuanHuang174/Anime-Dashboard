"""Bounded explicit confirmations. Independent transactions, no metadata writes."""
from django.db import DatabaseError
from rest_framework.exceptions import NotFound

from .binding import bind_record, IdentityConflict

CONTRACT = 'recommendations.identity-batch.v1'


def confirm_identity_batch(user, data, *, scope_context=None):
    results = []
    for item in data['items']:
        result = {'record_id': item['record_id'], 'bangumi_id': item['bangumi_id']}
        try:
            # bind_record owns its transaction/locks. A failed item rolls back only
            # itself; earlier committed rows remain acknowledged and retry-safe.
            linked = bind_record(record_id=item['record_id'], user_id=user.pk,
                target_id=item['bangumi_id'], source='user_confirmed',
                expected_version=item['expected_version'], ticket=item['ticket'], batch_id=data['batch_id'], scope_context=scope_context)
            result.update(outcome='success', code=linked['status'], version=linked['version'])
        except IdentityConflict:
            result.update(outcome='needs_review', code='identity_changed',
                          message='记录或候选已变化或凭证已过期，请重新搜索候选并核对。')
        except NotFound:
            # No disclosure of another user's record or unsafe catalog metadata.
            result.update(outcome='needs_review', code='unavailable',
                          message='记录或安全候选不可用，请重新读取核对。')
        except DatabaseError:
            result.update(outcome='retryable', code='temporarily_unavailable',
                          message='本条关联暂未确认，可重试原批次。')
        results.append(result)
    succeeded = sum(row['outcome'] == 'success' for row in results)
    return {'contract_version': CONTRACT, 'batch_id': str(data['batch_id']),
            'processed_count': len(results), 'success_count': succeeded,
            'failed_count': len(results) - succeeded, 'results': results}
