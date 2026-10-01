"""Non-destructive global-timeline merging for chapter-based subtitle work."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from .models import SubtitleProject
from .project_io import load_project
from .srt_export import format_srt_time, shifted_bounds

MERGE_SCHEMA_VERSION = 1
MERGE_EXTENSION = ".lyrasubmerge"
_SRT_TIME_RE = re.compile(
    r"^(?P<hours>\d{1,3}):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)[,.](?P<milliseconds>\d{1,3})$"
)


class SegmentSourceKind(StrEnum):
    SRT = "srt"
    LYRA_PROJECT = "lyrasub_project"


class SegmentPlacementMode(StrEnum):
    ABSOLUTE = "absolute"
    CONTINUOUS = "continuous"


@dataclass(slots=True)
class MergeEntry:
    text: str
    local_start_ms: int
    local_end_ms: int
    source_sequence: int = 0
    id: str = field(default_factory=lambda: f"me_{uuid4().hex}")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> MergeEntry:
        return cls(
            id=str(payload.get("id", f"me_{uuid4().hex}")),
            text=str(payload.get("text", "")),
            local_start_ms=int(payload.get("local_start_ms", 0)),
            local_end_ms=int(payload.get("local_end_ms", 0)),
            source_sequence=int(payload.get("source_sequence", 0)),
        )


@dataclass(slots=True)
class MergeSegment:
    title: str
    source_kind: SegmentSourceKind
    source_path: str
    audio_path: str = ""
    entries: list[MergeEntry] = field(default_factory=list)
    origin_ms: int | None = None
    placement_mode: SegmentPlacementMode = SegmentPlacementMode.ABSOLUTE
    gap_before_ms: int = 0
    audio_duration_ms: int | None = None
    id: str = field(default_factory=lambda: f"ms_{uuid4().hex}")

    @property
    def subtitle_end_ms(self) -> int | None:
        return max((entry.local_end_ms for entry in self.entries), default=None)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "source_kind": self.source_kind.value,
            "source_path": self.source_path,
            "audio_path": self.audio_path,
            "entries": [entry.to_dict() for entry in self.entries],
            "origin_ms": self.origin_ms,
            "placement_mode": self.placement_mode.value,
            "gap_before_ms": self.gap_before_ms,
            "audio_duration_ms": self.audio_duration_ms,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> MergeSegment:
        source_kind = SegmentSourceKind(str(payload.get("source_kind", SegmentSourceKind.SRT)))
        placement = SegmentPlacementMode(
            str(payload.get("placement_mode", SegmentPlacementMode.ABSOLUTE))
        )
        raw_entries = payload.get("entries", [])
        entries = [MergeEntry.from_dict(item) for item in raw_entries if isinstance(item, dict)]
        origin = payload.get("origin_ms")
        duration = payload.get("audio_duration_ms")
        return cls(
            id=str(payload.get("id", f"ms_{uuid4().hex}")),
            title=str(payload.get("title", "未命名片段")),
            source_kind=source_kind,
            source_path=str(payload.get("source_path", "")),
            audio_path=str(payload.get("audio_path", "")),
            entries=entries,
            origin_ms=int(origin) if origin is not None else None,
            placement_mode=placement,
            gap_before_ms=max(0, int(payload.get("gap_before_ms", 0))),
            audio_duration_ms=int(duration) if duration is not None else None,
        )


@dataclass(slots=True)
class MergeProject:
    title: str = "未命名分段合并"
    segments: list[MergeSegment] = field(default_factory=list)
    delivery_offset_ms: int = 0
    schema_version: int = MERGE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": MERGE_SCHEMA_VERSION,
            "title": self.title,
            "delivery_offset_ms": self.delivery_offset_ms,
            "segments": [segment.to_dict() for segment in self.segments],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> MergeProject:
        schema = int(payload.get("schema_version", 0))
        if schema > MERGE_SCHEMA_VERSION:
            raise ValueError("该分段合并工程由更高版本创建，当前版本不能安全打开。")
        raw_segments = payload.get("segments", [])
        return cls(
            title=str(payload.get("title", "未命名分段合并")),
            delivery_offset_ms=int(payload.get("delivery_offset_ms", 0)),
            schema_version=schema or MERGE_SCHEMA_VERSION,
            segments=[
                MergeSegment.from_dict(item) for item in raw_segments if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class MergeIssue:
    severity: str
    message: str
    segment_id: str | None = None
    entry_id: str | None = None


@dataclass(slots=True)
class GlobalMergeEntry:
    segment_id: str
    segment_title: str
    source_sequence: int
    text: str
    local_start_ms: int
    local_end_ms: int
    global_start_ms: int
    global_end_ms: int
    entry_id: str


def parse_srt_time(value: str) -> int:
    match = _SRT_TIME_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"无效的 SRT 时间码：{value!r}")
    milliseconds = int(match.group("milliseconds").ljust(3, "0"))
    return (
        int(match.group("hours")) * 3_600_000
        + int(match.group("minutes")) * 60_000
        + int(match.group("seconds")) * 1_000
        + milliseconds
    )


def parse_srt_text(text: str) -> list[MergeEntry]:
    """Parse standard SRT while retaining multiline subtitle text."""
    entries: list[MergeEntry] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff"))
    for block_number, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.split("\n")]
        if not any(line.strip() for line in lines):
            continue
        time_line_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_line_index is None:
            raise ValueError(f"SRT 第 {block_number} 个字幕块缺少时间码行。")
        start_raw, marker, end_raw = lines[time_line_index].partition("-->")
        if not marker:
            raise ValueError(f"SRT 第 {block_number} 个字幕块时间码无效。")
        subtitle_text = "\n".join(lines[time_line_index + 1 :]).strip()
        if not subtitle_text:
            raise ValueError(f"SRT 第 {block_number} 个字幕块缺少字幕文字。")
        start = parse_srt_time(start_raw)
        end = parse_srt_time(end_raw)
        if end <= start:
            raise ValueError(f"SRT 第 {block_number} 个字幕块结束时间必须晚于开始时间。")
        sequence = block_number
        if time_line_index and lines[0].strip().isdigit():
            sequence = int(lines[0].strip())
        entries.append(
            MergeEntry(
                text=subtitle_text,
                local_start_ms=start,
                local_end_ms=end,
                source_sequence=sequence,
            )
        )
    return entries


def read_audio_duration_ms(path: str | Path) -> int | None:
    """Read media duration without decoding the full audio stream."""
    source = Path(path)
    if not source.is_file():
        return None
    try:
        import av  # type: ignore[import-not-found]

        with av.open(str(source)) as container:
            if container.duration is not None:
                return max(0, round(float(container.duration) / float(av.time_base) * 1_000))
    except Exception:
        return None
    return None


def segment_from_srt(path: str | Path, audio_path: str | Path | None = None) -> MergeSegment:
    source = Path(path)
    entries = parse_srt_text(source.read_text(encoding="utf-8-sig"))
    audio = str(audio_path or "")
    return MergeSegment(
        title=source.stem,
        source_kind=SegmentSourceKind.SRT,
        source_path=str(source),
        audio_path=audio,
        entries=entries,
        audio_duration_ms=read_audio_duration_ms(audio) if audio else None,
    )


def _project_entries(project: SubtitleProject) -> list[MergeEntry]:
    entries: list[MergeEntry] = []
    for sequence, entry in enumerate(project.entries, start=1):
        bounds = shifted_bounds(entry, project.settings.offset_ms)
        if bounds is None or not entry.text.strip():
            continue
        start, end = bounds
        if end <= start:
            continue
        entries.append(
            MergeEntry(
                text=entry.text.strip(),
                local_start_ms=start,
                local_end_ms=end,
                source_sequence=sequence,
            )
        )
    return entries


def segment_from_lyrasub(path: str | Path, audio_path: str | Path | None = None) -> MergeSegment:
    source = Path(path)
    project = load_project(source)
    audio = str(audio_path or project.source.path or "")
    return MergeSegment(
        title=project.title or source.stem,
        source_kind=SegmentSourceKind.LYRA_PROJECT,
        source_path=str(source),
        audio_path=audio,
        entries=_project_entries(project),
        audio_duration_ms=read_audio_duration_ms(audio) if audio else project.source.duration_ms,
    )


def apply_continuous_origins(project: MergeProject) -> list[MergeIssue]:
    """Fill only continuous segments from the preceding confirmed segment duration."""
    issues: list[MergeIssue] = []
    for index, segment in enumerate(project.segments):
        if segment.placement_mode != SegmentPlacementMode.CONTINUOUS:
            continue
        if index == 0:
            if segment.origin_ms is None:
                segment.origin_ms = 0
            continue
        previous = project.segments[index - 1]
        if previous.origin_ms is None:
            issues.append(MergeIssue("error", "上一片段尚未确认全片起点。", segment.id))
            continue
        if previous.audio_duration_ms is None:
            issues.append(
                MergeIssue(
                    "error",
                    "上一片段没有可用 MP3 时长，无法按连续模式计算当前片段起点；请填写绝对全片起点。",
                    segment.id,
                )
            )
            continue
        segment.origin_ms = previous.origin_ms + previous.audio_duration_ms + segment.gap_before_ms
    return issues


def build_global_entries(project: MergeProject) -> list[GlobalMergeEntry]:
    entries: list[GlobalMergeEntry] = []
    for segment in project.segments:
        if segment.origin_ms is None:
            continue
        for entry in segment.entries:
            entries.append(
                GlobalMergeEntry(
                    segment_id=segment.id,
                    segment_title=segment.title,
                    source_sequence=entry.source_sequence,
                    text=entry.text,
                    local_start_ms=entry.local_start_ms,
                    local_end_ms=entry.local_end_ms,
                    global_start_ms=max(0, segment.origin_ms + entry.local_start_ms),
                    global_end_ms=max(0, segment.origin_ms + entry.local_end_ms),
                    entry_id=entry.id,
                )
            )
    return sorted(
        entries,
        key=lambda item: (
            item.global_start_ms,
            item.global_end_ms,
            item.segment_title,
            item.source_sequence,
        ),
    )


def validate_merge_project(project: MergeProject) -> list[MergeIssue]:
    issues: list[MergeIssue] = []
    previous_origin: int | None = None
    for segment in project.segments:
        if segment.origin_ms is None:
            issues.append(MergeIssue("error", "片段尚未确认全片起点。", segment.id))
            continue
        if segment.origin_ms < 0:
            issues.append(MergeIssue("error", "片段全片起点不能为负数。", segment.id))
        if previous_origin is not None and segment.origin_ms < previous_origin:
            issues.append(
                MergeIssue("error", "片段全片起点早于上一片段，顺序会发生倒置。", segment.id)
            )
        previous_origin = segment.origin_ms
        previous_local_end: int | None = None
        for entry in sorted(segment.entries, key=lambda item: item.local_start_ms):
            if not entry.text.strip():
                issues.append(MergeIssue("warning", "字幕文字为空。", segment.id, entry.id))
            if entry.local_end_ms <= entry.local_start_ms:
                issues.append(
                    MergeIssue("error", "本地字幕结束时间必须晚于开始时间。", segment.id, entry.id)
                )
            if previous_local_end is not None and entry.local_start_ms < previous_local_end:
                issues.append(
                    MergeIssue("warning", "片段内部字幕发生时间重叠。", segment.id, entry.id)
                )
            previous_local_end = max(previous_local_end or 0, entry.local_end_ms)

    previous: GlobalMergeEntry | None = None
    for entry in build_global_entries(project):
        if (
            previous
            and entry.global_start_ms < previous.global_end_ms
            and entry.segment_id != previous.segment_id
        ):
            issues.append(
                MergeIssue(
                    "warning",
                    f"与上一片段“{previous.segment_title}”的字幕发生跨段重叠。",
                    entry.segment_id,
                    entry.entry_id,
                )
            )
        if previous is None or entry.global_end_ms > previous.global_end_ms:
            previous = entry
    return issues


def render_merged_srt(project: MergeProject) -> str:
    """Render globally sorted entries with one optional final delivery offset."""
    blocks: list[str] = []
    for sequence, entry in enumerate(build_global_entries(project), start=1):
        start = max(0, entry.global_start_ms + project.delivery_offset_ms)
        end = max(start, entry.global_end_ms + project.delivery_offset_ms)
        if end <= start or not entry.text.strip():
            continue
        blocks.append(
            f"{sequence}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{entry.text.strip()}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def merge_project_path(path: str | Path) -> Path:
    target = Path(path)
    return (
        target if target.suffix.lower() == MERGE_EXTENSION else target.with_suffix(MERGE_EXTENSION)
    )


def save_merge_project(project: MergeProject, path: str | Path) -> Path:
    target = merge_project_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=".tmp", dir=target.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def load_merge_project(path: str | Path) -> MergeProject:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取分段合并工程：{source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("分段合并工程不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise ValueError("分段合并工程根节点必须是对象。")
    return MergeProject.from_dict(payload)
