"""Filtering rules: keep only projects that match the desired categories and
meet the minimum-hours threshold.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# Categories we care about. Matching is case-insensitive and substring-based
# so minor wording differences on Riipen ("Web & Mobile Development", etc.)
# still match.
TARGET_CATEGORIES = [
    "software development",
    "website development",
    "cloud technologies",
    "artificial intelligence",
]

MIN_HOURS = 60


def _matches_category(haystack: str) -> bool:
    haystack = haystack.lower()
    return any(target in haystack for target in TARGET_CATEGORIES)


def _meets_hours(hours) -> bool:
    if hours is None:
        return False
    try:
        return float(hours) >= MIN_HOURS
    except (TypeError, ValueError):
        logger.debug("Unparseable hours value: %r", hours)
        return False


def matches_filters(project: Dict) -> bool:
    """True if a single project passes both the category and hours rules.

    Category matching looks at both the listing card's tags and the richer
    skills text pulled from the project detail page (``skills_text``), since
    Riipen's high-level category may surface in either place.
    """
    tags = project.get("categories") or []
    haystack = " ".join(tags) + " " + (project.get("skills_text") or "")
    if not _matches_category(haystack):
        return False
    if not _meets_hours(project.get("hours")):
        return False
    return True


def filter_projects(projects: List[Dict]) -> List[Dict]:
    """Return only the projects matching ANY target category AND >= MIN_HOURS."""
    kept = [p for p in projects if matches_filters(p)]
    logger.info(
        "Filter: %d of %d projects match (categories=%s, min_hours=%d).",
        len(kept),
        len(projects),
        TARGET_CATEGORIES,
        MIN_HOURS,
    )
    return kept
