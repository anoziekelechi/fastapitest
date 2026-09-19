

"""
Generic email sending utility with retry logic and global template context.

Responsibilities:
    - Build plain-text or HTML emails
    - Provide global template variables (current_year, sitename, full_names)
    - Send emails
    - Retry transient SMTP/network failures

This module does NOT contain OTP business logic, and it does NOT depend on
any user domain schema. Callers pass `full_names` as a string.
"""

import logging
import smtplib
from datetime import datetime, timezone
from typing import Any

from fastapi_mail import FastMail, MessageSchema, MessageType
from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from api.home.logics import get_home_settings_logic


logger = logging.getLogger(__name__)


# =============================================================================
# RETRY CONFIGURATION
# =============================================================================

EMAIL_RETRY_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
)

EMAIL_MAX_ATTEMPTS = 4
EMAIL_RETRY_MIN_SECONDS = 2
EMAIL_RETRY_MAX_SECONDS = 30


# =============================================================================
# RETRY LOGGING
# =============================================================================

def _before_sleep_log(
    retry_state: RetryCallState,
) -> None:
    """
    Log a failed email attempt before Tenacity waits and retries.
    """

    attempt = retry_state.attempt_number

    exception = (
        retry_state.outcome.exception()
        if retry_state.outcome is not None
        else None
    )

    sleep_time = 0.0

    if retry_state.next_action is not None:
        sleep_time = getattr(
            retry_state.next_action,
            "sleep",
            0.0,
        )

    logger.warning(
        "Email send failed on attempt %s/%s: %s. "
        "Retrying in %.1fs...",
        attempt,
        EMAIL_MAX_ATTEMPTS,
        exception,
        sleep_time,
    )


# =============================================================================
# ACTUAL EMAIL DELIVERY
# =============================================================================

@retry(
    stop=stop_after_attempt(EMAIL_MAX_ATTEMPTS),
    wait=wait_exponential(
        multiplier=2,
        min=EMAIL_RETRY_MIN_SECONDS,
        max=EMAIL_RETRY_MAX_SECONDS,
    ),
    retry=retry_if_exception_type(
        EMAIL_RETRY_EXCEPTIONS
    ),
    before_sleep=_before_sleep_log,
    reraise=True,
)
async def _send_message(
    mailer: FastMail,
    message: MessageSchema,
    *,
    template_name: str | None = None,
) -> None:
    """
    Send an email.

    Only transient connection/network errors are retried.
    """

    if template_name is not None:
        await mailer.send_message(
            message,
            template_name=template_name,
        )
    else:
        await mailer.send_message(message)


# =============================================================================
# GLOBAL TEMPLATE CONTEXT
# =============================================================================

async def _global_email_context(
    db: AsyncSession,
    full_names: str,
) -> dict[str, Any]:
    """
    Return variables available to all HTML email templates.

    The sitename is read from the database (via get_home_settings_logic)
    so that email templates always reflect the current Home.sitename.

    `full_names` is provided by the caller and is required for every HTML
    email, since all templates greet the recipient by name.
    """

    home = await get_home_settings_logic(db)

    return {
        "current_year": datetime.now(timezone.utc).year,
        "sitename": home.sitename,
        "full_names": full_names,
    }


# =============================================================================
# GENERIC EMAIL SENDER
# =============================================================================

async def send_email(
    recipient: EmailStr,
    subject: str,
    mailer: FastMail,
    db: AsyncSession,
    full_names: str,
    *,
    body: str | None = None,
    template_name: str | None = None,
    template_body: dict[str, Any] | None = None,
) -> None:
    """
    Send a generic email.

    Use either:

        body="Plain text email"

    or:

        template_name="example.html"
        template_body={...}

    but never both.

    Global HTML template variables automatically include:

        current_year
        sitename
        full_names

    Anything else a template needs (otp, heading, message, etc.) must be
    passed via `template_body`.
    """

    # =========================================================================
    # VALIDATION
    # =========================================================================

    if body is None and template_name is None:
        raise ValueError(
            "Either 'body' or 'template_name' must be provided."
        )

    if body is not None and template_name is not None:
        raise ValueError(
            "Provide either 'body' or 'template_name', not both."
        )

    # =========================================================================
    # HTML EMAIL
    # =========================================================================

    if template_name is not None:
        global_context = await _global_email_context(
            db,
            full_names,
        )

        merged_body = {
            **global_context,
            **(template_body or {}),
        }

        message = MessageSchema(
            subject=subject,
            recipients=[recipient],
            template_body=merged_body,
            subtype=MessageType.html,
        )

    # =========================================================================
    # PLAIN-TEXT EMAIL
    # =========================================================================

    else:
        message = MessageSchema(
            subject=subject,
            recipients=[recipient],
            body=body,
            subtype=MessageType.plain,
        )

    # =========================================================================
    # SEND
    # =========================================================================

    try:
        await _send_message(
            mailer,
            message,
            template_name=template_name,
        )

    except Exception:
        logger.exception(
            "Failed to send email to %s after all applicable "
            "retry attempts (subject=%r)",
            recipient,
            subject,
        )
        raise

    logger.info(
        "Email sent successfully to %s (subject=%r)",
        recipient,
        subject,
    )






