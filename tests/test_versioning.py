from datetime import date
from pathlib import Path

import pytest

from lyrasub.versioning import alphabetic_revision, dated_version


def test_nsis_installer_source_uses_utf8_bom_and_chinese_names() -> None:
    installer = Path(__file__).parents[1] / "installer" / "setup.nsi"
    content = installer.read_bytes()
    assert content.startswith(b"\xef\xbb\xbf")
    decoded = content.decode("utf-8-sig")
    assert "Unicode true" in decoded
    assert "弦字幕 Plus（LyraSub Plus）" in decoded
    assert 'Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"' in decoded
    assert 'CreateShortcut "$SMPROGRAMS\\LyraSub\\弦字幕 Plus（LyraSub Plus）.lnk"' in decoded
    assert 'CreateShortcut "$DESKTOP\\弦字幕 Plus（LyraSub Plus）.lnk"' in decoded
    assert '"DisplayName" "${PRODUCT_NAME} ${PRODUCT_VERSION}"' in decoded


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
