"""Synthetic local PostgreSQL only: owner publication and anonymous visibility."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from anime.models import Anime, UserAnimeRecord
from anime.serializers import UserAnimeRecordSerializer
from anime.showcase_policy import publication_content
from anime.showcase_services import PUBLICATION_SALT, _publication_state
from anime.test_showcase_visibility import require_local_postgres
from recommendations import content_review
from recommendations.models import RecommendationAnime, RecommendationContentReview as Review, RecommendationPreference
from recommendations.serving_control import change_control


@override_settings(PUBLIC_SHOWCASE_USERNAME='XHcode', RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True,
    RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True, RECOMMENDATION_ALGORITHM_VERSION='content.v1',
    RECOMMENDATION_V2_BROWSE_ENABLED=False)
class ShowcaseAdultPublicationTests(APITestCase):
    def setUp(self):
        require_local_postgres()
        self.owner = get_user_model().objects.create_user(username='XHcode')
        self.other = get_user_model().objects.create_user(username='synthetic-visitor')
        self.manager = get_user_model().objects.create_superuser(username='synthetic-reviewer', password=None)
        self.catalog = RecommendationAnime.objects.create(bangumi_id=91010, title_zh='合成一般成人条目',
            title_jp='Synthetic adult work', season='2010-01', nsfw=True, tags='音乐', score=8)
        self.shared = Anime.objects.create(bgm_id=91010, chinese_title='合成一般成人条目',
            airing_period='2010-01', is_active=False)
        self.record = UserAnimeRecord.objects.create(user=self.owner, anime=self.shared,
            personal_score=0, review='合成主理人公开长评', custom_tags=['音乐'])
        UserAnimeRecord.objects.create(user=self.other, anime=self.shared, personal_score=9, review='其他用户私人内容')
        self.list_url = reverse('anime_api:user-anime-record-showcase-publication')
        self.post_url = reverse('anime_api:user-anime-record-publish-showcase', args=[self.record.pk])
        self.showcase_url = reverse('anime_api:showcase-list')
        self.client.force_authenticate(self.owner)

    def approve(self, catalog=None, days=0):
        catalog = catalog or self.catalog
        row = Review.objects.filter(anime=catalog).first()
        return content_review.mutate_review(actor=self.manager, bangumi_id=catalog.bangumi_id,
            action='approve', changes={'expected_version': row.review_version if row else 0,
                'expected_catalog_revision': content_review.catalog_revision(catalog),
                'expected_policy_version': content_review.POLICY_VERSION, 'operation_id': uuid4(),
                'reason': 'Synthetic general adult verification', 'evidence_reference': 'synthetic-publication',
                'exclusions_confirmed': True, 'general_adult_confirmed': True, 'days': days})

    def item(self):
        return next(item for item in self.client.get(self.list_url).data['results'] if item['record_id'] == self.record.pk)

    def confirm(self, ticket=None):
        return self.client.post(self.post_url, {'ticket': ticket or self.item()['ticket'], 'confirm_publication': True}, format='json')

    def revoke(self):
        row = Review.objects.get(anime=self.catalog)
        content_review.mutate_review(actor=self.manager, bangumi_id=self.catalog.bangumi_id, action='revoke',
            changes={'expected_version': row.review_version, 'expected_catalog_revision': content_review.catalog_revision(self.catalog),
                'expected_policy_version': content_review.POLICY_VERSION, 'operation_id': uuid4(), 'reason': 'Synthetic revoke'})

    def test_current_review_allows_explicit_owner_publication_and_anonymous_search(self):
        blocked = self.item()
        self.assertFalse(blocked['requires_confirmation']); self.assertIsNone(blocked['ticket'])
        self.assertEqual(blocked['code'], 'showcase_review_required')
        self.approve()
        item = self.item()
        self.assertTrue(item['requires_confirmation']); self.assertEqual(item['content_rating'], 'adult')
        records = list(UserAnimeRecord.objects.order_by('pk').values())
        self.assertEqual(self.confirm(item['ticket']).data['status'], 'published')
        self.client.force_authenticate(None)
        public = self.client.get(self.showcase_url, {'search': '合成一般成人条目'})
        self.assertEqual(public.status_code, 200); self.assertEqual(public.data['count'], 1)
        self.assertIn('no-store', public['Cache-Control'])
        self.assertNotIn('其他用户私人内容', str(public.data))
        library = self.client.get(reverse('anime_api:anime-list'), {'search': '合成一般成人条目'})
        self.assertEqual(library.data['count'], 1)
        self.assertEqual(self.client.get(reverse('anime_api:showcase-stats')).data['total_count'], 1)
        self.assertIn('音乐', self.client.get(reverse('anime_api:showcase-filter-options')).data['tags'])
        self.assertEqual(list(UserAnimeRecord.objects.order_by('pk').values()), records)

    def test_review_renewal_invalidates_old_confirmation_without_overwriting_private_data(self):
        self.approve(); ticket = self.item()['ticket']
        self.approve()
        self.assertEqual(self.confirm(ticket).status_code, 409)
        self.shared.refresh_from_db(); self.assertFalse(self.shared.is_active)
        self.assertEqual(self.confirm().status_code, 200)

    def test_revocation_expiry_or_metadata_change_hides_already_published_rows(self):
        for change in ('revoke', 'expire', 'metadata'):
            with self.subTest(change=change):
                self.client.force_authenticate(self.owner)
                self.approve(days=1)
                self.shared.refresh_from_db()
                if not self.shared.is_active:
                    self.assertEqual(self.confirm().status_code, 200)
                if change == 'revoke':
                    self.revoke()
                elif change == 'expire':
                    Review.objects.filter(anime=self.catalog).update(reviewed_at=timezone.now()-timedelta(days=2), expires_at=timezone.now()-timedelta(days=1))
                else:
                    self.catalog.synopsis = 'Changed public fixture'; self.catalog.save(update_fields=['synopsis'])
                self.client.force_authenticate(None)
                self.assertEqual(self.client.get(self.showcase_url).data['count'], 0)
                self.assertEqual(self.client.get(reverse('anime_api:anime-list')).data['count'], 0)
                self.assertEqual(self.client.get(reverse('anime_api:anime-detail', args=[self.shared.pk])).status_code, 404)
                self.assertEqual(self.client.get(reverse('anime_api:showcase-stats')).data['total_count'], 0)
                self.assertEqual(self.client.get(reverse('anime_api:showcase-filter-options')).data['tags'], [])

    def test_journal_add_publishes_reviewed_private_work_without_touching_other_owner(self):
        catalog = RecommendationAnime.objects.create(bangumi_id=91013, title_zh='合成手账新增',
            title_jp='Synthetic journal add', nsfw=True, tags='音乐', season='2026-10')
        self.approve(catalog)
        shared = Anime.objects.create(bgm_id=catalog.bangumi_id, chinese_title=catalog.title_zh,
            japanese_title=catalog.title_jp, airing_period=catalog.season, is_active=False,
            hidden_reason=Anime.HiddenReason.PRIVATE_ONLY)
        private = UserAnimeRecord.objects.create(user=self.other, anime=shared, review='完全保留的私人内容')
        before = UserAnimeRecord.objects.filter(pk=private.pk).values().get()
        response = self.client.post(reverse('anime_api:user-anime-record-add'), {'anime_data': {
            'chinese_title': catalog.title_zh, 'japanese_title': catalog.title_jp,
            'airing_period': catalog.season, 'base_tags': ['音乐']}}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['showcase_publication']['status'], 'published')
        shared.refresh_from_db(); self.assertTrue(shared.is_active)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=private.pk).values().get(), before)

    def test_known_content_exclusion_cannot_be_overridden_by_admin_approval_or_safe_marker(self):
        self.catalog.bangumi_id = 8019; self.catalog.save(update_fields=['bangumi_id'])
        self.shared.bgm_id = 8019; self.shared.save(update_fields=['bgm_id'])
        Review.objects.create(anime=self.catalog, status='approved', review_version=1,
            reviewed_at=timezone.now(), never_expires=True, expires_at=None, exclusions_confirmed=True,
            evidence_reference='synthetic-untrusted-approval', policy_version=content_review.POLICY_VERSION,
            catalog_revision=content_review.catalog_revision(self.catalog))
        item = self.item()
        self.assertFalse(item['requires_confirmation']); self.assertEqual(item['code'], 'showcase_content_excluded')
        ticket = signing.dumps(_publication_state(self.record), salt=PUBLICATION_SALT)
        self.assertEqual(self.confirm(ticket).status_code, 403)
        self.catalog.nsfw = False; self.catalog.save(update_fields=['nsfw'])
        self.shared.is_active = True; self.shared.save(update_fields=['is_active'])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.showcase_url).data['count'], 0)

    def test_owner_recommendation_add_publishes_reviewed_new_work_but_other_user_remains_private(self):
        change_control(self.manager, {'enabled': True, 'expected_version': 0, 'operation_id': str(uuid4()),
            'reason': 'Synthetic serving test', 'confirmed': True})
        for user, identity in ((self.owner, 91011), (self.other, 91012)):
            catalog = RecommendationAnime.objects.create(bangumi_id=identity, title_zh=f'合成新增 {identity}',
                nsfw=True, tags='音乐', season='2026-10', status_type='OVA', score=8)
            self.approve(catalog)
            RecommendationPreference.objects.get_or_create(user=user, defaults={'tags': ['音乐']})
            self.client.force_authenticate(user)
            result = self.client.post(reverse('recommendations:want', args=[identity]) + '?content_scope=include_adult', {}, format='json')
            self.assertEqual(result.status_code, 201, result.data)
            shared = Anime.objects.get(bgm_id=identity)
            self.assertEqual(shared.is_active, user == self.owner)
            if user == self.owner:
                self.assertEqual(result.data['showcase_publication']['status'], 'published')
            else:
                self.assertNotIn('showcase_publication', result.data)

    def test_unknown_catalog_row_with_null_identity_does_not_hide_normal_manual_works(self):
        RecommendationAnime.objects.create(title_zh='Synthetic unclassified row', nsfw=None)
        shared = Anime.objects.create(chinese_title='正常手工作品', airing_period='2026-10', is_active=True)
        UserAnimeRecord.objects.create(user=self.owner, anime=shared)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.showcase_url).data['count'], 1)

    def test_revocation_during_serialization_discards_public_payload(self):
        self.approve(); self.assertEqual(self.confirm().status_code, 200)
        self.client.force_authenticate(None)
        original = UserAnimeRecordSerializer.to_representation
        def revoke_after_read(serializer, record):
            data = original(serializer, record)
            self.revoke()
            return data
        with patch.object(UserAnimeRecordSerializer, 'to_representation', revoke_after_read):
            response = self.client.get(self.showcase_url)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('results', response.data)
