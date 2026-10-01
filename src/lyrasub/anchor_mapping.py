"""Map editable text back to source anchors without destroying source timing."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import Anchor, AnchorState, SubtitleEntry

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’._:-][A-Za-z0-9]+)*|[^\s]")
_AUDIBLE_PUNCTUATION = set("，。！？；：、,.!?;:()（）[]【】{}\"'“”‘’—-…")


@dataclass(slots=True)
class TextUnit:
    text: str
    normalized: str
    audible: bool
    newline_before: bool = False


@dataclass(slots=True)
class MappedUnit:
    unit: TextUnit
    anchor: Anchor | None

    @property
    def state(self) -> AnchorState:
        return self.anchor.state if self.anchor else AnchorState.UNANCHORED


def is_cjk(character: str) -> bool:
    return bool(_CJK_RE.fullmatch(character))


def normalize_token(text: str) -> str:
    return text.casefold().strip()


def is_audible(text: str) -> bool:
    return bool(text.strip()) and not all(char in _AUDIBLE_PUNCTUATION for char in text)


def tokenize_text(text: str) -> list[TextUnit]:
    """Split text into display-safe editable units.

    CJK characters are intentionally kept one by one. Space-delimited languages
    preserve words, numbers and compact identifiers as a single unit. Newline is
    carried as a flag on the following visible unit, allowing subtitle grouping.
    """
    units: list[TextUnit] = []
    newline_before = False
    buffer = ""

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        for token in _WORD_RE.findall(buffer):
            units.append(
                TextUnit(token, normalize_token(token), is_audible(token), newline_before=False)
            )
        buffer = ""

    for char in text:
        if char == "\n":
            flush_buffer()
            newline_before = True
            continue
        if char.isspace():
            flush_buffer()
            continue
        if is_cjk(char):
            flush_buffer()
            units.append(TextUnit(char, normalize_token(char), is_audible(char), newline_before))
            newline_before = False
            continue
        buffer += char
        if newline_before:
            # Preserve the newline before the first buffered token.
            flush_buffer()
            if units:
                units[-1].newline_before = True
            newline_before = False
    flush_buffer()
    if newline_before and units:
        units[-1].newline_before = True
    return units


def _anchor_tokens(anchors: Iterable[Anchor]) -> tuple[list[Anchor], list[str]]:
    visible = [anchor for anchor in anchors if anchor.text.strip()]
    return visible, [normalize_token(anchor.text) for anchor in visible]


def _estimate_anchor(
    text: str,
    left: Anchor | None,
    right: Anchor | None,
    position: int,
    total: int,
) -> Anchor:
    """Create a visible but reviewable fallback timing anchor.

    Estimates are only made in a gap bounded by known timings. At the project edge
    or after a large rewrite, the unit deliberately stays unanchored instead.
    """
    if not left or not right or left.end_ms is None or right.start_ms is None:
        return Anchor(text=text, state=AnchorState.UNANCHORED)
    span = max(0, right.start_ms - left.end_ms)
    start = left.end_ms + round(span * position / (total + 1))
    end = left.end_ms + round(span * (position + 1) / (total + 1))
    return Anchor(text=text, start_ms=start, end_ms=max(start, end), state=AnchorState.ESTIMATED)


def map_text_to_anchors(source_anchors: list[Anchor], edited_text: str) -> list[MappedUnit]:
    """Align edited text to source anchors using a non-destructive sequence match.

    Equal tokens inherit their original anchors. Inserted or replaced audible units
    get an estimated anchor only when both surrounding source anchors exist; all
    other unmatched units are explicitly unanchored. Punctuation remains visible
    but never creates a fake audible timing point.
    """
    source, source_tokens = _anchor_tokens(source_anchors)
    units = tokenize_text(edited_text)
    target_tokens = [unit.normalized for unit in units]
    matcher = SequenceMatcher(a=source_tokens, b=target_tokens, autojunk=False)
    target_map: dict[int, Anchor] = {}

    for tag, source_start, source_end, target_start, target_end in matcher.get_opcodes():
        if tag == "equal":
            for source_index, target_index in zip(
                range(source_start, source_end), range(target_start, target_end), strict=True
            ):
                target_map[target_index] = source[source_index]
            continue
        if tag == "delete":
            continue
        left = source[source_start - 1] if source_start > 0 else None
        right = source[source_end] if source_end < len(source) else None
        unmatched = [index for index in range(target_start, target_end) if units[index].audible]
        for order, target_index in enumerate(unmatched, start=1):
            target_map[target_index] = _estimate_anchor(
                units[target_index].text, left, right, order, len(unmatched)
            )

    return [MappedUnit(unit=unit, anchor=target_map.get(index)) for index, unit in enumerate(units)]


def _compose_text(units: list[MappedUnit]) -> str:
    output = ""
    for item in units:
        text = item.unit.text
        if not output:
            output = text
        elif _needs_space(output[-1], text[0]):
            output += f" {text}"
        else:
            output += text
    return output


def _needs_space(previous: str, current: str) -> bool:
    return previous.isascii() and previous.isalnum() and current.isascii() and current.isalnum()


def entries_from_edited_text(
    source_anchors: list[Anchor], edited_text: str
) -> tuple[list[Anchor], list[SubtitleEntry]]:
    """Produce entries while preserving all original anchors and adding only fallbacks.

    New fallback anchors are returned separately, so callers append them to a
    project without ever overwriting the source transcription layer.
    """
    mapped = map_text_to_anchors(source_anchors, edited_text)
    groups: list[list[MappedUnit]] = []
    current: list[MappedUnit] = []
    for item in mapped:
        if item.unit.newline_before and current:
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)

    generated: list[Anchor] = []
    entries: list[SubtitleEntry] = []
    original_ids = {anchor.id for anchor in source_anchors}
    for group in groups:
        text = _compose_text(group).strip()
        if not text:
            continue
        assigned = [item.anchor for item in group if item.anchor is not None]
        for anchor in assigned:
            if anchor.id not in original_ids and all(
                existing.id != anchor.id for existing in generated
            ):
                generated.append(anchor)
        timed = [
            anchor
            for anchor in assigned
            if anchor and anchor.has_timing and is_audible(anchor.text)
        ]
        entries.append(
            SubtitleEntry(
                text=text,
                anchor_ids=[anchor.id for anchor in assigned if anchor is not None],
                start_ms=timed[0].start_ms if timed else None,
                end_ms=timed[-1].end_ms if timed else None,
            )
        )
    return generated, entries


def entry_risk(entry: SubtitleEntry, anchor_index: dict[str, Anchor]) -> AnchorState:
    """Return the highest risk represented by an entry's anchors."""
    anchors = [
        anchor_index[anchor_id] for anchor_id in entry.anchor_ids if anchor_id in anchor_index
    ]
    if not anchors or entry.start_ms is None or entry.end_ms is None:
        return AnchorState.UNANCHORED
    states = {anchor.state for anchor in anchors}
    if AnchorState.UNANCHORED in states:
        return AnchorState.UNANCHORED
    if AnchorState.ESTIMATED in states:
        return AnchorState.ESTIMATED
    if AnchorState.MERGED in states:
        return AnchorState.MERGED
    return AnchorState.INHERITED


