"""
Custom DRF exception handler for production-grade error responses.

Ensures that all errors return clean JSON and never expose 500 stack traces.
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

# Storage providers (Cloudinary, S3, GCS, ...) raise vendor-specific exceptions
# when an upload exceeds the provider's quota. We catch them here and return a
# proper 400 instead of a generic 500.
_SIZE_ERROR_CLASSES: tuple = ()
try:
    from cloudinary.exceptions import BadRequest as _CloudinaryBadRequest  # type: ignore
    _SIZE_ERROR_CLASSES = _SIZE_ERROR_CLASSES + (_CloudinaryBadRequest,)
except Exception:  # pragma: no cover - cloudinary not installed in some envs
    _CloudinaryBadRequest = None  # type: ignore

try:
    from botocore.exceptions import ClientError as _BotoClientError  # type: ignore
    _SIZE_ERROR_CLASSES = _SIZE_ERROR_CLASSES + (_BotoClientError,)
except Exception:  # pragma: no cover - boto3 not installed in some envs
    _BotoClientError = None  # type: ignore


def _is_storage_size_error(exc: Exception) -> bool:
    """
    Returns True for exceptions raised by storage backends because the uploaded
    file exceeded the provider's size limit (e.g. Cloudinary's 10 MB cap).
    """
    if _SIZE_ERROR_CLASSES and isinstance(exc, _SIZE_ERROR_CLASSES):
        msg = str(exc).lower()
        if "size" in msg and ("large" in msg or "limit" in msg or "maximum" in msg):
            return True
        # Cloudinary's exact error includes "File size too large. Got ... Maximum is ..."
        if "file size too large" in msg:
            return True
    return False


# Specific Cloudinary error phrases we want to surface as a clean 400 instead
# of letting them bubble to a 500. Each entry is a lowercase substring that
# Cloudinary's `BadRequest` message is expected to contain.
_CLOUDINARY_USER_ERRORS = (
    "invalid image file",
    "invalid video file",
    "unsupported image format",
    "unsupported video format",
    "file size too large",
    "eager transformations",
    "eager transformation",
)


def _is_storage_user_error(exc: Exception) -> bool:
    """
    Returns True for storage-backend rejections caused by the user-uploaded
    payload (size, MIME, corrupt file, etc.) — i.e. errors that the user can
    fix by uploading a different file. We map these to a 400.
    """
    if _CloudinaryBadRequest is None:
        return False
    if not isinstance(exc, _CloudinaryBadRequest):
        return False
    msg = str(exc).lower()
    return any(needle in msg for needle in _CLOUDINARY_USER_ERRORS)


def custom_exception_handler(exc, context):
    """
    Custom exception handler that converts all exceptions to clean JSON.

    Response format:
        {
            "success": false,
            "message": "Human-readable error message",
            "errors": { ... }  # optional field-level errors
        }
    """
    # Let DRF handle known exceptions first
    response = exception_handler(exc, context)

    if response is not None:
        # DRF handled it — normalize the format
        error_data = {"success": False}

        if isinstance(response.data, dict):
            # Extract 'detail' or field-level errors
            detail = response.data.pop("detail", None)
            if detail:
                error_data["message"] = str(detail)
            elif response.data:
                error_data["message"] = "Validation failed."
                error_data["errors"] = response.data
            else:
                error_data["message"] = "An error occurred."
        elif isinstance(response.data, list):
            error_data["message"] = " ".join(str(item) for item in response.data)
        else:
            error_data["message"] = str(response.data)

        response.data = error_data
        return response

    # DRF didn't handle it — it's an unexpected error
    if isinstance(exc, DjangoValidationError):
        error_data = {
            "success": False,
            "message": "Validation failed.",
            "errors": exc.message_dict
            if hasattr(exc, "message_dict")
            else {"detail": exc.messages},
        }
        return Response(error_data, status=status.HTTP_400_BAD_REQUEST)

    if isinstance(exc, Http404):
        return Response(
            {"success": False, "message": "Resource not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Storage backends (Cloudinary / S3 / etc.) raise vendor exceptions when an
    # upload exceeds their quota. Convert these to a clean 400 with a useful
    # message instead of a generic 500.
    if _is_storage_size_error(exc):
        logger.warning(
            "Upload rejected by storage backend in %s: %s",
            context.get("view", "unknown"),
            exc,
        )
        return Response(
            {
                "success": False,
                "message": (
                    "Uploaded file exceeds the storage provider's size limit. "
                    "Please upload a smaller file and try again."
                ),
                "errors": {"media": str(exc)},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Any other Cloudinary / S3 rejection that stems from the uploaded payload
    # (corrupt bytes, wrong MIME, unsupported codec, eager transformation on
    # free plan, etc.) — return a 400 with the storage vendor's exact message
    # so the client can show something useful instead of a 500.
    if _is_storage_user_error(exc):
        logger.warning(
            "Upload rejected by storage backend in %s: %s",
            context.get("view", "unknown"),
            exc,
        )
        return Response(
            {
                "success": False,
                "message": (
                    "Uploaded file was not accepted. Please check the file "
                    "format, size, and contents and try again."
                ),
                "errors": {"media": str(exc)},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Completely unexpected error — log it, return generic message
    logger.exception("Unhandled exception in %s", context.get("view", "unknown"))
    return Response(
        {
            "success": False,
            "message": "An internal error occurred. Please try again later.",
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
