"""D3 fixed isolated PostgreSQL release pack. No production DB or HTTP."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_recommendation_8a import main, SUITES
from scripts.verify_recommendation_diagnostics import COMPARE_CASES

if __name__ == '__main__':
    sys.exit(main(suites=(*SUITES,
        'recommendations.tests.test_feedback_closure', 'recommendations.tests.test_feedback_deployment_guard',
        'recommendations.tests.test_browsing', 'recommendations.tests.test_browse_deployment_guard',
        'recommendations.tests.test_browse_closure', 'recommendations.tests.test_browse_closure_deployment_guard',
        'recommendations.tests.test_diagnostics', 'recommendations.tests.test_preference_discovery',
        'recommendations.tests.test_comparison.ComparisonPrimitiveTests',
        'recommendations.tests.test_ranking_v2.BalancedPoolTests',
        'recommendations.tests.test_ranking_v2.DifferentiatedRerankTests',
        'recommendations.tests.test_ranking_v2.V2RevisionTests',
        *('recommendations.tests.test_comparison.ComparisonDatabaseTests.' + name for name in COMPARE_CASES),
        'recommendations.tests.test_recall_diagnostics', 'recommendations.tests.test_recall_optimization',
        'recommendations.tests.test_runtime_optimization', 'recommendations.tests.test_v2_execution',
        'recommendations.tests.test_v2_browsing', 'recommendations.tests.test_v2_deployment',
        'recommendations.tests.test_exploration_candidate', 'recommendations.tests.test_protected_exploration',
        'recommendations.tests.test_unused_validation', 'recommendations.tests.test_discovery_candidate',
        'recommendations.tests.test_dual_discovery'),
        scope='v2d3-gated-dispatch-and-rollback-isolated-postgresql', output_name='acceptance-v2d3',
        extra_race_class='FeedbackBoundaryConcurrencyTests', expected_extra_races=6))
