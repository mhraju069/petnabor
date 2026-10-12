"""
Find (and optionally delete) Cloudinary media that no database row points to.

Until the storage delete fix, every "compress, then delete the original"
task (posts, stories, pets, profiles, comments) left the original behind,
and deleted/expired stories never removed their files. This lists those
leftovers.

    python manage.py cleanup_orphan_media            # dry run: report only
    python manage.py cleanup_orphan_media --delete   # actually delete

Only files older than --min-age-hours (default 24) are considered, so an
upload whose row is still being written is never touched.
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models

IMAGE_VIDEO_EXT = {
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "tif", "ico", "svg",
    "heic", "heif", "avif", "mp4", "mov", "avi", "mkv", "mpeg", "mpg", "webm",
    "flv", "wmv", "3gp", "m4v", "ogv",
}


def _public_id_from_url(url: str):
    """https://res.cloudinary.com/<cloud>/image/upload/v123/<public_id>.jpg → public_id"""
    if "/upload/" not in url:
        return None
    tail = url.split("/upload/", 1)[1].split("?", 1)[0]
    parts = tail.split("/")
    if parts and parts[0].startswith("v") and parts[0][1:].isdigit():
        parts = parts[1:]
    return _public_id("/".join(parts)) if parts else None


def _public_id(name: str) -> str:
    """Stored file name → Cloudinary public_id (images/videos drop the extension)."""
    if "." in name.rsplit("/", 1)[-1]:
        stem, ext = name.rsplit(".", 1)
        if ext.lower() in IMAGE_VIDEO_EXT:
            return stem
    return name


class Command(BaseCommand):
    help = "Report or delete Cloudinary media not referenced by any model."

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Delete the orphans (default: dry run).")
        parser.add_argument("--min-age-hours", type=int, default=24)
        parser.add_argument(
            "--prefix",
            default=None,
            help="Cloudinary folder to scan (default: the media prefix, e.g. media/cloudinary/).",
        )

    def handle(self, *args, **opts):
        if not getattr(settings, "USE_CLOUDINARY", False):
            raise CommandError("USE_CLOUDINARY is not enabled.")

        import cloudinary.api

        referenced = self._referenced_public_ids()
        self.stdout.write(f"Files referenced in the database: {len(referenced)}")

        prefix = opts["prefix"] or settings.MEDIA_URL.lstrip("/")
        cutoff = datetime.now(dt_timezone.utc) - timedelta(hours=opts["min_age_hours"])
        total_orphans = 0

        for resource_type in ("image", "video"):
            orphans = []
            cursor = None
            while True:
                page = cloudinary.api.resources(
                    type="upload",
                    resource_type=resource_type,
                    prefix=prefix,
                    max_results=500,
                    next_cursor=cursor,
                )
                for res in page.get("resources", []):
                    created = datetime.fromisoformat(res["created_at"].replace("Z", "+00:00"))
                    if res["public_id"] not in referenced and created < cutoff:
                        orphans.append(res["public_id"])
                cursor = page.get("next_cursor")
                if not cursor:
                    break

            total_orphans += len(orphans)
            self.stdout.write(f"{resource_type}: {len(orphans)} unreferenced file(s)")
            for public_id in orphans[:10]:
                self.stdout.write(f"  {public_id}")
            if len(orphans) > 10:
                self.stdout.write(f"  … and {len(orphans) - 10} more")

            if opts["delete"] and orphans:
                for i in range(0, len(orphans), 100):
                    cloudinary.api.delete_resources(orphans[i : i + 100], resource_type=resource_type)
                self.stdout.write(self.style.SUCCESS(f"Deleted {len(orphans)} {resource_type} file(s)."))

        if not opts["delete"]:
            self.stdout.write(
                self.style.WARNING(f"Dry run: {total_orphans} file(s) would be deleted. Re-run with --delete.")
            )

    def _referenced_public_ids(self) -> set:
        ids = set()
        for model in apps.get_models():
            file_fields = [f.name for f in model._meta.get_fields() if isinstance(f, models.FileField)]
            if not file_fields:
                continue
            manager = getattr(model, "all_objects", None) or model._default_manager
            for field in file_fields:
                for name in manager.exclude(**{field: ""}).exclude(**{f"{field}__isnull": True}).values_list(field, flat=True).iterator():
                    ids.add(_public_id(name))
                    ids.add(name)

        # Files referenced only by URL (e.g. chat media_url) count as used too.
        for model in apps.get_models():
            url_fields = [f.name for f in model._meta.get_fields() if isinstance(f, models.URLField)]
            for field in url_fields:
                for url in (
                    model._default_manager.filter(**{f"{field}__contains": "/upload/"})
                    .values_list(field, flat=True)
                    .iterator()
                ):
                    public_id = _public_id_from_url(url or "")
                    if public_id:
                        ids.add(public_id)
        return ids
