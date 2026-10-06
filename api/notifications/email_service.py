"""
Branded email delivery service for the notifications app.
"""
import logging
from django.core.mail import send_mail
from django.conf import settings
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

# ── Senders for branded Pet Nabor emails ──────────────────────────────────────
# Gmail (and most clients) display the sender as the bare local-part of the
# email address when no display name is set. We want all Pet Nabor transactional
# emails to appear as "Pet Nabor <noreply@petnabor.com>".
#
# `EMAIL_FROM_NAME` is read from environment / settings (default: "Pet Nabor")
# and combined with DEFAULT_FROM_EMAIL to build the RFC 5322 "From" header.
# Each sender can be fully overridden by setting the corresponding
# `EMAIL_FROM_NAME_*` setting.
_BRAND_FROM_NAME: str = getattr(settings, "EMAIL_FROM_NAME", "Pet Nabor")

_PASSWORD_RESET_FROM_EMAIL: str = getattr(
    settings,
    "EMAIL_FROM_NAME_PASSWORD_RESET",
    f"{_BRAND_FROM_NAME} <{settings.DEFAULT_FROM_EMAIL}>",
)
_OTP_VERIFY_FROM_EMAIL: str = getattr(
    settings,
    "EMAIL_FROM_NAME_OTP_VERIFY",
    f"{_BRAND_FROM_NAME} <{settings.DEFAULT_FROM_EMAIL}>",
)


class EmailService:
    """
    Reusable service for sending HTML/plain-text emails via the configured backend.
    All OTP and password-reset emails share the same Pet Nabor branded template.
    """

    # ── Brand constants ────────────────────────────────────────────────────────
    _LOGO_URL = "https://res.cloudinary.com/xclcbbmj/image/upload/v1791205028/a4ufstpu8ud9dbpz6f0l.svg"
    _COLOR_HEADER_BG  = "#BFE3F7"
    _COLOR_ACCENT     = "#F28C28"   # dashed border, strong highlights
    _COLOR_ACCENT_ALT = "#E05A1B"   # password-reset variant (deeper orange-red)
    _COLOR_TEXT       = "#1F2933"
    _COLOR_MUTED      = "#5B6670"
    _COLOR_PAGE_BG    = "#EAF4FB"
    _COLOR_CODE_BG    = "#FFF4E6"

    # ── Core send method ───────────────────────────────────────────────────────

    @staticmethod
    def send_html_email(subject, html_content, recipient_list, from_email=None):
        """Send an HTML email with an auto-generated plain-text fallback."""
        if not from_email:
            from_email = settings.DEFAULT_FROM_EMAIL

        plain_message = strip_tags(html_content)

        try:
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=from_email,
                recipient_list=recipient_list,
                html_message=html_content,
                fail_silently=False,
            )
            logger.info("Email sent: %s → %s", subject, recipient_list)
            return True
        except Exception as exc:
            logger.exception("Failed to send email: %s → %s | %s", subject, recipient_list, exc)
            return False

    # ── Template builder ───────────────────────────────────────────────────────

    @classmethod
    def _build_otp_html(
        cls,
        otp_code: str,
        expiry_minutes: int,
        title: str,
        subtitle: str,
        footer_note: str,
        accent_color: str,
    ) -> str:
        """
        Render the Pet Nabor OTP email template.
        Each digit of otp_code gets its own table cell to match the design exactly.
        """
        # Build one <td> per digit
        digits = list(str(otp_code))
        n = len(digits)
        digit_cells = []
        for i, d in enumerate(digits):
            # First cell has extra left padding, last has extra right padding
            pl = "20px" if i == 0     else "8px"
            pr = "20px" if i == n - 1 else "8px"
            digit_cells.append(
                f'<td style="padding:16px {pr} 16px {pl};font-size:32px;font-weight:700;'
                f'color:{cls._COLOR_TEXT};font-family:\'Courier New\',monospace;">{d}</td>'
            )
        digit_row = "\n              ".join(digit_cells)

        # Hidden preview text (shown in inbox before email is opened)
        preview = f"Your verification code: {otp_code}. Valid for {expiry_minutes} minutes."

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{title} - Pet Nabor</title>
</head>
<body style="margin:0;padding:0;background-color:{cls._COLOR_PAGE_BG};">

