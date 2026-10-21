#!/bin/sh
# Monthly: prove the latest backup restores. Downloads the newest database
# dump, loads it into a throwaway Postgres inside this container, checks
# that users exist, then deletes everything. Production is never touched.
set -eu
. /usr/local/bin/common.sh

WORK=/restore-test
PORT=5499
SOCK=/tmp

cleanup() {
  su-exec postgres pg_ctl -D "$WORK/data" -m immediate stop > /dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

rm -rf "$WORK"
mkdir -p "$WORK/out" "$WORK/data"

log "restoring latest database dump"
restic restore latest --host "$HOST_TAG" --tag db --target "$WORK/out" --include "$DUMP"
dump="$WORK/out$DUMP"
[ -s "$dump" ] || { log "ERROR: no dump in the latest snapshot"; exit 1; }

chown -R postgres:postgres "$WORK"
su-exec postgres initdb -D "$WORK/data" -A trust -U postgres > /dev/null
su-exec postgres pg_ctl -D "$WORK/data" -w -o "-p $PORT -k $SOCK -c listen_addresses=''" start > /dev/null

psql -h "$SOCK" -p "$PORT" -U postgres -qc "CREATE DATABASE restore_check"
# Ownership/privilege notices are expected (roles differ); the row checks
# below decide whether the restore worked.
pg_restore -h "$SOCK" -p "$PORT" -U postgres -d restore_check --no-owner --no-privileges "$dump" \
  > "$WORK/restore.log" 2>&1 || log "pg_restore reported $(grep -c 'error' "$WORK/restore.log" || true) error line(s)"

users=$(psql -h "$SOCK" -p "$PORT" -U postgres -d restore_check -Atc "SELECT count(*) FROM users_user")
messages=$(psql -h "$SOCK" -p "$PORT" -U postgres -d restore_check -Atc "SELECT count(*) FROM messaging_message" || echo 0)

if [ "${users:-0}" -le 0 ]; then
  log "ERROR: restored database has no users"
  tail -n 20 "$WORK/restore.log"
  exit 1
fi

write_metric petnabor_backup_restore_test_last_success_timestamp_seconds "$(date +%s)" \
  "When the last restore test succeeded (unix time)."
write_metric petnabor_backup_restore_test_users "$users" \
  "Users in the database restored by the last restore test."
log "restore test OK: $users users, $messages messages"
