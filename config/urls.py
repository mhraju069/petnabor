"""
Main URL configuration for the PetNabor project.
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from api.core.metrics import metrics_view
from api.users import web_account_deletion

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/", include("api.urls")),
    # Prometheus metrics for Grafana — token-protected (api/core/metrics.py).
    path("metrics/", metrics_view, name="prometheus-metrics"),
    # Public "delete your account" page (Google Play data-safety link).
    path("delete-account/", web_account_deletion.request_deletion, name="web-delete-account"),
    path(
        "delete-account/confirm/",
        web_account_deletion.confirm_deletion,
        name="web-delete-account-confirm",
    ),
    # OpenAPI Schema Generation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Serve uploaded media files via Django/Gunicorn (works in both DEBUG and production).
    # WhiteNoise handles /static/ — this handles /media/.
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
