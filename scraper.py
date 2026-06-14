"""Playwright-based login + scraping for the Riipen learner dashboard.

Riipen renders projects client-side, so we drive a real (headless) browser.
Session cookies are persisted to ``session_state.json`` so we don't log in on
every run; if the saved session is expired we re-authenticate automatically.

NOTE ON SELECTORS
-----------------
Riipen's markup is not publicly documented and changes over time. The
selectors below are best-effort and centralised in the ``SEL`` dict / the
``_extract_project`` function. If scraping returns 0 projects, run once with
HEADLESS=False, inspect the project cards in devtools, and update these.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

logger = logging.getLogger(__name__)

# DATA_DIR lets a cloud host keep the session on a persistent volume (/data)
# so it survives restarts; defaults to next to the code for local runs.
DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(__file__)
SESSION_FILE = os.path.join(DATA_DIR, "session_state.json")

# Riipen uses white-labeled subdomains (e.g. futurepath.riipen.com), so the
# login URL and any relative links must be resolved against the SAME origin as
# the experience URL — not a hardcoded app.riipen.com.
LOGIN_PATH = "/login"

# Whether to run the browser headless. Set HEADLESS=false in the env while
# debugging selectors so you can watch the page.
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"

# Optional browser channel. Playwright's bundled Chromium needs the MS Visual
# C++ Redistributable on Windows; if that's missing (error: "side-by-side
# configuration is incorrect"), set BROWSER_CHANNEL=msedge to use the
# system-installed Microsoft Edge instead. Leave unset to use bundled Chromium.
BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL") or None

# Centralised selectors — verified against futurepath.riipen.com markup
# (June 2026). Adjust if Riipen's markup changes.
SEL = {
    "email_input": "#session_email, input[name='session[email]'], input[type='email']",
    "password_input": "#session_password, input[name='session[password]'], input[type='password']",
    # The native login form submits via <input type=submit name=commit>, NOT a
    # <button> — a generic button[type=submit] would hit "Log in with Google".
    "submit_button": "#new_session input[type='submit'], input[name='commit']",
    # Each project card on the listing page is an <a> linking to /matches/<id>.
    "project_card": "a[href^='/matches/']",
}

DEFAULT_TIMEOUT_MS = 30_000


class LoginError(Exception):
    """Raised when authentication with Riipen fails."""


class ScrapeError(Exception):
    """Raised when the project listing cannot be scraped."""


async def _is_logged_in(page: Page) -> bool:
    """Heuristic: are we on an authenticated page (not the login form)?

    Detected by the ABSENCE of a password field: authenticated pages don't
    render one, the login/sessions pages do. This is far more reliable than
    looking for a nav/logout element, which isn't present on every page (e.g.
    the experience listing page).
    """
    url = page.url
    if "/login" in url or "/sessions" in url:
        return False
    try:
        has_password_field = await page.locator("input[type='password']").count()
        return has_password_field == 0
    except Exception:
        return False


def _origin_of(url: str) -> str:
    """Return the scheme://host origin of a URL, e.g. https://futurepath.riipen.com."""
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


