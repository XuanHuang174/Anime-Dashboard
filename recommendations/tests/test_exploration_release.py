"""11F: canary, rollback and session isolation using synthetic local accounts."""
from django.test import TransactionTestCase, SimpleTestCase, override_settings
from recommendations.exploration_contract import enabled, ExplorationDisabled
from recommendations import exploration_browsing as explore
from scripts.prepare_recommendation_exploration_rollout import environment_patch
from . import test_exploration_browsing as prior


class ExplorationRolloutConfigTests(SimpleTestCase):
    def test_release_patches_only_touch_exploration_and_always_rotate_epoch(self):
        patches = [environment_patch('canary', ['XHcode']), environment_patch('all'), environment_patch('off')]
        self.assertEqual(len({row['RECOMMENDATION_EXPLORATION_EPOCH'] for row in patches}), 3)
        for row in patches:
            self.assertEqual(set(row), {'RECOMMENDATION_EXPLORATION_ENABLED', 'RECOMMENDATION_EXPLORATION_CANARY_USERS', 'RECOMMENDATION_EXPLORATION_EPOCH'})
        self.assertEqual(patches[0]['RECOMMENDATION_EXPLORATION_CANARY_USERS'], 'XHcode')
        self.assertEqual(patches[1]['RECOMMENDATION_EXPLORATION_CANARY_USERS'], '')
        self.assertEqual(patches[2]['RECOMMENDATION_EXPLORATION_ENABLED'], 'False')
        for mode, users in [('canary', []), ('all', ['XHcode']), ('off', ['XHcode']), ('bad', []), ('canary', [' a']), ('canary', ['a,b'])]:
            with self.assertRaises(ValueError):
                environment_patch(mode, users)

    @override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True)
    def test_malformed_canary_never_expands_to_all_and_literal_true_is_required(self):
        for users in (None, 'XHcode', [None], ['']):
            with self.settings(RECOMMENDATION_EXPLORATION_CANARY_USERS=users):
                self.assertFalse(enabled())
        with self.settings(RECOMMENDATION_EXPLORATION_ENABLED='True', RECOMMENDATION_EXPLORATION_CANARY_USERS=[]):
            self.assertFalse(enabled())


@override_settings(RECOMMENDATION_EXPLORATION_ENABLED=True, RECOMMENDATION_EXPLORATION_CANARY_USERS=['11b-owner'],
                   RECOMMENDATION_EXPLORATION_EPOCH='11f', RECOMMENDATION_ALGORITHM_VERSION='content.v1', RECOMMENDATION_V2_BROWSE_ENABLED=False)
class ExplorationReleaseTests(TransactionTestCase):
    setUp = prior.SingleWorkSnapshotTests.setUp
    catalog = prior.SingleWorkSnapshotTests.catalog
    start = prior.SingleWorkSnapshotTests.start
    details = prior.SingleWorkSnapshotTests.details
    next = prior.SingleWorkSnapshotTests.next

    def test_canary_capabilities_match_all_entry_points_and_direct_service(self):
        self.assertTrue(enabled(self.owner)); self.assertFalse(enabled(self.other))
        self.assertTrue(self.client.get('/api/recommendations/capabilities/').data['exploration']['enabled'])
        self.assertTrue(self.client.get('/api/recommendations/explore/contract/').data['capabilities']['enabled'])
        first = self.start().data
        self.client.force_authenticate(self.other)
        caps = self.client.get('/api/recommendations/capabilities/')
        self.assertEqual(caps.status_code, 200)
        self.assertEqual([row['available'] for row in caps.data['exploration']['navigation']], [True, False, False])
        for path, params in [('contract/', {}), ('catalog/', {'q': '1'}), ('options/', {}),
                ('', {'intent': 'tags', 'include_tags': '音乐'}), ('', {'cursor': first['next_page_cursor']}),
                ('context/', {'snapshot_context': first['snapshot_context']}),
                (f'{first["results"][0]["bangumi_id"]}/', {'detail_context': first['results'][0]['detail_context']})]:
            response = self.client.get('/api/recommendations/explore/' + path, params)
            self.assertEqual(response.status_code, 503, path)
            self.assertEqual(response.data['code'], 'exploration_disabled')
        with self.assertRaises(ExplorationDisabled):
            explore.browse_page(self.other, {}, scope_context={})
        # Existing personal entry remains enabled; no canary-list disclosure.
        self.assertTrue(caps.data['browse_enabled'])
        self.assertNotIn('11b-owner', str(caps.data))

    def test_expanding_canary_or_rotating_epoch_invalidates_old_contexts(self):
        for change in ({'RECOMMENDATION_EXPLORATION_CANARY_USERS': []}, {'RECOMMENDATION_EXPLORATION_EPOCH': 'new-11f'}):
            first = self.start().data
            with self.settings(**change):
                self.assertEqual(self.next(first['next_page_cursor']).status_code, 409)
                self.assertEqual(self.details(first['results'][0]).status_code, 409)
                self.assertEqual(self.client.get('/api/recommendations/explore/context/', {'snapshot_context': first['snapshot_context']}).status_code, 409)
                self.assertEqual(self.start().status_code, 200)

    def test_rollback_is_independent_and_old_cursors_cannot_resurrect_after_release(self):
        first = self.start().data
        with self.settings(RECOMMENDATION_EXPLORATION_ENABLED=False, RECOMMENDATION_EXPLORATION_EPOCH='off-11f'):
            self.assertEqual(self.start().status_code, 503)
            caps = self.client.get('/api/recommendations/capabilities/').data
            self.assertTrue(caps['browse_enabled']); self.assertEqual(caps['algorithm_version'], 'content.v1')
            self.assertFalse(caps['exploration']['enabled'])
            self.assertEqual(self.client.get('/api/recommendations/', {'browse': 'true'}).status_code, 200)
        with self.settings(RECOMMENDATION_EXPLORATION_EPOCH='reenabled-11f'):
            self.assertEqual(self.next(first['next_page_cursor']).status_code, 409)
            self.assertEqual(self.start().status_code, 200)

    def test_other_user_cannot_consume_either_intent_context_even_when_both_released(self):
        with self.settings(RECOMMENDATION_EXPLORATION_CANARY_USERS=[]):
            for query in ({}, {'intent': 'tags', 'include_tags': '音乐'}):
                self.client.force_authenticate(self.owner)
                response = self.start() if not query else self.client.get('/api/recommendations/explore/', query)
                self.assertEqual(response.status_code, 200, response.data)
                first = response.data
                self.client.force_authenticate(self.other)
                self.assertEqual(self.next(first['next_page_cursor']).status_code, 409)
                self.assertEqual(self.details(first['results'][0]).status_code, 409)
        self.client.force_authenticate(None)
        self.assertEqual(self.start().status_code, 401)
