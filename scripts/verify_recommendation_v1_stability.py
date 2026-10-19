"""Pinned V1 user-journey regression. Dedicated loopback PostgreSQL ONLY.

No V2 quality tuning, production data, external HTTP or environment mutation in
the application. These overrides affect this isolated child process only.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_recommendation_8a import main, SUITES

STABLE_SUITES = (*SUITES,
    'recommendations.tests.test_feedback_closure', 'recommendations.tests.test_feedback_deployment_guard',
    'recommendations.tests.test_browsing', 'recommendations.tests.test_browse_deployment_guard',
    'recommendations.tests.test_browse_closure', 'recommendations.tests.test_browse_closure_deployment_guard',
    'recommendations.tests.test_extraction_api', 'recommendations.tests.test_cold_start_api',
    'recommendations.tests.test_legacy_identity', 'recommendations.tests.test_state_loop',
    'recommendations.tests.test_v1_stability',
    'recommendations.tests.test_recall_diagnostics.RecallConfigurationTests.test_original_engine_ast_is_frozen_except_explicit_observation_hooks')

if __name__ == '__main__':
    os.environ['RECOMMENDATION_V2_BROWSE_ENABLED'] = 'False'
    os.environ['RECOMMENDATION_BROWSE_ENABLED'] = 'True'
    sys.exit(main(suites=STABLE_SUITES, scope='stable-v1-core-1-9-isolated-postgresql',
        output_name='acceptance-v1-stability', extra_race_class='FeedbackBoundaryConcurrencyTests', expected_extra_races=6))
