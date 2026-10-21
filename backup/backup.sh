#!/bin/sh
# Database dump (+ media files) → restic repository.
#
#   backup.sh                      nightly: database + media
#   backup.sh --db-only            database only (before deploys/migrations)
#   backup.sh --db-only --tag X    with an extra tag, e.g. pre-deploy
set -eu
. /usr/local/bin/common.sh

db_only=false
extra_tag=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --db-only) db_only=true ;;
    --tag) shift; extra_tag="$1" ;;
    *) log "unknown option: $1"; exit 2 ;;
  esac
  shift
done

start=$(date +%s)
on_exit() {
  status=$?
  rm -f "$DUMP"
  if [ "$status" -ne 0 ]; then
    log "BACKUP FAILED (exit $status)"
    write_metric petnabor_backup_last_failure_timestamp_seconds "$(date +%s)" \
      "When the last backup failed (unix time)."
  fi
}
trap on_exit EXIT

ensure_repo
mkdir -p "$STAGING"

log "dumping database $POSTGRES_DB"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  -h "${POSTGRES_HOST:-db}" -p "${POSTGRES_PORT:-5432}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --format=custom --compress=6 --file="$DUMP"
log "database dump: $(du -h "$DUMP" | cut -f1)"

paths="$DUMP"
tags="--tag db"
if [ "$db_only" = false ] && [ -d /media ]; then
  paths="$paths /media"
  tags="$tags --tag media"
fi
[ -n "$extra_tag" ] && tags="$tags --tag $extra_tag"

log "uploading: $paths"
# shellcheck disable=SC2086
restic backup --host "$HOST_TAG" $tags --exclude-caches $paths

duration=$(( $(date +%s) - start ))
if [ "$db_only" = false ]; then
  write_metric petnabor_backup_last_success_timestamp_seconds "$(date +%s)" \
    "When the last full (database + media) backup finished (unix time)."
  write_metric petnabor_backup_last_duration_seconds "$duration" \
    "How long the last full backup took (seconds)."
  write_metric petnabor_backup_db_dump_bytes "$(stat -c %s "$DUMP")" \
    "Size of the last database dump (bytes)."
fi
log "backup finished in ${duration}s"
