from datetime import date

import pytest

from lyrasub.versioning import alphabetic_revision, dated_version


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, ""),
        (1, "A"),
        (26, "Z"),
        (27, "AA"),
        (28, "AB"),
        (52, "AZ"),
        (53, "BA"),
    ],
)
def test_alphabetic_revision(index: int, expected: str) -> None:
    assert alphabetic_revision(index) == expected


def test_dated_version_uses_optional_same_day_suffix() -> None:
    build_date = date(2026, 8, 20)
    assert dated_version(build_date) == "v260820"
    assert dated_version(build_date, revision=3) == "v260820C"


def test_negative_revision_is_rejected() -> None:
    with pytest.raises(ValueError):
        alphabetic_revision(-1)
