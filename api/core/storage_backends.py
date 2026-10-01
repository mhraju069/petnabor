from storages.backends.s3boto3 import S3Boto3Storage


# ── AWS S3 Backends ────────────────────────────────────────────────────────────

class StaticStorage(S3Boto3Storage):
    location = 'static'
    # Do not set default_acl here if bucket blocks public ACLs, CloudFront will serve it

class PublicMediaStorage(S3Boto3Storage):
    location = 'media'
    file_overwrite = False


# ── Cloudinary Backend ─────────────────────────────────────────────────────────

class CloudinaryMediaStorage:
    """
    Lazy-import wrapper so the Cloudinary package is only imported when
    USE_CLOUDINARY=True, keeping the module importable in non-Cloudinary envs.
    """

    def __new__(cls, *args, **kwargs):
        from cloudinary_storage.storage import MediaCloudinaryStorage
        return MediaCloudinaryStorage(*args, **kwargs)
