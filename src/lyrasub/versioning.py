"""Date-based release version helpers with same-day alphabetic revisions."""

from __future__ import annotations

from datetime import date


def alphabetic_revision(index: int) -> str:
    """Convert a one-based revision index to A..Z, AA..AZ, BA... notation.

    ``0`` represents the first release of a day and deliberately has no suffix.
    """
    if index < 0:
        raise ValueError("同日修订序号不能为负数。")
    if index == 0:
        return ""
    letters: list[str] = []
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def dated_version(build_date: date | None = None, revision: int = 0) -> str:
    """Return ``vYYMMDD`` plus an optional one-based alphabetic same-day revision."""
    current = build_date or date.today()
    return f"{current.strftime('v%y%m%d')}{alphabetic_revision(revision)}"
