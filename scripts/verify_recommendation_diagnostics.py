"""Current-baseline diagnostics regression; independent local PostgreSQL ONLY."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_recommendation_8a import main, SUITES

COMPARE_CASES = (
    'test_two_pass_matrix_same_accounts_same_snapshot_no_reviews_no_writes',
    'test_unlinked_unsafe_empty_private_and_saved_zero_cannot_forge_four_samples',
    'test_legacy_cold_sample_remains_in_cohort_and_unpaired_folds_are_reported',
    'test_version_controller_clones_context_and_rule_mismatch_is_rejected',
    'test_versioned_caches_separate_and_targeted_clear_does_not_clear_other_version',
    'test_command_requires_explicit_scope_and_authorization_never_scans_all_users',
    'test_ambiguous_email_or_three_samples_rejected_without_repair',
    'test_no_approval_fails_closed_but_v1_rollback_needs_no_approval',
)

if __name__ == '__main__':
    sys.exit(main(suites=(*SUITES,
        'recommendations.tests.test_feedback_closure', 'recommendations.tests.test_feedback_deployment_guard',
        'recommendations.tests.test_diagnostics',
        'recommendations.tests.test_comparison.ComparisonPrimitiveTests',
        'recommendations.tests.test_ranking_v2.BalancedPoolTests',
        'recommendations.tests.test_ranking_v2.DifferentiatedRerankTests',
        *('recommendations.tests.test_comparison.ComparisonDatabaseTests.' + name for name in COMPARE_CASES)),
        scope='current-baseline-diagnostics-isolated-postgresql', output_name='acceptance-v2-diagnostics',
        extra_race_class='FeedbackBoundaryConcurrencyTests', expected_extra_races=6))
