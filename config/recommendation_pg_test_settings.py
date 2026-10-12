"""Optional LOCAL PostgreSQL concurrency tests. NEVER falls back to DATABASE_URL."""
import os
from urllib.parse import urlsplit
from django.core.exceptions import ImproperlyConfigured

test_url = os.environ.get('RECOMMENDATION_TEST_DATABASE_URL', '').strip()
try:
    parsed = urlsplit(test_url)
    safe = (parsed.scheme in ('postgres', 'postgresql') and parsed.hostname in ('localhost', '127.0.0.1', '::1')
            and parsed.path == '/recommendation_isolated_tests' and not parsed.query and not parsed.fragment)
except ValueError:
    safe = False
if not safe:
    raise ImproperlyConfigured('必须显式设置独立本机 PostgreSQL 测试库 RECOMMENDATION_TEST_DATABASE_URL；禁止使用正式 DATABASE_URL。')

# Loads safe defaults; blanks production DB and R2 variables BEFORE .env loading.
from .recommendation_test_settings import *  # noqa: F403, E402
import dj_database_url

DATABASES = {'default': dj_database_url.parse(test_url)}
DATABASES['default']['TEST'] = {'NAME': 'test_recommendation_isolated_tests'}
DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True
