"""General-adult path: synthetic local PostgreSQL ONLY, no production catalog."""
from uuid import uuid4
from datetime import timedelta

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from anime.models import UserAnimeRecord
from recommendations import content_review
from recommendations.adult_policy import GENERAL_SCOPE_POLICY, PROHIBITED_IDENTITIES
from recommendations.content_scope import require_scope, release_snapshot
from recommendations.eligibility import own_eligibility, mutate_eligibility
from recommendations.models import (RecommendationAnime, RecommendationContentReview as Review,
    RecommendationContentEligibility as Eligibility, RecommendationEligibilityEvent as EligibilityEvent,
    RecommendationPreference, UserRecommendationFeedback as Feedback)
from recommendations.tests import test_controlled_serving_m2 as controlled
from recommendations.tests import test_controlled_release_m3 as drills


@override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True,
    RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True, RECOMMENDATION_ALGORITHM_VERSION='content.v1',
    RECOMMENDATION_V2_BROWSE_ENABLED=False)
class GeneralAdultScopeTests(TransactionTestCase):
    setUp = controlled.ControlledServingTests.setUp
    control = controlled.ControlledServingTests.control
    review = controlled.ControlledServingTests.review
    get = controlled.ControlledServingTests.get
    waiting_operation = drills.ControlledReleaseDrills.waiting_operation
    revoke_review = drills.ControlledReleaseDrills.revoke_review
    test_waiting_want_observes_work_revocation_after_catalog_lock = drills.ControlledReleaseDrills.test_waiting_want_observes_work_revocation_after_catalog_lock
    test_final_get_rechecks_safety_after_repeatable_read_snapshot = controlled.ControlledServingTests.test_final_get_rechecks_safety_after_repeatable_read_snapshot
    test_pre400_scope_and_all_styles_real_association = controlled.ControlledServingTests.test_pre400_scope_and_all_styles_real_association
    test_concurrent_adult_want_one_record_and_feedback_do_not_overwrite = controlled.ControlledServingTests.test_concurrent_adult_want_one_record_and_feedback_do_not_overwrite

    def enable(self):
        self.review(); self.control()
        return require_scope(self.owner, 'include_adult')

    def review_payload(self, work):
        anime = RecommendationAnime.objects.get(bangumi_id=work)
        row = Review.objects.filter(anime=anime).first()
        return {'expected_version': row.review_version if row else 0,
            'expected_catalog_revision': content_review.catalog_revision(anime),
            'expected_policy_version': content_review.POLICY_VERSION, 'operation_id': uuid4(),
            'reason': 'Isolated synthetic adult-only test', 'evidence_reference': 'synthetic-only',
            'exclusions_confirmed': True, 'general_adult_confirmed': True}

    def test_opt_in_never_needs_or_creates_account_approval(self):
        self.enable()
        with CaptureQueriesContext(connection) as queries:
            data = own_eligibility(self.owner)
        self.assertEqual(data['contract_version'], 'recommendations.eligibility.session.v1')
        self.assertFalse(data['can_apply']); self.assertFalse(data['manual_authorization_valid'])
        self.assertTrue(data['adult_access_allowed']); self.assertTrue(data['scope_control']['available'])
        self.assertEqual(data['age_assurance_status'], 'not_performed')
        self.assertEqual(require_scope(self.owner, 'include_adult')['content_scope_policy_version'], GENERAL_SCOPE_POLICY)
        self.assertEqual(require_scope(self.owner, 'include_adult')['eligibility_version'], 0)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in queries))
        self.assertFalse(Eligibility.objects.exists()); self.assertFalse(EligibilityEvent.objects.exists())
        self.client.force_authenticate(self.other)
        self.assertEqual(self.get('90/').status_code, 200)

    def test_application_endpoint_cannot_create_unneeded_grants(self):
        for action, actor in (('apply', self.owner), ('approve', self.manager)):
            with self.assertRaises(ValidationError):
                mutate_eligibility(actor=actor, subject_id=self.owner.pk, action=action, changes={
                    'expected_version': 0, 'operation_id': uuid4(), 'confirmed': True})
        self.assertFalse(Eligibility.objects.exists()); self.assertFalse(EligibilityEvent.objects.exists())

    def test_old_account_grant_changes_do_not_gate_or_rebind_session_scope(self):
        self.enable()
        before = require_scope(self.owner, 'include_adult')
        with override_settings(RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=False):
            controlled.ControlledServingTests.change(self)
            controlled.ControlledServingTests.change(self, action='approve', version=1)
        self.assertEqual(before, require_scope(self.owner, 'include_adult'))
        grant = Eligibility.objects.get(user=self.owner)
        now = timezone.now()
        Eligibility.objects.filter(pk=grant.pk).update(granted_at=now-timedelta(days=2), expires_at=now-timedelta(days=1))
        self.assertEqual(before, require_scope(self.owner, 'include_adult'))
        self.assertTrue(own_eligibility(self.owner)['adult_access_allowed'])
        with CaptureQueriesContext(connection) as queries:
            require_scope(self.owner, 'include_adult')
        self.assertFalse(any('recommendationcontenteligibility' in q['sql'].lower() for q in queries))

    def test_missing_review_service_closed_unknown_or_inactive_account_stay_closed(self):
        self.control()
        self.assertFalse(own_eligibility(self.owner)['adult_access_allowed'])
        self.assertEqual(self.get().status_code, 403)
        self.review(); self.assertEqual(self.get('90/').status_code, 200)
        self.assertEqual(self.get('91/').status_code, 404)
        self.control(False); self.assertEqual(self.get().status_code, 403)
        self.control(True)
        self.owner.is_active = False; self.owner.save(update_fields=['is_active'])
        self.assertFalse(own_eligibility(self.owner)['adult_access_allowed'])
        self.assertEqual(self.get().status_code, 403)

    def test_legacy_review_is_not_implicitly_recertified(self):
        self.enable()
        Review.objects.filter(anime__bangumi_id=90).update(policy_version='release-review.ledger.closed.v1')
        self.assertEqual(release_snapshot()[0], set())
        self.assertEqual(self.get().status_code, 403)
        self.assertFalse(own_eligibility(self.owner)['adult_access_allowed'])
        self.assertEqual(Review.objects.get().policy_version, 'release-review.ledger.closed.v1')

    def test_explicit_confirmation_is_required_and_strings_cannot_impersonate_true(self):
        for field in ('general_adult_confirmed', 'exclusions_confirmed'):
            for invalid in (False, 'true', 1, None):
                with self.assertRaises(ValidationError):
                    content_review.mutate_review(actor=self.manager, bangumi_id=90, action='approve',
                        changes={**self.review_payload(90), field: invalid})
        self.assertFalse(Review.objects.exists())

    def test_known_exclusion_and_risk_tags_cannot_be_overridden_by_review(self):
        for identity in PROHIBITED_IDENTITIES:
            RecommendationAnime.objects.create(bangumi_id=identity, title_zh='Synthetic prohibited identity', nsfw=True, tags='音乐')
            with self.assertRaises(ValidationError):
                content_review.mutate_review(actor=self.manager, bangumi_id=identity, action='approve', changes=self.review_payload(identity))
        for tag in ('萝莉', '幼女', '未成年', 'ＬＯＬＩ', 'ショタ'):
            RecommendationAnime.objects.filter(bangumi_id=90).update(tags=f'音乐,{tag}')
            with self.assertRaises(ValidationError):
                content_review.mutate_review(actor=self.manager, bangumi_id=90, action='approve', changes=self.review_payload(90))
        self.assertFalse(Review.objects.exists())

    def test_no_title_or_private_text_is_used_to_guess_general_adult_status(self):
        RecommendationAnime.objects.filter(bangumi_id=90).update(title_zh='成年-only claim is not proof')
        self.control()
        self.assertEqual(self.get().status_code, 403)
        self.assertFalse(Review.objects.exists())

    def test_safe_v1_identical_and_private_fields_untouched(self):
        before = self.client.get('/api/recommendations/', {'page_size':24}).data
        journal = list(UserAnimeRecord.objects.values()); prefs = list(RecommendationPreference.objects.values())
        self.enable()
        after = self.client.get('/api/recommendations/', {'page_size':24}).data
        for key in ('bangumi_id', 'score', 'reason', 'tags', 'watch_status'):
            self.assertEqual([r[key] for r in before['results']], [r[key] for r in after['results']])
        self.assertEqual(self.client.get('/api/recommendations/90/').status_code, 404)
        self.get(); own_eligibility(self.owner)
        self.assertEqual(journal, list(UserAnimeRecord.objects.values()))
        self.assertEqual(prefs, list(RecommendationPreference.objects.values()))
        self.assertFalse(Feedback.objects.exists()); self.assertFalse(Eligibility.objects.exists())

    def test_list_detail_evidence_parity_no_media_and_snapshot_invalidates_on_change(self):
        self.enable()
        page = self.get(browse=True, page_size=2)
        self.assertEqual(page.status_code, 200, page.data)
        card = page.data['results'][0]
        detail = self.get(f'{card["bangumi_id"]}/', evidence_token=card['evidence_token'])
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['evidence'], card['evidence'])
        self.assertEqual(self.get('90/').data['cover_url'], '')
        scope = page.data['content_scope_context']
        self.assertEqual(scope['content_scope_policy_version'], GENERAL_SCOPE_POLICY)
        self.control(False); self.control(True)
        self.assertEqual(self.get(cursor=page.data['next_page_cursor']).status_code, 409)
        self.assertEqual(self.get(f'{card["bangumi_id"]}/', evidence_token=card['evidence_token']).status_code, 409)

    def test_review_expiry_or_risk_reclassification_discards_old_snapshot(self):
        for change in ({'tags':'音乐,萝莉'}, {'nsfw':None}):
            with self.subTest(change=change):
                RecommendationAnime.objects.filter(bangumi_id=90).update(nsfw=True, tags='音乐')
                payload = self.review_payload(90)
                content_review.mutate_review(actor=self.manager, bangumi_id=90, action='approve', changes=payload)
                if self.get().status_code != 200: self.control()
                page = self.get(browse=True, page_size=2).data
                RecommendationAnime.objects.filter(bangumi_id=90).update(**change)
                self.assertEqual(release_snapshot()[0], set())
                self.assertEqual(self.get(cursor=page['next_page_cursor']).status_code, 409)
        RecommendationAnime.objects.filter(bangumi_id=90).update(nsfw=True, tags='音乐')
        content_review.mutate_review(actor=self.manager, bangumi_id=90, action='approve', changes=self.review_payload(90))
        Review.objects.filter(anime__bangumi_id=90).update(expires_at=timezone.now()-timedelta(seconds=1), reviewed_at=timezone.now()-timedelta(days=1))
        self.assertEqual(self.get().status_code, 403)

    def test_mode_change_invalidates_old_cursors_without_mixing_legacy_authorization(self):
        self.enable()
        page = self.get(browse=True, page_size=2).data
        with override_settings(RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=False):
            self.assertEqual(self.get().status_code, 403)
            self.assertEqual(self.get(cursor=page['next_page_cursor']).status_code, 403)
        self.assertFalse(Eligibility.objects.exists())
