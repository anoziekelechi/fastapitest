
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
