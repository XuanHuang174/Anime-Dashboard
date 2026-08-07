"""Metadata clients for the public Bangumi and Moegirlpedia APIs."""

import hashlib
import io
import logging
import re
import uuid
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from PIL import Image, UnidentifiedImageError

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile


logger = logging.getLogger(__name__)


class MetadataScraperError(RuntimeError):
    """Raised when a required third-party metadata request fails."""


class MetadataHTTPStatusError(MetadataScraperError):
    """Preserve an upstream non-200 response for per-node diagnostics."""

    def __init__(self, url, status_code, response_text):
        self.url = url
        self.status_code = status_code
        self.response_text = response_text
        super().__init__(f"第三方服务返回异常状态码：{status_code}")


class MetadataScraper:
    BANGUMI_OFFICIAL_API = "https://api.bgm.tv"
    BANGUMI_IMAGE_PROXY_SOURCE_HOSTS = frozenset({"lain.bgm.tv"})
    BANGUMI_OFFICIAL_IMAGE_HOSTS = frozenset(
        {
            "lain.bgm.tv",
            # Public mirror paired with api.bangumi.lol. Requests never carry
            # cookies or Bangumi access tokens; it is used for public artwork
            # only when the configured/official CDN is unreachable locally.
            "lain.bangumi.lol",
        }
    )
    MOEGIRL_API_URL = "https://zh.moegirl.org.cn/api.php"
    REQUEST_TIMEOUT = 8
    SEARCH_CACHE_SECONDS = 5 * 60
    DETAILS_CACHE_SECONDS = 60 * 60
    MOEGIRL_CACHE_SECONDS = 6 * 60 * 60
    # Bangumi requires third-party clients to identify themselves instead of
    # using a library/default crawler User-Agent.
    USER_AGENT = "XuanHuang/xh-anime-db (https://xh-anime.com)"
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
    RESPONSE_LOG_LIMIT = 1200
    MAX_POSTER_BYTES = 5 * 1024 * 1024
    POSTER_CONTENT_TYPES = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/pjpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    POSTER_IMAGE_FORMATS = {
        "JPEG": ".jpg",
        "PNG": ".png",
        "WEBP": ".webp",
        "GIF": ".gif",
    }

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
        headers=None,
    ):
        # Build a fresh, minimal header dictionary for every request. In
        # particular, never forward browser/session Cookies to a proxy node.
        request_headers = {
            "Accept": "application/json",
            "User-Agent": cls.USER_AGENT,
        }
        for key, value in (headers or {}).items():
            if str(key).casefold() != "cookie":
                request_headers[str(key)] = str(value)

        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=payload,
            headers=request_headers,
            timeout=cls.REQUEST_TIMEOUT,
        )
        if response.status_code != requests.codes.ok:
            raise MetadataHTTPStatusError(
                response.url or url,
                response.status_code,
                response.text,
            )

        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise MetadataScraperError("第三方服务返回了无法解析的数据。") from exc

    @classmethod
    def _response_log_preview(cls, value):
        normalized = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(normalized) <= cls.RESPONSE_LOG_LIMIT:
            return normalized
        return f"{normalized[:cls.RESPONSE_LOG_LIMIT]}…[响应内容已截断]"

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

        proxy_node = getattr(settings, "BANGUMI_PROXY_API", "")
        if getattr(settings, "BANGUMI_PREFER_FALLBACK", False):
            candidates = (*configured_nodes, proxy_node, cls.BANGUMI_OFFICIAL_API)
        else:
            candidates = (proxy_node, *configured_nodes, cls.BANGUMI_OFFICIAL_API)
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
            url = f"{node}/v0/{endpoint}"
            try:
                return cls._request_json(
                    url,
                    method=method,
                    params=params,
                    payload=payload,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": cls.USER_AGENT,
                    },
                )
            except MetadataHTTPStatusError as exc:
                last_error = exc
                logger.error(
                    "[Bangumi API 状态异常] 节点: %s, 状态码: %s, 返回内容: %s",
                    exc.url or url,
                    exc.status_code,
                    cls._response_log_preview(exc.response_text),
                )
            except requests.exceptions.RequestException as exc:
                last_error = exc
                logger.error(
                    "[Bangumi API 节点故障] 节点: %s, 报错原因: %s",
                    url,
                    exc,
                )
            except MetadataScraperError as exc:
                last_error = exc
                logger.error(
                    "[Bangumi API 响应异常] 节点: %s, 报错原因: %s",
                    url,
                    exc,
                )

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
            for host in cls.BANGUMI_IMAGE_PROXY_SOURCE_HOSTS
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

    @classmethod
    def _is_bangumi_image_url(cls, value):
        """Only download trusted Bangumi or configured proxy image hosts."""

        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False

        allowed_hosts = set(cls.BANGUMI_OFFICIAL_IMAGE_HOSTS)
        proxy = urlsplit(
            str(getattr(settings, "BANGUMI_PROXY_IMAGE", "") or "").strip()
        )
        if proxy.hostname:
            allowed_hosts.add(proxy.hostname.casefold())

        source_host = parsed.hostname.casefold()
        return any(
            source_host == host or source_host.endswith(f".{host}")
            for host in allowed_hosts
        )

    @classmethod
    def download_bangumi_poster(cls, poster_url):
        """Download a Bangumi poster into memory for an ImageField upload.

        Returning ``None`` is deliberate: remote media is optional and a CDN,
        proxy, image decoder, or object-storage outage must never abort the
        user's anime-record creation request.
        """

        poster_url = str(poster_url or "").strip()
        if not cls._is_bangumi_image_url(poster_url):
            return None

        try:
            response = requests.get(
                poster_url,
                headers={
                    "User-Agent": cls.BROWSER_USER_AGENT,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=cls.REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            content_type = content_type.split(";", 1)[0].strip().casefold()
            content = response.content
            if not content or len(content) > cls.MAX_POSTER_BYTES:
                raise ValueError("poster is empty or exceeds the 5 MB limit")

            with Image.open(io.BytesIO(content)) as image:
                image_format = str(image.format or "").upper()
                image.verify()
            if image_format not in cls.POSTER_IMAGE_FORMATS:
                raise ValueError(f"unsupported poster image format: {image_format}")

            url_suffix = PurePosixPath(urlsplit(poster_url).path).suffix.casefold()
            suffix = (
                url_suffix
                if url_suffix in set(cls.POSTER_CONTENT_TYPES.values())
                else cls.POSTER_CONTENT_TYPES.get(
                    content_type,
                    cls.POSTER_IMAGE_FORMATS[image_format],
                )
            )
            return ContentFile(
                content,
                name=f"bangumi-{uuid.uuid4().hex}{suffix}",
            )
        except (
            requests.exceptions.RequestException,
            OSError,
            UnidentifiedImageError,
            ValueError,
        ) as exc:
            logger.warning(
                "[Bangumi poster download failed] url=%s reason=%s",
                poster_url,
                exc,
            )
            return None

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
    def get_user_anime_collections(cls, uid, *, offset=0, limit=20):
        """Return one official Bangumi anime-collection page.

        The v0 endpoint already returns ``total / limit / offset / data``.
        Keeping this method page-based prevents the Django request from ever
        pulling a user's entire collection into memory.
        """

        uid = str(uid or "").strip()
        if not uid or len(uid) > 64:
            raise ValueError("请输入有效的 Bangumi UID。")
        try:
            offset = max(0, int(offset))
            limit = max(1, min(20, int(limit)))
        except (TypeError, ValueError) as exc:
            raise ValueError("分页参数必须是整数。") from exc

        payload = cls._safe_request(
            f"users/{quote(uid, safe='')}/collections",
            params={
                "subject_type": 2,
                "offset": offset,
                "limit": limit,
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise MetadataScraperError("Bangumi 收藏接口返回了异常的数据结构。")
        return payload

    @classmethod
    def proxy_bangumi_image_url(cls, value):
        """Public wrapper used by import views for collection thumbnails."""

        return cls._proxy_image_url(value)

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

        fallback_url = f"https://zh.moegirl.org.cn/{quote(keyword, safe='')}"
        try:
            response = requests.get(
                cls.MOEGIRL_API_URL,
                params={
                    "action": "opensearch",
                    "search": keyword,
                    "limit": 1,
                    "namespace": 0,
                    "format": "json",
                },
                headers={
                    "User-Agent": cls.BROWSER_USER_AGENT,
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=cls.REQUEST_TIMEOUT,
            )
            if response.status_code != requests.codes.ok:
                raise requests.exceptions.HTTPError(
                    f"Moegirl API returned HTTP {response.status_code}",
                    response=response,
                )
            payload = response.json()
            urls = payload[3] if isinstance(payload, list) and len(payload) > 3 else []
            result = str(urls[0]).strip() if urls else fallback_url
        except (
            requests.exceptions.RequestException,
            requests.exceptions.JSONDecodeError,
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            logger.warning(
                "[Moegirl API fallback] keyword=%s reason=%s",
                keyword,
                exc,
            )
            result = fallback_url

        cache.set(cache_key, result, cls.MOEGIRL_CACHE_SECONDS)
        return result


search_bangumi = MetadataScraper.search_bangumi
get_bangumi_details = MetadataScraper.get_bangumi_details
get_moegirl_url = MetadataScraper.get_moegirl_url
