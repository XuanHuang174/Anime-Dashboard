"""8A fail-closed PostgreSQL acceptance. No production access or external HTTP."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_recommendation_7c import SUITES as CORE7_SUITES, validate_target

EVIDENCE_V1_CASES = (
    'test_additive_typed_contract_and_all_categories_share_detail_evidence',
    'test_explicit_and_absent_evidence_still_have_honest_versions',
    'test_unchanged_requests_are_identical_and_metadata_never_enters_ranking',
    'test_rating_status_and_private_feature_changes_reject_old_card_without_leaking_new_reason',
    'test_preference_revision_changes_even_if_the_displayed_source_is_unchanged',
    'test_idf_and_selection_rule_revisions_invalidate_old_evidence_and_pagination',
    'test_service_refuses_foreign_or_unmarked_context_and_empty_list_has_version_metadata',
    'test_tokens_are_bound_to_authenticated_user_and_candidate_not_record_ids',
    'test_candidate_changes_including_direct_update_invalidate_evidence',
    'test_authentication_query_validation_and_safety_fail_closed',
    'test_detail_never_calls_the_ranking_engine_and_batch_safety_has_no_n_plus_one',
    'test_gets_have_no_db_writes_or_review_queries_and_leave_all_records_intact',
    'test_source_reclassified_during_explanation_is_rejected_before_serialization',
)
SUITES = ('recommendations.tests.test_feedback_api', 'recommendations.tests.test_feedback_concurrency',
          'recommendations.tests.test_preference_loop_api',
          *('recommendations.tests.test_evidence_contract.EvidenceContractTests.' + name for name in EVIDENCE_V1_CASES),
          *CORE7_SUITES)


def main():
    report = {'scope': '8a-feedback-isolated-postgresql', 'production_access': False,
              'external_http': False, 'algorithm': 'content.v1', 'suites': list(SUITES)}
    start = time.perf_counter()
    try:
        validate_target(os.environ.get('RECOMMENDATION_TEST_DATABASE_URL', '').strip())
        os.environ['DJANGO_SETTINGS_MODULE'] = 'config.recommendation_pg_test_settings'
        os.environ['RECOMMENDATION_ALGORITHM_VERSION'] = 'content.v1'
        import django
        django.setup()
        from django.db import connection
        from django.test.runner import DiscoverRunner
        if connection.vendor != 'postgresql':
            raise ValueError('PostgreSQL required; no fallback.')

        class Result(unittest.TextTestResult):
            def addSuccess(self, test):
                super().addSuccess(test)
                report.setdefault('passed_cases', []).append(test.id())

        class TextRunner(unittest.TextTestRunner):
            resultclass = Result

        class Runner(DiscoverRunner):
            test_runner = TextRunner

            def run_suite(self, suite, **kwargs):
                result = super().run_suite(suite, **kwargs)
                report.update(tests_run=result.testsRun,
                    failed_cases=[case.id() for case, _ in result.failures],
                    error_cases=[case.id() for case, _ in result.errors],
                    skipped_cases=[case.id() for case, _ in result.skipped])
                return result

        with patch('requests.sessions.Session.request', side_effect=AssertionError('External HTTP forbidden')):
            failures = Runner(verbosity=2, interactive=False, keepdb=False).run_tests(SUITES)
        feedback_races = sum('.FeedbackConcurrencyTests.' in name for name in report.get('passed_cases', []))
        want_races = sum('.WantConcurrencyTests.' in name for name in report.get('passed_cases', []))
        report.update(feedback_concurrency_passed=feedback_races, want_concurrency_passed=want_races)
        report['result'] = 'passed' if not failures and not report['skipped_cases'] and feedback_races == 6 and want_races == 4 else 'failed'
    except Exception as exc:
        report.update(result='blocked_or_failed', failure_type=type(exc).__name__)
    report['seconds'] = round(time.perf_counter() - start, 3)
    output = ROOT / '.tools/acceptance-8a'
    output.mkdir(parents=True, exist_ok=True)
    filename = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with filename.open('x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps({'result': report['result'], 'report_path': str(filename)}, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
