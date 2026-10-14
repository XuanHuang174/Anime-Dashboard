"""Explicit safe sandbox for recommendation tests, never Neon or R2.

Use --settings=config.recommendation_test_settings. These process-local values
take precedence over .env before the normal settings module is imported.
"""
import os

for name in ('DATABASE_URL', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
             'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_S3_CUSTOM_DOMAIN'):
    os.environ[name] = ''
os.environ['DEBUG'] = 'True'
os.environ['SECRET_KEY'] = 'isolated-recommendation-tests-not-for-deployment'

from .settings import *  # noqa: F403, E402

DATABASE_URL = ''
RECOMMENDATION_ALGORITHM_VERSION = 'content.v1'  # Tests opt into V2 explicitly.
RECOMMENDATION_V2_TEST_BYPASS = True  # Only this isolated test settings module.
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                      'LOCATION': 'recommendation-isolated-tests'}}
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
