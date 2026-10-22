"""Presentation adapter tests; PostgreSQL synthetic data only."""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from recommendations.eligibility import own_eligibility
from recommendations.models import RecommendationContentEligibility, RecommendationEligibilityEvent
from recommendations.scope_control import scope_control_contract


class ScopeControlTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(connection.settings_dict['NAME'], 'test_recommendation_isolated_tests')
        self.user = get_user_model().objects.create_user(username='synthetic-scope-switch')

    def test_descriptor_never_upgrades_permission_or_defaults(self):
        for available, serving in [(False, False), (False, True), (True, False), ('true', True)]:
            self.assertFalse(scope_control_contract(available=available, serving_enabled=serving, revision='x')['available'])
        control = scope_control_contract(available=True, serving_enabled=True, revision='x')
        self.assertTrue(control['available'])
        self.assertEqual(control['selection_mode'], 'session_draft')
        self.assertEqual(control['default_scope'], 'safe')

    def test_own_read_exposes_control_without_applying_or_creating_records(self):
        with CaptureQueriesContext(connection) as queries:
            data = own_eligibility(self.user)
        self.assertEqual(data['scope_control']['revision'], data['eligibility_revision'])
        self.assertEqual(data['scope_control']['available'], data['adult_access_allowed'])
        self.assertFalse(data['scope_control']['available'])
        self.assertFalse(RecommendationContentEligibility.objects.exists())
        self.assertFalse(RecommendationEligibilityEvent.objects.exists())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))

    def test_authenticated_endpoint_is_read_only_and_does_not_accept_other_owner(self):
        client = APIClient()
        self.assertIn(client.get('/api/recommendations/eligibility/').status_code, (401, 403))
        client.force_authenticate(self.user)
        response = client.get('/api/recommendations/eligibility/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['scope_control']['default_scope'], 'safe')
        self.assertEqual(client.get('/api/recommendations/eligibility/', {'user_id': 999}).status_code, 400)
        self.assertFalse(RecommendationContentEligibility.objects.exists())
