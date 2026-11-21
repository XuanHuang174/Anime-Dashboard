from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, close_old_connections
from django.test import SimpleTestCase, TransactionTestCase
from rest_framework.test import APITestCase, APIClient

from .models import Anime, UserAnimeRecord, UserProfile, FeaturedSubmission
from .rating_config import default_rating_preferences, legacy_rating_preferences, validate_rating_preferences, normalized_rating_preferences


def integer_preferences():
    fixture = Path(__file__).resolve().parents[1] / 'frontend/tests/fixtures/ratingIntegerContract.json'
    return json.loads(fixture.read_text(encoding='utf-8'))


class RatingConfigurationTests(SimpleTestCase):
    def test_eleven_integer_tiers_validate_and_roundtrip_while_twelfth_is_rejected(self):
        config = integer_preferences()
        validate_rating_preferences(config)
        self.assertEqual(normalized_rating_preferences(config), config)
        self.assertEqual([tier['min'] for tier in config['tiers']], list(range(10, -1, -1)))
        self.assertEqual([tier['stars'] for tier in config['tiers']], list(range(10, -1, -1)))
        old = {**config, 'star_max': 5, 'tiers': [
            {**tier, 'stars': min(tier['stars'], 5)} for tier in config['tiers'] if tier['min'] in (9, 8, 7, 6, 1, 0)]}
        self.assertEqual(normalized_rating_preferences(old), old)
        config['tiers'].insert(1, {**config['tiers'][1], 'min': 9.5})
        with self.assertRaises(ValidationError): validate_rating_preferences(config)

    def test_legacy_default_visual_upgrade_is_read_only_and_preserves_personal_choices(self):
        old = legacy_rating_preferences()
        old['statistics'].update(min=8, label='私人统计')
        old['tiers'][0].update(label='私人名称', stars=3)
        before = deepcopy(old)
        result = normalized_rating_preferences(old)
        self.assertEqual(old, before)
        self.assertEqual(result['tiers'][0]['color'], 'rainbow')
        self.assertEqual(result['tiers'][0]['label'], '私人名称')
        self.assertEqual(result['tiers'][0]['stars'], 3)
        self.assertEqual(result['statistics'], old['statistics'])
        old['tiers'][0]['star_color'] = 'teal'
        self.assertEqual(normalized_rating_preferences(old), {**old, 'display': {'mobile_stars': True, 'detail_stars': True, 'poster_style': 'stars'}})

    def test_previous_default_and_optional_view_settings_are_compatible(self):
        old = default_rating_preferences()
        del old['display']
        old['tiers'][0].update(color='classic', star_color='classic', effect='gradient')
        self.assertEqual(normalized_rating_preferences(old)['tiers'][0]['color'], 'rainbow')
        old['display'] = {'mobile_stars': True, 'detail_stars': True, 'poster_style': 'stars'}
        self.assertEqual(normalized_rating_preferences(old), old)
        for display in ({'mobile_stars': 'yes', 'detail_stars': False, 'poster_style': 'badge'},
                        {'mobile_stars': False, 'detail_stars': False, 'poster_style': 'html'},
                        {'mobile_stars': False, 'poster_style': 'badge'}):
            with self.assertRaises(ValidationError):
                validate_rating_preferences({**default_rating_preferences(), 'display': display})

    def test_default_contract_is_shared_with_frontend_and_new_objects_are_independent(self):
        fixture = Path(__file__).resolve().parents[1] / 'frontend/tests/fixtures/ratingPreferencesContract.json'
        config = default_rating_preferences()
        self.assertEqual(config['tiers'][0]['effect'], 'glow')
        self.assertEqual(config, json.loads(fixture.read_text(encoding='utf-8')))
        validate_rating_preferences(config)
        config['tiers'][0]['label'] = 'changed'
        self.assertEqual(default_rating_preferences()['tiers'][0]['label'], '殿堂')
        config['tiers'][0]['effect'] = 'gradient'
        self.assertEqual(normalized_rating_preferences(config), config)

    def test_non_finite_numbers_and_unsafe_or_ambiguous_settings_fail_closed(self):
        for value in (None, {}, [], {'version': 2}):
            self.assertEqual(normalized_rating_preferences(value), default_rating_preferences())
        for value in (True, '9.5', float('nan'), float('inf'), -1, 11, 9.99, 10 ** 1000):
            config = default_rating_preferences(); config['statistics']['min'] = value
            with self.subTest(value=str(value)[:30]):
                with self.assertRaises(ValidationError): validate_rating_preferences(config)


