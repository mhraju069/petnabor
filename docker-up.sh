#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Database snapshot before deploying (migrations can't be undone). Skipped
# only if the backup service isn't running yet; a failed backup stops the
# deploy. SKIP_PREDEPLOY_BACKUP=1 to deploy anyway.
if [ "${SKIP_PREDEPLOY_BACKUP:-0}" != "1" ] && docker compose ps --status running --services | grep -qx backup; then
  echo "Backing up the database before deploying…"
  docker compose exec -T backup backup.sh --db-only --tag pre-deploy || {
    echo "Pre-deploy backup failed: not deploying. (SKIP_PREDEPLOY_BACKUP=1 ./docker-up.sh to override)"
    exit 1
  }
fi

# Build and start all services
docker compose up --build -d

# Restart nginx after web is up to avoid stale upstream state
sleep 2
docker compose restart nginx

echo "Docker stack is up and nginx has been restarted."
