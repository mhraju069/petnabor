from storages.backends.s3boto3 import S3Boto3Storage


# ── AWS S3 Backends ────────────────────────────────────────────────────────────

class StaticStorage(S3Boto3Storage):
    location = 'static'
    # Do not set default_acl here if bucket blocks public ACLs, CloudFront will serve it

class PublicMediaStorage(S3Boto3Storage):
    location = 'media'
    file_overwrite = False


# ── Cloudinary Backends ────────────────────────────────────────────────────────

class CloudinaryMediaStorage:
    """
    Lazy-import wrapper so the Cloudinary package is only imported when
    USE_CLOUDINARY=True, keeping the module importable in non-Cloudinary envs.
    """

    def __new__(cls, *args, **kwargs):
        from cloudinary_storage.storage import MediaCloudinaryStorage
        return MediaCloudinaryStorage(*args, **kwargs)


class AutoMediaCloudinaryStorage:
    """
    Lazy-import wrapper for a Cloudinary storage backend that auto-detects
    `resource_type` per upload based on file extension.

    Why this exists:
    `MediaCloudinaryStorage` always uploads as `resource_type="image"`, which
    means Cloudinary rejects MP4/MOV/AVI videos with errors like:
        "Invalid image file"          (header check fails)
        "File size too large"         (image quota is ~10 MB on the free plan,
                                       while video quota is ~100 MB)
    By picking `image`/`video`/`raw` per file we use the right Cloudinary quota
    for each media type. This keeps image uploads identical to before, while
    allowing videos up to Cloudinary's per-resource-type limit.

    Mapping (case-insensitive):
        IMAGE_EXT  = {jpg, jpeg, png, gif, webp, bmp, tiff, ico, svg, heic, heif}
        VIDEO_EXT  = {mp4, mov, avi, mkv, mpeg, mpg, webm, flv, wmv, 3gp, m4v}
        else       = raw (no transformation)

    Implementation note:
    We single-inherit from `MediaCloudinaryStorage` and override
    `_get_resource_type` to return the correct `resource_type` per file. We
    do NOT try to mix in `VideoMediaCloudinaryStorage` /
    `RawMediaCloudinaryStorage` because they share a common base class with
    `MediaCloudinaryStorage`, which would create a diamond-inheritance MRO
    conflict. The `_get_resource_type` override is sufficient: Cloudinary's
    uploader accepts `resource_type="video"` even when the storage class is
    otherwise image-oriented.
    """

    _IMAGE_EXT = {
        "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "tif",
        "ico", "svg", "heic", "heif", "avif",
    }
    _VIDEO_EXT = {
        "mp4", "mov", "avi", "mkv", "mpeg", "mpg", "webm",
        "flv", "wmv", "3gp", "m4v", "ogv",
    }

    def __new__(cls, *args, **kwargs):
        from cloudinary_storage.storage import MediaCloudinaryStorage

        def _ext(name):
            return (name.rsplit(".", 1)[-1] if "." in name else "").lower()

        class _Auto(MediaCloudinaryStorage):
            def _save(self, name, content):
                # Cloudinary returns a public_id without extension for image/video
                # uploads. Keep the extension in the stored name so url() can
                # resolve the right resource_type later (else it falls back to raw
                # and 404s).
                ext = _ext(name)
                saved = super()._save(name, content)
                if ext in (cls._IMAGE_EXT | cls._VIDEO_EXT) and _ext(saved) != ext:
                    saved = f"{saved}.{ext}"
                return saved

            def _get_resource_type(self, name):
                ext = _ext(name)
                # Extensionless names are legacy image uploads (Cloudinary strips
                # the extension from image public_ids), so treat them as images.
                if not ext or ext in cls._IMAGE_EXT:
                    return "image"
                if ext in cls._VIDEO_EXT:
                    return "video"
                return "raw"

        return _Auto(*args, **kwargs)
