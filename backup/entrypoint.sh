#!/bin/sh
# Schedules the backup jobs with cron, or runs a command given to the
# container (e.g. `docker compose run --rm backup restic snapshots`).
set -eu

# Cron jobs don't get the container's environment: save it for them.
umask 077
export -p > /etc/backup.env

if [ "$#" -gt 0 ]; then
  # One-off commands (restic snapshots / restore …) get the same setup as
  # the scripts: the backup keys, not the app's AWS_* keys.
  . /usr/local/bin/common.sh
  exec "$@"
fi

: "${BACKUP_CRON:=0 3 * * *}"          # nightly backup (server time, UTC by default)
: "${PRUNE_CRON:=30 4 * * 0}"          # weekly: apply retention + verify
: "${RESTORE_TEST_CRON:=0 5 1 * *}"    # monthly: restore into a scratch DB

cat > /etc/crontabs/root <<EOF
$BACKUP_CRON /usr/local/bin/backup.sh > /proc/1/fd/1 2>&1
$PRUNE_CRON /usr/local/bin/prune.sh > /proc/1/fd/1 2>&1
$RESTORE_TEST_CRON /usr/local/bin/restore-test.sh > /proc/1/fd/1 2>&1
EOF

echo "[backup] scheduled: backup '$BACKUP_CRON', prune '$PRUNE_CRON', restore test '$RESTORE_TEST_CRON'"
exec crond -f -l 8
