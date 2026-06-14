"""Orchestrator for the Riipen project alert poller.

Run once per invocation (designed to be triggered by cron every 15 min):

    1. Scrape the experience listing (logging in / reusing session).
    2. Filter to target categories + minimum hours.
    3. Diff against seen_projects.json.
    4. Email an alert for each genuinely-new match, then immediately mark it
       seen so a later failure can't cause a duplicate alert.
"""

import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

import storage
from filter import filter_projects
from http_scraper import LoginError, ScrapeError, scrape_projects
from notifier import EmailSendError, send_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("riipen-alert")

# Login is via the session captured by login_setup.py (cookies), so no Riipen
# email/password is needed here.
REQUIRED_VARS = [
    "RIIPEN_EXPERIENCE_URL",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "ALERT_EMAIL",
]


def _load_config() -> dict:
    load_dotenv()
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        raise SystemExit(
            f"Missing required env vars: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill it in."
        )
    return {v: os.environ[v] for v in REQUIRED_VARS}


def run() -> int:
    started = datetime.now()
    logger.info("=== Riipen poller run started ===")

    cfg = _load_config()
    seen = storage.load_seen()

    # 1. Scrape ----------------------------------------------------------
    # Pass the seen set so only new projects get a detail-page fetch.
    try:
        projects = scrape_projects(
            experience_url=cfg["RIIPEN_EXPERIENCE_URL"],
            seen_ids=seen,
        )
    except LoginError as exc:
        logger.error("Login failed: %s", exc)
        return 2
    except ScrapeError as exc:
        logger.error("Scrape failed: %s", exc)
        return 3
    except Exception as exc:  # includes Playwright timeouts
        logger.exception("Unexpected error during scrape: %s", exc)
        return 3

    logger.info("Scraped %d projects total.", len(projects))

    # 2. Diff against seen, then filter the new ones --------------------
    new_projects = [p for p in projects if p["id"] not in seen]
    matching = filter_projects(new_projects)
    matching_ids = {p["id"] for p in matching}
    logger.info(
        "%d new project(s); %d match the filters.",
        len(new_projects),
        len(matching),
    )

    # 3. Alert + persist -------------------------------------------------
    sent, failed = 0, 0
    for project in matching:
        detected_at = datetime.now()
        try:
            send_alert(
                project,
                smtp_user=cfg["SMTP_USER"],
                smtp_password=cfg["SMTP_PASSWORD"],
                alert_email=cfg["ALERT_EMAIL"],
                detected_at=detected_at,
            )
            # Persist immediately so a crash can't re-alert this project.
            storage.add_seen([project["id"]])
            sent += 1
        except EmailSendError as exc:
            # Do NOT mark seen — we want to retry on the next run.
            logger.error(
                "Could not alert on %r: %s", project.get("title"), exc
            )
            failed += 1

    # 4. Mark non-matching but successfully-enriched projects as seen, so
    #    we don't re-fetch their detail pages on every future run. Projects
    #    that failed enrichment stay unseen and get retried next time.
    processed = [
        p["id"]
        for p in new_projects
        if p["id"] not in matching_ids and p.get("enriched")
    ]
    if processed:
        storage.add_seen(processed)
        logger.info("Marked %d non-matching project(s) as seen.", len(processed))

    if not matching:
        logger.info("Nothing new to alert on.")

    logger.info("Alerts sent: %d, failed: %d.", sent, failed)
    _log_finish(started)
    return 0 if failed == 0 else 4


def _log_finish(started: datetime) -> None:
    elapsed = (datetime.now() - started).total_seconds()
    logger.info("=== Run finished in %.1fs ===", elapsed)


if __name__ == "__main__":
    sys.exit(run())
