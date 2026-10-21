#!/bin/sh
# Shared setup for the backup scripts (sourced, not run).

# Cron jobs: load the container environment saved by entrypoint.sh.
if [ -f /etc/backup.env ]; then
  # shellcheck disable=SC1091
  . /etc/backup.env
fi

log() { echo "[backup] $(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"; }

require() {
  for name in "$@"; do
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
      log "ERROR: $name is not set (see docs/backups.md)"
      exit 1
    fi
  done
}

require RESTIC_REPOSITORY RESTIC_PASSWORD BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET_ACCESS_KEY \
  POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD

# restic's S3 backend reads AWS_* names; the backup keys use their own
# names so they never mix with the app's storage/e-mail keys in .env.
export AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY"
export RESTIC_REPOSITORY RESTIC_PASSWORD

HOST_TAG="${BACKUP_HOST_TAG:-petnabor}"
STAGING=/staging
DUMP="$STAGING/petnabor.dump"

# Metrics for Grafana alerts: node-exporter reads /metrics/*.prom.
write_metric() {
  name="$1"; value="$2"; help="$3"
  mkdir -p /metrics
  tmp="/metrics/.$name.tmp"
  printf '# HELP %s %s\n# TYPE %s gauge\n%s %s\n' "$name" "$help" "$name" "$name" "$value" > "$tmp"
  mv "$tmp" "/metrics/$name.prom"
}

ensure_repo() {
  if ! restic cat config > /dev/null 2>&1; then
    log "no repository yet: initialising $RESTIC_REPOSITORY"
    restic init
  fi
}
