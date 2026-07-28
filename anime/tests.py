import json
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from .models import (
    Anime,
    FeaturedSubmission,
    UserAnimeRecord,
    UserAnimeTag,
    UserNotification,
    UserProfile,
    UserQuickFilter,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    FRONTEND_URL="http://127.0.0.1:5173",
)
class RegistrationApiTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_registration_creates_inactive_email_login_and_sends_link(self):
        response = self.client.post(
            reverse("anime_api:register"),
            {
                "email": "New.User@Example.com",
                "password": "SaaS-Activation-2026!",
                "password_confirm": "SaaS-Activation-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(email="new.user@example.com")
        self.assertEqual(user.username, "new.user@example.com")
        self.assertFalse(user.is_active)
        self.assertTrue(user.check_password("SaaS-Activation-2026!"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http://127.0.0.1:5173/activate?uid=", mail.outbox[0].body)
        self.assertIn("&token=", mail.outbox[0].body)

    def test_registration_rejects_duplicate_email_case_insensitively(self):
        get_user_model().objects.create_user(
            username="owner",
            email="owner@example.com",
            password="Existing-Account-2026!",
        )

        response = self.client.post(
            reverse("anime_api:register"),
            {
                "email": "OWNER@EXAMPLE.COM",
                "password": "Another-Account-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_registration_rejects_malformed_and_mistyped_email_domains(self):
        for email in ("invalid@qq.co", "invalid@qq.com123"):
            with self.subTest(email=email):
                response = self.client.post(
                    reverse("anime_api:register"),
                    {
                        "email": email,
                        "password": "Strict-Email-2026!",
                        "password_confirm": "Strict-Email-2026!",
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    str(response.data["email"][0]),
                    "请输入正确格式的邮箱地址。",
                )
        self.assertEqual(len(mail.outbox), 0)

    def test_activation_enables_inactive_user(self):
        user = get_user_model().objects.create_user(
            username="pending@example.com",
            email="pending@example.com",
            password="Pending-Account-2026!",
            is_active=False,
        )
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        response = self.client.post(
            reverse("anime_api:verify-email"),
            {"uid": uid, "token": token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_activation_rejects_invalid_token(self):
        user = get_user_model().objects.create_user(
            username="invalid@example.com",
            email="invalid@example.com",
            password="Pending-Account-2026!",
            is_active=False,
        )
        uid = urlsafe_base64_encode(force_bytes(user.pk))

        response = self.client.post(
            reverse("anime_api:verify-email"),
            {"uid": uid, "token": "invalid-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        user.refresh_from_db()
        self.assertFalse(user.is_active)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    FRONTEND_URL="http://127.0.0.1:5173",
)
class PasswordResetApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="reset@example.com",
            email="reset@example.com",
            password="Old-Password-2026!",
        )

    def test_request_sends_reset_link_for_existing_email(self):
        response = self.client.post(
            reverse("anime_api:password-reset-request"),
            {"email": "RESET@EXAMPLE.COM"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(
            "http://127.0.0.1:5173/reset-password?uid=",
            mail.outbox[0].body,
        )
        self.assertIn("&token=", mail.outbox[0].body)

    def test_request_does_not_reveal_unknown_email(self):
        response = self.client.post(
            reverse("anime_api:password-reset-request"),
            {"email": "missing@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_request_rejects_invalid_email_before_sending(self):
        for email in ("invalid@qq.co", "invalid@qq.com123"):
            with self.subTest(email=email):
                response = self.client.post(
                    reverse("anime_api:password-reset-request"),
                    {"email": email},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    str(response.data["email"][0]),
                    "请输入正确格式的邮箱地址。",
                )
        self.assertEqual(len(mail.outbox), 0)

    def test_confirm_updates_password_and_invalidates_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        response = self.client.post(
            reverse("anime_api:password-reset-confirm"),
            {
                "uid": uid,
                "token": token,
                "new_password": "New-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-Password-2026!"))
        self.assertFalse(default_token_generator.check_token(self.user, token))

    def test_confirm_rejects_invalid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.client.post(
            reverse("anime_api:password-reset-confirm"),
            {
                "uid": uid,
                "token": "invalid-token",
                "new_password": "New-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Old-Password-2026!"))


class ChangePasswordApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="local-test-user",
            password="Old-Local-Password-2026!",
        )

    def test_anonymous_user_cannot_change_password(self):
        response = self.client.post(
            reverse("anime_api:change-password"),
            {
                "old_password": "Old-Local-Password-2026!",
                "new_password": "New-Local-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wrong_old_password_is_rejected(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("anime_api:change-password"),
            {
                "old_password": "Wrong-Password-2026!",
                "new_password": "New-Local-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("old_password", response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Old-Local-Password-2026!"))

    def test_authenticated_user_can_change_password(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("anime_api:change-password"),
            {
                "old_password": "Old-Local-Password-2026!",
                "new_password": "New-Local-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password("Old-Local-Password-2026!"))
        self.assertTrue(self.user.check_password("New-Local-Password-2026!"))


class JwtRotationApiTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_refresh_rotates_token_and_blacklists_the_previous_one(self):
        get_user_model().objects.create_user(
            username="jwt-rotation-user",
            password="Jwt-Rotation-Password-2026!",
        )
        login_response = self.client.post(
            reverse("token_obtain_pair"),
            {
                "username": "jwt-rotation-user",
                "password": "Jwt-Rotation-Password-2026!",
            },
            format="json",
        )
        old_refresh = login_response.data["refresh"]

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": old_refresh},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        self.assertIn("access", refresh_response.data)
        self.assertIn("refresh", refresh_response.data)
        self.assertNotEqual(refresh_response.data["refresh"], old_refresh)

        reused_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": old_refresh},
            format="json",
        )
        self.assertEqual(reused_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_returns_chinese_error_for_invalid_credentials(self):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {
                "username": "missing@example.com",
                "password": "Wrong-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            str(response.data["detail"]),
            "邮箱或密码不正确，请检查后重试。",
        )


class SensitiveAuthThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_login_rejects_the_fourth_request_from_one_ip_in_a_minute(self):
        for request_number in range(3):
            response = self.client.post(
                reverse("token_obtain_pair"),
                {"username": "missing-user", "password": "wrong-password"},
                format="json",
                HTTP_X_FORWARDED_FOR=f"203.0.113.{request_number + 1}",
            )
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        throttled_response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "missing-user", "password": "wrong-password"},
            format="json",
            HTTP_X_FORWARDED_FOR="203.0.113.200",
        )
        self.assertEqual(
            throttled_response.status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    def test_sensitive_endpoints_share_the_same_per_ip_budget(self):
        first_response = self.client.post(
            reverse("anime_api:password-reset-request"),
            {"email": "missing-one@example.com"},
            format="json",
        )
        second_response = self.client.post(
            reverse("anime_api:password-reset-request"),
            {"email": "missing-two@example.com"},
            format="json",
        )
        third_response = self.client.post(
            reverse("anime_api:register"),
            {"email": "invalid@example.com", "password": "short"},
            format="json",
        )
        throttled_response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "missing-user", "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(third_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            throttled_response.status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


class UserProfileApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="profile@example.com",
            email="profile@example.com",
            password="Profile-Account-2026!",
        )

    def test_new_user_automatically_receives_profile(self):
        profile = UserProfile.objects.get(user=self.user)

        self.assertEqual(profile.nickname, "")
        self.assertFalse(profile.avatar)

    def test_anonymous_user_cannot_read_profile(self):
        response = self.client.get(reverse("anime_api:user-profile"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_read_and_update_own_profile(self):
        self.client.force_authenticate(self.user)
        get_response = self.client.get(reverse("anime_api:user-profile"))
        patch_response = self.client.patch(
            reverse("anime_api:user-profile"),
            {
                "nickname": "番剧旅人",
                "showcase_subtitle": "这是我的追番展示宣言。",
            },
            format="json",
        )

        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_response.data["username"], self.user.username)
        self.assertEqual(get_response.data["email"], self.user.email)
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data["nickname"], "番剧旅人")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.nickname, "番剧旅人")
        self.assertEqual(
            self.user.profile.showcase_subtitle,
            "这是我的追番展示宣言。",
        )

    def test_public_showcase_profile_exposes_identity_without_email(self):
        self.user.profile.nickname = "公开主理人"
        self.user.profile.showcase_subtitle = "公开展示副标题"
        self.user.profile.save(update_fields=("nickname", "showcase_subtitle"))

        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            response = self.client.get(reverse("anime_api:showcase-profile"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], self.user.username)
        self.assertEqual(response.data["nickname"], "公开主理人")
        self.assertEqual(response.data["showcase_subtitle"], "公开展示副标题")
        self.assertNotIn("email", response.data)

    def test_authenticated_user_can_upload_avatar_as_multipart(self):
        image_buffer = BytesIO()
        Image.new("RGB", (32, 32), color="purple").save(image_buffer, format="PNG")
        avatar = SimpleUploadedFile(
            "avatar.png",
            image_buffer.getvalue(),
            content_type="image/png",
        )
        self.client.force_authenticate(self.user)

        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            response = self.client.patch(
                reverse("anime_api:user-profile"),
                {"nickname": "紫色头像", "avatar": avatar},
                format="multipart",
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["nickname"], "紫色头像")
            self.assertIn("/media/users/avatars/", response.data["avatar"])
            self.assertFalse(response.data["avatar"].startswith(("blob:", "data:")))
            self.user.profile.refresh_from_db()
            self.assertTrue(
                (Path(media_root) / self.user.profile.avatar.name).is_file()
            )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    FRONTEND_URL="http://127.0.0.1:5173",
)
class UserAccountDeletionApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.email = "delete-me@example.com"
        self.password = "Delete-Me-2026!"
        self.user = get_user_model().objects.create_user(
            username="XHcode",
            email=self.email,
            password=self.password,
        )
        self.anime = Anime.objects.create(
            chinese_title="共享资料不能随账户删除",
            airing_period="2026-7",
        )
        UserAnimeRecord.objects.create(user=self.user, anime=self.anime)

    def test_wrong_password_does_not_delete_account(self):
        self.client.force_authenticate(self.user)
        response = self.client.delete(
            reverse("anime_api:user-account"),
            {"password": "wrong-password", "confirmation": "DELETE"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(get_user_model().objects.filter(email=self.email).exists())

    def test_delete_removes_private_data_preserves_library_and_allows_email_reuse(self):
        user_id = self.user.pk
        self.client.force_authenticate(self.user)
        delete_response = self.client.delete(
            reverse("anime_api:user-account"),
            {"password": self.password, "confirmation": "DELETE"},
            format="json",
        )

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())
        self.assertFalse(UserProfile.objects.filter(user_id=user_id).exists())
        self.assertFalse(UserAnimeRecord.objects.filter(user_id=user_id).exists())
        self.assertFalse(UserQuickFilter.objects.filter(user_id=user_id).exists())
        self.assertTrue(Anime.objects.filter(pk=self.anime.pk, is_active=True).exists())

        self.client.force_authenticate(user=None)
        register_response = self.client.post(
            reverse("anime_api:register"),
            {
                "email": self.email,
                "password": "Recreated-Account-2026!",
                "password_confirm": "Recreated-Account-2026!",
            },
            format="json",
        )
        self.assertEqual(register_response.status_code, status.HTTP_201_CREATED)


class AnimeApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="reader_one",
            password="test-password-1",
        )
        self.other_user = user_model.objects.create_user(
            username="reader_two",
            password="test-password-2",
        )
        self.anime = Anime.objects.create(
            chinese_title="测试番剧",
            japanese_title="テストアニメ",
            airing_period="2026-1",
            studio="测试动画公司",
            episodes="12",
            description="测试剧情简介",
            poster_url="https://example.com/poster.jpg",
            baike_url="https://example.com/baike",
            base_tags=["日常", "治愈"],
        )
        self.other_anime = Anime.objects.create(
            chinese_title="另一部番剧",
            airing_period="2026-4",
        )
        self.record = UserAnimeRecord.objects.create(
            user=self.user,
            anime=self.anime,
            watch_status=UserAnimeRecord.WatchStatus.WATCHING,
        )
        UserAnimeRecord.objects.create(
            user=self.other_user,
            anime=self.other_anime,
            watch_status=UserAnimeRecord.WatchStatus.COMPLETED,
        )

    def test_public_anime_library_allows_anonymous_read(self):
        response = self.client.get(reverse("anime_api:anime-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_global_pagination_defaults_to_20_and_caps_page_size_at_100(self):
        Anime.objects.bulk_create(
            [
                Anime(
                    chinese_title=f"分页测试番剧 {index}",
                    airing_period="2026-7",
                )
                for index in range(103)
            ]
        )

        default_response = self.client.get(reverse("anime_api:anime-list"))
        capped_response = self.client.get(
            reverse("anime_api:anime-list"),
            {"page_size": 500},
        )

        self.assertEqual(default_response.data["count"], 105)
        self.assertEqual(len(default_response.data["results"]), 20)
        self.assertIsNotNone(default_response.data["next"])
        self.assertEqual(len(capped_response.data["results"]), 100)

    def test_public_showcase_filters_follow_owner_and_are_read_only(self):
        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            response = self.client.get(reverse("anime_api:showcase-filter-list"))
            write_response = self.client.post(
                reverse("anime_api:showcase-filter-list"),
                {"name": "不允许写入", "tags": ["日常"]},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 5)
        self.assertEqual(write_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_public_anime_library_can_be_searched(self):
        response = self.client.get(
            reverse("anime_api:animes-list"),
            {"search": self.anime.chinese_title},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.anime.id)

    def test_retired_anime_is_hidden_from_search_and_cannot_be_added(self):
        self.anime.is_active = False
        self.anime.save(update_fields=("is_active",))

        search_response = self.client.get(
            reverse("anime_api:animes-list"),
            {"search": self.anime.chinese_title},
        )
        self.assertEqual(search_response.status_code, status.HTTP_200_OK)
        self.assertEqual(search_response.data["results"], [])

        self.client.force_authenticate(self.other_user)
        add_response = self.client.post(
            reverse("anime_api:user-anime-record-add"),
            {"anime_id": self.anime.id},
            format="json",
        )
        self.assertEqual(add_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            UserAnimeRecord.objects.filter(
                user=self.other_user,
                anime=self.anime,
            ).exists()
        )

    def test_deleting_showcase_owner_record_deletes_unreferenced_anime(self):
        anime_id = self.anime.id
        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            self.client.force_authenticate(self.user)
            response = self.client.delete(
                reverse(
                    "anime_api:user-anime-record-detail",
                    kwargs={"pk": self.record.pk},
                )
            )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Anime.objects.filter(pk=anime_id).exists())

    def test_deleting_showcase_record_outside_api_also_deletes_anime(self):
        anime_id = self.anime.id
        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            self.record.delete()

        self.assertFalse(Anime.objects.filter(pk=anime_id).exists())

    def test_showcase_delete_only_retires_anime_used_by_another_user(self):
        UserAnimeRecord.objects.create(
            user=self.other_user,
            anime=self.anime,
            watch_status=UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
        )

        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            self.record.delete()

        self.anime.refresh_from_db()
        self.assertFalse(self.anime.is_active)
        self.assertTrue(
            UserAnimeRecord.objects.filter(
                user=self.other_user,
                anime=self.anime,
            ).exists()
        )

    def test_deleting_regular_user_record_keeps_public_anime_active(self):
        self.record.delete()

        self.anime.refresh_from_db()
        self.assertTrue(self.anime.is_active)

    def test_authenticated_user_can_create_public_anime(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("anime_api:animes-list"),
            {
                "chinese_title": "全新公共番剧",
                "japanese_title": "新作アニメ",
                "airing_period": "2028-1",
                "studio": "新公司",
                "episodes": "12",
                "description": "全新剧情",
                "base_tags": ["原创"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Anime.objects.filter(chinese_title="全新公共番剧").exists()
        )

    def test_public_anime_rejects_unknown_tag_color(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            reverse("anime_api:animes-detail", kwargs={"pk": self.anime.pk}),
            {"tag_colors": {"日常": "arbitrary-css"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("tag_colors", response.data)

    def test_public_anime_endpoint_cannot_change_private_tag_colors(self):
        self.anime.tag_colors = {"日常": "blue"}
        self.anime.save(update_fields=("tag_colors",))
        self.client.force_authenticate(self.user)

        response = self.client.patch(
            reverse("anime_api:animes-detail", kwargs={"pk": self.anime.pk}),
            {"tag_colors": {"日常": "rose"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.anime.refresh_from_db()
        self.assertEqual(self.anime.tag_colors, {"日常": "blue"})

    def test_direct_anime_create_accepts_multipart_tags_and_poster(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), color="blue").save(image_buffer, format="PNG")
        image_buffer.seek(0)
        poster = SimpleUploadedFile(
            "direct-poster.png",
            image_buffer.read(),
            content_type="image/png",
        )

        self.client.force_authenticate(self.user)
        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            response = self.client.post(
                reverse("anime_api:animes-list"),
                {
                    "chinese_title": "直接上传创建",
                    "japanese_title": "直接作成",
                    "airing_period": "2028-10",
                    "base_tags": json.dumps(["上传", "原创"], ensure_ascii=False),
                    "poster_image": poster,
                },
                format="multipart",
            )

            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            created_anime = Anime.objects.get(chinese_title="直接上传创建")
            self.assertEqual(created_anime.base_tags, ["上传", "原创"])
            self.assertTrue(created_anime.poster_image.name)
            self.assertTrue(
                (Path(media_root) / created_anime.poster_image.name).is_file()
            )
            self.assertFalse(
                response.data["poster_image"].startswith(("blob:", "data:"))
            )

    def test_direct_anime_create_rejects_unsupported_image_format(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), color="blue").save(image_buffer, format="BMP")
        poster = SimpleUploadedFile(
            "unsupported.bmp",
            image_buffer.getvalue(),
            content_type="image/bmp",
        )

        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("anime_api:animes-list"),
            {
                "chinese_title": "不支持格式的海报",
                "airing_period": "2028-10",
                "poster_image": poster,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("poster_image", response.data)

    def test_add_endpoint_can_link_existing_anime(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("anime_api:user-anime-record-add"),
            {
                "anime_id": self.other_anime.id,
                "watch_status": UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
                "custom_tags": ["待补番"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["anime"]["id"], self.other_anime.id)
        self.assertTrue(
            UserAnimeRecord.objects.filter(
                user=self.user,
                anime=self.other_anime,
            ).exists()
        )

    def test_add_endpoint_can_create_new_anime_and_record(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("anime_api:user-anime-record-add"),
            {
                "anime_data": {
                    "chinese_title": "一次创建的新番",
                    "japanese_title": "一括作成",
                    "airing_period": "2028-4",
                    "studio": "事务动画",
                    "episodes": "13",
                    "description": "一次请求创建",
                    "poster_url": "https://example.com/new.jpg",
                    "baike_url": "https://example.com/wiki",
                    "base_tags": ["原创", "测试"],
                },
                "personal_score": "8.8",
                "watch_status": UserAnimeRecord.WatchStatus.WATCHING,
                "review": "正在观看",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_anime = Anime.objects.get(chinese_title="一次创建的新番")
        self.assertTrue(
            UserAnimeRecord.objects.filter(
                user=self.user,
                anime=created_anime,
            ).exists()
        )
        self.assertEqual(response.data["anime"]["studio"], "事务动画")

    def test_add_endpoint_accepts_multipart_poster_upload(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), color="purple").save(image_buffer, format="PNG")
        image_buffer.seek(0)
        poster = SimpleUploadedFile(
            "poster.png",
            image_buffer.read(),
            content_type="image/png",
        )
        anime_data = {
            "chinese_title": "带上传海报的新番",
            "japanese_title": "画像付き",
            "airing_period": "2028-7",
            "base_tags": ["测试"],
        }

        self.client.force_authenticate(self.user)
        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            response = self.client.post(
                reverse("anime_api:user-anime-record-add"),
                {
                    "anime_data": json.dumps(anime_data, ensure_ascii=False),
                    "poster_image": poster,
                    "watch_status": UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
                },
                format="multipart",
            )

            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            created_anime = Anime.objects.get(chinese_title="带上传海报的新番")
            self.assertTrue(created_anime.poster_image.name.endswith("poster.png"))

    def test_anonymous_user_cannot_modify_shared_anime(self):
        response = self.client.patch(
            reverse("anime_api:animes-detail", kwargs={"pk": self.anime.pk}),
            {"studio": "不应写入"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_public_showcase_exposes_only_configured_users_records(self):
        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            response = self.client.get(reverse("anime_api:showcase-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["user"], self.user.username)

    def test_private_records_only_return_current_users_data(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse("anime_api:user-anime-record-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user"], self.user.username)
        self.assertEqual(results[0]["anime"]["id"], self.anime.id)
        self.assertEqual(
            results[0]["anime"]["poster_url"],
            self.anime.poster_url,
        )
        self.assertEqual(results[0]["anime"]["studio"], self.anime.studio)
        self.assertEqual(results[0]["anime"]["episodes"], self.anime.episodes)
        self.assertEqual(results[0]["anime"]["tag_colors"], {})
        self.assertEqual(
            results[0]["anime"]["description"],
            self.anime.description,
        )
        self.assertEqual(
            results[0]["anime"]["baike_url"],
            self.anime.baike_url,
        )

    def test_record_stats_use_all_of_current_users_records(self):
        self.record.personal_score = Decimal("9.6")
        self.record.watch_status = UserAnimeRecord.WatchStatus.COMPLETED
        self.record.custom_tags = ["OVA", "泡面番"]
        self.record.save()
        self.anime.base_tags = ["日常", "剧场版"]
        self.anime.save(update_fields=("base_tags",))
        third_anime = Anime.objects.create(
            chinese_title="未评分番剧",
            airing_period="未定档",
        )
        UserAnimeRecord.objects.create(user=self.user, anime=third_anime)
        self.client.force_authenticate(self.user)

        response = self.client.get(
            reverse("anime_api:user-anime-record-stats"),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["completed_count"], 1)
        self.assertEqual(response.data["average_score"], "9.60")
        self.assertEqual(response.data["movie_count"], 1)
        self.assertEqual(response.data["ova_count"], 1)
        self.assertEqual(response.data["short_count"], 1)
        self.assertEqual(response.data["masterpiece_count"], 1)
        self.assertEqual(response.data["pending_count"], 1)

    def test_public_showcase_stats_use_configured_owner_only(self):
        self.record.personal_score = Decimal("8.8")
        self.record.watch_status = UserAnimeRecord.WatchStatus.COMPLETED
        self.record.save()

        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            response = self.client.get(reverse("anime_api:showcase-stats"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["completed_count"], 1)
        self.assertEqual(response.data["average_score"], "8.80")

    def test_private_records_support_bounded_all_page_size(self):
        extra_anime = [
            Anime.objects.create(
                chinese_title=f"全部模式测试番剧 {index}",
                airing_period="2026-7",
            )
            for index in range(2)
        ]
        UserAnimeRecord.objects.bulk_create(
            [
                UserAnimeRecord(user=self.user, anime=anime)
                for anime in extra_anime
            ]
        )
        self.client.force_authenticate(self.user)

        with patch(
            "anime.pagination.UserAnimeRecordPagination.max_all_size",
            2,
        ):
            response = self.client.get(
                reverse("anime_api:user-anime-record-list"),
                {"page_size": "all"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 2)
        self.assertTrue(
            all(item["user"] == self.user.username for item in response.data)
        )
        self.assertEqual(response.headers["X-Result-Limit"], "2")
        self.assertEqual(response.headers["X-Result-Truncated"], "true")

    def test_unrated_records_are_sorted_after_all_rated_records_before_pagination(self):
        rated_anime = [
            Anime.objects.create(
                chinese_title=f"已评分排序测试番剧 {index}",
                airing_period="2026-7",
            )
            for index in range(10)
        ]
        UserAnimeRecord.objects.bulk_create(
            [
                UserAnimeRecord(
                    user=self.user,
                    anime=anime,
                    personal_score=Decimal("8.0"),
                    watch_status=UserAnimeRecord.WatchStatus.COMPLETED,
                )
                for anime in rated_anime
            ]
        )
        self.client.force_authenticate(self.user)

        first_page = self.client.get(
            reverse("anime_api:user-anime-record-list"),
            {"page": 1, "page_size": 10},
        )
        second_page = self.client.get(
            reverse("anime_api:user-anime-record-list"),
            {"page": 2, "page_size": 10},
        )

        self.assertEqual(first_page.status_code, status.HTTP_200_OK)
        self.assertTrue(
            all(item["personal_score"] is not None for item in first_page.data["results"])
        )
        self.assertEqual(len(second_page.data["results"]), 1)
        self.assertIsNone(second_page.data["results"][0]["personal_score"])

    def test_record_filters_run_before_pagination(self):
        extra_anime = [
            Anime.objects.create(
                chinese_title=f"分页筛选测试番剧 {index}",
                airing_period=f"{2010 + index}-1",
                base_tags=["筛选目标"] if index in {2, 7, 11} else ["其他"],
            )
            for index in range(15)
        ]
        UserAnimeRecord.objects.bulk_create(
            [
                UserAnimeRecord(
                    user=self.user,
                    anime=anime,
                    personal_score=Decimal("8.0"),
                    watch_status=(
                        UserAnimeRecord.WatchStatus.WATCHING
                        if index in {2, 7, 11}
                        else UserAnimeRecord.WatchStatus.COMPLETED
                    ),
                )
                for index, anime in enumerate(extra_anime)
            ]
        )
        self.client.force_authenticate(self.user)

        response = self.client.get(
            reverse("anime_api:user-anime-record-list"),
            {
                "page": 1,
                "page_size": 10,
                "status": UserAnimeRecord.WatchStatus.WATCHING,
                "tag": "筛选目标",
                "sort": "date-desc",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 3)
        self.assertIsNone(response.data["next"])
        self.assertTrue(
            all(item["watch_status"] == "watching" for item in response.data["results"])
        )
        periods = [item["anime"]["airing_period"] for item in response.data["results"]]
        self.assertEqual(periods, ["2021-1", "2017-1", "2012-1"])

    def test_public_showcase_search_and_filter_options_cover_full_dataset(self):
        target_anime = Anime.objects.create(
            chinese_title="全量筛选选项专用番剧",
            airing_period="1999-10",
            base_tags=["全量专属标签"],
        )
        UserAnimeRecord.objects.create(
            user=self.user,
            anime=target_anime,
            personal_score=Decimal("7.5"),
        )

        with self.settings(PUBLIC_SHOWCASE_USERNAME=self.user.username):
            search_response = self.client.get(
                reverse("anime_api:showcase-list"),
                {"page_size": 10, "search": "全量筛选选项专用"},
            )
            options_response = self.client.get(
                reverse("anime_api:showcase-filter-options"),
            )

        self.assertEqual(search_response.data["count"], 1)
        self.assertEqual(
            search_response.data["results"][0]["anime"]["chinese_title"],
            target_anime.chinese_title,
        )
        self.assertIn("全量专属标签", options_response.data["tags"])
        self.assertIn("1999", options_response.data["years"])

    def test_record_create_automatically_binds_authenticated_user(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("anime_api:user-anime-record-list"),
            {
                "anime_id": self.other_anime.id,
                "watch_status": UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
                "custom_tags": ["待补"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = UserAnimeRecord.objects.get(pk=response.data["id"])
        self.assertEqual(created.user, self.user)

    @override_settings(USER_ANIME_RECORD_LIMIT=1)
    def test_record_quota_blocks_both_create_endpoints(self):
        self.client.force_authenticate(self.user)
        initial_count = UserAnimeRecord.objects.filter(user=self.user).count()

        requests = (
            (
                reverse("anime_api:user-anime-record-list"),
                {"anime_id": self.other_anime.id},
            ),
            (
                reverse("anime_api:user-anime-record-add"),
                {"anime_id": self.other_anime.id},
            ),
        )
        for url, payload in requests:
            with self.subTest(url=url):
                response = self.client.post(url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(
                    response.data["detail"],
                    "您的追番记录已达到容量上限",
                )

        self.assertEqual(
            UserAnimeRecord.objects.filter(user=self.user).count(),
            initial_count,
        )

    def test_duplicate_user_anime_record_is_rejected(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("anime_api:user-anime-record-list"),
            {
                "anime_id": self.anime.id,
                "watch_status": UserAnimeRecord.WatchStatus.COMPLETED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("anime_id", response.data)

    def test_cors_allows_react_development_origin(self):
        response = self.client.get(
            reverse("anime_api:anime-list"),
            HTTP_ORIGIN="http://localhost:5173",
        )

        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "http://localhost:5173",
        )

    def test_jwt_login_refresh_and_private_record_access(self):
        login_response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.user.username, "password": "test-password-1"},
            format="json",
        )

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertIn("access", login_response.data)
        self.assertIn("refresh", login_response.data)

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        self.assertIn("access", refresh_response.data)

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}"
        )
        records_response = self.client.get(
            reverse("anime_api:user-anime-record-list")
        )
        self.assertEqual(records_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(records_response.data["results"]), 1)

    def test_private_records_reject_anonymous_requests(self):
        response = self.client.get(reverse("anime_api:user-anime-record-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_quick_filters_are_created_and_isolated_per_user(self):
        self.client.force_authenticate(self.user)
        create_response = self.client.post(
            reverse("anime_api:quick-filter-list"),
            {
                "name": "治愈日常",
                "tags": ["治愈", "日常"],
                "match_mode": UserQuickFilter.MatchMode.ALL,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(self.other_user)
        other_response = self.client.get(reverse("anime_api:quick-filter-list"))
        self.assertEqual(other_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(other_response.data["results"]), 5)
        self.assertNotIn(
            "治愈日常",
            [item["name"] for item in other_response.data["results"]],
        )

        self.client.force_authenticate(self.user)
        owner_response = self.client.get(reverse("anime_api:quick-filter-list"))
        self.assertEqual(len(owner_response.data["results"]), 6)
        self.assertIn(
            "治愈日常",
            [item["name"] for item in owner_response.data["results"]],
        )

    def test_user_can_update_and_delete_own_quick_filter(self):
        quick_filter = UserQuickFilter.objects.create(
            user=self.user,
            name="百合作品",
            tags=["真百", "轻百"],
        )
        self.client.force_authenticate(self.user)
        update_response = self.client.patch(
            reverse("anime_api:quick-filter-detail", kwargs={"pk": quick_filter.pk}),
            {"name": "百合精选", "match_mode": "all"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)

        delete_response = self.client.delete(
            reverse("anime_api:quick-filter-detail", kwargs={"pk": quick_filter.pk})
        )
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(UserQuickFilter.objects.filter(pk=quick_filter.pk).exists())

    def test_default_quick_filter_can_be_deleted_without_recreation(self):
        quick_filter = UserQuickFilter.objects.get(
            user=self.user,
            name="百合 (真/轻)",
        )
        self.client.force_authenticate(self.user)
        response = self.client.delete(
            reverse("anime_api:quick-filter-detail", kwargs={"pk": quick_filter.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        list_response = self.client.get(reverse("anime_api:quick-filter-list"))
        self.assertEqual(len(list_response.data["results"]), 4)
        self.assertNotIn(
            "百合 (真/轻)",
            [item["name"] for item in list_response.data["results"]],
        )

    def test_user_can_update_their_own_record(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            reverse(
                "anime_api:user-anime-record-detail",
                kwargs={"pk": self.record.pk},
            ),
            {
                "personal_score": "9.6",
                "watch_status": UserAnimeRecord.WatchStatus.COMPLETED,
                "review": "值得反复观看。",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.record.refresh_from_db()
        self.assertEqual(str(self.record.personal_score), "9.6")
        self.assertEqual(
            self.record.watch_status,
            UserAnimeRecord.WatchStatus.COMPLETED,
        )
        self.assertEqual(response.data["anime"]["id"], self.anime.id)

    def test_user_can_atomically_update_anime_and_private_details(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            reverse(
                "anime_api:user-anime-record-update-details",
                kwargs={"pk": self.record.pk},
            ),
            {
                "anime": {
                    "chinese_title": "修改后的番剧名",
                    "japanese_title": "変更後のタイトル",
                    "airing_period": "2027-4",
                    "studio": "新制作公司",
                    "episodes": "13",
                    "base_tags": ["新标签", "治愈"],
                    "tag_colors": {"新标签": "violet", "治愈": "emerald"},
                    "poster_url": "https://example.com/new-poster.jpg",
                    "description": "更新后的剧情简介",
                    "baike_url": "https://example.com/new-baike",
                },
                "personal_score": "9.9",
                "watch_status": UserAnimeRecord.WatchStatus.COMPLETED,
                "review": "更新后的个人评价",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.anime.refresh_from_db()
        self.record.refresh_from_db()
        self.assertEqual(self.anime.chinese_title, "修改后的番剧名")
        self.assertEqual(self.anime.base_tags, ["新标签", "治愈"])
        self.assertEqual(self.anime.tag_colors, {})
        self.assertEqual(
            {
                tag.name: tag.color
                for tag in self.record.tag_preferences.all()
            },
            {"新标签": "violet", "治愈": "emerald"},
        )
        self.assertEqual(
            response.data["anime"]["tag_colors"],
            {"新标签": "violet", "治愈": "emerald"},
        )
        self.assertEqual(self.anime.baike_url, "https://example.com/new-baike")
        self.assertEqual(str(self.record.personal_score), "9.9")
        self.assertEqual(self.record.review, "更新后的个人评价")
        self.assertEqual(
            response.data["anime"]["description"],
            "更新后的剧情简介",
        )

    def test_user_can_update_details_with_multipart_poster(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), color="orange").save(image_buffer, format="PNG")
        image_buffer.seek(0)
        poster = SimpleUploadedFile(
            "updated-poster.png",
            image_buffer.read(),
            content_type="image/png",
        )
        anime_data = {
            "chinese_title": "本地海报更新",
            "airing_period": "2029-1",
            "base_tags": ["本地上传"],
            "tag_colors": {"本地上传": "rose"},
        }
        record_data = {
            "personal_score": "9.4",
            "watch_status": UserAnimeRecord.WatchStatus.COMPLETED,
            "review": "通过 multipart 同步更新。",
        }

        self.client.force_authenticate(self.user)
        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            response = self.client.patch(
                reverse(
                    "anime_api:user-anime-record-update-details",
                    kwargs={"pk": self.record.pk},
                ),
                {
                    "anime": json.dumps(anime_data, ensure_ascii=False),
                    "record": json.dumps(record_data, ensure_ascii=False),
                    "poster_image": poster,
                },
                format="multipart",
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.anime.refresh_from_db()
            self.record.refresh_from_db()
            self.assertEqual(self.anime.chinese_title, "本地海报更新")
            self.assertTrue(
                self.anime.poster_image.name.endswith("updated-poster.png")
            )
            self.assertTrue(
                (Path(media_root) / self.anime.poster_image.name).is_file()
            )
            self.assertFalse(
                response.data["anime"]["poster_image"].startswith(
                    ("blob:", "data:")
                )
            )
            self.assertEqual(str(self.record.personal_score), "9.4")
            self.assertEqual(
                response.data["anime"]["tag_colors"],
                {"本地上传": "rose"},
            )

    def test_saving_poster_url_clears_previous_uploaded_poster(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), color="teal").save(image_buffer, format="PNG")
        image_buffer.seek(0)
        self.anime.poster_image = SimpleUploadedFile(
            "old-local-poster.png",
            image_buffer.read(),
            content_type="image/png",
        )

        self.client.force_authenticate(self.user)
        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            self.anime.save(update_fields=("poster_image",))
            response = self.client.patch(
                reverse(
                    "anime_api:user-anime-record-update-details",
                    kwargs={"pk": self.record.pk},
                ),
                {
                    "anime": {
                        "poster_url": "https://example.com/replacement-poster.jpg",
                    },
                },
                format="json",
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.anime.refresh_from_db()
            self.assertFalse(self.anime.poster_image)
            self.assertEqual(
                self.anime.poster_url,
                "https://example.com/replacement-poster.jpg",
            )
            self.assertIsNone(response.data["anime"]["poster_image"])

    def test_tag_color_update_is_isolated_between_users(self):
        other_record = UserAnimeRecord.objects.create(
            user=self.other_user,
            anime=self.anime,
            watch_status=UserAnimeRecord.WatchStatus.WATCHING,
        )
        UserAnimeTag.objects.bulk_create(
            [
                UserAnimeTag(record=self.record, name="日常", color="sky"),
                UserAnimeTag(record=other_record, name="日常", color="sky"),
            ]
        )
        self.client.force_authenticate(self.user)

        response = self.client.patch(
            reverse(
                "anime_api:user-anime-record-update-details",
                kwargs={"pk": self.record.pk},
            ),
            {"anime": {"tag_colors": {"日常": "rose"}}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.anime.refresh_from_db()
        self.assertEqual(self.anime.tag_colors, {})
        self.assertEqual(
            UserAnimeTag.objects.get(record=self.record, name="日常").color,
            "rose",
        )
        self.assertEqual(
            UserAnimeTag.objects.get(record=other_record, name="日常").color,
            "sky",
        )

        self.client.force_authenticate(self.other_user)
        other_response = self.client.get(
            reverse(
                "anime_api:user-anime-record-detail",
                kwargs={"pk": other_record.pk},
            )
        )
        self.assertEqual(
            other_response.data["anime"]["tag_colors"],
            {"日常": "sky"},
        )

    def test_user_can_delete_only_their_own_record(self):
        other_record = UserAnimeRecord.objects.get(user=self.other_user)
        self.client.force_authenticate(self.user)

        forbidden_response = self.client.delete(
            reverse(
                "anime_api:user-anime-record-detail",
                kwargs={"pk": other_record.pk},
            )
        )
        self.assertEqual(forbidden_response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.delete(
            reverse(
                "anime_api:user-anime-record-detail",
                kwargs={"pk": self.record.pk},
            )
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(UserAnimeRecord.objects.filter(pk=self.record.pk).exists())
        self.assertTrue(UserAnimeRecord.objects.filter(pk=other_record.pk).exists())


class JournalDataTransferApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="journal-owner@example.com",
            email="journal-owner@example.com",
            password="Journal-Owner-2026!",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other-journal@example.com",
            email="other-journal@example.com",
            password="Other-Journal-2026!",
        )
        self.old_anime = Anime.objects.create(
            chinese_title="原有番剧",
            japanese_title="元のアニメ",
            airing_period="2024-1",
            studio="原制作公司",
            episodes="12",
            description="原剧情简介",
            poster_url="https://example.com/old.jpg",
            baike_url="https://example.com/old",
            base_tags=["日常"],
            tag_colors={"日常": "blue"},
        )
        self.old_record = UserAnimeRecord.objects.create(
            user=self.user,
            anime=self.old_anime,
            personal_score=Decimal("8.5"),
            watch_status=UserAnimeRecord.WatchStatus.COMPLETED,
            review="原有评价",
            custom_tags=["收藏"],
        )
        UserAnimeTag.objects.bulk_create(
            [
                UserAnimeTag(record=self.old_record, name="日常", color="blue"),
                UserAnimeTag(record=self.old_record, name="收藏", color="rose"),
            ]
        )
        UserAnimeRecord.objects.create(
            user=self.other_user,
            anime=self.old_anime,
            watch_status=UserAnimeRecord.WatchStatus.WATCHING,
        )
        self.client.force_authenticate(self.user)

    def _replacement_payload(self):
        return {
            "format": "anime-journal",
            "version": 1,
            "records": [
                {
                    "anime": {
                        "chinese_title": "导入番剧",
                        "japanese_title": "インポートアニメ",
                        "airing_period": "2026-7",
                        "studio": "导入制作公司",
                        "episodes": "13",
                        "description": "导入剧情简介",
                        "poster_url": "https://example.com/imported.jpg",
                        "baike_url": "https://example.com/imported",
                        "base_tags": ["治愈", "日常"],
                        "tag_colors": {"治愈": "emerald", "日常": "blue"},
                    },
                    "personal_score": "9.7",
                    "watch_status": UserAnimeRecord.WatchStatus.COMPLETED,
                    "review": "导入后的评价",
                    "custom_tags": ["年度推荐"],
                }
            ],
        }

    def test_export_contains_portable_data_without_internal_ids(self):
        response = self.client.get(reverse("anime_api:journal-export"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response["Content-Type"],
            "application/json; charset=utf-8",
        )
        self.assertIn("attachment;", response["Content-Disposition"])
        payload = json.loads(response.content)
        self.assertEqual(payload["format"], "anime-journal")
        self.assertEqual(payload["version"], 2)
        self.assertEqual(len(payload["records"]), 1)
        exported_record = payload["records"][0]
        self.assertNotIn("id", exported_record)
        self.assertNotIn("user", exported_record)
        self.assertNotIn("anime_id", exported_record)
        self.assertNotIn("id", exported_record["anime"])
        self.assertEqual(exported_record["anime"]["chinese_title"], "原有番剧")
        self.assertEqual(
            exported_record["anime"]["tag_colors"],
            {"日常": "blue", "收藏": "rose"},
        )
        self.assertEqual(exported_record["personal_score"], "8.5")

    def test_json_import_replaces_only_current_users_records(self):
        response = self.client.post(
            reverse("anime_api:journal-import"),
            self._replacement_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["imported_count"], 1)
        records = UserAnimeRecord.objects.filter(user=self.user).select_related("anime")
        self.assertEqual(records.count(), 1)
        imported = records.get()
        self.assertEqual(imported.anime.chinese_title, "导入番剧")
        self.assertEqual(imported.personal_score, Decimal("9.7"))
        self.assertEqual(imported.custom_tags, ["年度推荐"])
        self.assertEqual(imported.anime.tag_colors, {})
        self.assertEqual(
            {
                tag.name: tag.color
                for tag in imported.tag_preferences.all()
            },
            {"治愈": "emerald", "日常": "blue"},
        )
        self.assertTrue(
            UserAnimeRecord.objects.filter(
                user=self.other_user,
                anime=self.old_anime,
            ).exists()
        )

    def test_one_users_export_can_replace_another_users_journal(self):
        export_response = self.client.get(reverse("anime_api:journal-export"))
        payload = json.loads(export_response.content)
        self.client.force_authenticate(self.other_user)

        import_response = self.client.post(
            reverse("anime_api:journal-import"),
            payload,
            format="json",
        )

        self.assertEqual(import_response.status_code, status.HTTP_200_OK)
        imported = UserAnimeRecord.objects.get(user=self.other_user)
        self.assertEqual(imported.anime, self.old_anime)
        self.assertEqual(imported.review, "原有评价")
        self.assertEqual(
            {
                tag.name: tag.color
                for tag in imported.tag_preferences.all()
            },
            {"日常": "blue", "收藏": "rose"},
        )

    def test_multipart_json_file_import_is_supported(self):
        upload = SimpleUploadedFile(
            "journal.json",
            json.dumps(self._replacement_payload(), ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )

        response = self.client.post(
            reverse("anime_api:journal-import"),
            {"file": upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            UserAnimeRecord.objects.get(user=self.user).anime.chinese_title,
            "导入番剧",
        )

    def test_invalid_import_keeps_existing_records(self):
        response = self.client.post(
            reverse("anime_api:journal-import"),
            {
                "records": [
                    {
                        "anime": {
                            "chinese_title": "无效番剧",
                            "airing_period": "2026-10",
                        },
                        "personal_score": "99.0",
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            UserAnimeRecord.objects.filter(pk=self.old_record.pk).exists()
        )

    def test_unexpected_bulk_create_failure_rolls_back_deletion(self):
        with patch(
            "anime.views.UserAnimeRecord.objects.bulk_create",
            side_effect=RuntimeError("simulated database failure"),
        ):
            response = self.client.post(
                reverse("anime_api:journal-import"),
                self._replacement_payload(),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "导入失败，原有手账数据已完整保留。",
        )
        self.assertTrue(
            UserAnimeRecord.objects.filter(pk=self.old_record.pk).exists()
        )
        self.assertFalse(Anime.objects.filter(chinese_title="导入番剧").exists())

    def test_import_and_export_require_authentication(self):
        self.client.force_authenticate(user=None)

        export_response = self.client.get(reverse("anime_api:journal-export"))
        import_response = self.client.post(
            reverse("anime_api:journal-import"),
            {"records": []},
            format="json",
        )

        self.assertEqual(export_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(import_response.status_code, status.HTTP_401_UNAUTHORIZED)


class FeaturedColumnApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="featured-owner@example.com",
            email="featured-owner@example.com",
            password="Featured-Owner-2026!",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other-owner@example.com",
            email="other-owner@example.com",
            password="Other-Owner-2026!",
        )
        self.anime = Anime.objects.create(
            chinese_title="精选测试番剧",
            japanese_title="特集テスト",
            airing_period="2026-10",
            poster_url="https://example.com/featured.jpg",
            base_tags=["剧场版", "动画"],
        )
        self.record = UserAnimeRecord.objects.create(
            user=self.user,
            anime=self.anime,
            personal_score=Decimal("9.6"),
            watch_status=UserAnimeRecord.WatchStatus.COMPLETED,
            review="一篇只属于当前用户的长评。",
            custom_tags=["力荐", "剧场版"],
        )
        self.client.force_authenticate(self.user)

    def test_profile_has_default_featured_limit(self):
        self.assertEqual(self.user.profile.max_featured_limit, 3)

    def test_submit_creates_an_independent_pending_snapshot(self):
        response = self.client.post(
            reverse("anime_api:featured-submission-submit"),
            {"anime_id": self.anime.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = FeaturedSubmission.objects.get()
        self.assertEqual(submission.user, self.user)
        self.assertEqual(submission.source_anime, self.anime)
        self.assertEqual(response.data["source_anime_id"], self.anime.pk)
        self.assertEqual(
            submission.audit_status,
            FeaturedSubmission.AuditStatus.PENDING,
        )
        self.assertEqual(submission.chinese_title, "精选测试番剧")
        self.assertEqual(submission.japanese_title, "特集テスト")
        self.assertEqual(submission.year, 2026)
        self.assertEqual(submission.score, Decimal("9.6"))
        self.assertEqual(submission.review, "一篇只属于当前用户的长评。")
        self.assertEqual(submission.poster_url, "https://example.com/featured.jpg")
        self.assertEqual(submission.tags, ["剧场版", "动画", "力荐"])

        self.anime.chinese_title = "修改后的公共资料"
        self.anime.save(update_fields=("chinese_title",))
        self.record.review = "修改后的私人长评"
        self.record.save(update_fields=("review",))
        submission.refresh_from_db()
        self.assertEqual(submission.chinese_title, "精选测试番剧")
        self.assertEqual(submission.review, "一篇只属于当前用户的长评。")

    def test_submit_rejects_user_at_approved_limit(self):
        self.user.profile.max_featured_limit = 1
        self.user.profile.save(update_fields=("max_featured_limit",))
        FeaturedSubmission.objects.create(
            user=self.user,
            chinese_title="已收录作品",
            year=2025,
            audit_status=FeaturedSubmission.AuditStatus.APPROVED,
        )

        response = self.client.post(
            reverse("anime_api:featured-submission-submit"),
            {"anime_id": self.anime.pk},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(FeaturedSubmission.objects.count(), 1)

    def test_state_endpoint_ignores_historical_rejections(self):
        approved = FeaturedSubmission.objects.create(
            user=self.user,
            source_anime=self.anime,
            chinese_title="已收录作品",
            year=2025,
            audit_status=FeaturedSubmission.AuditStatus.APPROVED,
        )
        FeaturedSubmission.objects.create(
            user=self.user,
            source_anime=self.anime,
            chinese_title="历史拒绝记录",
            year=2024,
            audit_status=FeaturedSubmission.AuditStatus.REJECTED,
        )

        response = self.client.get(
            reverse("anime_api:featured-submission-state"),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["max_featured_limit"], 3)
        self.assertEqual(response.data["approved_count"], 1)
        self.assertFalse(response.data["is_at_limit"])
        self.assertEqual(len(response.data["submissions"]), 1)
        self.assertEqual(response.data["submissions"][0]["id"], approved.pk)

    def test_submit_rejects_parallel_pending_or_removal_request(self):
        for audit_status in (
            FeaturedSubmission.AuditStatus.PENDING,
            FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED,
        ):
            with self.subTest(audit_status=audit_status):
                FeaturedSubmission.objects.all().delete()
                FeaturedSubmission.objects.create(
                    user=self.user,
                    chinese_title="处理中作品",
                    year=2025,
                    audit_status=audit_status,
                )

                response = self.client.post(
                    reverse("anime_api:featured-submission-submit"),
                    {"anime_id": self.anime.pk},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(FeaturedSubmission.objects.count(), 1)

    def test_approved_submission_can_be_changed_to_removal_requested(self):
        submission = FeaturedSubmission.objects.create(
            user=self.user,
            source_anime=self.anime,
            chinese_title=self.anime.chinese_title,
            year=2026,
            audit_status=FeaturedSubmission.AuditStatus.APPROVED,
        )

        response = self.client.post(
            reverse(
                "anime_api:featured-submission-request-removal",
                kwargs={"pk": submission.pk},
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(
            submission.audit_status,
            FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED,
        )

    def test_non_approved_submission_cannot_request_removal(self):
        submission = FeaturedSubmission.objects.create(
            user=self.user,
            source_anime=self.anime,
            chinese_title=self.anime.chinese_title,
            year=2026,
            audit_status=FeaturedSubmission.AuditStatus.PENDING,
        )

        response = self.client.post(
            reverse(
                "anime_api:featured-submission-request-removal",
                kwargs={"pk": submission.pk},
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_audit_result_transition_creates_user_notification(self):
        submission = FeaturedSubmission.objects.create(
            user=self.user,
            source_anime=self.anime,
            chinese_title=self.anime.chinese_title,
            year=2026,
        )

        submission.audit_status = FeaturedSubmission.AuditStatus.APPROVED
        submission.save(update_fields=("audit_status", "updated_at"))

        notification = UserNotification.objects.get(user=self.user)
        self.assertIn("已被精选专栏收录", notification.message)
        self.assertFalse(notification.is_read)

    def test_submission_detail_is_strictly_user_isolated(self):
        submission = FeaturedSubmission.objects.create(
            user=self.other_user,
            chinese_title="他人的投稿",
            year=2025,
        )

        response = self.client.get(
            reverse(
                "anime_api:featured-submission-detail",
                kwargs={"pk": submission.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_notification_can_only_mark_own_message_as_read(self):
        notification = UserNotification.objects.create(
            user=self.user,
            message="你的精选专栏投稿已进入审核。",
        )
        other_notification = UserNotification.objects.create(
            user=self.other_user,
            message="他人的站内通知。",
        )

        response = self.client.patch(
            reverse(
                "anime_api:notification-detail",
                kwargs={"pk": notification.pk},
            ),
            {"is_read": True, "message": "不允许篡改消息"},
            format="json",
        )
        hidden_response = self.client.get(
            reverse(
                "anime_api:notification-detail",
                kwargs={"pk": other_notification.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertEqual(notification.message, "你的精选专栏投稿已进入审核。")
        self.assertEqual(hidden_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_notification_poll_returns_unread_and_marks_them_read(self):
        unread = UserNotification.objects.create(
            user=self.user,
            message="新的审核结果。",
        )
        UserNotification.objects.create(
            user=self.user,
            message="旧消息。",
            is_read=True,
        )

        first_response = self.client.get(
            reverse("anime_api:notification-poll"),
        )
        second_response = self.client.get(
            reverse("anime_api:notification-poll"),
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(first_response.data), 1)
        self.assertEqual(first_response.data[0]["id"], unread.pk)
        unread.refresh_from_db()
        self.assertTrue(unread.is_read)
        self.assertEqual(second_response.data, [])

    def test_public_featured_endpoint_only_returns_approved_and_filters(self):
        approved = FeaturedSubmission.objects.create(
            user=self.user,
            chinese_title="公开精选作品",
            japanese_title="公開作品",
            year=2026,
            tags=["剧场版", "力荐"],
            audit_status=FeaturedSubmission.AuditStatus.APPROVED,
        )
        FeaturedSubmission.objects.create(
            user=self.other_user,
            chinese_title="仍在审核作品",
            year=2026,
            tags=["剧场版"],
            audit_status=FeaturedSubmission.AuditStatus.PENDING,
        )
        self.client.force_authenticate(user=None)

        response = self.client.get(
            reverse("anime_api:featured-list"),
            {"search": "公开", "tag": "力荐"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        records = response.data.get("results", response.data)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], approved.pk)
        self.assertNotIn("user", records[0])
        self.assertEqual(records[0]["author_name"], "手账主理人")


class AdminFeaturedSubmissionApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="column-owner@example.com",
            email="column-owner@example.com",
            password="OwnerPass123!",
        )
        self.superuser = get_user_model().objects.create_superuser(
            username="root-admin",
            email="root@example.com",
            password="AdminPass123!",
        )

    def create_submission(self, audit_status):
        return FeaturedSubmission.objects.create(
            user=self.user,
            chinese_title="审核测试番剧",
            japanese_title="審査テスト",
            year=2026,
            review="这是一段等待超级管理员处理的长评。",
            audit_status=audit_status,
        )

    def test_regular_user_cannot_access_admin_queue(self):
        self.client.force_authenticate(user=self.user)

        session_response = self.client.get(reverse("anime_api:admin-session"))
        queue_response = self.client.get(
            reverse("anime_api:admin-featured-submission-list")
        )

        self.assertEqual(session_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(queue_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_queue_only_contains_actionable_submissions(self):
        pending = self.create_submission(FeaturedSubmission.AuditStatus.PENDING)
        removal = self.create_submission(
            FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED
        )
        self.create_submission(FeaturedSubmission.AuditStatus.APPROVED)
        self.client.force_authenticate(user=self.superuser)

        response = self.client.get(
            reverse("anime_api:admin-featured-submission-list")
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        records = response.data.get("results", response.data)
        self.assertEqual({item["id"] for item in records}, {pending.pk, removal.pk})
        self.assertEqual(records[0]["submitter"]["email"], self.user.email)

    def test_admin_can_approve_pending_submission_and_notify_owner(self):
        submission = self.create_submission(FeaturedSubmission.AuditStatus.PENDING)
        self.client.force_authenticate(user=self.superuser)

        response = self.client.post(
            reverse(
                "anime_api:admin-featured-submission-approve",
                kwargs={"pk": submission.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(
            submission.audit_status,
            FeaturedSubmission.AuditStatus.APPROVED,
        )
        self.assertTrue(UserNotification.objects.filter(user=self.user).exists())

    def test_admin_can_approve_removal_and_delete_snapshot(self):
        submission = self.create_submission(
            FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED
        )
        submission_id = submission.pk
        self.client.force_authenticate(user=self.superuser)

        response = self.client.post(
            reverse(
                "anime_api:admin-featured-submission-approve",
                kwargs={"pk": submission.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            FeaturedSubmission.objects.filter(pk=submission_id).exists()
        )
        self.assertTrue(
            UserNotification.objects.filter(
                user=self.user,
                message__contains="已按你的申请撤下",
            ).exists()
        )

    def test_admin_can_reject_removal_and_restore_approved_status(self):
        submission = self.create_submission(
            FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED
        )
        self.client.force_authenticate(user=self.superuser)

        response = self.client.post(
            reverse(
                "anime_api:admin-featured-submission-reject",
                kwargs={"pk": submission.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(
            submission.audit_status,
            FeaturedSubmission.AuditStatus.APPROVED,
        )
        self.assertTrue(
            UserNotification.objects.filter(
                user=self.user,
                message__contains="撤下申请未获批准",
            ).exists()
        )
