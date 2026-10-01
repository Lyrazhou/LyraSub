"""Application identity and immutable public metadata."""

from __future__ import annotations

APP_NAME_ZH = "弦字幕 Plus"
APP_NAME_EN = "LyraSub Plus"
APP_DISPLAY_NAME = f"{APP_NAME_ZH}（{APP_NAME_EN}）"
# Keep the established ID so Plus inherits the standard edition's local model,
# settings and installation ecosystem instead of forcing users to reconfigure.
APP_ID = "com.lyrastudio.lyrasub"

# The release process freezes this value into the build manifest.
# v260822A adds shared file-dialog directory memory and stable target-language selection.
VERSION = "v260822A"
VERSION_FORMAT = "vYYMMDD + optional A–Z / AA… same-day revision"

ABOUT_PRODUCER = "本工具由天琴工作室（Lyra Studio）出品 · 海舟（Lyrazhou）制作"
ABOUT_EMAIL = "Email: fantasyland@163.com"

SETUP_FILENAME = "LyraSub-Plus-setup.exe"
PATCH_FILENAME = "LyraSub-patch.exe"
PROJECT_EXTENSION = ".lyrasub"

DEFAULT_MAX_CJK_CHARS = 15
DEFAULT_MAX_LATIN_CHARS = 42
DEFAULT_OFFSET_MS = 0