<!-- Preview text (hidden) -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preview}</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:{cls._COLOR_PAGE_BG};">
  <tr><td align="center" style="padding:24px 12px;">

    <table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0"
           style="width:100%;max-width:480px;background-color:#FFFFFF;border-radius:20px;
                  overflow:hidden;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">

      <!-- ── Header / Logo ──────────────────────────────────── -->
      <tr>
        <td align="center" style="background-color:{cls._COLOR_HEADER_BG};padding:36px 24px 28px;">
          <a href="https://www.petnabor.com" target="_blank" style="text-decoration:none;">
            <img src="{cls._LOGO_URL}" alt="Pet Nabor" width="180"
                 style="display:block;width:180px;max-width:100%;height:auto;border:0;margin:0 auto;">
          </a>
        </td>
      </tr>

      <!-- ── Title & subtitle ────────────────────────────────── -->
      <tr>
        <td align="center" style="padding:32px 32px 8px;">
          <h1 style="margin:0;font-size:24px;line-height:1.3;color:{cls._COLOR_TEXT};font-weight:700;">
            {title}
          </h1>
          <p style="margin:12px 0 0;font-size:14px;line-height:1.6;color:{cls._COLOR_MUTED};">
            {subtitle}
          </p>
        </td>
      </tr>

      <!-- ── OTP code ────────────────────────────────────────── -->
      <tr>
        <td align="center" style="padding:28px 24px 8px;">
          <div style="font-size:20px;font-weight:700;color:{cls._COLOR_TEXT};">Verification Code</div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                 style="margin:18px auto 0;background-color:{cls._COLOR_CODE_BG};
                        border:2px dashed {accent_color};border-radius:14px;">
            <tr>
              {digit_row}
            </tr>
          </table>
          <p style="margin:18px 0 0;font-size:13px;color:{cls._COLOR_MUTED};">
            This code is valid for <strong style="color:{cls._COLOR_TEXT};">{expiry_minutes} minutes.</strong>
          </p>
        </td>
      </tr>

      <!-- ── Security footer ─────────────────────────────────── -->
      <tr>
        <td align="center" style="padding:28px 40px 32px;">
          <p style="margin:0;padding-top:20px;border-top:1px solid #E3EAF0;
                    font-size:12px;line-height:1.6;color:{cls._COLOR_MUTED};">
            {footer_note}
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""

    # ── Public email methods ───────────────────────────────────────────────────

    @classmethod
    def send_otp_email(
        cls,
        email: str,
        otp_code: str,
        expiry_minutes: int,
        subject: str = "Verify Your Account - Pet Nabor",
    ) -> bool:
        """Send an account-verification OTP email using the Pet Nabor branded template."""
        html = cls._build_otp_html(
            otp_code=otp_code,
            expiry_minutes=expiry_minutes,
            title="Verify Your Account",
            subtitle="Enter the verification code below to complete your account registration.",
            footer_note=(
                "Do not share this code with anyone, including anyone claiming "
                "to represent our service."
            ),
            accent_color=cls._COLOR_ACCENT,
        )
        return cls.send_html_email(
            subject,
            html,
            [email],
            from_email=_OTP_VERIFY_FROM_EMAIL,
        )

    @classmethod
    def send_password_reset_email(
        cls,
        email: str,
        otp_code: str,
        expiry_minutes: int,
    ) -> bool:
        """Send a password-reset OTP email using the Pet Nabor branded template."""
        subject = "Reset Your Password - Pet Nabor"
        html = cls._build_otp_html(
            otp_code=otp_code,
            expiry_minutes=expiry_minutes,
            title="Reset Your Password",
            subtitle="Use the code below to reset your Pet Nabor account password.",
            footer_note=(
                "If you didn't request a password reset, please ignore this email. "
                "Your account remains secure."
            ),
            accent_color=cls._COLOR_ACCENT_ALT,
        )
        return cls.send_html_email(
            subject,
            html,
            [email],
            from_email=_PASSWORD_RESET_FROM_EMAIL,
        )
