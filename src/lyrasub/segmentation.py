"""Deterministic local subtitle segmentation used when LLM assistance is unavailable."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .anchor_mapping import is_cjk

_SENTENCE_BREAKS = set("。！？!?；;")
_CLAUSE_BREAKS = set("，、,:：—-…")
_TOKEN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]|[A-Za-z0-9]+(?:['’._:/-][A-Za-z0-9]+)*|[^\s]"
)


@dataclass(slots=True)
class SegmentationSuggestion:
    entries: list[str]
    notes: list[str]


def visual_length(text: str) -> int:
    """Approximate the agreed line target without splitting normal English words."""
    total = 0
    for char in text:
        if char.isspace():
            continue
        total += 1 if is_cjk(char) else 1
    return total


def _is_cjk_dominant(tokens: list[str]) -> bool:
    visible = "".join(tokens)
    return sum(1 for char in visible if is_cjk(char)) >= max(1, len(visible.replace(" ", "")) // 2)


def _join_tokens(tokens: list[str]) -> str:
    output = ""
    for token in tokens:
        if not output:
            output = token
            continue
        prior = output[-1]
        if prior.isascii() and prior.isalnum() and token[0].isascii() and token[0].isalnum():
            output += f" {token}"
        else:
            output += token
    return output


def _split_two_lines(tokens: list[str], target: int) -> str:
    text = _join_tokens(tokens)
    if visual_length(text) <= target:
        return text
    candidates: list[tuple[int, int]] = []
    for index, token in enumerate(tokens[:-1], start=1):
        left = _join_tokens(tokens[:index])
        right = _join_tokens(tokens[index:])
        if visual_length(left) <= target + 2 and visual_length(right) <= target + 2:
            score = abs(visual_length(left) - visual_length(right))
            if token in _CLAUSE_BREAKS:
                score -= 3
            candidates.append((score, index))
    if not candidates:
        return text
    _, index = min(candidates)
    return f"{_join_tokens(tokens[:index])}\n{_join_tokens(tokens[index:])}"


def segment_locally(text: str, max_cjk_chars: int, max_latin_chars: int) -> SegmentationSuggestion:
    """Offline fallback: prefer complete sentences and avoid filling every line to target."""
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return SegmentationSuggestion([], [])
    target = max_cjk_chars if _is_cjk_dominant(tokens) else max_latin_chars
    entries: list[str] = []
    current: list[str] = []
    notes: list[str] = []

    for token in tokens:
        candidate = [*current, token]
        candidate_text = _join_tokens(candidate)
        if current and visual_length(candidate_text) > target * 2 + 4:
            entries.append(_split_two_lines(current, target))
            current = [token]
            continue
        current = candidate
        if token in _SENTENCE_BREAKS:
            # A short complete sentence is more natural than one padded toward the limit.
            entries.append(_split_two_lines(current, target))
            current = []
        elif token in _CLAUSE_BREAKS and visual_length(candidate_text) > target + 4:
            # Commas are only a fallback break after the subtitle has clearly exceeded one line.
            entries.append(_split_two_lines(current, target))
            current = []
    if current:
        entries.append(_split_two_lines(current, target))

    notes.append(
        "当前为离线兜底切分；如需按复杂语义、语气和自然意群编排，建议使用 LLM 自然语义切分。"
    )
    for entry in entries:
        if any(visual_length(line) > target + 2 for line in entry.splitlines()):
            notes.append(f"“{entry.replace(chr(10), ' / ')}”因语义或长词略超目标行长。")
    return SegmentationSuggestion(entries, notes)
