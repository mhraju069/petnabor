#!/bin/sh
# Weekly: drop backups outside the retention policy, then verify the
# repository (structure + a random 5% of the data).
set -eu
. /usr/local/bin/common.sh

KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-12}"

log "applying retention: $KEEP_DAILY daily, $KEEP_WEEKLY weekly, $KEEP_MONTHLY monthly"
restic forget --host "$HOST_TAG" --group-by host,tags \
  --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY" \
  --prune

log "checking repository"
restic check --read-data-subset=5%

write_metric petnabor_backup_last_prune_timestamp_seconds "$(date +%s)" \
  "When retention + repository check last succeeded (unix time)."
log "prune + check done"