"""
OTP and support contact email utilities.

This module contains OTP-specific email presentation logic.

It does NOT:
    - generate OTPs
    - hash OTPs
    - store OTPs in Redis
    - validate OTPs
    - consume OTPs
    - manage rate limits

Those responsibilities belong to the authentication/OTP logic layer.
"""

import logging

from fastapi_mail import FastMail
from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.settings import get_settings
from api.users.schemas import ReadUser
from api.utils.email import send_email


logger = logging.getLogger(__name__)


# =============================================================================
# OTP CONFIGURATION
# =============================================================================

OTP_LABELS = {
    "registration": "registration",
    "login": "login",
    "email_change": "email change",
    "change_password": "change password",
    "password_reset": "password reset",
}

OTP_EXPIRE_MINUTES = get_settings().otp_expire_minutes


# =============================================================================
# OTP EMAIL
# =============================================================================

async def send_otp(
    email: EmailStr,
    otp: str,
    subject: str,
    otp_type: str,
    mailer: FastMail,
    db: AsyncSession,
    user: ReadUser,
) -> None:
    """
    Send an OTP email using the shared HTML email utility.

    Supported OTP types:
        - registration
        - login
        - email_change
        - change_password
        - password_reset

    The recipient's full name is provided to the global template context so
    that the greeting reads, e.g.:

        Dear Ada Kelechi,
        Your login OTP is 123456. It expires in 10 minutes.

    Raises:
        ValueError: if `otp_type` is not recognized.
    """

    # =========================================================================
    # VALIDATE OTP TYPE
    # =========================================================================

    if otp_type not in OTP_LABELS:
        raise ValueError(
            f"Unsupported OTP type: {otp_type!r}"
        )

    otp_label = OTP_LABELS[otp_type]

    # =========================================================================
    # SEND EMAIL
    # =========================================================================

    try:
        await send_email(
            recipient=email,
            subject=subject,
            mailer=mailer,
            db=db,
            full_names=user.full_names,
            template_name="emails/otp.html",
            template_body={
                "otp": otp,
                "label": otp_label,
                "expires_in_minutes": OTP_EXPIRE_MINUTES,
            },
        )

    except Exception:
        logger.critical(
            "OTP email delivery failed after all applicable "
            "retry attempts (type=%s)",
            otp_type,
            exc_info=True,
        )
        raise

    logger.info(
        "OTP email sent successfully (type=%s)",
        otp_type,
    )


# =============================================================================
# SUPPORT CONTACT EMAIL
# =============================================================================

async def send_support_message(
    support_email: EmailStr,
    user_email: EmailStr,
    user_full_names: str,
    message: str,
    mailer: FastMail,
    db: AsyncSession,
) -> None:
    """
    Send a disabled user's contact message to the support email.

    Args:
        support_email:   Destination (country.email_support or settings.mail_username)
        user_email:      The user who sent the message
        user_full_names: The disabled user's full name (used for the greeting)
        message:         The user's message
        mailer:          FastMail instance
        db:              AsyncSession (used for global template context)

    Raises:
        Exception: re-raised after logging, so callers can decide whether the
            support contact failure should surface to the user.
    """

    try:
        await send_email(
            recipient=support_email,
            subject=f"Disabled Account Contact: {user_email}",
            mailer=mailer,
            db=db,
            full_names=user_full_names,
            template_name="emails/support.html",
            template_body={
                "user_email": user_email,
                "message": message,
            },
        )

        logger.info(
            "Support message from %s sent to %s",
            user_email,
            support_email,
        )

    except Exception:
        logger.critical(
            "CRITICAL: Failed to send support message "
            "from %s to %s",
            user_email,
            support_email,
            exc_info=True,
        )
        raise








_OTP_LABELS: dict[str, str] = {
    "registration": "registration",
    "login": "login",
    "email_change": "email change",
    "password_reset": "password reset",
    "change_password": "password change",
}





