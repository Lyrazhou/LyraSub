from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TranslationRecord:
    """One subtitle submitted to an LLM without losing its project identity."""

    entry_id: str
    text: str


def is_retryable_translation_response_error(message: str) -> bool:
    """Return whether shrinking a translation batch can plausibly repair a response."""
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in (
            "unterminated string",
            "unterminated",
            "expecting ',' delimiter",
            "expecting property name",
            "翻译条目缺少唯一 id 或文本",
            "没有返回 translations 数组",
            "翻译条目与输入不一致",
        )
    )


def chunk_translation_records(
    records: list[TranslationRecord], max_chars: int
) -> list[list[TranslationRecord]]:
    """Split only between subtitles, never inside one subtitle's text.

    A record longer than ``max_chars`` is emitted on its own; callers can show that
    condition to the user while still preserving request/response identity.
    """
    if max_chars < 1_000:
        raise ValueError("翻译每批字符数不能小于 1000。")
    batches: list[list[TranslationRecord]] = []
    current: list[TranslationRecord] = []
    current_chars = 0
    for record in records:
        text = record.text.strip()
        if not record.entry_id or not text:
            raise ValueError("翻译条目必须包含稳定 ID 和非空文本。")
        size = len(text)
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(TranslationRecord(record.entry_id, text))
        current_chars += size
        if current_chars >= max_chars:
            batches.append(current)
            current = []
            current_chars = 0
    if current:
        batches.append(current)
    return batches