def entries_from_text_list(
    source_anchors: list[Anchor], entry_texts: list[str]
) -> tuple[list[Anchor], list[SubtitleEntry]]:
    """Map a list of subtitle entries while preserving intentional internal line breaks."""
    normalized_entries = [text.strip() for text in entry_texts if text.strip()]
    # A separator newline represents an entry boundary. Internal line breaks are
    # spaces during alignment but restored in the visible SubtitleEntry text.
    alignment_text = "\n".join(text.replace("\n", " ") for text in normalized_entries)
    mapped = map_text_to_anchors(source_anchors, alignment_text)
    groups: list[list[MappedUnit]] = []
    current: list[MappedUnit] = []
    for item in mapped:
        if item.unit.newline_before and current:
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)

    generated: list[Anchor] = []
    original_ids = {anchor.id for anchor in source_anchors}
    entries: list[SubtitleEntry] = []
    for index, group in enumerate(groups):
        if index >= len(normalized_entries):
            break
        assigned = [item.anchor for item in group if item.anchor is not None]
        for anchor in assigned:
            if anchor.id not in original_ids and all(
                existing.id != anchor.id for existing in generated
            ):
                generated.append(anchor)
        timed = [
            anchor
            for anchor in assigned
            if anchor and anchor.has_timing and is_audible(anchor.text)
        ]
        entries.append(
            SubtitleEntry(
                text=normalized_entries[index],
                anchor_ids=[anchor.id for anchor in assigned if anchor is not None],
                start_ms=timed[0].start_ms if timed else None,
                end_ms=timed[-1].end_ms if timed else None,
            )
        )
    return generated, entries
