## message using background task.py


from tenacity import retry, stop_after_attempt,wait_exponential, retry_if_exception_type
import logging
import asyncio
from fastapi_mail import FastMail, MessageSchema, MessageType
from api.core.mail import mail_config
from api.core.celery import app_name

logger=logging.getLogger(__name__)

@retry(
    stop=stop_after_attempt(4),
    wait= wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, Exception)),
    before_sleep=lambda rs:logger.warning(
        f"OTP email failed(attempt {rs.attempt_number}).Retrying in {rs.next_action.sleep:.1f}s..."),
    reraise=True,
)
async def _send_otp_email(recipient:str, otp:str, subject:str) -> None:
    message=MessageSchema(
        subject=subject,
        recipients=[recipient],
        body=f"Your OTP is {otp} it expires in 20 minutes",
        subtype=MessageType.plain,
    )
    fm=FastMail(mail_config)
    await fm.send_message(message)
    logger.info(f"OTP email sent to {recipient}")
    
    
@app_name.task(bind=True,max_retries=0)
def send_otp_email_task(self, recipient:str, otp:str, subject:str):
    try:
        loop=asyncio.get_event_loop()
        loop.run_until_complete(_send_otp_email(recipient,otp,subject))
    except Exception as exc:
        logger.error(f"Failed to send OTP to {recipient} after all retries: {exc}")
        raise
      
      
####   vs direct message no background task user_email.py



from fastapi_mail import MessageSchema,MessageType
from src.ecommerce.dependency import Mailer
from tenacity import retry, stop_after_attempt,wait_exponential, retry_if_exception_type
import smtplib
import socket

@retry(
    stop=stop_after_attempt(4),
    wait= wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((
        ConnectionError, 
        TimeoutError,
        socket.timeout,
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected, 
        smtplib.SMTPAuthenticationError,
        smtplib.SMTPException
        )),
    # before_sleep=lambda rs:logger.warning(
    #     f"OTP email failed(attempt {rs.attempt_number}).Retrying in {rs.next_action.sleep:.1f}s..."),
    reraise=True,
)

async def send_email(recipient:str, body:str, subject:str,mailer: Mailer) -> None:
    message=MessageSchema(
        subject=subject,
        recipients=[recipient],
        body=body,
        subtype=MessageType.plain,
    )
    await mailer.send_message(message)




