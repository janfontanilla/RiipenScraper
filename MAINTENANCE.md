# Maintenance

Day-to-day this runs itself on GitHub Actions every ~15 minutes. The only
recurring task is refreshing the login session when it eventually expires.

## When a run goes red (session expired)

**Symptom:** a "Riipen poller" run fails and GitHub emails you about it. The
failed step log shows something like *"Saved session is no longer valid
(redirected to login)"* or a `LoginError`.

**Fix (about 1 minute, done on your local PC):**

1. Open PowerShell in the project folder:
   ```powershell
   cd "C:\Users\janfo\OneDrive\Desktop\RiipenScraper\riipen-alert"
   ```

2. Re-create the session by logging in manually:
   ```powershell
   python login_setup.py
   ```
   An Edge window opens — log in to Riipen the way you normally do. Once you
   reach your dashboard it saves `session_state.json` and closes.

3. Copy the new session to your clipboard as base64:
   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("session_state.json")) | Set-Clipboard
   ```

4. Update the GitHub secret:
   - Go to **Settings → Secrets and variables → Actions**
     (https://github.com/janfontanilla/RiipenScraper/settings/secrets/actions)
   - Click **`SESSION_STATE_B64`** → **Update** (or the pencil) → paste (Ctrl+V) → **Update secret**

5. Re-run the workflow to confirm: **Actions → Riipen poller → Run workflow**.
   A green check means you're back in business.

> Tip: if runs almost never go red, you're on a "sliding" session that stays
> alive as long as the poller keeps hitting Riipen — nothing to do.

## Repository secrets (the 7 the workflow needs)

| Secret | Value |
| --- | --- |
| `SESSION_STATE_B64` | base64 of `session_state.json` (see refresh steps above) |
| `RIIPEN_EXPERIENCE_URL` | your experience listing URL |
| `SMTP_SERVER` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | the Gmail address that sends alerts |
| `SMTP_PASSWORD` | the Gmail **app password** |
| `ALERT_EMAIL` | where alerts are delivered |

## Common changes

- **Change which categories trigger alerts** — edit `TARGET_CATEGORIES` in
  `filter.py`, commit, and push.
- **Change the minimum hours** — edit `MIN_HOURS` in `filter.py`.
- **Change the schedule** — edit the `cron` line in
  `.github/workflows/poll.yml` (currently `*/15 * * * *`).
- **Stop re-alerting / reset history** — delete the contents of
  `seen_projects.json` (set it to `{"seen": []}`) and commit. The next run
  will treat all current projects as new and alert on every match again.

## Testing

- **Email path only:** `python test_email.py` (sends one sample alert).
- **Full local run:** `python main.py` (uses your local `session_state.json`).
- **Cloud run on demand:** GitHub → Actions → Riipen poller → Run workflow.

## How it works (quick reference)

- `login_setup.py` — one-time/occasional manual login (Playwright) → session.
- `http_scraper.py` — fetches listing + detail pages over plain HTTP (no
  browser) using the session cookies.
- `filter.py` — keeps target categories AND ≥ 60 hours.
- `storage.py` — tracks already-alerted project IDs in `seen_projects.json`.
- `notifier.py` — sends the email via SMTP.
- `main.py` — orchestrates a single run (what the cron triggers).