class RatingPreferencesAPITests(APITestCase):
    url = '/api/rating-preferences/'

    def setUp(self):
        self.owner = get_user_model().objects.create_user('rating-owner')
        self.other = get_user_model().objects.create_user('rating-other')
        self.client.force_authenticate(self.owner)

    def record(self, score, title=None):
        anime = Anime.objects.create(chinese_title=title or f'合成作品{UserAnimeRecord.objects.count()}', airing_period='2020-4', base_tags=['日常'])
        return UserAnimeRecord.objects.create(user=self.owner, anime=anime, personal_score=score, watch_status='completed', review='原长评')

    def save(self, config=None, revision=0):
        return self.client.patch(self.url, {'preferences': config or default_rating_preferences(), 'revision': revision}, format='json')

    def custom(self):
        config = integer_preferences(); config['statistics'].update(label='我的推荐', min=8.0, max=9.0)
        config['tiers'][0].update(label='珍藏', color='teal', star_color='teal', effect='glow')
        config['display'].update(mobile_stars=True, detail_stars=True, poster_style='stars')
        return config

    def test_read_histogram_includes_zero_excludes_other_owners_and_never_exposes_records(self):
        for score in (None, 0, 8, 8, 10): self.record(score)
        UserAnimeRecord.objects.create(user=self.other, anime=Anime.objects.create(chinese_title='其他人的私密作品'), personal_score=9)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(response.data['histogram'], [{'score': None, 'count': 1}, {'score': 0.0, 'count': 1}, {'score': 8.0, 'count': 2}, {'score': 10.0, 'count': 1}])
        self.assertNotIn('作品', json.dumps(response.data, ensure_ascii=False))
        self.assertIn('no-store', response['Cache-Control'])

    def test_legacy_account_default_is_presented_classically_without_writing_on_read(self):
        profile = UserProfile.objects.get(user=self.owner)
        old = legacy_rating_preferences()
        old['statistics'].update(min=8, label='自己的统计')
        profile.rating_preferences = old
        profile.save(update_fields=['rating_preferences'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['preferences']['tiers'][0]['color'], 'rainbow')
        self.assertEqual(response.data['preferences']['statistics'], old['statistics'])
        profile.refresh_from_db()
        self.assertEqual(profile.rating_preferences, old)
        self.assertEqual(profile.rating_revision, 0)

    def test_save_only_updates_own_presentation_and_original_scores_and_snapshots_are_untouched(self):
        record = self.record(Decimal('9.9')); before = UserAnimeRecord.objects.get(pk=record.pk).__dict__.copy()
        config = self.custom(); response = self.save(config)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['revision'], 1)
        self.assertEqual(UserProfile.objects.get(user=self.other).rating_preferences, default_rating_preferences())
        after = UserAnimeRecord.objects.get(pk=record.pk).__dict__
        before.pop('_state'); after.pop('_state'); self.assertEqual(before, after)
        self.assertEqual(Anime.objects.get(pk=record.anime_id).base_tags, ['日常'])
        self.assertEqual(self.client.get(self.url).data['preferences'], config)

    def test_optimistic_revision_and_strict_body_prevent_silent_overwrites(self):
        self.assertEqual(self.save(self.custom()).status_code, 200)
        self.assertEqual(self.save(default_rating_preferences()).status_code, 409)
        self.assertEqual(UserProfile.objects.get(user=self.owner).rating_preferences, self.custom())
        self.assertEqual(self.client.patch(self.url, {'preferences': self.custom(), 'revision': 1, 'user_id': self.other.pk}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.url, {'preferences': self.custom(), 'revision': True}, format='json').status_code, 400)

    def test_invalid_configurations_do_not_modify_profile(self):
        variants = []
        for change in (
            lambda c: c.update(star_max=0), lambda c: c.update(show_stars='yes'),
            lambda c: c['tiers'][0].update(stars=11), lambda c: c['tiers'][0].update(color='url(javascript:bad)'),
            lambda c: c['tiers'][0].update(min=9.5), lambda c: c['tiers'][-1].update(min=.1),
            lambda c: c['tiers'][0].update(effect='html'), lambda c: c['tiers'][0].update(label=''),
            lambda c: c['statistics'].update(min=9, max=8), lambda c: c['statistics'].update(cards=[]),
            lambda c: c['statistics'].update(cards=['average', 'average']), lambda c: c.update(extra='not allowed'),
        ):
            config = default_rating_preferences(); change(config); variants.append(config)
        for config in variants:
            with self.subTest(config=config): self.assertEqual(self.save(config).status_code, 400)
        profile = UserProfile.objects.get(user=self.owner)
        self.assertEqual(profile.rating_revision, 0); self.assertEqual(profile.rating_preferences, default_rating_preferences())

    def test_general_profile_endpoint_cannot_bypass_versioned_save(self):
        self.client.patch('/api/user/profile/', {'rating_preferences': self.custom(), 'rating_revision': 999}, format='json')
        profile = UserProfile.objects.get(user=self.owner)
        self.assertEqual(profile.rating_revision, 0); self.assertEqual(profile.rating_preferences, default_rating_preferences())

    def test_stale_profile_and_admin_edits_cannot_replace_new_rating_configuration(self):
        from types import SimpleNamespace
        from django.contrib.admin import site
        from .admin import UserProfileAdmin
        from .serializers import UserProfileSerializer
        stale = UserProfile.objects.get(user=self.owner)
        stale_admin = UserProfile.objects.get(user=self.owner)
        self.save(self.custom())
        serializer = UserProfileSerializer(stale, data={'nickname': '新昵称'}, partial=True)
        serializer.is_valid(raise_exception=True); serializer.save()
        stale_admin.showcase_subtitle = '后台修改'
        UserProfileAdmin(UserProfile, site).save_model(None, stale_admin, SimpleNamespace(changed_data=['showcase_subtitle']), True)
        profile = UserProfile.objects.get(user=self.owner)
        self.assertEqual(profile.rating_preferences, self.custom()); self.assertEqual(profile.rating_revision, 1)
        self.assertEqual(profile.nickname, '新昵称'); self.assertEqual(profile.showcase_subtitle, '后台修改')

    def test_full_journal_private_and_public_statistics_use_inclusive_custom_range(self):
        for score in (None, 0, 7.9, 8, 9, 9.1, 10): self.record(score)
        self.save(self.custom())
        private = self.client.get('/api/records/stats/?page=2&search=不存在').data
        self.assertEqual(private['masterpiece_count'], 2); self.assertEqual(private['total_count'], 7)
        self.assertEqual(private['rating_revision'], 1)
        profile = UserProfile.objects.get(user=self.owner); profile.public_status = 'approved'; profile.is_public = True; profile.save()
        self.client.force_authenticate(None)
        public = self.client.get('/api/public-journal/users/rating-owner/stats/').data
        self.assertEqual(public['masterpiece_count'], 2); self.assertEqual(public['rating_preferences'], self.custom())
        lobby = self.client.get('/api/public-journal/users/').data
        lobby = lobby.get('results', []) if isinstance(lobby, dict) else lobby
        self.assertEqual(lobby[0]['masterpiece_count'], 2); self.assertEqual(lobby[0]['rating_preferences'], self.custom())
        self.assertNotIn('email', lobby[0]); self.assertNotIn('histogram', lobby[0])

    def test_zero_only_range_does_not_include_unrated_and_empty_owner_keeps_own_configuration(self):
        config = self.custom(); config['statistics'].update(min=0, max=0)
        self.save(config); self.assertEqual(self.client.get('/api/records/stats/').data['rating_preferences'], config)
        self.record(None); self.record(0); self.record(.1)
        self.assertEqual(self.client.get('/api/records/stats/').data['masterpiece_count'], 1)

    def test_featured_style_is_server_snapshot_and_stays_stable_after_author_changes(self):
        record = self.record(8.5)
        config = self.custom(); self.save(config)
        response = self.client.post('/api/featured-submissions/submit/', {'anime_id': record.anime_id, 'rating_preferences': default_rating_preferences()}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        snapshot = FeaturedSubmission.objects.get(user=self.owner)
        self.assertEqual(snapshot.rating_preferences, config)
        self.save(default_rating_preferences(), revision=1)
        snapshot.refresh_from_db(); self.assertEqual(snapshot.rating_preferences, config)
        snapshot.audit_status = 'approved'; snapshot.save(update_fields=['audit_status'])
        self.client.force_authenticate(None)
        response = self.client.get(f'/api/featured/{snapshot.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['rating_preferences'], config)

    def test_json_backup_roundtrip_old_backup_compatibility_and_bad_settings_rollback(self):
        record = self.record(8.5); config = self.custom(); self.save(config)
        backup = json.loads(self.client.get('/api/export/').content)
        self.assertEqual(backup['rating_preferences'], config)
        self.assertNotIn('rating_revision', backup)
        self.save(default_rating_preferences(), revision=1)
        response = self.client.post('/api/import/', backup, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.get(self.url).data['preferences'], config)
        legacy_settings = deepcopy(backup)
        legacy_settings['rating_preferences'].pop('display')
        self.assertEqual(self.client.post('/api/import/', legacy_settings, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url).data['preferences'], config)
        old = deepcopy(backup); old.pop('rating_preferences')
        self.assertEqual(self.client.post('/api/import/', old, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url).data['preferences'], config)
        before = list(UserAnimeRecord.objects.filter(user=self.owner).values())
        bad = deepcopy(backup); bad['rating_preferences']['statistics']['min'] = 11
        self.assertEqual(self.client.post('/api/import/', bad, format='json').status_code, 400)
        self.assertEqual(list(UserAnimeRecord.objects.filter(user=self.owner).values()), before)
        # A later transaction failure must roll back the presentation update too.
        previous_revision = self.client.get(self.url).data['revision']
        with patch('anime.views.Anime.objects.get_or_create', side_effect=RuntimeError('synthetic import failure')):
            changed = deepcopy(backup); changed['rating_preferences'] = default_rating_preferences()
            with self.assertLogs('anime.views', level='ERROR'):
                self.assertEqual(self.client.post('/api/import/', changed, format='json').status_code, 400)
        self.assertEqual(self.client.get(self.url).data['preferences'], config)
        self.assertEqual(self.client.get(self.url).data['revision'], previous_revision)

    def test_anonymous_cannot_access_private_configuration_or_unapproved_journal(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        self.assertIn(self.save().status_code, (401, 403))
        self.assertEqual(self.client.get('/api/public-journal/users/rating-owner/stats/').status_code, 404)


class RatingPreferencesConcurrencyTests(TransactionTestCase):
    def test_two_tabs_with_same_revision_only_one_can_save(self):
        if connection.vendor != 'postgresql': self.skipTest('Requires real PostgreSQL row locks')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user = get_user_model().objects.create_user('rating-concurrent'); barrier = Barrier(2)
        def save(index):
            close_old_connections()
            try:
                client = APIClient(); client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                config = default_rating_preferences(); config['statistics']['label'] = f'配置{index}'
                barrier.wait(timeout=10)
                return client.patch('/api/rating-preferences/', {'preferences': config, 'revision': 0}, format='json').status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: outcomes = list(pool.map(save, (1, 2)))
        self.assertEqual(sorted(outcomes), [200, 409]); self.assertEqual(UserProfile.objects.get(user=user).rating_revision, 1)
