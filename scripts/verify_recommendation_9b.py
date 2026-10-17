"""9B compatibility and audit-guard regression: independent PostgreSQL only."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_recommendation_8a import main, SUITES

if __name__ == '__main__':
    sys.exit(main(suites=(*SUITES, 'recommendations.tests.test_feedback_closure',
                         'recommendations.tests.test_feedback_deployment_guard',
                         'recommendations.tests.test_browsing', 'recommendations.tests.test_browse_deployment_guard'),
                  scope='9b-browse-compatibility-isolated-postgresql', output_name='acceptance-9b',
                  extra_race_class='FeedbackBoundaryConcurrencyTests', expected_extra_races=6))
