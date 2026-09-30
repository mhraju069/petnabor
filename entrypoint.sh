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

exec "$@"
