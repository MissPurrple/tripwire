"""Sending email, used for the weekly summary.

Works with any email service that offers SMTP (Gmail with an app password,
Resend, Brevo, Mailgun...). Set these in Vercel's Environment Variables:

  SMTP_HOST      e.g. smtp.gmail.com or smtp.resend.com
  SMTP_PORT      587 (usual) or 465
  SMTP_USER      the login the email service gives you
  SMTP_PASSWORD  the password or API key it gives you
  MAIL_FROM      who the email is from, e.g. "Tripwire <hello@example.org>"

Without them, Tripwire simply doesn't send email, and says so on the page.
"""

import os
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid


class MailError(Exception):
    """An email couldn't be sent. The message says why, in plain words."""


def configured():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("MAIL_FROM"))


def send(to, subject, text, html=None):
    if not configured():
        raise MailError("Email isn't set up on this site yet.")
    message = EmailMessage()
    message["From"] = os.environ["MAIL_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain="tripwire")
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.starttls()
        with server:
            if user:
                server.login(user, password or "")
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("The email service didn't accept the login. Check SMTP_USER and SMTP_PASSWORD.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"The email couldn't be sent ({exc.__class__.__name__}). Check the SMTP settings.") from exc
