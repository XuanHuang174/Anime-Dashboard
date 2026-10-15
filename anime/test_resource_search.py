import json
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from .resource_search import AnimeResourceSearchView, MAX_RESPONSE_BYTES


@override_settings(
    ANIME_SEARCH_API_URL="https://internal.example.test/",
    ANIME_SEARCH_API_KEY="Bearer server-only-secret",
)
class AnimeResourceSearchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.user = get_user_model()(id=987, username="resource-test")
        self.url = reverse("anime_api:anime-resource-search")

    def call(self, keyword="孤独摇滚", authenticated=True, method="get"):
        request = getattr(self.factory, method)(self.url, {"keyword": keyword})
        if authenticated:
            force_authenticate(request, user=self.user)
        return AnimeResourceSearchView.as_view()(request)

    def upstream(self, payload, status=200, headers=None):
        response = Mock(status_code=status, headers=headers or {})
        response.iter_content.return_value = [json.dumps(payload).encode()]
        return response

    @patch("anime.resource_search.requests.get")
    def test_requires_login_and_get(self, get):
        self.assertIn(self.call(authenticated=False).status_code, (401, 403))
        self.assertEqual(self.call(method="post").status_code, 405)
        get.assert_not_called()

    @patch("anime.resource_search.requests.get")
    def test_encoded_keyword_bearer_and_allowlisted_response(self, get):
        get.return_value = self.upstream([{
            "title": "[字幕组] 孤独摇滚", "magnet": "magnet:?xt=urn:btih:ABC123&dn=test",
            "date": "2026-10-08", "size": "1.2 GB", "uploader": "字幕组",
            "internal_api_key": "must-not-leak",
        }])
        response = self.call(" 孤独摇滚 &x=1/2 ")
        self.assertEqual(response.status_code, 200)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://internal.example.test/search?keyword=%E5%AD%A4%E7%8B%AC%E6%91%87%E6%BB%9A%20%26x%3D1%2F2")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer server-only-secret")
        self.assertEqual(kwargs["timeout"], 10)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("internal_api_key", response.data["results"][0])
        self.assertEqual(response.data["results"][0]["publisher"], "字幕组")
        get.return_value.close.assert_called_once()

    @patch("anime.resource_search.requests.get")
    def test_empty_and_wrapped_results(self, get):
        for payload in ([], {"results": []}, {"data": []}, {"data": {"items": []}}):
            get.return_value = self.upstream(payload)
            response = self.call()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["results"], [])

    @patch("anime.resource_search.requests.get")
    def test_timeout_connection_and_503_are_json_errors(self, get):
        for exception in (requests.Timeout(), requests.ConnectionError()):
            get.side_effect = exception
            response = self.call()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data["status"], "error")
            self.assertIn("正在唤醒", response.data["message"])
        get.side_effect = None
        get.return_value = self.upstream({}, status=503)
        self.assertEqual(self.call().status_code, 503)

    @patch("anime.resource_search.requests.get")
    def test_bad_response_redirect_and_oversize_are_safe(self, get):
        for response in (
            self.upstream({}, status=302), self.upstream({}, status=401),
            self.upstream({"unexpected": "schema"}), self.upstream({"status": "error"}),
            self.upstream([], headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)}),
        ):
            get.return_value = response
            result = self.call()
            self.assertEqual(result.status_code, 502)
            self.assertEqual(result.data["status"], "error")
            response.close.assert_called_once()

    @patch("anime.resource_search.requests.get")
    def test_invalid_json_and_stream_limit(self, get):
        for chunks in ([b"<html>upstream failed</html>"], [b" " * (MAX_RESPONSE_BYTES + 1)]):
            get.return_value = self.upstream([])
            get.return_value.iter_content.return_value = chunks
            self.assertEqual(self.call().status_code, 502)

    @patch("anime.resource_search.requests.get")
    def test_validation_and_missing_config(self, get):
        for keyword in ("", "  ", "x" * 201):
            self.assertEqual(self.call(keyword).status_code, 400)
        with override_settings(ANIME_SEARCH_API_KEY=""):
            self.assertEqual(self.call().status_code, 503)
        for base in ("http://insecure.test", "https://user:pass@host.test", "https://host.test?x=1", "https://["):
            with override_settings(ANIME_SEARCH_API_URL=base):
                self.assertEqual(self.call().status_code, 503)
        get.assert_not_called()

    @patch("anime.resource_search.requests.get")
    def test_raw_key_safe_magnet_and_result_cap(self, get):
        get.return_value = self.upstream({"results": [
            {"title": "unsafe", "magnet": "javascript:alert(1)"},
            *[{"title": "anime", "magnet_link": "magnet:?xt=urn:btih:ABC"}] * 120,
        ]})
        with override_settings(ANIME_SEARCH_API_KEY="raw-secret"):
            response = self.call()
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer raw-secret")
        self.assertEqual(response.data["results"][0]["magnet"], "")
        self.assertEqual(len(response.data["results"]), 100)

    @patch("anime.resource_search.requests.get")
    def test_rate_limit(self, get):
        get.return_value = self.upstream([])
        for _ in range(20):
            self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.call().status_code, 429)
        self.assertEqual(get.call_count, 20)

    @patch("anime.resource_search.requests.get")
    def test_users_have_separate_limits_and_no_shared_result_or_private_payload(self, get):
        get.return_value = self.upstream([])
        for _ in range(20):
            self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.call().status_code, 429)
        self.user = get_user_model()(id=988, username="second-resource-user")
        get.return_value = self.upstream([{"title": "公开资源", "magnet": "magnet:?xt=urn:btih:ABC",
            "user_id": 987, "personal_score": "9.9", "review": "DO_NOT_FORWARD"}])
        response = self.call()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_count, 21)
        self.assertEqual(set(response.data['results'][0]), {'title', 'magnet', 'size', 'published_at', 'publisher'})
        self.assertNotIn('DO_NOT_FORWARD', str(response.data))
