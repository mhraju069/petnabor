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

from api.core.app_links import android_asset_links, apple_app_site_association
from api.core.metrics import metrics_view
from api.post.web_share import post_share_page
from api.users import web_account_deletion
from api.users.child_safety import child_safety_standards

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/", include("api.urls")),
    # Prometheus metrics for Grafana — token-protected (api/core/metrics.py).
    path("metrics/", metrics_view, name="prometheus-metrics"),
    # Public child safety standards (Google Play Child Safety Standards).
    path("child-safety/", child_safety_standards, name="child-safety"),
    # Public "delete your account" page (Google Play data-safety link).
    path("delete-account/", web_account_deletion.request_deletion, name="web-delete-account"),
    path(
        "delete-account/confirm/",
        web_account_deletion.confirm_deletion,
        name="web-delete-account-confirm",
    ),
    # Shared post links: open the app (app links), else a web preview.
    path(".well-known/apple-app-site-association", apple_app_site_association),
    path(".well-known/assetlinks.json", android_asset_links),
    re_path(r"^post/(?P<post_id>[0-9a-fA-F-]{36})/?$", post_share_page, name="post-share"),
    # OpenAPI Schema Generation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Serve uploaded media files via Django/Gunicorn (works in both DEBUG and production).
    # WhiteNoise handles /static/ — this handles /media/.
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
