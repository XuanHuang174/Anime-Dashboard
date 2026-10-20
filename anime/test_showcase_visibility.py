"""Synthetic-only integration/row-lock regressions; never run against Neon."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from anime.admin import AnimeAdmin
from anime.models import Anime, UserAnimeRecord, UserAnimeTag
from anime.showcase_services import publish_new_showcase_record, withdraw_showcase_anime
from anime.utils.scraper import MetadataScraper
from anime.views import BangumiCollectionBatchImportView
from recommendations.models import RecommendationAnime
from recommendations.want import add_want


def require_local_postgres():
    assert connection.vendor == "postgresql", "PostgreSQL required, not SQLite"
    assert connection.settings_dict["HOST"] in ("127.0.0.1", "localhost", "::1")
    assert connection.settings_dict["NAME"] == "test_recommendation_isolated_tests"


@skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL")
@override_settings(PUBLIC_SHOWCASE_USERNAME="XHcode")
class ShowcaseVisibilityTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        require_local_postgres()
        super().setUpClass()

    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="XHcode")
        self.other = get_user_model().objects.create_user(username="private-holder")
        self.catalog = RecommendationAnime.objects.create(
            bangumi_id=91001, title_zh="公开衔接测试", title_jp="Public Story",
            season="2026-04", status_type="TV", nsfw=False, tags="日常,音乐",
        )
        self.want_url = reverse("recommendations:want", args=[self.catalog.bangumi_id])
        self.add_url = reverse("anime_api:user-anime-record-add")
        self.showcase_url = reverse("anime_api:showcase-list")

    def private_record(self, reason=Anime.HiddenReason.PRIVATE_ONLY):
        shared = Anime.objects.create(
            bgm_id=self.catalog.bangumi_id, chinese_title=self.catalog.title_zh,
            japanese_title=self.catalog.title_jp, airing_period=self.catalog.season,
            is_active=False, hidden_reason=reason, description="共享原始简介",
        )
        record = UserAnimeRecord.objects.create(
            user=self.other, anime=shared, watch_status="completed", personal_score=Decimal("0.0"),
            review="完整私人长评\n" * 50, watched_eps=12, total_eps=12,
            metadata_initialized=True, private_chinese_title="另一个人的私人标题",
            private_base_tags=[], custom_tags=["二刷", "私人管理标签"],
        )
        return shared, record

    def get_public(self):
        self.client.force_authenticate(None)
        return self.client.get(self.showcase_url, {"search": self.catalog.title_zh})

    def assert_visible_and_addable(self, shared):
        shared.refresh_from_db()
        self.assertTrue(shared.is_active)
        self.assertEqual(shared.hidden_reason, "")
        public = self.get_public()
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.data["count"], 1)
        self.assertEqual(public.data["results"][0]["anime"]["id"], shared.pk)
        self.assertNotIn("hidden_reason", public.data["results"][0]["anime"])
        self.assertNotIn("showcase_publication", public.data["results"][0])
        third = get_user_model().objects.create_user(username="public-search-visitor")
        self.client.force_authenticate(third)
        added = self.client.post(self.add_url, {"anime_id": shared.pk}, format="json")
        self.assertEqual(added.status_code, 201)
        self.assertNotIn("showcase_publication", added.data)

    def manual_add(self):
        self.client.force_authenticate(self.owner)
        return self.client.post(self.add_url, {"anime_data": {
            "chinese_title": self.catalog.title_zh, "japanese_title": self.catalog.title_jp,
            "airing_period": self.catalog.season, "base_tags": ["音乐"],
        }}, format="json")

    def import_batch(self):
        self.client.force_authenticate(self.owner)
        payload = {"total": 1, "data": [{"subject_id": self.catalog.bangumi_id,
            "type": 1, "subject": {"id": self.catalog.bangumi_id,
                "name_cn": self.catalog.title_zh, "name": self.catalog.title_jp}}]}
        with patch.object(MetadataScraper, "get_user_anime_collections", return_value=payload), \
             patch.object(BangumiCollectionBatchImportView, "_fetch_subject_details",
                          return_value=({"airing_period": self.catalog.season}, "")), \
             patch.object(BangumiCollectionBatchImportView, "_ensure_poster_persisted", return_value=""):
            return self.client.post(reverse("anime_api:bangumi-collection-batch"),
                                    {"uid": "synthetic-collection"}, format="json")

    def test_private_first_then_owner_want_publishes_without_private_mutation(self):
        self.client.force_authenticate(self.other)
        first = self.client.post(self.want_url, {}, format="json")
        self.assertEqual(first.status_code, 201)
        shared = Anime.objects.get(bgm_id=self.catalog.bangumi_id)
        self.assertFalse(shared.is_active)
        self.assertEqual(shared.hidden_reason, Anime.HiddenReason.PRIVATE_ONLY)
        private_before = list(UserAnimeRecord.objects.filter(user=self.other).values())
        self.assertEqual(self.get_public().data["count"], 0)
        self.client.force_authenticate(self.owner)
        second = self.client.post(self.want_url, {}, format="json")
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.data["anime_id"], first.data["anime_id"])
        self.assertEqual(second.data["showcase_publication"]["status"], "published")
        self.assertEqual(list(UserAnimeRecord.objects.filter(user=self.other).values()), private_before)
        self.assertEqual(Anime.objects.count(), 1)
        self.assert_visible_and_addable(shared)

    def test_manual_owner_add_reuses_and_publishes_only_visibility_fields(self):
        shared, private = self.private_record()
        UserAnimeTag.objects.create(record=private, name="二刷", color="blue")
        colors_before = list(private.tag_preferences.values())
        before = Anime.objects.filter(pk=shared.pk).values().get()
        private_before = UserAnimeRecord.objects.filter(pk=private.pk).values().get()
        response = self.manual_add()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["showcase_publication"]["status"], "published")
        after = Anime.objects.filter(pk=shared.pk).values().get()
        for key in before.keys() - {"is_active", "hidden_reason", "updated_at"}:
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=private.pk).values().get(), private_before)
        self.assertEqual(list(private.tag_preferences.values()), colors_before)
        self.assertEqual(Anime.objects.count(), 1)
        self.assert_visible_and_addable(shared)

    def test_owner_import_new_record_publishes_existing_private_identity(self):
        shared, private = self.private_record()
        before = UserAnimeRecord.objects.filter(pk=private.pk).values().get()
        response = self.import_batch()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["created"], 1)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=private.pk).values().get(), before)
        self.assert_visible_and_addable(shared)

    def test_new_owner_want_still_public_and_repeated_want_is_write_free(self):
        self.client.force_authenticate(self.owner)
        first = self.client.post(self.want_url, {}, format="json")
        self.assertEqual(first.status_code, 201)
        before = list(UserAnimeRecord.objects.values())
        with CaptureQueriesContext(connection) as queries:
            repeated = self.client.post(self.want_url, {}, format="json")
        self.assertEqual(repeated.status_code, 200)
        self.assertFalse(repeated.data["created"])
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("UPDATE ", "INSERT ", "DELETE ")) for q in queries))
        self.assertEqual(self.get_public().data["count"], 1)

    def test_regular_manual_add_does_not_publish_or_touch_shared_data(self):
        shared, private = self.private_record()
        before = Anime.objects.filter(pk=shared.pk).values().get()
        ordinary = get_user_model().objects.create_user(username="another-private-user")
        self.owner = ordinary  # Exercise the same standard-add payload, not XHcode.
        response = self.manual_add()
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("showcase_publication", response.data)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)
        self.assertEqual(self.get_public().data["count"], 0)

    def test_admin_and_unknown_hidden_rows_require_review_in_all_three_entries(self):
        for reason in (Anime.HiddenReason.ADMIN_RETIRED, Anime.HiddenReason.LEGACY_UNKNOWN):
            for entry in ("manual", "want", "import"):
                with self.subTest(reason=reason, entry=entry):
                    # Keep each case independent without removing any real data.
                    self.catalog.bangumi_id += 1
                    self.catalog.title_zh += "例"
                    self.catalog.save()
                    self.want_url = reverse("recommendations:want", args=[self.catalog.bangumi_id])
                    shared, _ = self.private_record(reason)
                    if entry == "manual":
                        response = self.manual_add()
                    elif entry == "want":
                        self.client.force_authenticate(self.owner)
                        response = self.client.post(self.want_url, {}, format="json")
                    else:
                        response = self.import_batch()
                    self.assertIn(response.status_code, (200, 201))
                    notice = (response.data["showcase_publication_notices"][0] if entry == "import"
                              else response.data["showcase_publication"])
                    self.assertEqual(notice["status"], "review_required")
                    shared.refresh_from_db()
                    self.assertFalse(shared.is_active)
                    self.assertEqual(shared.hidden_reason, reason)
                    self.assertEqual(self.get_public().data["count"], 0)

    def test_owner_delete_and_explicit_readd_restores_with_other_record_unchanged(self):
        shared, other = self.private_record()
        shared.is_active = True
        shared.save(update_fields=["is_active"])
        owner_record = UserAnimeRecord.objects.create(user=self.owner, anime=shared)
        before = UserAnimeRecord.objects.filter(pk=other.pk).values().get()
        owner_record.delete()
        shared.refresh_from_db()
        self.assertEqual(shared.hidden_reason, Anime.HiddenReason.SHOWCASE_REMOVED)
        response = self.manual_add()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(UserAnimeRecord.objects.filter(pk=other.pk).values().get(), before)
        self.assert_visible_and_addable(shared)

    def test_delete_does_not_relabel_existing_admin_ban(self):
        shared, _ = self.private_record(Anime.HiddenReason.ADMIN_RETIRED)
        record = UserAnimeRecord.objects.create(user=self.owner, anime=shared)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        record.delete()
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)

    def test_already_owned_hidden_record_retry_never_republishes_or_changes_fields(self):
        shared, _ = self.private_record()
        record = UserAnimeRecord.objects.create(user=self.owner, anime=shared, personal_score=0,
            review="旧长评", watch_status="on_hold", custom_tags=["年度最佳"])
        before = list(UserAnimeRecord.objects.values())
        shared_before = Anime.objects.filter(pk=shared.pk).values().get()
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.want_url, {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["record_id"], record.pk)
        self.assertEqual(list(UserAnimeRecord.objects.values()), before)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), shared_before)

    def test_search_is_read_only_and_does_not_repair_existing_hidden_rows(self):
        shared, _ = self.private_record()
        UserAnimeRecord.objects.create(user=self.owner, anime=shared)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        self.client.force_authenticate(None)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(self.showcase_url).data["count"], 0)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("UPDATE ", "INSERT ", "DELETE ")) for q in queries))
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)

    def test_admin_deactivation_records_non_republishable_reason(self):
        shared, _ = self.private_record()
        shared.is_active = True
        shared.save(update_fields=["is_active"])
        shared.is_active = False
        model_admin = AnimeAdmin(Anime, admin.site)
        model_admin.save_model(RequestFactory().post("/admin/"), shared,
                               SimpleNamespace(changed_data=["is_active"]), True)
        shared.refresh_from_db()
        self.assertEqual(shared.hidden_reason, Anime.HiddenReason.ADMIN_RETIRED)
        self.assertEqual(self.manual_add().data["showcase_publication"]["status"], "review_required")

    def test_new_public_manual_work_need_not_exist_in_recommendation_catalog(self):
        self.catalog.title_zh = "不在推荐库的手工作品"
        self.catalog.bangumi_id = 99999
        self.assertEqual(self.manual_add().status_code, 201)
        self.assertEqual(self.get_public().data["count"], 1)

    def test_unsafe_recommendation_cannot_publish_private_identity(self):
        shared, _ = self.private_record()
        self.client.force_authenticate(self.owner)
        for rating in (True, None):
            self.catalog.nsfw = rating
            self.catalog.save(update_fields=["nsfw"])
            self.assertEqual(self.client.post(self.want_url, {}, format="json").status_code, 404)
            self.assertEqual(self.client.get(reverse("recommendations:detail", args=[shared.bgm_id])).status_code, 404)
        shared.refresh_from_db()
        self.assertFalse(shared.is_active)
        self.assertFalse(UserAnimeRecord.objects.filter(user=self.owner).exists())

    def test_publication_rolls_back_together_with_record_creation(self):
        shared, _ = self.private_record()
        before = Anime.objects.filter(pk=shared.pk).values().get()
        with self.assertRaises(RuntimeError), patch("recommendations.want.want_contract", side_effect=RuntimeError("rollback")):
            add_want(user=self.owner, bangumi_id=shared.bgm_id)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)
        self.assertFalse(UserAnimeRecord.objects.filter(user=self.owner).exists())

    def test_import_metadata_fill_cannot_overwrite_hidden_reason(self):
        shared, _ = self.private_record(Anime.HiddenReason.ADMIN_RETIRED)
        defaults = BangumiCollectionBatchImportView._subject_defaults({}, request_user=self.owner,
            details={"chinese_title": shared.chinese_title})
        BangumiCollectionBatchImportView._get_or_update_anime(bgm_id=shared.bgm_id, defaults=defaults)
        shared.refresh_from_db()
        self.assertEqual(shared.hidden_reason, Anime.HiddenReason.ADMIN_RETIRED)
        self.assertFalse(shared.is_active)

    def test_owner_import_existing_record_does_not_republish_hidden_identity(self):
        shared, _ = self.private_record()
        UserAnimeRecord.objects.create(user=self.owner, anime=shared)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        response = self.import_batch()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["created"], 0)
        self.assertEqual(response.data["showcase_publication_notices"], [])
        after = Anime.objects.filter(pk=shared.pk).values().get()
        # Existing imports may fill empty public metadata (including the old
        # empty-tags save) and touch updated_at. Publication must not change
        # visibility or any other values; preserve the pre-existing import path.
        for field in before.keys() - {"updated_at"}:
            self.assertEqual(after[field], before[field], field)

    def test_nickname_does_not_grant_showcase_publication(self):
        shared, _ = self.private_record()
        self.other.profile.nickname = "XHcode"
        self.other.profile.save(update_fields=["nickname"])
        record = UserAnimeRecord.objects.get(user=self.other)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        self.assertIsNone(publish_new_showcase_record(record=record, created=True))
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)

    def test_stale_withdraw_callback_does_not_hide_current_owner_record(self):
        shared, _ = self.private_record()
        shared.is_active = True
        shared.save(update_fields=["is_active"])
        UserAnimeRecord.objects.create(user=self.owner, anime=shared)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        withdraw_showcase_anime(shared.pk)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)

    def test_losing_import_creation_defaults_cannot_overwrite_existing_visibility(self):
        shared, _ = self.private_record(Anime.HiddenReason.ADMIN_RETIRED)
        before = Anime.objects.filter(pk=shared.pk).values().get()
        owner_defaults = BangumiCollectionBatchImportView._subject_defaults({}, request_user=self.owner,
            details={"chinese_title": shared.chinese_title})
        # Simulate an insert arriving between the preliminary lookup and the
        # final create. The real get_or_create must preserve the winner's row.
        missing_lookup = SimpleNamespace(first=lambda: None)
        with patch.object(Anime.objects, "filter", return_value=missing_lookup):
            result, created = BangumiCollectionBatchImportView._get_or_update_anime(
                bgm_id=shared.bgm_id, defaults=owner_defaults)
        self.assertFalse(created)
        self.assertEqual(result.pk, shared.pk)
        self.assertEqual(Anime.objects.filter(pk=shared.pk).values().get(), before)


@skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL")
@override_settings(PUBLIC_SHOWCASE_USERNAME="XHcode")
class ShowcaseVisibilityConcurrencyTests(TransactionTestCase):
    def setUp(self):
        require_local_postgres()
        self.owner = get_user_model().objects.create_user(username="XHcode")
        self.other = get_user_model().objects.create_user(username="private-race")
        self.shared = Anime.objects.create(bgm_id=92001, chinese_title="并发公开",
            airing_period="2026-04", is_active=False, hidden_reason=Anime.HiddenReason.PRIVATE_ONLY)
        RecommendationAnime.objects.create(bangumi_id=92001, title_zh="并发公开", season="2026-04", nsfw=False)

    def worker(self, user_id, barrier=None, started=None):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
                cursor.execute("SET statement_timeout = '20s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            user = get_user_model().objects.get(pk=user_id)
            if barrier:
                barrier.wait(timeout=10)
            if started:
                started.set()
            return pid, add_want(user=user, bangumi_id=self.shared.bgm_id)
        finally:
            close_old_connections()

    def test_same_owner_parallel_add_has_one_record_and_one_public_identity(self):
        barrier = Barrier(6)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.worker(self.owner.pk, barrier), range(6)))
        self.assertEqual(len({pid for pid, _ in results}), 6)
        self.assertEqual(sum(result["created"] for _, result in results), 1)
        self.assertEqual(UserAnimeRecord.objects.filter(user=self.owner).count(), 1)
        self.shared.refresh_from_db()
        self.assertTrue(self.shared.is_active)

    def test_owner_and_regular_user_compete_without_losing_publication(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, user.pk, barrier) for user in (self.owner, self.other)]
            results = [future.result(timeout=25) for future in futures]
        self.assertEqual(len({pid for pid, _ in results}), 2)
        self.assertTrue(all(row["created"] for _, row in results))
        self.assertEqual(Anime.objects.filter(bgm_id=92001).count(), 1)
        self.assertEqual(UserAnimeRecord.objects.count(), 2)
        self.shared.refresh_from_db()
        self.assertTrue(self.shared.is_active)

    def test_row_locked_admin_retirement_wins_over_waiting_owner_publish(self):
        started = Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                locked = Anime.objects.select_for_update().get(pk=self.shared.pk)
                locked.hidden_reason = Anime.HiddenReason.ADMIN_RETIRED
                locked.save(update_fields=["hidden_reason"])
                future = pool.submit(self.worker, self.owner.pk, started=started)
                self.assertTrue(started.wait(timeout=10))
            _, result = future.result(timeout=25)
        self.assertEqual(result["showcase_publication"]["status"], "review_required")
        self.shared.refresh_from_db()
        self.assertFalse(self.shared.is_active)
        self.assertEqual(self.shared.hidden_reason, Anime.HiddenReason.ADMIN_RETIRED)


@skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL")
class ShowcaseVisibilityMigrationTests(TransactionTestCase):
    def test_incremental_migration_preserves_existing_public_and_hidden_flags(self):
        require_local_postgres()
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        before_target = [("anime", "0022_useranimerecord_total_eps_and_more")]
        try:
            executor.migrate(before_target)
            historical = executor.loader.project_state(before_target).apps.get_model("anime", "Anime")
            active = historical.objects.create(chinese_title="旧公开资料", airing_period="2026-1", is_active=True)
            hidden = historical.objects.create(chinese_title="旧隐藏资料", airing_period="2026-1", is_active=False)
            executor = MigrationExecutor(connection)
            executor.migrate(leaves)
            self.assertTrue(Anime.objects.get(pk=active.pk).is_active)
            saved = Anime.objects.get(pk=hidden.pk)
            self.assertFalse(saved.is_active)
            self.assertEqual(saved.hidden_reason, Anime.HiddenReason.LEGACY_UNKNOWN)
            # Old processes may still INSERT during a rolling deployment. The
            # server default must accept their pre-0023 column list unchanged.
            old_writer = historical.objects.create(chinese_title="滚动部署旧进程", airing_period="2026-1", is_active=False)
            self.assertEqual(Anime.objects.get(pk=old_writer.pk).hidden_reason, Anime.HiddenReason.LEGACY_UNKNOWN)
        finally:
            MigrationExecutor(connection).migrate(leaves)
