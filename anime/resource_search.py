"""Authenticated, server-only gateway to the internal AnimeSearch service."""

import json
import logging
import re
import time
from urllib.parse import quote, urlsplit

import requests
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)
SOURCE_UNAVAILABLE = "数据源站点目前开启了防御或正在唤醒，请稍后再试"
MAX_RESPONSE_BYTES = 2_000_000


def _text(item, *names, limit=2000):
    for name in names:
        value = item.get(name)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()[:limit]
    return ""


def normalize_resource_results(payload):
    """Expose only display metadata; never forward upstream settings/errors."""
    if isinstance(payload, dict):
        if payload.get("status") == "error" or payload.get("success") is False:
            raise ValueError("Upstream reported an error")
        payload = payload.get("results", payload.get("data", payload.get("items")))
        if isinstance(payload, dict):
            payload = payload.get("results", payload.get("items"))
    if not isinstance(payload, list):
        raise ValueError("Invalid search result envelope")

    results = []
    for item in payload[:100]:
        if not isinstance(item, dict):
            continue
        title = _text(item, "title", "name")
        if not title:
            continue
        magnet = _text(item, "magnet", "magnet_link", "magnet_url", "magnetLink", limit=8192)
        # Only BT magnet URIs are actionable. Reject javascript/http redirects.
        if not re.match(r"^magnet:\?xt=urn:bt(?:ih|mh):[a-zA-Z0-9]+(?:&|$)", magnet, re.I):
            magnet = ""
        results.append({
            "title": title,
            "published_at": _text(item, "published_at", "publish_time", "date", "time", "pubDate"),
            "size": _text(item, "size", "file_size", "filesize"),
            "publisher": _text(item, "publisher", "uploader", "author", "team"),
            "magnet": magnet,
        })
    return results


@method_decorator(never_cache, name="dispatch")
class AnimeResourceSearchView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    http_method_names = ["get", "options"]

    @method_decorator(ratelimit(
        group="anime-resource-search", key="user", rate="20/m", method="GET", block=False,
    ))
    def get(self, request):
        def error(message, code):
            return Response({"status": "error", "message": message}, status=code)

        if getattr(request, "limited", False):
            return error("搜索过于频繁，请稍后再试。", 429)
        keyword = request.query_params.get("keyword", "").strip()
        if not keyword or len(keyword) > 200:
            return error("请填写 1 到 200 字的搜索关键词。", 400)

        base = settings.ANIME_SEARCH_API_URL.strip().rstrip("/")
        key = re.sub(r"^Bearer\s+", "", settings.ANIME_SEARCH_API_KEY.strip(), flags=re.I).strip()
        try:
            parsed = urlsplit(base)
            valid_url = (parsed.scheme == "https" and parsed.hostname
                         and not parsed.username and not parsed.password
                         and not parsed.query and not parsed.fragment)
        except ValueError:
            valid_url = False
        if not valid_url or not key or not key.isascii() or "\r" in key or "\n" in key:
            return error("资源搜索服务尚未配置，请联系管理员。", 503)

        # Only the configured server can be contacted; keyword cannot alter the target.
        url = f"{base}/search?keyword={quote(keyword, safe='')}"
        response = None
        started = time.monotonic()
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                timeout=10,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code in (503, 504):
                return error(SOURCE_UNAVAILABLE, 503)
            if response.status_code != 200:
                logger.warning("AnimeSearch upstream HTTP status=%s", response.status_code)
                return error("资源搜索暂时不可用，请稍后再试或使用网页搜索。", 502)
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > MAX_RESPONSE_BYTES:
                raise ValueError("Search response too large")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16 * 1024):
                if time.monotonic() - started > 10:
                    raise requests.Timeout()
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("Search response too large")
            results = normalize_resource_results(json.loads(body))
            return Response({"status": "success", "keyword": keyword, "results": results})
        except requests.Timeout:
            return error(SOURCE_UNAVAILABLE, 503)
        except requests.RequestException:
            # Do not log exception strings: they can contain internal service URLs.
            logger.warning("AnimeSearch upstream connection failed")
            return error(SOURCE_UNAVAILABLE, 503)
        except (ValueError, TypeError):
            logger.warning("AnimeSearch upstream returned invalid data")
            return error("资源搜索返回的数据异常，请稍后再试或使用网页搜索。", 502)
        finally:
            if response is not None:
                response.close()
