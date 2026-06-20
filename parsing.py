"""Pure parsing helpers shared by the scrapers — no browser/network deps, so
both the HTTP poller and the Playwright login module can import them freely.
"""

import re
from typing import Optional
from urllib.parse import urlparse


def origin_of(url: str) -> str:
    """Return the scheme://host origin, e.g. https://futurepath.riipen.com."""
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


def parse_total_hours(text: str) -> Optional[float]:
    """Extract total project hours from a detail page's Payment section.

    Riipen formats it as e.g. "50 hrs. x C$23.34 per hour". We anchor on the
    "hrs. x" so we don't match "15 hrs. per invoice" or "Minimum payable: 4 hrs.".
    """
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*hrs?\.?\s*[x×]", text, re.IGNORECASE)
    return float(match.group(1)) if match else None


def project_id_from_url(url: str, fallback: str) -> str:
    """Derive a stable ID from a project URL (e.g. /matches/7zXpZozE -> 7zXpZozE).

    Riipen uses alphanumeric slugs, and listing URLs may carry a query string,
    so we match the slug after the resource segment off the path only.
    Prioritize /matches/ over /experiences/ since URLs can nest both.
    """
    if url:
        path = urlparse(url).path
        # Try /matches/ first (the actual project)
        match = re.search(r"/matches/([^/]+)", path)
        if match:
            return match.group(1)
        # Fall back to other resource types
        match = re.search(r"/(?:projects?|experiences?)/([^/]+)", path)
        if match:
            return match.group(1)
        segment = path.rstrip("/").split("/")[-1]
        if segment:
            return segment
    return fallback
