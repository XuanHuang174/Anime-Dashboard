"""No live requests: verify 9C audit cannot manufacture deployment acceptance."""
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from scripts.verify_recommendation_browse_closure_deployment import BrowseClosureDeploymentAudit, main


class BrowseClosureDeploymentGuardTests(SimpleTestCase):
    def test_rollout_helper_always_pins_v1_and_rotates_epoch_without_side_effects(self):
        from scripts.prepare_recommendation_browse_rollout import environment_patch
        off, on = environment_patch(False), environment_patch(True)
        self.assertEqual(off['RECOMMENDATION_BROWSE_ENABLED'], 'False')
        self.assertEqual(on['RECOMMENDATION_BROWSE_ENABLED'], 'True')
        self.assertEqual(off['RECOMMENDATION_ALGORITHM_VERSION'], 'content.v1')
        self.assertEqual(on['RECOMMENDATION_ALGORITHM_VERSION'], 'content.v1')
        self.assertNotEqual(off['RECOMMENDATION_BROWSE_EPOCH'], on['RECOMMENDATION_BROWSE_EPOCH'])

    def test_confirmation_required_before_constructing_client(self):
        with patch('scripts.verify_recommendation_browse_closure_deployment.BrowseClosureDeploymentAudit') as client:
            with self.assertRaises(SystemExit):
                main(['--expected-account', 'synthetic@example.invalid'])
        client.assert_not_called()

    def test_pre9c_404_is_only_a_baseline_not_a_completed_acceptance(self):
        audit = BrowseClosureDeploymentAudit(session=Mock())
        with patch.object(audit, 'request', return_value=Mock(status_code=404)):
            audit.capability_checks(allow_pre9c=True)
            self.assertFalse(audit.report['deployment_9c_verified'])
            with self.assertRaises(AssertionError):
                audit.capability_checks(allow_pre9c=False)

    def test_ordinary_errors_are_not_reported_as_a_successful_rolling_deployment(self):
        audit = BrowseClosureDeploymentAudit(session=Mock())
        for status in (401, 429, 500, 503):
            with patch.object(audit, 'request', return_value=Mock(status_code=status)):
                with self.assertRaises(AssertionError):
                    audit.capability_checks(allow_pre9c=True)
        self.assertFalse(audit.report['deployment_9c_verified'])
