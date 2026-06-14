"""Outlook SMTP email notifier.

Sends one HTML alert per new matching project via smtp-mail.outlook.com
using STARTTLS on port 587.
"""

import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import Dict

logger = logging.getLogger(__name__)

# SMTP host/port are configurable so we can use any STARTTLS provider.
# Defaults target Gmail because Outlook personal accounts have disabled
# basic-auth SMTP (error 535 5.7.139). To use a different provider, set
# SMTP_SERVER / SMTP_PORT in .env, e.g. smtp-mail.outlook.com / 587.
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))


class EmailSendError(Exception):
    """Raised when an alert email cannot be delivered."""


def _build_message(
    project: Dict,
    *,
    smtp_user: str,
    alert_email: str,
    detected_at: datetime,
) -> EmailMessage:
    title = project.get("title") or "Untitled project"
    company = project.get("company") or "Unknown company"
    categories = ", ".join(project.get("categories") or []) or "—"
    hours = project.get("hours")
    hours_str = f"{hours} hours" if hours is not None else "—"
    url = project.get("url") or "#"
    ts = detected_at.strftime("%Y-%m-%d %H:%M:%S %Z").strip()

    msg = EmailMessage()
    msg["Subject"] = f"🚨 New Riipen Project: {title}"
    msg["From"] = formataddr(("Riipen Alerts", smtp_user))
    msg["To"] = alert_email

    plain = (
        f"New Riipen project matching your criteria:\n\n"
        f"Title:      {title}\n"
        f"Company:    {company}\n"
        f"Category:   {categories}\n"
        f"Hours:      {hours_str}\n"
        f"Link:       {url}\n"
        f"Detected:   {ts}\n"
    )
    msg.set_content(plain)

    html = f"""\
<html>
  <body style="font-family: Arial, Helvetica, sans-serif; color: #1a1a1a;">
    <h2 style="margin-bottom: 4px;">🚨 New Riipen Project</h2>
    <h3 style="margin-top: 0; color: #2b6cb0;">{escape(title)}</h3>
    <table cellpadding="6" style="border-collapse: collapse;">
      <tr><td><strong>Company</strong></td><td>{escape(company)}</td></tr>
      <tr><td><strong>Category</strong></td><td>{escape(categories)}</td></tr>
      <tr><td><strong>Hours</strong></td><td>{escape(hours_str)}</td></tr>
      <tr><td><strong>Detected</strong></td><td>{escape(ts)}</td></tr>
    </table>
    <p style="margin-top: 16px;">
      <a href="{escape(url)}"
         style="background:#2b6cb0;color:#fff;padding:10px 18px;
                text-decoration:none;border-radius:6px;display:inline-block;">
        View project on Riipen
      </a>
    </p>
    <p style="color:#777;font-size:12px;">Sent by your Riipen project poller.</p>
  </body>
</html>
"""
    msg.add_alternative(html, subtype="html")
    return msg


def send_alert(
    project: Dict,
    *,
    smtp_user: str,
    smtp_password: str,
    alert_email: str,
    detected_at: datetime = None,
) -> None:
    """Send one alert email for ``project`` via the configured SMTP server.

    Raises EmailSendError on any SMTP / auth failure so the caller can log
    it and continue with the remaining projects.
    """
    detected_at = detected_at or datetime.now()
    msg = _build_message(
        project,
        smtp_user=smtp_user,
        alert_email=alert_email,
        detected_at=detected_at,
    )

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info(
            "Sent alert to %s for project %r via %s.",
            alert_email,
            project.get("title"),
            SMTP_SERVER,
        )
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailSendError(
            f"SMTP authentication failed for {smtp_user} on {SMTP_SERVER}: {exc}"
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailSendError(f"Failed to send alert email: {exc}") from exc
