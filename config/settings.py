"""
Django settings for the PetNabor project.
"""

from pathlib import Path
from datetime import timedelta
import firebase_admin
from firebase_admin import credentials
from dotenv import load_dotenv
import os

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DEBUG") == "True"

ALLOWED_HOSTS = (
    os.getenv("DJANGO_ALLOWED_HOSTS").split(",")
    if os.getenv("DJANGO_ALLOWED_HOSTS")
    else []
)

# ALLOWED_HOSTS += [ "172.252.13.85", "localhost", "127.0.0.1"]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

CSRF_TRUSTED_ORIGINS = [
    origin if origin.startswith(("http://", "https://")) else f"http://{origin}"
    for origin in (os.getenv("CSRF_TRUSTED_ORIGINS").split(",") if os.getenv("CSRF_TRUSTED_ORIGINS") else [])
    if origin
]
# + [
#     "http://localhost:8002",
#     "http://127.0.0.1:8002",
#     "https://app.petnabor.com",
#     "http://172.252.13.85",
#     "https://172.252.13.85",
# ]

INSTALLED_APPS = [
    "daphne",
    # django-unfold must come BEFORE django.contrib.admin for custom styling
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "api.users",
    "api.notifications",
    "api.pet",
    "api.friends",
    "api.messaging",
    "api.post",
    "api.report",
    "api.story",
    "api.vendor",
    "api.blog",
    "api.meeting",
    "api.referral",
    "api.product",
    "api.site_settings",
    "api.wishlist",
    "api.tip",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "api.users.middleware.UpdateLastActiveMiddleware",
    "api.users.middleware.VerificationEnforcementMiddleware",
]

CELERY_BROKER_URL = "redis://redis:6379/0"
CELERY_RESULT_BACKEND = "redis://redis:6379/0"

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/1")

# Redis Cache Configuration
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "db": "1",
        },
        "KEY_PREFIX": "petnabor",
        "TIMEOUT": 300,
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                {
                    "address": os.getenv("REDIS_URL", "redis://redis:6379/1"),
                    "socket_timeout": None,
                }
            ],
        },
    },
}

