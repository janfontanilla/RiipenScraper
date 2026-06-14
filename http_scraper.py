"""Lightweight HTTP scraper — no browser required.

Riipen's listing and detail pages are server-rendered, so we fetch them with
plain HTTP using the session cookies captured by login_setup.py, then parse the
HTML with BeautifulSoup. This is the recurring poller's scraping layer; it
replaces the Playwright scraper (which is now only used for the one-time manual
login in login_setup.py).
"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from parsing import origin_of, parse_total_hours, project_id_from_url

logger = logging.getLogger(__name__)

# Keep state (incl. the session) on a persistent volume in the cloud via DATA_DIR.
DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(__file__)
SESSION_FILE = os.path.join(DATA_DIR, "session_state.json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 30


class LoginError(Exception):
    """Raised when the saved session is missing or no longer valid."""


class ScrapeError(Exception):
    """Raised when the listing cannot be fetched or parsed."""


def _load_cookie_header(session_file: str) -> str:
    """Build a Cookie header from the Playwright storage-state file."""
    if not os.path.exists(session_file):
        raise LoginError(
            f"No session file at {session_file}. Run 'python login_setup.py' "
            "to log in once and save a session."
        )
    with open(session_file, encoding="utf-8") as fh:
        state = json.load(fh)
    cookies = [c for c in state.get("cookies", []) if "riipen.com" in c.get("domain", "")]
    if not cookies:
        raise LoginError("Session file has no riipen.com cookies; re-run login_setup.py.")
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _fetch(url: str, cookie_header: str) -> str:
    """GET a page with the session cookie; raise LoginError if bounced to login."""
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie_header,
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            final_url = resp.geturl()
    except urllib.error.URLError as exc:
        raise ScrapeError(f"HTTP request to {url} failed: {exc}") from exc

    if "/login" in final_url or 'name="session[password]"' in html:
        raise LoginError(
            "Saved session is no longer valid (redirected to login). "
            "Re-run 'python login_setup.py' to refresh it."
        )
    return html


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _parse_listing(html: str, origin: str) -> List[Dict]:
    """Parse the experience listing HTML into base project dicts."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("a[href^='/matches/']")
    logger.info("Found %d project cards.", len(cards))

    projects: List[Dict] = []
    for card in cards:
        href = card.get("href", "")
        url = origin + href if href.startswith("/") else href

        title_el = card.select_one(".name-tag__name")
        title = ""
        if title_el:
            title = (title_el.get("title") or _text(title_el)).strip()
        if not title:
            continue

        company = _text(card.select_one(".text-dark-blue-600.line-clamp-1"))
        if not company:
            img = card.select_one("img[alt]")
            if img:
                company = img.get("alt", "").strip()

        categories: List[str] = []
        for chip in card.select("span.bg-orange-100"):
            t = _text(chip)
            if t and t not in categories:
                categories.append(t)

        projects.append(
            {
                "id": project_id_from_url(url, fallback=title),
                "title": title,
                "company": company,
                "categories": categories,
                "hours": None,
                "url": url,
            }
        )
    return projects


def _enrich(project: Dict, cookie_header: str) -> None:
    """Fetch a project's detail page to fill in total hours + skills text."""
    html = _fetch(project["url"], cookie_header)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    hours = parse_total_hours(text)
    if hours is not None:
        project["hours"] = hours
    # Keep detail text so the filter can match skills like "artificial
    # intelligence" that don't appear on the listing card.
    project["skills_text"] = text
    project["enriched"] = True
    logger.info("Enriched %r: hours=%s.", project.get("title"), project.get("hours"))


def scrape_projects(
    *,
    experience_url: str,
    session_file: str = SESSION_FILE,
    seen_ids: Optional[set] = None,
    **_ignored,
) -> List[Dict]:
    """Fetch + parse the listing over HTTP, enriching new projects with hours.

    Extra kwargs (e.g. email/password) are accepted and ignored so this is a
    drop-in replacement for the old Playwright scraper signature.
    """
    seen_ids = seen_ids or set()
    origin = origin_of(experience_url)
    cookie_header = _load_cookie_header(session_file)

    logger.info("Fetching listing: %s", experience_url)
    html = _fetch(experience_url, cookie_header)
    projects = _parse_listing(html, origin)
    if not projects:
        logger.warning("No projects parsed from the listing page.")

    to_enrich = [p for p in projects if p["id"] not in seen_ids]
    logger.info("Enriching %d new project(s) from detail pages.", len(to_enrich))
    for project in to_enrich:
        try:
            _enrich(project, cookie_header)
        except LoginError:
            raise  # session died mid-run; surface it
        except Exception as exc:  # one bad detail page shouldn't sink the run
            logger.warning("Failed to enrich %r: %s", project.get("title"), exc)

    return projects
