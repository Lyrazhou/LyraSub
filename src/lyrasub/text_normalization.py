"""Non-destructive Chinese script normalization for generated text."""

from __future__ import annotations

from opencc import OpenCC

_T2S = OpenCC("t2s")


def to_simplified_chinese(text: str) -> str:
    """Convert traditional Chinese characters to simplified Chinese.

    The converter leaves Latin text, numerals, punctuation, timecodes and already
    simplified characters unchanged. It is intentionally invoked only for
    generated/LLM text; manual table edits remain exactly as the user typed them.
    """
    return _T2S.convert(text) if text else text
