"""8C aggregate acceptance: explicit local PostgreSQL, no SQLite/Neon/HTTP."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_recommendation_8a import main, SUITES

if __name__ == '__main__':
    sys.exit(main(suites=(*SUITES, 'recommendations.tests.test_feedback_closure',
                         'recommendations.tests.test_feedback_deployment_guard'),
        scope='8c-feedback-closure-isolated-postgresql', output_name='acceptance-8c',
        extra_race_class='FeedbackBoundaryConcurrencyTests', expected_extra_races=6))