AUTH_USER_MODEL = "users.User"
ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": os.getenv("POSTGRES_DB"),
        "USER": os.getenv("POSTGRES_USER"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD"),
        "HOST": os.getenv("POSTGRES_HOST"),
        "PORT": os.getenv("POSTGRES_PORT"),
        "TEST": {
            "NAME": "test_petnabor_db_new",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# ─── Storage Configuration ────────────────────────────────────────────────────
# Priority: Cloudinary → AWS S3 → Local filesystem
# Switch backends by setting USE_CLOUDINARY=True or USE_S3=True in .env.

USE_CLOUDINARY = os.getenv("USE_CLOUDINARY") == "True"
USE_S3 = os.getenv("USE_S3") == "True"

if USE_CLOUDINARY:
    # ── Cloudinary ────────────────────────────────────────────────────────────
    import cloudinary

    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME"),
        "API_KEY": os.getenv("CLOUDINARY_API_KEY"),
        "API_SECRET": os.getenv("CLOUDINARY_API_SECRET"),
        # Serve assets over HTTPS from Cloudinary's CDN
        "SECURE": True,
        # Optional: organise all uploads under a folder prefix in your account
        "MEDIA_TAG": os.getenv("CLOUDINARY_MEDIA_TAG", "petnabor"),
    }

    cloudinary.config(
        cloud_name=CLOUDINARY_STORAGE["CLOUD_NAME"],
        api_key=CLOUDINARY_STORAGE["API_KEY"],
        api_secret=CLOUDINARY_STORAGE["API_SECRET"],
        secure=True,
    )

    # Cloudinary generates its own CDN URLs — MEDIA_URL is a dummy prefix here.
    STATIC_URL = "/static/"
    MEDIA_URL = "/media/cloudinary/"

    STORAGES = {
        "default": {
            # django-cloudinary-storage backend handles all model FileField/ImageField uploads.
            "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    WHITENOISE_MANIFEST_STRICT = False

elif USE_S3:
    # ── AWS S3 / CloudFront ───────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
    AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN")

    # Custom django-storages classes to separate static/media folders in S3 bucket.
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/static/"
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/media/"

    STORAGES = {
        "default": {
            "BACKEND": "api.core.storage_backends.PublicMediaStorage",
        },
        "staticfiles": {
            "BACKEND": "api.core.storage_backends.StaticStorage",
        },
    }

else:
    # ── Local filesystem (development / no cloud) ─────────────────────────────
    STATIC_URL = "/static/"
    MEDIA_URL = "/media/"

    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            # Django 4.2+: FileSystemStorage does NOT auto-read MEDIA_ROOT from settings
            # when configured via STORAGES dict. Must pass location and base_url explicitly.
            "OPTIONS": {
                "location": MEDIA_ROOT,
                "base_url": MEDIA_URL,
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    WHITENOISE_MANIFEST_STRICT = False

    # Ensure local media directory exists on startup
    os.makedirs(MEDIA_ROOT, exist_ok=True)

# ── Cloudinary apps (only injected when USE_CLOUDINARY=True) ─────────────────
# django-cloudinary-storage requires these two entries to be in INSTALLED_APPS.
if USE_CLOUDINARY:
    INSTALLED_APPS += ["cloudinary_storage", "cloudinary"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# django-unfold Admin Configuration
UNFOLD = {
    "SITE_TITLE": "api.site_settings.admin_utils.site_title_callback",
    "SITE_HEADER": "api.site_settings.admin_utils.site_header_callback",
    "SITE_SUBHEADER": "Social Media Dashboard",
    "SITE_URL": "/",
    "SITE_SYMBOL": "pets",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "ENVIRONMENT": "api.users.admin_utils.environment_callback",
    "DASHBOARD_CALLBACK": "api.users.admin_utils.dashboard_callback",
    "BORDER_RADIUS": "8px",
    "COLORS": {
        "font": {
            "subtle-light": "107 114 128",
            "subtle-dark": "156 163 175",
            "default-light": "17 24 39",
            "default-dark": "243 244 246",
        },
        "primary": {
            "50": "245 243 255",
            "100": "237 233 254",
            "200": "221 214 254",
            "300": "196 181 253",
            "400": "167 139 250",
            "500": "139 92 246",
            "600": "124 58 237",
            "700": "109 40 217",
            "800": "91 33 182",
            "900": "76 29 149",
            "950": "46 16 101",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Dashboard",
                "separator": False,
                "items": [
                    {
                        "title": "Overview",
                        "icon": "dashboard",
                        "link": "/admin/",
                    },
                ],
            },
            {
                "title": "User Management",
                "separator": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "people",
                        "link": "/admin/users/user/",
                        "badge": "api.users.admin_utils.user_count_badge",
                    },
                    {
                        "title": "Profiles",
                        "icon": "manage_accounts",
                        "link": "/admin/users/profile/",
                    },
                    {
                        "title": "OTP Verifications",
                        "icon": "verified",
                        "link": "/admin/users/otpverification/",
                    },
                ],
            },
            {
                "title": "Posts & Feed",
                "separator": True,
                "items": [
                    {
                        "title": "Posts",
                        "icon": "article",
                        "link": "/admin/post/post/",
                    },
                    {
                        "title": "Post Media",
                        "icon": "perm_media",
                        "link": "/admin/post/postmedia/",
                    },
                    {
                        "title": "Post Likes",
                        "icon": "thumb_up",
                        "link": "/admin/post/postlike/",
                    },
                    {
                        "title": "Post Comments",
                        "icon": "comment",
                        "link": "/admin/post/postcomment/",
                    },
                    {
                        "title": "Saved Posts",
                        "icon": "bookmark",
                        "link": "/admin/post/savedpost/",
                    },
                    {
                        "title": "Hashtags",
                        "icon": "tag",
                        "link": "/admin/post/hashtag/",
                    },
                ],
            },
            {
                "title": "Stories",
                "separator": True,
                "items": [
                    {
                        "title": "Stories",
                        "icon": "auto_stories",
                        "link": "/admin/story/story/",
                    },
                    {
                        "title": "Story Views",
                        "icon": "visibility",
                        "link": "/admin/story/storyview/",
                    },
                    {
                        "title": "Story Reactions",
                        "icon": "favorite",
                        "link": "/admin/story/storyreaction/",
                    },
                    {
                        "title": "Story Replies",
                        "icon": "reply",
                        "link": "/admin/story/storyreply/",
                    },
                ],
            },
            {
                "title": "Blogs",
                "separator": True,
                "items": [
                    {
                        "title": "Blogs",
                        "icon": "rss_feed",
                        "link": "/admin/blog/blog/",
                    },
                    {
                        "title": "Blog Categories",
                        "icon": "category",
                        "link": "/admin/blog/blogcategory/",
                    },
                    {
                        "title": "Blog Likes",
                        "icon": "thumb_up",
                        "link": "/admin/blog/bloglike/",
                    },
                    {
                        "title": "Blog Comments",
                        "icon": "comment",
                        "link": "/admin/blog/blogcomment/",
                    },
                    {
                        "title": "Blog Views",
                        "icon": "visibility",
                        "link": "/admin/blog/blogviewtracker/",
                    },
                ],
            },
            {
                "title": "Social & Connections",
                "separator": True,
                "items": [
                    {
                        "title": "Friend Requests",
                        "icon": "person_add",
                        "link": "/admin/friends/friendrequest/",
                    },
                    {
                        "title": "Friendships",
                        "icon": "group",
                        "link": "/admin/friends/friendship/",
                    },
                    {
                        "title": "User Blocks",
                        "icon": "block",
                        "link": "/admin/friends/userblock/",
                    },
                ],
            },
            {
                "title": "Meetings",
                "separator": True,
                "items": [
                    {
                        "title": "Meetings",
                        "icon": "event",
                        "link": "/admin/meeting/meeting/",
                    },
                    {
                        "title": "Meeting Feedback",
                        "icon": "rate_review",
                        "link": "/admin/meeting/meetingfeedback/",
                    },
                ],
            },
            {
                "title": "Messaging",
                "separator": True,
                "items": [
                    {
                        "title": "Chat Threads",
                        "icon": "forum",
                        "link": "/admin/messaging/chatthread/",
                    },
                    {
                        "title": "Thread Participants",
                        "icon": "group_add",
                        "link": "/admin/messaging/threadparticipant/",
                    },
                    {
                        "title": "Messages",
                        "icon": "chat",
                        "link": "/admin/messaging/message/",
                    },
                ],
            },
            {
                "title": "Pets",
                "separator": True,
                "items": [
                    {
                        "title": "Pet Profiles",
                        "icon": "pets",
                        "link": "/admin/pet/petprofile/",
                    },
                ],
            },
            {
                "title": "Moderation",
                "separator": True,
                "items": [
                    {
                        "title": "Reports",
                        "icon": "flag",
                        "link": "/admin/report/report/",
                        "badge": "api.users.admin_utils.pending_reports_badge",
                    },
                ],
            },
            {
                "title": "Notifications",
                "separator": True,
                "items": [
                    {
                        "title": "Sent Notifications",
                        "icon": "notifications",
                        "link": "/admin/notifications/notifications/",
                    },
                    {
                        "title": "FCM Devices",
                        "icon": "devices",
                        "link": "/admin/notifications/fcmdevice/",
                    },
                    {
                        "title": "Settings",
                        "icon": "tune",
                        "link": "/admin/notifications/notificationsettings/",
                    },
                ],
            },
            {
                "title": "Referral System",
                "separator": True,
                "items": [
                    {
                        "title": "Referral Settings",
                        "icon": "settings",
                        "link": "/admin/referral/referralsettings/",
                    },
                    {
                        "title": "Wallets",
                        "icon": "wallet",
                        "link": "/admin/referral/referralwallet/",
                    },
                    {
                        "title": "Transactions",
                        "icon": "receipt_long",
                        "link": "/admin/referral/referraltransaction/",
                    },
                ],
            },
            {
                "title": "Vendor & Store",
                "separator": True,
                "items": [
                    {
                        "title": "Vendors",
                        "icon": "storefront",
                        "link": "/admin/vendor/vendor/",
                    },
                    {
                        "title": "Vendor Plans",
                        "icon": "card_membership",
                        "link": "/admin/vendor/vendorplan/",
                    },
                    {
                        "title": "Subscriptions",
                        "icon": "subscriptions",
                        "link": "/admin/vendor/vendorsubscription/",
                    },
                    {
                        "title": "Products",
                        "icon": "inventory_2",
                        "link": "/admin/product/product/",
                    },
                    {
                        "title": "Categories",
                        "icon": "category",
                        "link": "/admin/product/categories/",
                    },
                    {
                        "title": "Brands",
                        "icon": "verified",
                        "link": "/admin/product/brand/",
                    },
                    {
                        "title": "Wishlists",
                        "icon": "favorite",
                        "link": "/admin/wishlist/productwishlist/",
                    },
                    {
                        "title": "Product Events",
                        "icon": "analytics",
                        "link": "/admin/product/productevent/",
                    },
                ],
            },
            {
                "title": "Tip & Payments",
                "separator": True,
                "items": [
                    {
                        "title": "Tip Settings",
                        "icon": "tune",
                        "link": "/admin/tip/tipsettings/",
                    },
                    {
                        "title": "Connect Accounts",
                        "icon": "account_balance",
                        "link": "/admin/tip/stripeconnectaccount/",
                    },
                    {
                        "title": "Tips",
                        "icon": "volunteer_activism",
                        "link": "/admin/tip/tip/",
                    },
                    {
                        "title": "Withdrawals",
                        "icon": "payments",
                        "link": "/admin/tip/tipwithdrawal/",
                    },
                ],
            },
            {
                "title": "Site Configuration",
                "separator": True,
                "items": [
                    {
                        "title": "Global Settings",
                        "icon": "settings_applications",
                        "link": "/admin/site_settings/sitesettings/",
                    },
                ],
            },
        ],
    },
    "TABS": [],
}

# Media and storage configuration is defined in the Storage section above.

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # No global DEFAULT_THROTTLE_CLASSES — each view declares its own throttle
    # class(es) explicitly so limits are always per-user or per-identity,
    # never accidentally shared across unrelated users on the same IP.
    "DEFAULT_THROTTLE_RATES": {
        # OTP: 5 OTPs per 5 minutes per email/phone identity
        "otp_send": "5/5minute",
        # OTP verify: 10 attempts per hour per email/phone identity
        "otp_verify": "10/hour",
        # Login: 40 attempts per hour per account identity
        "auth_login": "40/hour",
        # Messaging: 5000 messages per hour per user
        "messaging_send": "5000/hour",
        # Reactions: 200 per minute per user (across any posts/blogs)
        "post_like": "200/minute",
        # Comments: 30 per minute per user
        "post_comment": "30/minute",
        # Post save/bookmark: 100 per minute per user
        "post_save": "100/minute",
    },
    "EXCEPTION_HANDLER": "api.users.exception_handler.custom_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "PetNabor API",
    "DESCRIPTION": "Interactive API documentation for the PetNabor application.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/",
    "SCHEMA_PATH_PREFIX_TRIM": True,
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayOperationId": True,
    },
    "ENUM_NAME_OVERRIDES": {
        "PrivacyEnum": "api.post.models.Post.Privacy",
        "MediaTypeEnum": "api.post.models.PostMedia.MediaType"
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

FIREBASE_CREDENTIALS = os.path.join(BASE_DIR, "firebase-credentials.json")

def initialize_firebase():
    if not firebase_admin._apps:
        if os.path.exists(FIREBASE_CREDENTIALS):
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        else:
            print("Warning: firebase-credentials.json not found. Firebase services will not work.")

initialize_firebase()

# Twilio Configuration (Phone OTP)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

# ─── Stripe Configuration ──────────────────────────────────────────────────
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Base URL for Stripe Connect redirect URLs
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "https://petnabor.com")

# Email Configuration
# Priority: Resend → AWS SES → generic SMTP
USE_RESEND = os.getenv("USE_RESEND") == "True"
USE_SES = os.getenv("USE_SES") == "True"

if USE_RESEND:
    # ── Resend (smtp.resend.com relay) ────────────────────────────────────────
    # Resend exposes a standard SMTP relay — no extra package needed.
    # Docs: https://resend.com/docs/send-with-smtp
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.resend.com"
    EMAIL_PORT = 465
    EMAIL_USE_SSL = True       # Port 465 uses SSL, not STARTTLS
    EMAIL_USE_TLS = False      # Must be False when EMAIL_USE_SSL=True
    EMAIL_HOST_USER = "resend" # Resend always uses the literal string "resend"
    EMAIL_HOST_PASSWORD = os.getenv("RESEND_API_KEY", "")

elif USE_SES:
    # ── AWS SES ───────────────────────────────────────────────────────────────
    EMAIL_BACKEND = "django_ses.SESBackend"
    AWS_SES_REGION_NAME = os.getenv("AWS_SES_REGION_NAME", "us-east-1")
    AWS_SES_REGION_ENDPOINT = os.getenv("AWS_SES_REGION_ENDPOINT", "email.us-east-1.amazonaws.com")

else:
    # ── Generic SMTP (fallback) ────────────────────────────────────────────────
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("EMAIL_HOST", "")
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@petnabor.com")

# Display name for password reset emails (RFC 5322 "From" header friendly-name).
# Combined with DEFAULT_FROM_EMAIL as `"{EMAIL_FROM_NAME} <{DEFAULT_FROM_EMAIL}">`
# at the email-service layer so Gmail renders "Pet Nabor" instead of "info".
EMAIL_FROM_NAME: str = os.getenv("EMAIL_FROM_NAME", "Pet Nabor")

# OTP Configuration
OTP_LENGTH = int(os.getenv("OTP_LENGTH", "4"))
OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "5"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))