async def _dismiss_cookie_banner(page: Page) -> None:
    """Dismiss the cookie-consent banner if present, so it can't intercept
    clicks on the login button. Best-effort: ignore if not shown."""
    for label in ("Necessary Only", "Accept All", "Accept all", "Reject all"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0:
                await btn.first.click(timeout=3_000)
                logger.info("Dismissed cookie banner via '%s'.", label)
                return
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue


async def _login(page: Page, email: str, password: str, origin: str) -> None:
    """Fill and submit the Riipen login form on the experience's own origin."""
    login_url = origin + LOGIN_PATH
    logger.info("Logging in to Riipen (%s) as %s ...", login_url, email)
    await page.goto(login_url, wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)

    await _dismiss_cookie_banner(page)

    try:
        await page.fill(SEL["email_input"], email, timeout=DEFAULT_TIMEOUT_MS)
        await page.fill(SEL["password_input"], password, timeout=DEFAULT_TIMEOUT_MS)
        await page.click(SEL["submit_button"], timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        raise LoginError(
            "Could not find the login form fields — selectors may be stale."
        ) from exc

    # Wait for navigation away from the login page.
    try:
        await page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass

    if not await _is_logged_in(page):
        raise LoginError(
            "Login appears to have failed (still on login page). "
            "Check RIIPEN_EMAIL / RIIPEN_PASSWORD."
        )
    logger.info("Login successful.")


def _parse_total_hours(body_text: str) -> Optional[float]:
    """Extract total project hours from a detail page's Payment section.

    Riipen formats it as e.g. "50 hrs. x C$23.34 per hour" or
    "100 hrs. x C$23.34 per hour". We anchor on the "hrs. x" so we don't
    accidentally match "15 hrs. per invoice" or "Minimum payable: 4 hrs.".
    """
    if not body_text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*hrs?\.?\s*[x×]", body_text, re.IGNORECASE)
    return float(match.group(1)) if match else None


def _project_id_from_url(url: str, fallback: str) -> str:
    """Derive a stable ID from the project URL, else fall back to the title.

    Riipen uses alphanumeric slugs (e.g. /experiences/vO1X9bVE), not numeric
    IDs, so match any non-slash token after the resource segment. Strip any
    query string (?_gl=...) first so the same project always yields one ID.
    """
    if url:
        path = urlparse(url).path
        match = re.search(r"/(?:matches|projects?|experiences?)/([^/]+)", path)
        if match:
            return match.group(1)
        # Use the last path segment as a stable-ish ID.
        segment = path.rstrip("/").split("/")[-1]
        if segment:
            return segment
    return fallback


async def _extract_project(card, origin: str) -> Optional[Dict]:
    """Extract one project's fields from a card element handle.

    Returns None if the card has no recognisable title (e.g. it's a layout
    element that matched the selector by accident). ``origin`` is used to
    absolutise relative links against the experience's own subdomain.
    """
    async def text_of(selectors: List[str]) -> str:
        for sel in selectors:
            el = await card.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
        return ""

    # Title lives in a .name-tag__name element (with a full `title` attr).
    title = await text_of([".name-tag__name", "[data-test='project-title']", "h2", "h3"])
    if not title:
        title_el = await card.query_selector(".name-tag__name[title]")
        if title_el:
            title = (await title_el.get_attribute("title") or "").strip()
    if not title:
        return None

    # Company: the small line under the title, falling back to the logo alt.
    company = await text_of(
        ["[data-test='company-name']", ".text-dark-blue-600.line-clamp-1", ".company"]
    )
    if not company:
        img = await card.query_selector("img[alt]")
        if img:
            company = (await img.get_attribute("alt") or "").strip()

    # Category / skill tags are orange chips.
    categories: List[str] = []
    for sel in ["span.bg-orange-100", "[data-test='category']", ".tag", ".chip"]:
        for el in await card.query_selector_all(sel):
            txt = (await el.inner_text()).strip()
            if txt and txt not in categories:
                categories.append(txt)
        if categories:
            break

    # NOTE: the listing card does NOT expose total project hours or a posted
    # date — only a pay rate ("23.34 CAD per hour") and an apply-by deadline.
    # Total hours therefore must come from each project's detail page; that
    # enrichment step is added once the detail-page markup is confirmed.
    hours = None
    rate_text = await text_of(["[class*='dollar'] ~ span", ".rate"])
    deadline = ""
    for el in await card.query_selector_all("span"):
        txt = (await el.inner_text()).strip()
        if txt.lower().startswith("apply before"):
            deadline = txt.replace("Apply before", "").strip()
            break

    # The card element is itself the <a>, so read href off it directly;
    # fall back to a descendant anchor just in case.
    url = await card.get_attribute("href") or ""
    if not url:
        link = await card.query_selector("a[href]")
        if link:
            url = await link.get_attribute("href") or ""
    if url.startswith("/"):
        url = origin + url

    project_id = _project_id_from_url(url, fallback=title)

    return {
        "id": project_id,
        "title": title,
        "company": company,
        "categories": categories,
        "hours": hours,
        "rate": rate_text,
        "apply_before": deadline,
        "posted_date": deadline,  # best available proxy from the listing card
        "url": url,
    }


async def _scrape_listing(page: Page, experience_url: str, origin: str) -> List[Dict]:
    """Navigate to the experience page and extract every project card."""
    logger.info("Navigating to experience listing: %s", experience_url)
    await page.goto(
        experience_url, wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS
    )

    if not await _is_logged_in(page):
        raise ScrapeError("Not authenticated when reaching the listing page.")

    try:
        await page.wait_for_selector(SEL["project_card"], timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.warning(
            "No project cards found with selector %r. The page may be empty "
            "or the selector is stale.",
            SEL["project_card"],
        )
        return []

    cards = await page.query_selector_all(SEL["project_card"])
    logger.info("Found %d candidate project cards.", len(cards))

    projects: List[Dict] = []
    for card in cards:
        try:
            project = await _extract_project(card, origin)
        except Exception as exc:  # one bad card shouldn't sink the run
            logger.warning("Failed to parse a project card: %s", exc)
            continue
        if project:
            projects.append(project)

    logger.info("Extracted %d projects.", len(projects))
    return projects


async def _enrich_from_detail(page: Page, project: Dict) -> None:
    """Visit a project's detail page to fill in total hours + skills text.

    The listing card lacks total hours, so we open /matches/<id> and read the
    Payment section ("N hrs. x C$… per hour") plus the full body text, which
    we keep as ``skills_text`` for richer category matching. Sets
    ``project['enriched'] = True`` on success so the caller knows it's safe to
    skip re-fetching next run.
    """
    url = project.get("url")
    if not url:
        return
    await page.goto(url, wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)
    body = await page.inner_text("body")

    hours = _parse_total_hours(body)
    if hours is not None:
        project["hours"] = hours
    # Keep the detail text so the category filter can match skills like
    # "artificial intelligence" that don't appear on the listing card.
    project["skills_text"] = body
    project["enriched"] = True
    logger.info(
        "Enriched %r: hours=%s.", project.get("title"), project.get("hours")
    )


async def scrape_projects(
    *,
    email: str,
    password: str,
    experience_url: str,
    session_file: str = SESSION_FILE,
    seen_ids: Optional[set] = None,
) -> List[Dict]:
    """Top-level scrape entry point.

    Reuses a saved session if present; re-authenticates and saves a fresh
    session if the stored one is missing or expired. Projects whose id is NOT
    in ``seen_ids`` are enriched with detail-page data (hours/skills); already
    seen projects skip that extra page load.
    """
    seen_ids = seen_ids or set()
    origin = _origin_of(experience_url)
    async with async_playwright() as pw:
        launch_kwargs = {"headless": HEADLESS}
        if BROWSER_CHANNEL:
            launch_kwargs["channel"] = BROWSER_CHANNEL
            logger.info("Launching browser via channel '%s'.", BROWSER_CHANNEL)
        browser: Browser = await pw.chromium.launch(**launch_kwargs)
        try:
            have_session = os.path.exists(session_file)
            context = await browser.new_context(
                storage_state=session_file if have_session else None
            )
            context.set_default_timeout(DEFAULT_TIMEOUT_MS)
            page = await context.new_page()

            # Try the listing directly using the saved session first.
            needs_login = True
            if have_session:
                try:
                    await page.goto(
                        experience_url,
                        wait_until="networkidle",
                        timeout=DEFAULT_TIMEOUT_MS,
                    )
                    needs_login = not await _is_logged_in(page)
                    if needs_login:
                        logger.info("Saved session expired; re-authenticating.")
                except PlaywrightTimeoutError:
                    logger.warning("Timeout loading listing with saved session.")
                    needs_login = True

            if needs_login:
                # Riipen blocks scripted form-login (POST /sessions -> 400), so
                # by default we require a session captured via login_setup.py.
                # Set RIIPEN_AUTO_LOGIN=true to attempt automated form login.
                if os.environ.get("RIIPEN_AUTO_LOGIN", "false").lower() == "true":
                    await _login(page, email, password, origin)
                    await context.storage_state(path=session_file)
                    logger.info("Saved fresh session to %s.", session_file)
                else:
                    raise LoginError(
                        "No valid Riipen session. Run 'python login_setup.py' "
                        "to log in once manually and save the session, then "
                        "re-run. (Automated login is disabled because Riipen "
                        "rejects scripted logins; set RIIPEN_AUTO_LOGIN=true to "
                        "force an attempt.)"
                    )

            projects = await _scrape_listing(page, experience_url, origin)

            # Enrich only projects we haven't processed before, to limit the
            # number of detail-page loads each run.
            to_enrich = [p for p in projects if p["id"] not in seen_ids]
            logger.info(
                "Enriching %d new project(s) from detail pages.", len(to_enrich)
            )
            for project in to_enrich:
                try:
                    await _enrich_from_detail(page, project)
                except PlaywrightTimeoutError:
                    logger.warning(
                        "Timeout enriching %r; will retry next run.",
                        project.get("title"),
                    )
                except Exception as exc:  # one bad detail page shouldn't sink the run
                    logger.warning(
                        "Failed to enrich %r: %s", project.get("title"), exc
                    )

            return projects
        finally:
            await browser.close()


def scrape_projects_sync(**kwargs) -> List[Dict]:
    """Blocking wrapper around :func:`scrape_projects` for non-async callers."""
    return asyncio.run(scrape_projects(**kwargs))
