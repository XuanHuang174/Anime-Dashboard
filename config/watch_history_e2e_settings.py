"""Local synthetic A3 browser fixtures only. Never reads production DB or media."""
from .recommendation_test_settings import *  # noqa: F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / '.tools' / 'watch-history-a3.sqlite3'}}  # noqa: F405
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
REST_FRAMEWORK = {**REST_FRAMEWORK, 'DEFAULT_THROTTLE_CLASSES': []}  # noqa: F405
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
