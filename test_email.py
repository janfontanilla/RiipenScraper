"""Standalone check that Outlook SMTP auth + sending works.

Run this BEFORE the full poller to confirm your app password is valid:

    python test_email.py

It sends one sample alert to ALERT_EMAIL using a fake project, so you don't
need working Riipen credentials to verify the email path.
"""

import logging
import os
import sys

from dotenv import load_dotenv

from notifier import EmailSendError, send_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SAMPLE_PROJECT = {
    "title": "TEST - Riipen alert email check",
    "company": "Riipen Alert Poller",
    "categories": ["Artificial Intelligence"],
    "hours": 80,
    "url": "https://futurepath.riipen.com/experiences/vO1X9bVE",
}


def main() -> int:
    load_dotenv()
    for var in ("SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL"):
        if not os.environ.get(var):
            print(f"Missing {var} in .env")
            return 1

    try:
        send_alert(
            SAMPLE_PROJECT,
            smtp_user=os.environ["SMTP_USER"],
            smtp_password=os.environ["SMTP_PASSWORD"],
            alert_email=os.environ["ALERT_EMAIL"],
        )
    except EmailSendError as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print(f"\nSUCCESS: test email sent to {os.environ['ALERT_EMAIL']}. Check your inbox.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