# Email verification token expiry in hours
EMAIL_VERIFICATION_EXPIRY_HOURS = int(
    os.getenv("EMAIL_VERIFICATION_EXPIRY_HOURS", "24")
)

# CORS Configuration
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = (
    os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if os.getenv("CORS_ALLOWED_ORIGINS")
    else []
)

# Post / Media Processing Settings
POST_MEDIA_MAX_SIZE_BYTES = int(os.getenv("POST_MEDIA_MAX_SIZE_MB", "50")) * 1024 * 1024
POST_IMAGE_MAX_DIM = (1920, 1080)
POST_IMAGE_MEDIUM_DIM = (800, 800)
POST_IMAGE_THUMB_DIM = (400, 400)
POST_IMAGE_QUALITY = int(os.getenv("POST_IMAGE_QUALITY", "85"))
POST_THUMB_QUALITY = int(os.getenv("POST_THUMB_QUALITY", "75"))
POST_ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "mp4", "mov"}

# Allowed MIME types for media uploads
POST_ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
POST_ALLOWED_VIDEO_MIME = {"video/mp4", "video/quicktime"}
POST_ALLOWED_MIME_TYPES = POST_ALLOWED_IMAGE_MIME | POST_ALLOWED_VIDEO_MIME

# Story Settings (expiry duration in hours)
STORY_EXPIRY_HOURS: int = int(os.getenv("STORY_EXPIRY_HOURS", "24"))

# File Upload Settings
# Keep 5GB upload option enabled.
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE_MB", "5000")) * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE_MB", "5000")) * 1024 * 1024
FILE_UPLOAD_TEMP_DIR = os.path.join(BASE_DIR, "tmp_uploads")

# ─── Sentry Configuration ──────────────────────────────────────────────────
SENTRY_DSN = os.getenv("SENTRY_DSN")

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    
    def strip_sensitive_data(event, hint):
        """Scrub sensitive data from Sentry events."""
        if 'request' in event and 'headers' in event['request']:
            headers = event['request']['headers']
            for key in ['Authorization', 'authorization']:
                if key in headers:
                    headers[key] = '[Filtered]'
        return event

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
        ],
        environment=os.getenv("SENTRY_ENVIRONMENT", "development"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE") or 0.2),
        send_default_pii=False,
        before_send=strip_sensitive_data,
    )
