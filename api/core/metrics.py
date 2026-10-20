"""
GET /metrics/ — Prometheus metrics (requests, latency, errors, DB queries)
for Grafana. See monitoring/README.md.

Not public: it answers only with "Authorization: Bearer <METRICS_TOKEN>"
(Prometheus sends it; see monitoring/prometheus/prometheus.yml). With no
METRICS_TOKEN set it's switched off and returns 404, like any unknown URL.

The web server runs several worker processes; django-prometheus combines
them when PROMETHEUS_MULTIPROC_DIR is set (docker-compose web service).
"""

import hmac

from django.conf import settings
from django.http import Http404
from django_prometheus.exports import ExportToDjangoView


def metrics_view(request):
    token = getattr(settings, "METRICS_TOKEN", "")
    if not token:
        raise Http404()
    sent = request.headers.get("Authorization", "")
    if not hmac.compare_digest(sent, f"Bearer {token}"):
        # 404 rather than 401: don't advertise that the endpoint exists.
        raise Http404()
    return ExportToDjangoView(request)
