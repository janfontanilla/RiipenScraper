# Riipen Project Alert

Monitors your Riipen learner experience dashboard and emails you when **new**
projects matching your criteria appear. Designed to run as a standalone poller
on a 15-minute cron schedule.

## What it does

1. **Login (one-time, local):** `login_setup.py` opens a real browser so you log
   in by hand once; it saves the session cookies to `session_state.json`.
   (Riipen blocks scripted logins, so this manual step is required.)
2. **Poll (recurring, no browser):** `main.py` fetches your experience listing
   and each new project's detail page over plain HTTP using those cookies — the
   pages are server-rendered, so no browser is needed. It extracts title,
   company, category/skill tags, total hours, and URL.
3. Keeps only projects in **Software Development, Website Development,
   Cloud Technologies, or Artificial Intelligence** that have **≥ 60 hours**.
4. Compares against `seen_projects.json` and emails an alert for every
   genuinely new match — then records it immediately so you're never alerted
   twice.

## Project layout

```
riipen-alert/
├── .env.example            # copy to .env and fill in
├── README.md
├── requirements.txt        # full local install (incl. Playwright for login)
├── requirements-poller.txt # minimal deps for the recurring poller (no browser)
├── login_setup.py          # one-time manual login -> session_state.json (Playwright)
├── http_scraper.py         # HTTP listing/detail fetch + parse (no browser)
├── scraper.py              # Playwright helpers used by login_setup.py
├── parsing.py              # pure parsing helpers (hours, ids, origin)
├── filter.py               # category + hours filtering
├── notifier.py             # SMTP email sender
├── storage.py              # seen_projects.json read/write
├── main.py                 # orchestrator (run this every 15 min)
└── Dockerfile              # slim, browser-free image for cloud deploy
```

## Setup

Requires **Python 3.11+**.

```bash
cd riipen-alert
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium   # one-time browser download

cp .env.example .env               # then edit .env with your values
```

### Configuring `.env`

| Variable | Notes |
| --- | --- |
| `RIIPEN_EMAIL` / `RIIPEN_PASSWORD` | Your Riipen login. |
| `RIIPEN_EXPERIENCE_URL` | Open your project listing page while logged in and copy the URL from the address bar. |
| `SMTP_SERVER` / `SMTP_PORT` | SMTP host/port. Defaults to `smtp.gmail.com` / `587`. |
| `SMTP_USER` / `SMTP_PASSWORD` | Account that sends the alerts + its **app password**. See note below. |
| `ALERT_EMAIL` | Where alerts are delivered (can be any address). |

> **Why Gmail, not Outlook?** Microsoft has disabled basic-auth SMTP on
> personal `outlook.com` accounts (login fails with `535 5.7.139 basic
> authentication is disabled`), and app passwords don't bypass it — only
> OAuth2 works. Gmail still supports app-password SMTP, so it's the simplest
> sender. For Gmail: enable **2-Step Verification**, then **Google Account →
> Security → App passwords**, generate one, and paste it as `SMTP_PASSWORD`.
> Alerts can still be *delivered* to your Outlook inbox via `ALERT_EMAIL`.

## Logging in (one-time)

Riipen rejects scripted form logins (the login endpoint returns HTTP 400 for
automated POSTs), so the poller authenticates by **reusing a session you create
manually once**:

```bash
python login_setup.py
```

