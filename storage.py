"""Persistence for the set of project IDs we have already alerted on.

Stored as a small JSON file on disk so state survives between poller runs.
"""

import json
import logging
import os
from typing import Iterable, Set

logger = logging.getLogger(__name__)

# State lives next to the code by default, but DATA_DIR lets a cloud host point
# it at a persistent volume (e.g. /data) so it survives container restarts.
DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(__file__)
DEFAULT_PATH = os.path.join(DATA_DIR, "seen_projects.json")


def load_seen(path: str = DEFAULT_PATH) -> Set[str]:
    """Return the set of previously-seen project IDs.

    A missing or unreadable file is treated as "nothing seen yet" so the
    first run (or a corrupted file) never crashes the poller.
    """
    if not os.path.exists(path):
        logger.info("No seen-projects file at %s; starting fresh.", path)
        return set()

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s (%s); starting fresh.", path, exc)
        return set()

    # Accept either a bare list or {"seen": [...]} for forward compatibility.
    if isinstance(data, dict):
        data = data.get("seen", [])
    if not isinstance(data, list):
        logger.warning("Unexpected format in %s; starting fresh.", path)
        return set()

    return {str(pid) for pid in data}


def save_seen(seen: Iterable[str], path: str = DEFAULT_PATH) -> None:
    """Persist the set of seen project IDs.

    Writes to a temp file then atomically replaces the target so an
    interrupted run cannot leave a half-written, corrupt file.
    """
    seen_list = sorted({str(pid) for pid in seen})
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump({"seen": seen_list}, fh, indent=2)
        os.replace(tmp_path, path)
        logger.info("Saved %d seen project IDs to %s.", len(seen_list), path)
    except OSError as exc:
        logger.error("Failed to write %s: %s", path, exc)
        raise


def add_seen(project_ids: Iterable[str], path: str = DEFAULT_PATH) -> Set[str]:
    """Merge ``project_ids`` into the stored set and persist immediately.

    Returns the updated set. Used right after alerting so a crash mid-run
    never re-alerts on a project we already emailed about.
    """
    seen = load_seen(path)
    seen.update(str(pid) for pid in project_ids)
    save_seen(seen, path)
    return seen
