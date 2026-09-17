async def generate_and_send_otp(
    user: User,
    otp_type: str,
    subject: str,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    override_email: str | None = None,
) -> None:
    """
    Generate OTP, store its SHA-256 hash in Redis, then queue the email.

    Redis key:
        otp:{user_id}:{otp_type}

    Redis value:
        SHA-256(otp)

    Supported types:
        registration
        login
        email_change
        change_password
        password_reset
    """

    allowed = {
        "registration",
        "login",
        "email_change",
        "change_password",
        "password_reset",
    }

    if otp_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OTP type",
        )

    user_id = get_user_id(user)

    # =========================================================================
    # RATE LIMIT
    # =========================================================================

    rate_key = f"otp_rate:{user_id}:{otp_type}"

    count = await redis.incr(rate_key)

    if count == 1:
        await redis.expire(
            rate_key,
            OTP_RATE_WINDOW,
        )

    if count > OTP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again in 1 hour.",
        )

    # =========================================================================
    # GENERATE OTP
    # =========================================================================

    otp = generate_otp()
    otp_hash = hash_otp(otp)

    otp_key = f"otp:{user_id}:{otp_type}"

    # =========================================================================
    # STORE HASH
    #
    # SET overwrites any previous OTP for this user + type.
    #
    # Therefore:
    #
    # old OTP → immediately invalid
    # new OTP → becomes the only valid OTP
    # =========================================================================

    await redis.set(
        otp_key,
        otp_hash,
        ex=int(
            timedelta(
                minutes=OTP_EXPIRE_MINUTES
            ).total_seconds()
        ),
    )

    recipient = (
        override_email
        if override_email is not None
        else user.email
    )

    logger.info(
        "OTP stored for user_id=%s (type=%s). "
        "Queuing email...",
        user_id,
        otp_type,
    )

    # =========================================================================
    # QUEUE EMAIL
    #
    # Plaintext OTP exists only in application memory.
    # It is never stored in Redis.
    # =========================================================================

    background_tasks.add_task(
        send_otp,
        email=recipient,
        otp=otp,
        subject=subject,
        otp_type=otp_type,
        mailer=mailer,
    )



#updated latest from deepseek
"""
Generic email sending utility with retry logic and global template context.

Responsibilities:
    - Build plain-text or HTML emails
    - Provide global template variables (current_year, sitename)
    - Send emails
    - Retry transient SMTP/network failures

This module does NOT contain OTP business logic.
"""

import logging
import smtplib
import socket
from datetime import datetime, timezone
from typing import Any

from fastapi_mail import FastMail, MessageSchema, MessageType
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from api.services.home import get_home_settings_logic


logger = logging.getLogger(__name__)


# =============================================================================
# RETRY CONFIGURATION
# =============================================================================

