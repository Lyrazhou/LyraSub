"""Project persistence with atomic writes and an explicit format boundary."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .constants import PROJECT_EXTENSION
from .models import SubtitleProject

CURRENT_SCHEMA_VERSION = 6
AUTOSAVE_SUFFIX = ".autosave.lyrasub"


class ProjectFormatError(ValueError):
    """Raised when a selected project cannot be safely interpreted."""


def normalize_project_path(path: str | Path) -> Path:
    target = Path(path)
    return (
        target
        if target.suffix.lower() == PROJECT_EXTENSION
        else target.with_suffix(PROJECT_EXTENSION)
    )


def autosave_path(path: str | Path) -> Path:
    """Return a sibling recovery file without replacing the user's saved project."""
    project_path = normalize_project_path(path)
    return project_path.with_name(f".{project_path.stem}{AUTOSAVE_SUFFIX}")


def save_project(project: SubtitleProject, path: str | Path) -> Path:
    """Atomically persist a project without storing credentials.

    The caller is responsible for choosing a user-writable location. ``os.replace``
    avoids a partially written project after a crash or unexpected shutdown.
    """
    destination = normalize_project_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    project.schema_version = CURRENT_SCHEMA_VERSION
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp", dir=destination.parent, text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return destination


def save_autosave(project: SubtitleProject, path: str | Path) -> Path:
    """Atomically write a recovery snapshot next to an already named project."""
    return save_project(project, autosave_path(path))


def remove_autosave(path: str | Path) -> None:
    autosave_path(path).unlink(missing_ok=True)


def load_project(path: str | Path) -> SubtitleProject:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProjectFormatError(f"无法读取项目文件：{source}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectFormatError("项目文件不是有效的 JSON 格式。") from exc
    if not isinstance(payload, dict):
        raise ProjectFormatError("项目文件根节点必须是对象。")
    if int(payload.get("schema_version", 0)) > CURRENT_SCHEMA_VERSION:
        raise ProjectFormatError("该项目由更高版本的弦字幕创建，当前版本不能安全打开。")
    try:
        return SubtitleProject.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise ProjectFormatError("项目数据字段不完整或格式不受支持。") from exc
