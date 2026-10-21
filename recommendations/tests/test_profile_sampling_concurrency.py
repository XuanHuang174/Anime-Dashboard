"""Real row-lock races and additive migration on LOCAL disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
import os
from decimal import Decimal
from threading import Barrier

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

from anime.models import Anime, UserAnimeRecord
from recommendations.models import RecommendationPreference
from recommendations.services import patch_preferences, PreferenceVersionConflict


class LocalPostgresCase(TransactionTestCase):
    def setUp(self):
        self.assertEqual(os.environ.get('DJANGO_SETTINGS_MODULE'),'config.recommendation_pg_test_settings')
        self.assertEqual(connection.vendor,'postgresql')
        self.assertEqual(connection.settings_dict['NAME'],'test_recommendation_isolated_tests')
        self.assertIn(connection.settings_dict['HOST'],('127.0.0.1','localhost','::1'))


@override_settings(RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED=True)
class SamplingLockTests(LocalPostgresCase):
    def setUp(self):
        super().setUp()
        self.owner=get_user_model().objects.create_user(username='sampling-race-owner')
        self.other=get_user_model().objects.create_user(username='sampling-race-other')

    def parallel(self,calls):
        barrier,pids=Barrier(len(calls)),[]
        def run(call):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    pids.append(cursor.fetchone()[0])
                user=get_user_model().objects.get(pk=call[0])
                barrier.wait(timeout=10)
                try:
                    return patch_preferences(user=user,changes=call[1])
                except PreferenceVersionConflict:
                    return {'conflict':True}
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            result=list(pool.map(run,calls))
        self.assertEqual(len(set(pids)),len(calls))
        return result

    def test_six_first_saves_one_row_one_revision_and_five_conflicts(self):
        changes={'profile_min_score':Decimal('7.0'),'expected_version':0}
        results=self.parallel([(self.owner.pk,changes)]*6)
        self.assertEqual(sum(row.get('conflict',False) for row in results),5)
        self.assertEqual(RecommendationPreference.objects.count(),1)
        saved=RecommendationPreference.objects.get()
        self.assertEqual((saved.version,saved.profile_min_score),(1,Decimal('7.0')))

    def test_tags_and_threshold_stale_concurrent_edits_never_partially_overwrite(self):
        RecommendationPreference.objects.create(user=self.owner,tags=['音乐'],version=1)
        results=self.parallel([(self.owner.pk,{'add_tags':['日常'],'expected_version':1}),
                               (self.owner.pk,{'profile_min_score':Decimal('7.0'),'expected_version':1})])
        self.assertEqual(sum(row.get('conflict',False) for row in results),1)
        saved=RecommendationPreference.objects.get()
        self.assertEqual(saved.version,2)
        self.assertIn((tuple(saved.tags),saved.profile_min_score),
                      ((('音乐',),Decimal('7.0')),(('日常','音乐'),Decimal('8.5'))))

    def test_two_owners_have_independent_versions_and_rows(self):
        changes={'profile_min_score':Decimal('6.0'),'expected_version':0}
        results=self.parallel([(user.pk,changes) for user in (self.owner,self.other)])
        self.assertFalse(any(row.get('conflict') for row in results))
        self.assertEqual(RecommendationPreference.objects.count(),2)
        self.assertEqual(set(RecommendationPreference.objects.values_list('version',flat=True)),{1})


class SamplingMigrationTests(LocalPostgresCase):
    def test_existing_preferences_gain_default_without_touching_journal_fields(self):
        owner=get_user_model().objects.create_user(username='sampling-migration-owner')
        anime=Anime.objects.create(chinese_title='迁移保护作品',base_tags=['音乐'])
        UserAnimeRecord.objects.create(user=owner,anime=anime,personal_score=0,watch_status='on_hold',
            custom_tags=['PRIVATE_UNKNOWN'],review='PRIVATE_REVIEW_PRESERVED')
        original=list(UserAnimeRecord.objects.values())
        before=[('recommendations','0005_recommendation_feedback')]
        after=[('recommendations','0006_preference_profile_sampling')]
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        executor.migrate(before)
        try:
            old=executor.loader.project_state(before).apps.get_model('recommendations','RecommendationPreference')
            record=old.objects.create(user_id=owner.pk,tags=['百合'],version=7)
            original_time=(record.created_at,record.updated_at)
        finally:
            executor=MigrationExecutor(connection)
            executor.migrate(after)
            # Restore ALL current leaves, not just this historical migration.
            # Otherwise later tests lose additive tables (e.g. N2 eligibility).
            executor=MigrationExecutor(connection)
            executor.migrate(latest)
        saved=RecommendationPreference.objects.get(user=owner)
        self.assertEqual((saved.tags,saved.version,saved.profile_min_score),(['百合'],7,Decimal('8.5')))
        self.assertEqual((saved.created_at,saved.updated_at),original_time)
        self.assertEqual(saved.profile_policy_version,'profile-sampling.v1')
        self.assertEqual(list(UserAnimeRecord.objects.values()),original)
        # An old backend process may still insert only the old fields during a
        # rolling deploy. Persistent DB defaults, not just ORM defaults, protect it.
        other=get_user_model().objects.create_user(username='sampling-old-server-owner')
        old.objects.create(user_id=other.pk,tags=['音乐'],version=1)
        rolling=RecommendationPreference.objects.get(user=other)
        self.assertEqual((rolling.profile_min_score,rolling.profile_policy_version),(Decimal('8.5'),'profile-sampling.v1'))
