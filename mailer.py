"""Sends this app's emails: the 6-digit verification code, the password reset link, and a teammate's invitation."""
import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

import config

logger = logging.getLogger(__name__)


class MailNotConfigured(RuntimeError):
    pass


def send_email(to: str, subject: str, body: str) -> None:
    if not config.SMTP_HOST:
        if config.IS_DEV:
            # A developer has no mail server: the message (with its code or link) goes to the console instead.
            logger.warning(f"[mail:not sent, development] to={to} subject={subject!r}\n{body}")
            return
        raise MailNotConfigured("SMTP_HOST is not set")
    message = EmailMessage()
    message["From"] = config.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
        smtp.starttls()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(message)


def send_verification_code(to: str, name: str, code: str) -> None:
    send_email(
        to, f"{code} is your Advart Gen verification code",
        f"Hi {name},\n\nYour Advart Gen verification code is {code}.\n"
        f"It expires in {config.VERIFY_CODE_MINUTES} minutes.\n\n"
        "If you did not create an Advart Gen account, you can ignore this email.\n",
    )


def send_reset_link(to: str, name: str, token: str) -> None:
    link = f"{config.FRONTEND_URL}/reset-password?token={token}"
    send_email(
        to, "Reset your Advart Gen password",
        f"Hi {name},\n\nUse this link to choose a new password:\n{link}\n\n"
        f"It expires in {config.RESET_LINK_MINUTES} minutes. If you did not ask for it, you can ignore this email.\n",
    )


def send_invite(to: str, name: str, inviter: str, company: str, token: Optional[str]) -> None:
    """token = a set-your-password link for someone new; None = they already have a login (it just gained a brand)."""
    if token:
        how = (f"Choose your password here to get started:\n{config.FRONTEND_URL}/reset-password?token={token}&invite=1\n\n"
               f"The link works for {config.INVITE_LINK_DAYS} days.\n")
    else:
        how = f"Sign in as usual at {config.FRONTEND_URL}/login and pick {company} from the Brand menu.\n"
    send_email(to, f"{inviter} invited you to {company} on Advart Gen", f"Hi {name},\n\n{inviter} added you to {company} on Advart Gen.\n\n{how}")
