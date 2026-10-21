# Backups & data retention

The database and uploaded media are backed up **off the server** every night
by the `backup` service (`backup/`, `docker-compose.yml`), with
[restic](https://restic.net) to a Backblaze B2 bucket (any S3-compatible
storage works). Backups are encrypted on the server before upload.

| What | When | Kept |
|---|---|---|
| Database dump + media files | nightly, 03:00 UTC | 7 daily, 4 weekly, 12 monthly |
| Database dump only (`pre-deploy` tag) | every `./docker-up.sh` | same rule |
| Retention + repository check | Sundays 04:30 UTC | — |
| Restore test (throwaway database) | 1st of the month, 05:00 UTC | — |

If a backup or the restore test stops succeeding, Grafana e-mails
`ALERT_EMAILS` ("Backups missing" after 26 h, "Backup restore test failing"
after 35 days). Those alerts need the monitoring stack running
(`monitoring/README.md`); backups run either way.

---

## One-time setup

### 1. Backblaze B2 (≈ $6/TB/month, first 10 GB free)

1. Create an account at backblaze.com → **B2 Cloud Storage**.
2. **Create a bucket**: name e.g. `petnabor-backups`, **Private**,
   **Default encryption: enable**, **Object Lock: enable**.
3. Bucket → **Object Lock** → default retention **Governance, 30 days**.
   Even with the server's key, nothing can be really deleted for 30 days
   (protects against a hacked server or a mistake).
4. Bucket → **Lifecycle settings** → *Keep prior versions for 30 days*.
5. **Application Keys → Add a New Application Key**: name `petnabor-backup`,
   access **only this bucket**, **Read and Write**. Copy the `keyID` and
   `applicationKey` (shown once).
6. Note the bucket's **Endpoint** (e.g. `s3.us-west-004.backblazeb2.com`).

### 2. Server `.env`

```bash
RESTIC_REPOSITORY=s3:https://<endpoint>/petnabor-backups/restic
RESTIC_PASSWORD=<long random password>     # openssl rand -base64 32
BACKUP_S3_ACCESS_KEY_ID=<keyID>
BACKUP_S3_SECRET_ACCESS_KEY=<applicationKey>
```

**Save `RESTIC_PASSWORD` in your password manager now.** Backups are
encrypted with it: if the server is lost and the password with it, the
backups can't be read by anyone, including you.

### 3. Start it and take the first backup

```bash
docker compose up -d --build backup
docker compose exec backup backup.sh          # first one: creates the repository
docker compose exec backup restore-test.sh    # proves it restores
docker compose run --rm backup restic snapshots
```

With monitoring: recreate node-exporter so it reads the backup metrics:
`docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d node-exporter grafana`

---

## Everyday commands

```bash
docker compose logs --tail=80 backup                       # last runs
docker compose run --rm backup restic snapshots            # list backups
docker compose exec backup backup.sh --db-only --tag manual  # database only, now
docker compose exec backup backup.sh                       # full backup, now
```

Deploys: `./docker-up.sh` takes a database backup first and stops if it
fails. Deploying with `git pull` + restart instead? Run
`docker compose exec backup backup.sh --db-only --tag pre-deploy` before any
change that has migrations.

---

## Restoring

> Restoring replaces production data. Take a fresh `--db-only` backup of the
> current state first, even if it's broken.

**Database** (latest backup; for an older one use its ID from
`restic snapshots` instead of `latest`):

```bash
docker compose stop web celery
docker compose run --rm -v "$PWD/restore:/restore" backup \
  restic restore latest --tag db --target /restore --include /staging/petnabor.dump
docker compose cp restore/staging/petnabor.dump db:/tmp/petnabor.dump
docker compose exec db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner /tmp/petnabor.dump'
docker compose start web celery
rm -rf restore
```

**Media files:**

```bash
docker compose run --rm -v "$PWD/restore:/restore" backup \
  restic restore latest --tag media --target /restore --include /media
# then copy restore/media/* into the media volume, e.g.:
docker compose cp restore/media/. web:/app/media/
```

**New server:** install Docker, clone the repo, restore `.env` from your
password manager, `docker compose up -d db backup`, restore the database and
media as above, then `./docker-up.sh`.

---

## Data retention

| Data | Rule |
|---|---|
| In-app notifications | Deleted after 90 days if read, 180 if unread (nightly task `purge_old_notifications`; `NOTIFICATION_RETENTION_*_DAYS`) |
| Stories | Deleted 24 h after posting (existing task) |
| Chat messages | Kept. "Delete for everyone" wipes the content immediately |
| Tips | Kept (financial records; the user link is cleared when an account is deleted) |
| Withdrawals (payouts) | Kept, same way; each keeps its Stripe payout ID and the Stripe account (`acct_…`) it was paid from |
| Server logs (Loki) | 14 days |
| Metrics (Prometheus) | 15 days |
| Backups | Up to 12 months (monthly), plus 30 days of Object Lock |

**Deleted accounts and backups:** an account deleted in the app is removed
from the live database at once, but stays in backups until they age out
(up to ~13 months). Never restore a backup to bring a deleted user back; after
any restore, re-run deletions made since that backup. The privacy policy and
the Play Store / App Store data-safety answers should say deleted data is
removed from backups within 13 months.
