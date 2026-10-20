#!/bin/bash
# bash entrypoint.sh
set -e

# Prometheus multi-process metrics (web only; see api/core/metrics.py).
# With PROMETHEUS_MULTIPROC_DIR set, EVERY Django process — including the
# collectstatic/migrate below — writes metric files there, so the folder
# must exist before any of them runs (otherwise: FileNotFoundError and the
# container never starts).
reset_metrics_dir() {
    if [ -n "$PROMETHEUS_MULTIPROC_DIR" ]; then
        rm -rf "$PROMETHEUS_MULTIPROC_DIR"
        mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
    fi
}
reset_metrics_dir

# Ensure Django FILE_UPLOAD_TEMP_DIR and MEDIA_ROOT directories exist before startup.
mkdir -p /app/tmp_uploads
mkdir -p /app/media

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "Applying database migrations..."
python manage.py migrate

# Empty it again: the metrics should be the server's, not collectstatic's
# and migrate's.
reset_metrics_dir

exec "$@"
