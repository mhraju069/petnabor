from storages.backends.s3boto3 import S3Boto3Storage

class StaticStorage(S3Boto3Storage):
    location = 'static'
    # Do not set default_acl here if bucket blocks public ACLs, CloudFront will serve it

class PublicMediaStorage(S3Boto3Storage):
    location = 'media'
    file_overwrite = False