A real browser window opens — log in however you normally do (email/password,
**Log in with Google**, or LinkedIn). Once you reach your dashboard, the script
saves your session to `session_state.json` and closes. `main.py` then reuses
that session. Re-run `login_setup.py` only when the session eventually expires
(you'll see a "No valid Riipen session" error).

> The browser uses Microsoft Edge via `BROWSER_CHANNEL=msedge` on Windows
> because Playwright's bundled Chromium needs the MS Visual C++ Redistributable.
> Keep that variable set (it's in `.env`).

## Running

```bash
python main.py
```

Exit codes: `0` success, `2` login failure, `3` scrape failure,
`4` one or more email sends failed. Every run logs a timestamped summary to the
console.

### First run / debugging selectors

Riipen's HTML isn't publicly documented, so the CSS selectors in `scraper.py`
(the `SEL` dict and `_extract_project`) are best-effort. If a run scrapes
**0 projects**, watch the browser to find the right selectors:

```bash
HEADLESS=false python main.py
```

Inspect the project cards in devtools and update `SEL` accordingly.

## Scheduling

The script is a one-shot poller — it runs, alerts, and exits. Use an external
scheduler to run it every 15 minutes.

### GitHub Actions (free 24/7) — recommended

The recurring poller needs no browser, so it runs comfortably on GitHub
Actions' free scheduled runners. State persists by committing `seen_projects.json`
back to the repo; the session is supplied as an encrypted secret.

**Assumes the repo root is this `riipen-alert` folder** (the workflow lives at
`.github/workflows/poll.yml`).

1. **Push this folder to a GitHub repo** (private recommended). `seen_projects.json`
   is committed; `.env` and `session_state.json` are gitignored.

2. **Create the session secret.** On your machine, after running
   `login_setup.py`, base64-encode the session and copy it:
   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("session_state.json")) | Set-Clipboard
   ```
   In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, name it `SESSION_STATE_B64`, paste the value.

3. **Add the other secrets** the same way (one each):
   `RIIPEN_EXPERIENCE_URL`, `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`,
   `SMTP_PASSWORD`, `ALERT_EMAIL`.

4. **Enable Actions** (Actions tab). The poller then runs every 15 min. Use
   **Run workflow** on the "Riipen poller" workflow to test it immediately.

**Notes & caveats**
- When the session expires, `main.py` exits non-zero and the workflow run shows
  as **failed** — GitHub emails you about failed runs, which is a free
  "refresh the session" signal. To fix: re-run `login_setup.py` locally and
  update the `SESSION_STATE_B64` secret.
- GitHub may delay scheduled runs under load (occasionally 15–30+ min instead
  of exactly 15), and disables schedules on repos with no commits for 60 days
  (the poller's own state commits keep it alive).
- Datacenter IPs may cause Riipen to expire sessions sooner than your home IP.

### Linux / macOS cron

```bash
crontab -e
```

```cron
# Every 15 minutes. Adjust the paths to your checkout + venv.
*/15 * * * * cd /home/you/riipen-alert && /home/you/riipen-alert/.venv/bin/python main.py >> /home/you/riipen-alert/poller.log 2>&1
```

### Railway (24/7 cloud) — with the Docker image

This repo ships a `Dockerfile` (based on Playwright's official image, so
Chromium + Linux deps are included — no Edge/VC++ workaround needed, and you
must **not** set `BROWSER_CHANNEL` in the cloud).

**1. Create the project & service**
- Push this folder to a Git repo and create a Railway project from it. Railway
  auto-detects the `Dockerfile`.

**2. Set environment variables** (Railway → service → Variables) — one per
`.env` entry, **except** `BROWSER_CHANNEL` (leave it unset on Linux):
`RIIPEN_EMAIL`, `RIIPEN_PASSWORD`, `RIIPEN_EXPERIENCE_URL`, `SMTP_SERVER`,
`SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL`.

**3. Attach a persistent volume** (Railway → service → Volumes) mounted at
**`/data`**. The image already sets `DATA_DIR=/data`, so `session_state.json`
and `seen_projects.json` live there and survive restarts. Without this, every
run looks "fresh" and you'd re-alert everything.

**4. Seed the login session onto the volume.** This is the manual step that
can't be automated (Riipen blocks scripted login):
- Run `python login_setup.py` **on your local machine** and log in. This writes
  `session_state.json`.
- Upload that file into the volume at `/data/session_state.json` (use
  `railway run`/`railway ssh`, or a one-off `railway run cp` step, or Railway's
  volume file tools).

**5. Schedule it.** Railway → service → Settings → **Cron Schedule**:
```
*/15 * * * *
```
The container runs `python main.py` once per trigger and exits.

> **Session refresh:** when the session expires, runs exit with code 2 and log
> "No valid Riipen session". Re-run `login_setup.py` locally and re-upload the
> file to `/data`. Consider watching the logs or exit codes so you notice when
> alerts go quiet. Running from a datacenter IP may also cause Riipen to expire
> sessions sooner than it does from your home network.

## Files written at runtime

- `session_state.json` — saved Playwright login session (gitignored).
- `seen_projects.json` — IDs of projects already alerted on (gitignored).
