from django.apps import AppConfig


class AnimeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'anime'
    verbose_name = '番剧与追番管理'

    def ready(self):
        # Register lifecycle rules used by both REST API and Django Admin deletes.
        from . import signals  # noqa: F401
