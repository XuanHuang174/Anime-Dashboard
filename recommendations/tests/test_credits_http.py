"""13C real PostgreSQL, synthetic public archive and own-only HTTP contracts."""
import uuid
import json
from pathlib import Path
from time import perf_counter
import shutil
import subprocess
from django.conf import settings
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from anime.models import Anime, UserAnimeRecord
from recommendations.models import RecommendationAnime, UserRecommendationFeedback, UserRecommendationFeedbackState
from recommendations.binding import bind_record
from recommendations.credits_models import CreditsHead, CreditsState, CreditsSnapshot
from recommendations.credits_source import ArchivePlan
from recommendations.credits_store import publish_batch
from .test_credits_source import CreditsArchiveFixture

ROOT = '/api/recommendations/credits/'


@override_settings(RECOMMENDATION_CREDITS_VOICE_ACTOR_ENABLED=True, RECOMMENDATION_CREDITS_STUDIO_ENABLED=True,
                   RECOMMENDATION_CREDITS_CANARY_USERS=[])
class CreditsHTTPTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user('credits-owner')
        self.other = get_user_model().objects.create_user('credits-other')
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        self.fixture = CreditsArchiveFixture()
        # 27 works: 25 safe, one adult, one unknown. More than two pages.
        self.fixture.rows['subject.jsonlines'] = [{'id': i, 'type': 2, 'name': f'源{i}',
            'nsfw': i == 26 if i != 27 else None} for i in range(1, 28)]
        self.fixture.rows['subject-persons.jsonlines'] = [
            {'subject_id': i, 'person_id': 201, 'position': 67, 'appear_eps': '源备注'} for i in range(1, 28)]
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'目录{i}',
            score=7, nsfw=False if i < 26 else True if i == 26 else None,
            season='2026-01', status_type='TV') for i in range(1, 28)])
        self.publish()

    def tearDown(self): self.fixture.close()

    def publish(self, version='dump-2026-10-20.000000Z', seeds=None):
        ids = seeds or list(range(1, 28))
        checksum = self.fixture.archive(version)
        with ArchivePlan(self.fixture.path, checksum, ids) as plan:
            for start in range(0, len(ids), 25):
                publish_batch(plan.batch(ids[start:start + 25]))

    def get(self, suffix, **params):
        return self.client.get(ROOT + suffix, params)

    def works(self, kind='studio', person=201, **params):
        return self.get(f'entities/{kind}/{person}/works/', **params)

    def record(self, user=None, identity=1, **changes):
        anime, _ = Anime.objects.get_or_create(bgm_id=identity, defaults={
            'chinese_title': f'私人{identity}', 'airing_period': '2026-1'}) if identity else (Anime.objects.create(
                chinese_title='私人未绑定', airing_period='2026-1'), True)
        return UserAnimeRecord.objects.create(user=user or self.owner, anime=anime, **changes)

    def test_search_alias_id_namespace_and_no_outbound_io(self):
        with patch('socket.socket', side_effect=AssertionError('No upstream requests')):
            for q in ('合成动画', 'person:201', 'https://bgm.tv/person/201'):
                response = self.get('search/', kind='studio', q=q)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['results'][0]['person_id'], 201)
                self.assertEqual(response.data['results'][0]['work_count'], 25)
            response = self.get('search/', kind='voice_actor', q='ＳＹＮＴＨＥＴＩＣ   ACTOR')
            self.assertEqual(response.data['results'][0]['person_id'], 101)
        self.assertFalse(self.get('search/', kind='voice_actor', q='person:201').data['results'])
        for q in ('201', 'https://bgm.tv/subject/201', 'https://example.org/person/201', 'x\ny'):
            self.assertEqual(self.get('search/', kind='studio', q=q).status_code, 400)

    def test_auth_default_closed_canary_and_independent_kinds(self):
        self.client.force_authenticate(None)
        self.assertIn(self.works().status_code, (401, 403))
        self.client.force_authenticate(self.owner)
        with override_settings(RECOMMENDATION_CREDITS_STUDIO_ENABLED=False):
            self.assertEqual(self.works().data['code'], 'credits_disabled')
            self.assertEqual(self.works('voice_actor', 101).status_code, 200)
            summary = self.get('subjects/1/').data
            self.assertEqual(summary['sections']['studio']['status'], 'disabled')
        for users in (['credits-other'], 'credits-owner', [None]):
            with override_settings(RECOMMENDATION_CREDITS_CANARY_USERS=users):
                self.assertEqual(self.works().status_code, 503)
        with override_settings(RECOMMENDATION_SERIES_ENABLED=False, RECOMMENDATION_EXPLORATION_ENABLED=False):
            self.assertEqual(self.works().status_code, 200)
        for url in ('subjects/1/', 'entities/nope/201/', 'record/1/'):
            self.assertEqual(self.client.post(ROOT + url, {}, format='json').status_code, 405 if 'nope' not in url else 404)

    def test_stable_pages_ties_and_cursor_only_inherits_conditions(self):
        first = self.works(year_from=2026)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['count'], 25)
        self.assertEqual([r['bangumi_id'] for r in first.data['results']], list(range(1, 13)))
        second = self.works(cursor=first.data['next_cursor'])
        third = self.works(cursor=second.data['next_cursor'])
        self.assertEqual([r['bangumi_id'] for r in second.data['results']], list(range(13, 25)))
        self.assertEqual([r['bangumi_id'] for r in third.data['results']], [25])
        self.assertIsNone(third.data['next_cursor'])
        self.assertEqual(first.data['expires_at'], third.data['expires_at'])
        self.assertEqual(self.works(cursor=first.data['next_cursor'], year_from=2025).status_code, 409)
        self.assertEqual(self.works(cursor=first.data['next_cursor'] + 'x').status_code, 400)

    def test_cursor_binds_account_session_scope_catalog_and_credits(self):
        token = self.works().data['next_cursor']
        with override_settings(RECOMMENDATION_CREDITS_EPOCH='new-release'):
            self.assertEqual(self.works(cursor=token).status_code, 409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.works(cursor=token).status_code, 409)
        self.client.force_authenticate(self.owner, token='new-session')
        self.assertEqual(self.works(cursor=token).status_code, 409)
        self.client.force_authenticate(self.owner)
        RecommendationAnime.objects.filter(bangumi_id=25).update(score=8)
        self.assertEqual(self.works(cursor=token).status_code, 409)
        token = self.works().data['next_cursor']
        CreditsState.objects.filter(pk=1).update(revision=500)
        self.assertEqual(self.works(cursor=token).status_code, 409)

    def test_completed_hide_owned_ambiguous_identity_and_cross_owner(self):
        self.record(identity=1, watch_status='completed', personal_score=9, review='PRIVATE_SENTINEL')
        self.record(identity=2, watch_status='watching')
        self.record(user=self.other, identity=3, watch_status='completed')
        stale = self.record(identity=None, watch_status='completed')
        bind_record(record_id=stale.pk, user_id=self.owner.pk, target_id=4, source='user_confirmed')
        stale.metadata_initialized = True; stale.private_chinese_title = '身份变化'; stale.save()
        self.assertEqual(self.works().data['count'], 22)
        self.assertEqual(self.works(view='completed').status_code, 400)
        completed = self.works(view='completed', hide_recorded='false')
        self.assertEqual([r['bangumi_id'] for r in completed.data['results']], [1])
        self.assertEqual(completed.data['results'][0]['watch_status'], 'completed')
        self.assertNotIn('PRIVATE_SENTINEL', str(completed.data))

    def test_own_record_identity_read_and_cursor_invalidates_after_status_or_feedback(self):
        other = self.record(user=self.other, identity=1)
        own = self.record(identity=2, watch_status='plan')
        self.assertEqual(self.get(f'record/{other.pk}/').status_code, 404)
        response = self.get(f'record/{own.pk}/')
        self.assertEqual(response.data['bangumi_id'], 2)
        token = self.works().data['next_cursor']
        own.watch_status = 'completed'; own.save()
        self.assertEqual(self.works(cursor=token).status_code, 409)
        token = self.works().data['next_cursor']
        UserRecommendationFeedbackState.objects.create(user=self.owner, feedback_version=1)
        UserRecommendationFeedback.objects.create(user=self.owner, bangumi_id=3, is_active=True, feedback_version=1,
                                                   hidden_at=timezone.now())
        self.assertEqual(self.works(cursor=token).status_code, 409)
        self.assertEqual(self.works().data['count'], 23)

    def test_filters_score_unknown_dates_types_and_strict_params(self):
        RecommendationAnime.objects.filter(bangumi_id=1).update(score=0, season='未知')
        RecommendationAnime.objects.filter(bangumi_id=2).update(score=5.9, season='2008-1', status_type='OVA')
        RecommendationAnime.objects.filter(bangumi_id=3).update(score=6, season='2009-10')
        low = self.works(max_score=6, max_score_exclusive='true')
        self.assertEqual([r['bangumi_id'] for r in low.data['results']], [2])
        early = self.works(sort='release_asc')
        self.assertEqual([r['bangumi_id'] for r in early.data['results'][:2]], [2, 3])
        self.assertEqual(early.data['results'][0]['release_period'], '2008-1')
        self.assertEqual(early.data['results'][0]['release_precision'], 'catalog_period')
        self.assertEqual(self.works(year_from=2008, year_to=2008, type='OVA').data['count'], 1)
        for params in ({'user_id': self.other.pk}, {'year_from': '2e3'}, {'year_to': ''}, {'hide_recorded': '1'},
                       {'sort': 'random'}, {'view': 'seen'}, {'min_score': 8, 'max_score': 7}):
            self.assertEqual(self.works(**params).status_code, 400, params)
        self.assertEqual(self.client.get(ROOT + 'search/?kind=studio&q=a&q=b').status_code, 400)
        self.assertEqual(self.client.get(ROOT + 'entities/studio/201/works/?cursor=x&cursor=y').status_code, 400)

    def test_subject_multirole_dedup_company_roles_dubs_and_unknown_role_annotation(self):
        self.fixture.rows['character.jsonlines'].append({'id': 302, 'name': '第二角色', 'role': 1})
        self.fixture.rows['person-characters.jsonlines'] += [
            {'subject_id': 1, 'person_id': 101, 'character_id': 302, 'type': 0, 'summary': '第2集'},
            {'subject_id': 2, 'person_id': 101, 'character_id': 301, 'type': 5, 'summary': ''}]
        self.fixture.rows['subject-characters.jsonlines'] += [
            {'subject_id': 1, 'character_id': 302, 'type': 999, 'order': 1},
            {'subject_id': 2, 'character_id': 301, 'type': 1, 'order': 0}]
        self.fixture.rows['subject-persons.jsonlines'].append({'subject_id': 1, 'person_id': 201, 'position': 42, 'appear_eps': ''})
        self.publish('dump-2026-10-21.000000Z', [1, 2])
        response = self.works('voice_actor', 101)
        self.assertEqual(response.data['count'], 1)
        roles = response.data['results'][0]['evidence']['characters']
        self.assertEqual([r['character_id'] for r in roles], [301, 302])
        self.assertTrue(roles[1]['annotation_warnings'])
        section = self.get('subjects/1/').data['sections']
        self.assertEqual(section['voice_actor']['count'], 1)
        self.assertEqual(section['studio']['results'][0]['evidence']['position_or_cast_type'], 67)

    def test_partial_never_leaks_new_names_or_history_aliases(self):
        self.fixture.rows['person.jsonlines'][0]['name'] = '新版姓名'
        self.fixture.rows['person.jsonlines'][0]['infobox'] = '{{Infobox Crt\n|别名= 新版别名\n}}'
        self.fixture.rows['subject-characters.jsonlines'] = []
        self.publish('dump-2026-10-21.000000Z', [1])
        section = self.get('subjects/1/').data['sections']['voice_actor']
        self.assertEqual(section['status'], 'observed')
        self.assertEqual(section['last_attempt_status'], 'partial')
        self.assertEqual(section['results'][0]['name'], '合成声优')
        self.assertFalse(self.get('search/', kind='voice_actor', q='新版').data['results'])
        self.fixture.rows['subject-characters.jsonlines'] = [{'subject_id': 1, 'character_id': 301, 'type': 1, 'order': 0}]
        self.publish('dump-2026-10-22.000000Z', [1])
        self.assertFalse(self.get('search/', kind='voice_actor', q='Synthetic Actor').data['results'])
        self.assertEqual(self.get('search/', kind='voice_actor', q='新版').data['results'][0]['name'], '新版姓名')

    def test_coverage_empty_partial_not_loaded_stale_and_no_entity_leak(self):
        self.assertEqual(self.get('subjects/2/').data['sections']['voice_actor']['status'], 'empty_observed')
        CreditsHead.objects.filter(bangumi_id=2, kind='studio').update(current=None, last_attempt=None)
        self.assertEqual(self.get('subjects/2/').data['sections']['studio']['status'], 'not_loaded')
        self.fixture.rows['subject-characters.jsonlines'] = []
        CreditsHead.objects.filter(bangumi_id=1, kind='voice_actor').update(current=None)
        self.publish('dump-2026-10-21.000000Z', [1])
        self.assertEqual(self.get('subjects/1/').data['sections']['voice_actor']['status'], 'partial')
        self.assertEqual(self.get('entities/voice_actor/101/').status_code, 404)
        CreditsSnapshot.objects.update(source_at=timezone.now() - timedelta(days=46))
        self.assertEqual(self.get('subjects/1/').data['sections']['studio']['status'], 'stale')
        self.assertFalse(self.get('search/', kind='studio', q='合成').data['results'])

    def test_incompatible_source_versions_and_cross_snapshot_endpoints_are_not_complete(self):
        from recommendations.credits_models import CreditsAssertion
        CreditsSnapshot.objects.update(mapping_version='future-unmapped-version')
        self.assertEqual(self.get('subjects/1/').data['sections']['studio']['status'], 'partial')
        self.assertEqual(self.get('entities/studio/201/').status_code, 404)
        from recommendations.credits_contract import MAPPING_VERSION
        CreditsSnapshot.objects.update(mapping_version=MAPPING_VERSION)
        CreditsAssertion.objects.filter(observation__bangumi_id=1, observation__kind='voice_actor').update(person=None)
        self.assertEqual(self.get('subjects/1/').data['sections']['voice_actor']['status'], 'partial')
        self.assertEqual(self.get('entities/voice_actor/101/').status_code, 404)

    def test_source_catalog_rating_mismatch_unknown_and_invalid_pointer_fail_closed(self):
        self.assertEqual(self.get('subjects/26/').status_code, 404)
        self.assertEqual(self.get('subjects/27/').status_code, 404)
        RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=True)
        self.assertEqual(self.works('voice_actor', 101).status_code, 404)
        RecommendationAnime.objects.filter(bangumi_id=26).update(nsfw=False)
        self.assertEqual(self.get('subjects/26/').data['sections']['studio']['status'], 'partial')
        self.assertEqual(self.works().data['count'], 24)
        wrong = CreditsHead.objects.get(bangumi_id=2, kind='voice_actor').current_id
        CreditsHead.objects.filter(bangumi_id=2, kind='studio').update(current_id=wrong)
        self.assertEqual(self.get('subjects/2/').status_code, 503)

    def test_no_private_scores_reviews_writes_and_bounded_query_count(self):
        self.record(watch_status='completed', personal_score=9, review='PRIVATE_REVIEW')
        with CaptureQueriesContext(connection) as queries:
            result = self.works()
        self.assertEqual(result.status_code, 200, result.data)
        sql = '\n'.join(q['sql'].lower() for q in queries)
        for forbidden in ('personal_score', '"review"', 'insert into', 'update ', 'delete from'):
            self.assertNotIn(forbidden, sql)
        self.assertLessEqual(len(queries), 55)
        self.assertIn('no-store', result['Cache-Control'])
        self.assertIn('Authorization', result['Vary'])
        self.assertFalse(result.data['coverage']['complete_filmography'])

    def test_response_boundary_rechecks_catalog_and_kind_gate(self):
        from recommendations import credits_views
        # Simulate a commit immediately after the independent read-only snapshot.
        original_read = credits_views.reads.read
        def after_read(operation):
            result = original_read(operation)
            RecommendationAnime.objects.filter(bangumi_id=1).update(nsfw=True)
            return result
        with patch.object(credits_views.reads, 'read', side_effect=after_read):
            response = self.works()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'credits_snapshot_changed')

    @override_settings(RECOMMENDATION_RESTRICTED_CONTENT_ENABLED=True, RECOMMENDATION_NSFW_SELF_SERVICE_ENABLED=True)
    def test_adult_review_scope_count_roles_and_revocation(self):
        from recommendations.content_review import mutate_review, catalog_revision, POLICY_VERSION
        from recommendations.serving_control import change_control
        from recommendations.credits_contract import normalize_alias
        from recommendations.credits_models import CreditsPerson, CreditsAlias, CreditsAssertion
        admin = get_user_model().objects.create_superuser('credits-admin', password='synthetic')
        # Company exists exclusively on an adult subject; it must not leak in safe search.
        current = CreditsHead.objects.get(bangumi_id=26, kind='studio').current
        person = CreditsPerson.objects.create(snapshot_id=current.snapshot_id, person_id=202, person_type=2,
            name='成人关联公司', careers=['producer'], alias_status='observed', source_revision='a' * 64)
        alias = normalize_alias(202, person.name)
        CreditsAlias.objects.create(person=person, name=alias['name'], search_key=alias['search_key'])
        CreditsAssertion.objects.create(observation=current, person=person, source_person_id=202,
            source_code=67, source_eligible=True)
        self.assertFalse(self.get('search/', kind='studio', q='成人关联').data['results'])
        self.assertEqual(self.get('entities/studio/202/').status_code, 404)
        self.assertEqual(self.works(content_scope='include_adult').status_code, 403)
        change_control(admin, {'enabled': True, 'expected_version': 0, 'operation_id': str(uuid.uuid4()),
            'reason': 'synthetic', 'confirmed': True})
        anime = RecommendationAnime.objects.get(bangumi_id=26)
        mutate_review(actor=admin, bangumi_id=26, action='approve', changes={'expected_version': 0,
            'expected_catalog_revision': catalog_revision(anime), 'expected_policy_version': POLICY_VERSION,
            'operation_id': uuid.uuid4(), 'reason': 'synthetic', 'evidence_reference': 'synthetic',
            'exclusions_confirmed': True, 'general_adult_confirmed': True, 'days': 30})
        response = self.works(content_scope='include_adult')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 26)
        self.assertEqual(self.get('search/', kind='studio', q='成人关联', content_scope='include_adult').data['results'][0]['work_count'], 1)
        adult = self.works(person=202, content_scope='include_adult').data['results'][0]
        self.assertEqual(adult['cover_url'], '')
        token = response.data['next_cursor']
        self.assertEqual(self.works(cursor=token).status_code, 409)
        with override_settings(RECOMMENDATION_CREDITS_STUDIO_ENABLED=False):
            disabled = self.works(cursor=token, content_scope='include_adult')
            self.assertEqual(disabled.data['code'], 'credits_disabled')
        change_control(admin, {'enabled': False, 'expected_version': 1, 'operation_id': str(uuid.uuid4()),
            'reason': 'synthetic', 'confirmed': True})
        self.assertIn(self.works(cursor=token, content_scope='include_adult').status_code, (403, 409))

    def test_http_reads_cannot_mutate_database_even_accidentally(self):
        from recommendations import credits_views
        def bad(*args, **kwargs):
            CreditsState.objects.filter(pk=1).update(revision=999)
        with patch.object(credits_views.reads, 'entity_header', side_effect=bad):
            self.assertEqual(self.get('entities/studio/201/').status_code, 503)
        self.assertNotEqual(CreditsState.objects.get(pk=1).revision, 999)

    def test_summary_limits_expanded_list_search_collisions_and_search_limit(self):
        for i in range(102, 162):
            self.fixture.rows['person.jsonlines'].append({'id': i, 'name': '同名声优', 'type': 1, 'career': ['seiyu'], 'infobox': ''})
            self.fixture.rows['person-characters.jsonlines'].append(
                {'subject_id': 1, 'person_id': i, 'character_id': 301, 'type': 0, 'summary': ''})
        self.publish('dump-2026-10-21.000000Z', [1])
        summary = self.get('subjects/1/').data['sections']['voice_actor']
        self.assertEqual((summary['count'], len(summary['results']), summary['has_more']), (61, 50, True))
        full = self.get('subjects/1/', expanded='true').data['sections']['voice_actor']
        self.assertEqual(len(full['results']), 61)
        self.assertFalse(full['has_more'])
        search = self.get('search/', kind='voice_actor', q='同名声优').data
        self.assertTrue(search['has_more'])
        self.assertEqual([row['person_id'] for row in search['results']], list(range(102, 117)))

    def test_changed_source_to_empty_and_expiry_invalidate_continuations(self):
        first = self.works()
        with patch('django.core.signing.time.time', return_value=0):
            expired = self.works().data['next_cursor']
        self.assertEqual(self.works(cursor=expired).status_code, 409)
        self.fixture.rows['subject-persons.jsonlines'] = []
        self.publish('dump-2026-10-21.000000Z')
        self.assertEqual(self.works(cursor=first.data['next_cursor']).status_code, 409)
        self.assertEqual(self.works().status_code, 404)

    def test_wire_fixture_and_size_limits_fail_explicitly(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/credits_http_contract.v1.json').read_text(encoding='utf-8'))
        endpoints = [self.get('search/', kind='studio', q='合成'), self.get('subjects/1/'),
            self.get(f'record/{self.record().pk}/'), self.get('entities/studio/201/'), self.works()]
        for response, spec in zip(endpoints, fixture['endpoints']):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['contract_version'], spec['version'])
            self.assertTrue(set(fixture['common_response_fields']) <= set(response.data))
        self.assertTrue(set(fixture['works_response_fields']) <= set(endpoints[-1].data))
        self.assertTrue(set(fixture['result_evidence_fields']) <= set(endpoints[-1].data['results'][0]['evidence']))
        with patch('recommendations.credits_read.MAX_RESPONSE_BYTES', 50):
            failure = self.works()
            self.assertEqual(failure.status_code, 503)
            self.assertEqual(failure.data['code'], 'credits_data_unavailable')
        with patch('recommendations.credits_read.MAX_WORKS', 10):
            self.assertEqual(self.works().status_code, 503)

    def test_actual_http_responses_pass_frontend_13d_validators(self):
        record = self.record()
        payload = {'search': self.get('search/', kind='studio', q='合成').data,
            'summary': self.get('subjects/1/').data, 'full': self.get('subjects/1/', expanded='true').data,
            'record': self.get(f'record/{record.pk}/').data, 'works': self.works().data}
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required for 13D actual-wire contract verification')
        result = subprocess.run([node, str(settings.BASE_DIR / 'frontend/tests/fixtures/verifyCreditsWire.mjs')],
            input=json.dumps(payload), encoding='utf-8', capture_output=True, cwd=settings.BASE_DIR, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '5')

    def test_gate_and_own_state_rechecked_at_response_boundary(self):
        from recommendations import credits_views
        original = credits_views.reads.read
        record = self.record(identity=1, watch_status='plan')
        def after_read(operation):
            result = original(operation)
            record.watch_status = 'completed'; record.save()
            return result
        with patch.object(credits_views.reads, 'read', side_effect=after_read):
            self.assertEqual(self.works().status_code, 409)
        def closing(operation):
            result = original(operation)
            from django.conf import settings
            settings.RECOMMENDATION_CREDITS_STUDIO_ENABLED = False
            return result
        with override_settings(RECOMMENDATION_CREDITS_STUDIO_ENABLED=True):
            with patch.object(credits_views.reads, 'read', side_effect=closing):
                self.assertEqual(self.works().data['code'], 'credits_disabled')

    def test_large_entity_and_50000_catalog_local_http_baseline(self):
        """Synthetic scale receipt, not a Render latency/load/coverage claim."""
        from recommendations.credits_models import (CreditsSubject, CreditsPerson, CreditsCharacter,
            CreditsObservation, CreditsAssertion)
        RecommendationAnime.objects.bulk_create([RecommendationAnime(bangumi_id=i, title_zh=f'合成目录{i}',
            score=7, season='2026-01', status_type='TV', nsfw=False) for i in range(28, 50001)], batch_size=1000)
        head = CreditsHead.objects.get(bangumi_id=1, kind='studio')
        snapshot = head.current.snapshot
        company = CreditsPerson.objects.get(snapshot=snapshot, person_id=201)
        actor = CreditsPerson.objects.get(snapshot=snapshot, person_id=101)
        first = CreditsCharacter.objects.get(snapshot=snapshot, character_id=301)
        second = CreditsCharacter.objects.create(snapshot=snapshot, character_id=302, name='合成第二角色', entity_role=1,
                                                 source_revision='a' * 64)
        ids = range(100, 10100)
        CreditsSubject.objects.bulk_create([CreditsSubject(snapshot=snapshot, bangumi_id=i, subject_type=2,
            name=f'合成源{i}', nsfw=False, source_revision='a' * 64) for i in ids], batch_size=1000)
        observations = CreditsObservation.objects.bulk_create([CreditsObservation(snapshot=snapshot, bangumi_id=i,
            kind=kind, coverage='observed', fingerprint='a' * 64) for i in ids for kind in ('studio', 'voice_actor')], batch_size=1000)
        CreditsHead.objects.bulk_create([CreditsHead(bangumi_id=o.bangumi_id, kind=o.kind, current=o, last_attempt=o)
            for o in observations], batch_size=1000)
        facts = []
        for observation in observations:
            if observation.kind == 'studio':
                facts.append(CreditsAssertion(observation=observation, person=company, source_person_id=201,
                    source_code=67, source_eligible=True))
            else:
                for character in (first, second):
                    facts.append(CreditsAssertion(observation=observation, person=actor, source_person_id=101,
                        character=character, source_character_id=character.character_id, source_code=0,
                        source_eligible=True, role_type=1, source_order=0))
        CreditsAssertion.objects.bulk_create(facts, batch_size=1000)
        receipts = []
        for kind, person in (('studio', 201), ('voice_actor', 101)):
            cursor = None
            for phase in ('cold', 'warm', 'continuation'):
                started = perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    response = self.works(kind, person, **({'cursor': cursor} if phase == 'continuation' else {}))
                seconds = round(perf_counter() - started, 4)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['count'], 10025 if kind == 'studio' else 10001)
                self.assertLessEqual(len(queries), 55)
                self.assertLess(seconds, 10)
                receipts.append({'kind': kind, 'phase': phase, 'seconds': seconds, 'sql': len(queries)})
                cursor = response.data['next_cursor']
        print('13C synthetic 50000 catalog / 30000 credits HTTP baseline: ' + json.dumps(receipts))
