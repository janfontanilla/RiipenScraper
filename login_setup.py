"""One-time interactive login to capture a reusable Riipen session.

Riipen rejects automated form-login POSTs (anti-bot: the login endpoint
returns HTTP 400 for scripted submissions). So instead of automating the
login, we open a real browser window, let YOU log in by hand (email/password,
Google, LinkedIn — whatever you normally use), and then save the resulting
session cookies to ``session_state.json``. The poller (main.py) reuses that
session and only needs you to re-run this when the session eventually expires.

Usage:
    python login_setup.py

A browser window opens. Log in normally. Once you reach your dashboard the
script detects it, saves the session, and closes automatically.
"""

import asyncio
import os

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from scraper import SESSION_FILE, _is_logged_in, _origin_of

load_dotenv()

EXPERIENCE_URL = os.environ.get("RIIPEN_EXPERIENCE_URL", "https://futurepath.riipen.com")
BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL") or None
LOGIN_WAIT_SECONDS = int(os.environ.get("LOGIN_WAIT_SECONDS", "300"))


async def main() -> int:
    origin = _origin_of(EXPERIENCE_URL)
    login_url = origin + "/login"

    print("\n" + "=" * 64)
    print("  Riipen manual login — a browser window will open.")
    print("  Log in the way you normally do (password / Google / LinkedIn).")
    print(f"  Waiting up to {LOGIN_WAIT_SECONDS}s for you to reach your dashboard.")
    print("=" * 64 + "\n")

    async with async_playwright() as pw:
        launch_kwargs = {"headless": False}
        if BROWSER_CHANNEL:
            launch_kwargs["channel"] = BROWSER_CHANNEL
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(login_url, wait_until="domcontentloaded")

        # Poll until we're authenticated or we time out. We treat "logged in"
        # as: on the Riipen site, off the /login and /sessions pages, and the
        # password field is gone (a far more reliable signal than a nav bar).
        host = _origin_of(EXPERIENCE_URL).split("//", 1)[-1]
        logged_in = False
        last_url = ""
        for i in range(LOGIN_WAIT_SECONDS):
            await asyncio.sleep(1)
            try:
                url = page.url
                if url != last_url:
                    print(f"[{i:3}s] page at: {url}", flush=True)
                    last_url = url
                on_site = host in url
                on_auth_page = "/login" in url or "/sessions" in url
                has_pw = await page.locator("input[type='password']").count() > 0
                if on_site and not on_auth_page and not has_pw:
                    logged_in = True
                    break
            except Exception:
                # page may be mid-navigation; keep waiting
                continue

        if not logged_in:
            print("\nTimed out waiting for login. Nothing saved. Re-run when ready.")
            await browser.close()
            return 1

        await context.storage_state(path=SESSION_FILE)
        print(f"\nSuccess! Session saved to {SESSION_FILE}.")
        print("You can now run: python main.py")
        await browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
