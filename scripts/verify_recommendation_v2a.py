"""V2-A observational noninterference + V1/9C regression, loopback PG ONLY."""
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
        'recommendations.tests.test_recall_diagnostics'),
        scope='v2a-full-pipeline-zero-side-effect-isolated-postgresql', output_name='acceptance-v2a',
        extra_race_class='FeedbackBoundaryConcurrencyTests', expected_extra_races=6))
