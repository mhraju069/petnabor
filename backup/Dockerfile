# Backups: nightly database dump + media files → restic repository
# (Backblaze B2 or any S3-compatible storage). See docs/backups.md.
#
# Same PostGIS/Postgres 16 image as the `db` service, so pg_dump matches the
# server, and the monthly restore test can load PostGIS dumps.
FROM postgis/postgis:16-3.4-alpine

RUN apk add --no-cache restic su-exec ca-certificates tzdata

COPY common.sh backup.sh prune.sh restore-test.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/*.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
