# The recurring poller needs no browser (Riipen pages are server-rendered and
# fetched over plain HTTP), so a slim Python image is enough — no Playwright.
# The session is created locally with login_setup.py and supplied via the
# /data volume (see README).
FROM python:3.12-slim

WORKDIR /app

COPY requirements-poller.txt .
RUN pip install --no-cache-dir -r requirements-poller.txt

COPY . .

# State (session + seen-projects) lives on a persistent volume mounted here.
ENV DATA_DIR=/data

# One run per container start; the cloud scheduler invokes this every 15 min.
CMD ["python", "main.py"]
