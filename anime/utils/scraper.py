"""Metadata clients for the public Bangumi and Moegirlpedia APIs."""

import hashlib
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache


class MetadataScraperError(RuntimeError):
    """Raised when a required third-party metadata request fails."""


class MetadataScraper:
    BANGUMI_OFFICIAL_API = "https://api.bgm.tv"
    BANGUMI_OFFICIAL_IMAGE_HOSTS = frozenset({"lain.bgm.tv"})
    MOEGIRL_API_URL = "https://zh.moegirl.org.cn/api.php"
    REQUEST_TIMEOUT = 8
    SEARCH_CACHE_SECONDS = 5 * 60
    DETAILS_CACHE_SECONDS = 60 * 60
    MOEGIRL_CACHE_SECONDS = 6 * 60 * 60
    USER_AGENT = getattr(
        settings,
        "BANGUMI_USER_AGENT",
        (
            "XuanHuang174/Anime-Dashboard "
            "(https://github.com/XuanHuang174/Anime-Dashboard)"
        ),
    )

    @staticmethod
    def _cache_key(namespace, value):
        digest = hashlib.sha256(str(value).casefold().encode("utf-8")).hexdigest()
        return f"metadata-scraper:{namespace}:{digest}"

    @classmethod
    def _request_json(
        cls,
        url,
        *,
        method="GET",
        params=None,
        payload=None,
    ):
        if params:
            url = f"{url}?{urlencode(params)}"

        body = None
        headers = {
            "Accept": "application/json",
            "User-Agent": cls.USER_AGENT,
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=cls.REQUEST_TIMEOUT) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))
        except HTTPError as exc:
            raise MetadataScraperError(
                f"第三方服务返回异常状态码：{exc.code}"
            ) from exc
        except (URLError, socket.timeout, TimeoutError) as exc:
            raise MetadataScraperError("第三方服务连接超时或暂时不可用。") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MetadataScraperError("第三方服务返回了无法解析的数据。") from exc

    @staticmethod
    def _normalize_origin(value):
        origin = str(value or "").strip().rstrip("/")
        if not origin:
            return ""

        # BANGUMI_API_NODES stores origins. Accepting a mistakenly appended
        # `/v0` here avoids producing `/v0/v0/...` without changing the
        # documented environment-variable format.
        if origin.casefold().endswith("/v0"):
            origin = origin[:-3].rstrip("/")

        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    @classmethod
    def _bangumi_api_nodes(cls):
        configured_nodes = getattr(settings, "BANGUMI_API_NODES", ())
        if isinstance(configured_nodes, str):
            configured_nodes = (configured_nodes,)

        candidates = (
            getattr(settings, "BANGUMI_PROXY_API", ""),
            *configured_nodes,
            cls.BANGUMI_OFFICIAL_API,
        )
        nodes = []
        for candidate in candidates:
            node = cls._normalize_origin(candidate)
            if node and node not in nodes:
                nodes.append(node)
        return tuple(nodes)

    @classmethod
    def _safe_request(
        cls,
        endpoint,
        *,
        method="GET",
        params=None,
        payload=None,
    ):
        """Try the configured Bangumi proxy first, then official fallbacks."""

        endpoint = str(endpoint or "").lstrip("/")
        last_error = None
        for node in cls._bangumi_api_nodes():
            try:
                return cls._request_json(
                    f"{node}/v0/{endpoint}",
                    method=method,
                    params=params,
                    payload=payload,
                )
            except MetadataScraperError as exc:
                last_error = exc

        if last_error is not None:
            raise MetadataScraperError(
                "Bangumi 所有 API 节点均暂时不可用。"
            ) from last_error
        raise MetadataScraperError("未配置可用的 Bangumi API 节点。")

    @classmethod
    def _proxy_image_url(cls, value):
        """Replace only official Bangumi CDN origins and preserve the URL tail."""

        original = str(value or "").strip()
        if not original:
            return ""

        source_url = f"https:{original}" if original.startswith("//") else original
        source = urlsplit(source_url)
        source_host = (source.hostname or "").casefold()
        is_official_image = any(
            source_host == host or source_host.endswith(f".{host}")
            for host in cls.BANGUMI_OFFICIAL_IMAGE_HOSTS
        )
        if not is_official_image:
            return original

        proxy_origin = str(
            getattr(settings, "BANGUMI_PROXY_IMAGE", "") or ""
        ).strip().rstrip("/")
        if proxy_origin and "://" not in proxy_origin:
            proxy_origin = f"https://{proxy_origin}"
        proxy = urlsplit(proxy_origin)
        if proxy.scheme not in {"http", "https"} or not proxy.netloc:
            return original

        return urlunsplit(
            (
                proxy.scheme,
                proxy.netloc,
                source.path,
                source.query,
                source.fragment,
            )
        )

    @staticmethod
    def _as_text(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            nested = value.get("v", value.get("value", value.get("k", "")))
            return MetadataScraper._as_text(nested)
        if isinstance(value, list):
            parts = []
            for item in value:
                text = MetadataScraper._as_text(item)
                if text and text not in parts:
                    parts.append(text)
            return "、".join(parts)
        return str(value).strip()

    @staticmethod
    def _normalize_infobox_key(value):
        return re.sub(r"[\s:：()（）]", "", str(value or "")).casefold()

    @classmethod
    def _get_infobox_value(cls, infobox, aliases):
        normalized_aliases = {
            cls._normalize_infobox_key(alias)
            for alias in aliases
        }
        entries = []
        for item in infobox or []:
            if not isinstance(item, dict):
                continue
            key = cls._normalize_infobox_key(item.get("key"))
            value = cls._as_text(item.get("value"))
            if key and value:
                entries.append((key, value))

        for key, value in entries:
            if key in normalized_aliases:
                return value
        for key, value in entries:
            if any(alias in key for alias in normalized_aliases):
                return value
        return ""

    @staticmethod
    def _to_airing_period(value):
        match = re.search(r"(?P<year>\d{4})(?:\D+(?P<month>\d{1,2}))?", value or "")
        if not match:
            return value.strip() if isinstance(value, str) else ""

        year = match.group("year")
        month_text = match.group("month")
        if not month_text:
            return year

        month = max(1, min(12, int(month_text)))
        season_month = (1, 4, 7, 10)[(month - 1) // 3]
        return f"{year}-{season_month}"

    @staticmethod
    def _top_tags(tags):
        valid_tags = [
            tag
            for tag in (tags or [])
            if isinstance(tag, dict) and str(tag.get("name", "")).strip()
        ]
        valid_tags.sort(
            key=lambda tag: int(tag.get("count") or 0),
            reverse=True,
        )
        names = []
        for tag in valid_tags:
            name = str(tag["name"]).strip()
            if name not in names:
                names.append(name)
            if len(names) == 8:
                break
        return ",".join(names)

    @classmethod
    def _get_studio_from_persons(cls, subject_id):
        """Fallback for subjects whose infobox omits the animation studio."""

        try:
            persons = cls._safe_request(f"subjects/{subject_id}/persons")
        except MetadataScraperError:
            return ""

        studio_relations = {
            cls._normalize_infobox_key("动画制作"),
            cls._normalize_infobox_key("動畫製作"),
            cls._normalize_infobox_key("アニメーション制作"),
        }
        studios = []
        for person in persons if isinstance(persons, list) else []:
            if not isinstance(person, dict):
                continue
            relation = cls._normalize_infobox_key(person.get("relation"))
            name = cls._as_text(person.get("name"))
            if relation in studio_relations and name and name not in studios:
                studios.append(name)
        return "、".join(studios)

    @classmethod
    def search_bangumi(cls, keyword):
        keyword = str(keyword or "").strip()
        if not keyword:
            return []

        image_proxy = getattr(settings, "BANGUMI_PROXY_IMAGE", "")
        cache_key = cls._cache_key(
            "bangumi-search",
            f"{keyword}:{image_proxy}",
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        payload = cls._safe_request(
            "search/subjects",
            method="POST",
            params={"limit": 5, "offset": 0},
            payload={
                "keyword": keyword,
                "sort": "match",
                "filter": {
                    "type": [2],
                    "nsfw": False,
                },
            },
        )
        if not isinstance(payload, dict):
            raise MetadataScraperError("Bangumi 搜索接口返回了异常的数据结构。")

        results = []
        for subject in payload.get("data", [])[:5]:
            if not isinstance(subject, dict):
                continue
            images = subject.get("images") or {}
            if not isinstance(images, dict):
                images = {}
            results.append(
                {
                    "id": subject.get("id"),
                    "chinese_title": (
                        subject.get("name_cn") or subject.get("name") or ""
                    ).strip(),
                    "japanese_title": (subject.get("name") or "").strip(),
                    "thumbnail": (
                        cls._proxy_image_url(
                            images.get("grid")
                            or images.get("small")
                            or images.get("medium")
                            or ""
                        )
                    ),
                }
            )

        cache.set(cache_key, results, cls.SEARCH_CACHE_SECONDS)
        return results

    @classmethod
    def get_bangumi_details(cls, subject_id):
        try:
            subject_id = int(subject_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Bangumi 条目 ID 必须是正整数。") from exc
        if subject_id <= 0:
            raise ValueError("Bangumi 条目 ID 必须是正整数。")

        image_proxy = getattr(settings, "BANGUMI_PROXY_IMAGE", "")
        cache_key = cls._cache_key(
            "bangumi-details",
            f"{subject_id}:{image_proxy}",
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        subject = cls._safe_request(f"subjects/{subject_id}")
        if not isinstance(subject, dict):
            raise MetadataScraperError("Bangumi 详情接口返回了异常的数据结构。")

        infobox = subject.get("infobox") or []
        air_date = cls._get_infobox_value(
            infobox,
            (
                "放送开始",
                "放送開始",
                "首播时间",
                "首播日期",
                "上映年度",
            ),
        ) or cls._as_text(subject.get("date"))
        studio = cls._get_infobox_value(
            infobox,
            (
                "动画制作",
                "動畫製作",
                "アニメーション制作",
                "动画制作公司",
                "制作公司",
            ),
        )
        if not studio:
            studio = cls._get_studio_from_persons(subject_id)
        episodes = (
            cls._as_text(subject.get("eps"))
            or cls._get_infobox_value(
                infobox,
                ("话数", "話数", "集数", "集數"),
            )
        )
        images = subject.get("images") or {}
        if not isinstance(images, dict):
            images = {}

        details = {
            "bangumi_id": subject_id,
            "chinese_title": (
                subject.get("name_cn") or subject.get("name") or ""
            ).strip(),
            "japanese_title": (subject.get("name") or "").strip(),
            "air_date": air_date,
            "airing_period": cls._to_airing_period(air_date),
            "studio": studio,
            "episodes": episodes,
            "description": cls._as_text(subject.get("summary")),
            "poster_url": cls._proxy_image_url(
                images.get("large")
                or images.get("common")
                or images.get("medium")
                or ""
            ),
            "tags": cls._top_tags(subject.get("tags")),
        }
        cache.set(cache_key, details, cls.DETAILS_CACHE_SECONDS)
        return details

    @classmethod
    def get_moegirl_url(cls, keyword):
        keyword = str(keyword or "").strip()
        if not keyword:
            return ""

        cache_key = cls._cache_key("moegirl", keyword)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            payload = cls._request_json(
                cls.MOEGIRL_API_URL,
                params={
                    "action": "opensearch",
                    "search": keyword,
                    "limit": 1,
                    "namespace": 0,
                    "format": "json",
                    "origin": "*",
                },
            )
            urls = payload[3] if isinstance(payload, list) and len(payload) > 3 else []
            result = urls[0] if urls else ""
        except (MetadataScraperError, IndexError, TypeError):
            # Moegirlpedia occasionally rejects anonymous traffic. Autofill
            # should still return the useful Bangumi metadata in that case.
            result = ""

        cache.set(cache_key, result, cls.MOEGIRL_CACHE_SECONDS)
        return result


search_bangumi = MetadataScraper.search_bangumi
get_bangumi_details = MetadataScraper.get_bangumi_details
get_moegirl_url = MetadataScraper.get_moegirl_url
