"""9C complete scoped matrix on dedicated loopback PostgreSQL; no Neon tests."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_recommendation_8a import main, SUITES

if __name__ == '__main__':
    sys.exit(main(suites=(*SUITES, 'recommendations.tests.test_feedback_closure',
                         'recommendations.tests.test_feedback_deployment_guard',
                         'recommendations.tests.test_browsing', 'recommendations.tests.test_browse_deployment_guard',
                         'recommendations.tests.test_browse_closure',
                         'recommendations.tests.test_browse_closure_deployment_guard'),
                  scope='9c-rollback-cache-concurrency-isolated-postgresql', output_name='acceptance-9c',
                  extra_race_class='FeedbackBoundaryConcurrencyTests', expected_extra_races=6))
