from django.apps import AppConfig


class PostConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api.post'

    def ready(self):
        from . import signals  # noqa: F401
