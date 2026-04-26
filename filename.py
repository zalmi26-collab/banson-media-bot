"""Build target filenames for uploaded media."""
from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath

from parser import Destination


def build_filename(
    dest: Destination,
    on_date: date,
    original_name: str | None,
    existing_names: set[str],
) -> str:
    """Return a unique filename for this destination + date.

    Pattern: ``{plot}-{building}-{apt}-{stage} - {dd.mm.yy}{ext}``
    Disambiguates by appending ``(2)``, ``(3)``, ... when ``existing_names`` already
    has the candidate. Comparison is case-insensitive (Drive matches that way).
    """
    ext = _extract_ext(original_name)
    date_str = on_date.strftime("%d.%m.%y")
    base = f"{dest.plot}-{dest.building}-{dest.apartment}-{dest.stage} - {date_str}"

    existing_lower = {n.lower() for n in existing_names}
    candidate = f"{base}{ext}"
    if candidate.lower() not in existing_lower:
        return candidate

    i = 2
    while True:
        candidate = f"{base} ({i}){ext}"
        if candidate.lower() not in existing_lower:
            return candidate
        i += 1


def _extract_ext(original: str | None) -> str:
    """Lower-cased extension with leading dot, or empty if missing/unknown."""
    if not original:
        return ""
    suffix = PurePosixPath(original).suffix.lower()
    if not suffix or len(suffix) > 6:
        return ""
    return suffix
