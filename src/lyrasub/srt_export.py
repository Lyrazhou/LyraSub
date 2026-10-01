"""SRT rendering and non-destructive export validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import BilingualOrder, SubtitleEntry, SubtitleOutputMode


@dataclass(slots=True)
class ExportIssue:
    entry_id: str
    severity: str
    message: str


def format_srt_time(milliseconds: int) -> str:
    value = max(0, int(milliseconds))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def shifted_bounds(entry: SubtitleEntry, offset_ms: int) -> tuple[int, int] | None:
    start = entry.effective_start_ms
    end = entry.effective_end_ms
    if start is None or end is None:
        return None
    start = max(0, start + offset_ms)
    end = max(start, end + offset_ms)
    return start, end


def subtitle_output_text(
    entry: SubtitleEntry,
    output_mode: SubtitleOutputMode = SubtitleOutputMode.PRIMARY,
    bilingual_order: BilingualOrder = BilingualOrder.PRIMARY_TOP,
) -> str:
    """Return SRT-safe output text without changing the project entry."""
    primary = " ".join(part.strip() for part in entry.text.splitlines() if part.strip())
    translation = " ".join(
        part.strip() for part in entry.translation_text.splitlines() if part.strip()
    )
    if output_mode == SubtitleOutputMode.TRANSLATION:
        return translation or primary
    if output_mode == SubtitleOutputMode.BILINGUAL:
        if not translation:
            return primary
        ordered = (primary, translation)
        if bilingual_order == BilingualOrder.TRANSLATION_TOP:
            ordered = (translation, primary)
        return "\n".join(part for part in ordered if part)
    return primary


def validate_entries(
    entries: list[SubtitleEntry],
    offset_ms: int = 0,
    output_mode: SubtitleOutputMode = SubtitleOutputMode.PRIMARY,
) -> list[ExportIssue]:
    issues: list[ExportIssue] = []
    previous_end: int | None = None
    for entry in entries:
        bounds = shifted_bounds(entry, offset_ms)
        if bounds is None:
            issues.append(ExportIssue(entry.id, "error", "缺少可导出的起止时间。"))
            continue
        start, end = bounds
        if not entry.text.strip():
            issues.append(ExportIssue(entry.id, "warning", "字幕文本为空。"))
        if output_mode != SubtitleOutputMode.PRIMARY and not entry.translation_text.strip():
            issues.append(ExportIssue(entry.id, "warning", "尚无译文；导出时将回退使用原文。"))
        if end <= start:
            issues.append(ExportIssue(entry.id, "warning", "字幕时长为零，建议复核。"))
        if previous_end is not None and start < previous_end:
            issues.append(ExportIssue(entry.id, "warning", "与上一条字幕发生时间重叠。"))
        previous_end = max(previous_end or 0, end)
    return issues


def render_srt(
    entries: list[SubtitleEntry],
    offset_ms: int = 0,
    output_mode: SubtitleOutputMode = SubtitleOutputMode.PRIMARY,
    bilingual_order: BilingualOrder = BilingualOrder.PRIMARY_TOP,
) -> str:
    """Render only valid, timed entries; validation is exposed to the UI separately."""
    blocks: list[str] = []
    sequence = 1
    for entry in entries:
        bounds = shifted_bounds(entry, offset_ms)
        text = subtitle_output_text(entry, output_mode, bilingual_order)
        if bounds is None or not text:
            continue
        start, end = bounds
        blocks.append(f"{sequence}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}")
        sequence += 1
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_srt(
    entries: list[SubtitleEntry],
    destination: str | Path,
    offset_ms: int = 0,
    output_mode: SubtitleOutputMode = SubtitleOutputMode.PRIMARY,
    bilingual_order: BilingualOrder = BilingualOrder.PRIMARY_TOP,
) -> Path:
    target = Path(destination)
    if target.suffix.lower() != ".srt":
        target = target.with_suffix(".srt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_srt(entries, offset_ms, output_mode, bilingual_order),
        encoding="utf-8",
        newline="\n",
    )
    return target
