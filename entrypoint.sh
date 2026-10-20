#!/bin/bash
# bash entrypoint.sh
set -e

# Ensure Django FILE_UPLOAD_TEMP_DIR and MEDIA_ROOT directories exist before startup.
mkdir -p /app/tmp_uploads
mkdir -p /app/media

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "Applying database migrations..."
python manage.py migrate

# Prometheus multi-process metrics (web only; see api/core/metrics.py):
# start each run with an empty folder so old worker numbers don't linger.
if [ -n "$PROMETHEUS_MULTIPROC_DIR" ]; then
    rm -rf "$PROMETHEUS_MULTIPROC_DIR"
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
fi

exec "$@"