EMAIL_RETRY_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    socket.timeout,
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
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Return variables available to all HTML email templates.

    The sitename is read from the database (via get_home_settings_logic)
    so that the email footer always reflects the current Home.sitename.
    """

    home = await get_home_settings_logic(session)

    return {
        "current_year": datetime.now(timezone.utc).year,
        "sitename": home.sitename,
    }


# =============================================================================
# GENERIC EMAIL SENDER
# =============================================================================

async def send_email(
    recipient: str,
    subject: str,
    mailer: FastMail,
    session: AsyncSession,
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
        global_context = await _global_email_context(session)

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
OTP email utilities.

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
from sqlalchemy.ext.asyncio import AsyncSession

from api.utils.email import send_email


logger = logging.getLogger(__name__)


# =============================================================================
# OTP CONFIGURATION
# =============================================================================

OTP_LABELS = {
    "registration": "registration",
    "login": "login",
    "email_change": "email change",
    "password_reset": "password reset",
}

OTP_EXPIRE_MINUTES = 10


# =============================================================================
# OTP EMAIL
# =============================================================================

async def send_otp(
    email: str,
    otp: str,
    subject: str,
    otp_type: str,
    mailer: FastMail,
    session: AsyncSession,
) -> None:
    """
    Send an OTP email using the shared HTML email utility.

    Supported OTP types:
        - registration
        - login
        - email_change
        - password_reset

    The session is used only to resolve the current sitename for the email
    footer (via get_home_settings_logic). OTP lifecycle is unaffected.
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
            session=session,
            template_name="otp.html",
            template_body={
                "otp": otp,
                "label": otp_label,
                "otp_type": otp_type,
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


#old
"""Email sending utilities with retry logic."""
import logging
import smtplib
import socket
from pydantic import EmailStr
from fastapi_mail import FastMail, MessageSchema, MessageType
from tenacity import (
    RetryCallState,
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)
def _before_sleep_log(retry_state: RetryCallState) -> None:
    attempt=retry_state.attempt_number
    if retry_state.next_action is not None:
        sleep_time=getattr(retry_state.next_action, "sleep",0)
        logger.warning(
            f"Email send failed(attempt {attempt}). "
            f"retrying in {sleep_time:.1f}s...."
        )
    else:
        logger.warning(f"Email send failed(attempt {attempt}).Retrying")


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((
        ConnectionError,
        TimeoutError,
        socket.timeout,
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPException,
        # ✅ NOT SMTPAuthenticationError - retrying bad credentials is pointless
    )),
    before_sleep=_before_sleep_log,
    reraise=True,
)
async def send_email(
    recipient: str,
    subject: str,
    body: str,
    mailer: FastMail,
) -> None:
    """
    Send email with automatic retry on transient failures.
    
    Args:
        recipient: Recipient email address
        subject: Email subject
        body: Email body (plain text)
        mailer: FastMail instance (from app.state)
        
    Raises:
        Exception: After all retries exhausted (caller should log/handle)
    """
    message = MessageSchema(
        subject=subject,
        recipients=[recipient], # type: ignore[arg-type]
        body=body,
        subtype=MessageType.plain,
    )
    
    try:
        await mailer.send_message(message)
        logger.info(f"Email sent successfully to {recipient}")
    except Exception:
        logger.error(f"Failed to send email to {recipient} after all retries")
        raise


async def send_otp(
    email: str,
    otp: str,
    subject: str,
    otp_type: str,
    mailer: FastMail,
) -> None:
    """
    Send OTP email - wraps send_email with OTP-specific formatting.
    
    Args:
        email: Recipient email
        otp: 6-digit OTP code
        subject: Email subject
        otp_type: Type of OTP ("registration" or "login")
        mailer: FastMail instance
    """
    body = (
        f"Your {otp_type} OTP is: {otp}\n\n"
        f"This code expires in 10 minutes.\n"
        f"If you didn't request this, please ignore this email."
    )
    
    try:
        await send_email(
            recipient=email,
            subject=subject,
            body=body,
            mailer=mailer,
        )
    except Exception as exc:
        # ✅ This is the key part the original advice was likely about:
        # Don't let a failed background email vanish silently.
        # Log loudly so ops/alerts can catch it.
        logger.critical(
            f"CRITICAL: OTP email to {email} failed after all retries. "
            f"User cannot complete {otp_type}. Error: {exc}"
        )
        # Optionally: write to a "failed_notifications" table or alert system
        # so support can manually intervene if this happens repeatedly
        
        
        
        
        


async def send_support_message(
    support_email: str,
    user_email: str,
    message: str,
    mailer: FastMail,
) -> None:
    """
    Send disabled user's contact message to support email.

    Args:
        support_email: Destination (country.email_support or settings.mail_username)
        user_email: The user who sent the message
        message: The user's message
        mailer: FastMail instance
    """
    body = (
        f"A disabled user has contacted support.\n\n"
        f"From: {user_email}\n"
        f"Message:\n{message}\n\n"
        f"Please review this account and take appropriate action."
    )

    try:
        await send_email(
            recipient=support_email,
            subject=f"Disabled Account Contact: {user_email}",
            body=body,
            mailer=mailer,
        )
        logger.info(
            f"Support message from {user_email} "
            f"sent to {support_email}"
        )
    except Exception as exc:
        logger.critical(
            f"CRITICAL: Failed to send support message "
            f"from {user_email} to {support_email}. Error: {exc}"
        )
