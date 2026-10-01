"""Domain model for projects, source-aligned anchors and subtitle entries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .constants import DEFAULT_MAX_CJK_CHARS, DEFAULT_MAX_LATIN_CHARS, DEFAULT_OFFSET_MS


class AnchorState(StrEnum):
    INHERITED = "inherited"
    MERGED = "merged"
    ESTIMATED = "estimated"
    UNANCHORED = "unanchored"


class ProcessingDevice(StrEnum):
    AUTO = "auto"
    NVIDIA_GPU = "cuda"
    CPU = "cpu"


class LanguageMode(StrEnum):
    AUTO = "auto"
    CHINESE = "zh"
    ENGLISH = "en"


class SubtitleLayoutMode(StrEnum):
    SINGLE_LINE = "single_line"
    TWO_LINES = "two_lines"


class SubtitleOutputMode(StrEnum):
    PRIMARY = "primary"
    TRANSLATION = "translation"
    BILINGUAL = "bilingual"


class BilingualOrder(StrEnum):
    PRIMARY_TOP = "primary_top"
    TRANSLATION_TOP = "translation_top"


class PreviewAudioSource(StrEnum):
    VIDEO = "video"
    RECOGNITION = "recognition"


@dataclass(slots=True)
class Anchor:
    """One smallest source-aligned audible unit.

    ``text`` is a visible source unit; punctuation can be retained but should not be
    used as the first audible anchor when composing an SRT entry.
    """

    id: str = field(default_factory=lambda: f"a_{uuid4().hex}")
    text: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    state: AnchorState = AnchorState.INHERITED
    source_segment_id: str | None = None
    confidence: float | None = None

    @property
    def has_timing(self) -> bool:
        return self.start_ms is not None and self.end_ms is not None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Anchor:
        return cls(**{**payload, "state": AnchorState(payload.get("state", "inherited"))})


@dataclass(slots=True)
class SubtitleEntry:
    """User-editable subtitle composed from zero or more source anchors."""

    id: str = field(default_factory=lambda: f"s_{uuid4().hex}")
    text: str = ""
    translation_text: str = ""
    anchor_ids: list[str] = field(default_factory=list)
    start_ms: int | None = None
    end_ms: int | None = None
    manual_start_ms: int | None = None
    manual_end_ms: int | None = None
    force_confirmed: bool = False
    review_confirmed: bool = False

    @property
    def effective_start_ms(self) -> int | None:
        return self.manual_start_ms if self.manual_start_ms is not None else self.start_ms

    @property
    def effective_end_ms(self) -> int | None:
        return self.manual_end_ms if self.manual_end_ms is not None else self.end_ms

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SubtitleEntry:
        return cls(**payload)


@dataclass(slots=True)
class ProjectSettings:
    language_mode: LanguageMode = LanguageMode.AUTO
    device: ProcessingDevice = ProcessingDevice.AUTO
    model_id: str = "small"
    max_cjk_chars: int = DEFAULT_MAX_CJK_CHARS
    max_latin_chars: int = DEFAULT_MAX_LATIN_CHARS
    offset_ms: int = DEFAULT_OFFSET_MS
    use_llm_cleanup: bool = False
    use_llm_segmentation: bool = False
    subtitle_layout: SubtitleLayoutMode = SubtitleLayoutMode.SINGLE_LINE
    normalize_simplified_chinese: bool = True
    subtitle_output_mode: SubtitleOutputMode = SubtitleOutputMode.PRIMARY
    bilingual_order: BilingualOrder = BilingualOrder.PRIMARY_TOP
    primary_language_label: str = "原文"
    translation_language_label: str = "译文"
    preview_audio_source: PreviewAudioSource = PreviewAudioSource.VIDEO

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["language_mode"] = self.language_mode.value
        payload["device"] = self.device.value
        payload["subtitle_layout"] = self.subtitle_layout.value
        payload["subtitle_output_mode"] = self.subtitle_output_mode.value
        payload["bilingual_order"] = self.bilingual_order.value
        payload["preview_audio_source"] = self.preview_audio_source.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProjectSettings:
        return cls(
            **{
                **payload,
                "language_mode": LanguageMode(payload.get("language_mode", "auto")),
                "device": ProcessingDevice(payload.get("device", "auto")),
                "subtitle_layout": SubtitleLayoutMode(
                    payload.get("subtitle_layout", SubtitleLayoutMode.SINGLE_LINE.value)
                ),
                "subtitle_output_mode": SubtitleOutputMode(
                    payload.get("subtitle_output_mode", SubtitleOutputMode.PRIMARY.value)
                ),
                "bilingual_order": BilingualOrder(
                    payload.get("bilingual_order", BilingualOrder.PRIMARY_TOP.value)
                ),
                "primary_language_label": str(payload.get("primary_language_label", "原文")),
                "translation_language_label": str(
                    payload.get("translation_language_label", "译文")
                ),
                "preview_audio_source": PreviewAudioSource(
                    payload.get("preview_audio_source", PreviewAudioSource.VIDEO.value)
                ),
            }
        )


@dataclass(slots=True)
class ProjectDraft:
    """Unconfirmed editor state that can be restored without altering source anchors."""

    original_editor_text: str = ""
    cleaned_editor_text: str = ""
    table_rows: list[dict[str, str]] = field(default_factory=list)
    active_page: int = 0
    captured_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_editor_text": self.original_editor_text,
            "cleaned_editor_text": self.cleaned_editor_text,
            "table_rows": self.table_rows,
            "active_page": self.active_page,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProjectDraft:
        rows = payload.get("table_rows", [])
        safe_rows = [
            {
                "text": str(row.get("text", "")),
                "start": str(row.get("start", "")),
                "end": str(row.get("end", "")),
            }
            for row in rows
            if isinstance(row, dict)
        ]
        return cls(
            original_editor_text=str(payload.get("original_editor_text", "")),
            cleaned_editor_text=str(payload.get("cleaned_editor_text", "")),
            table_rows=safe_rows,
            active_page=max(0, int(payload.get("active_page", 0))),
            captured_at=str(payload.get("captured_at", "")),
        )


@dataclass(slots=True)
class SourceAudio:
    # ``path`` is the recognition source.  ``preview_video_path`` is optional
    # high-bitrate picture media used only during visual subtitle review.
    path: str = ""
    preview_video_path: str = ""
    duration_ms: int | None = None
    detected_language: str | None = None
    detected_language_probability: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SourceAudio:
        return cls(**payload)


@dataclass(slots=True)
class SubtitleProject:
    """The complete persistent project; it intentionally never contains an API key."""

    schema_version: int = 6
    app_version: str = "v260820"
    title: str = "未命名项目"
    source: SourceAudio = field(default_factory=SourceAudio)
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    original_text: str = ""
    cleaned_text: str = ""
    anchors: list[Anchor] = field(default_factory=list)
    entries: list[SubtitleEntry] = field(default_factory=list)
    workflow_notes: list[str] = field(default_factory=list)
    future_waveform: dict[str, Any] = field(
        default_factory=lambda: {"manual_overrides": [], "waveform_cache": None}
    )
    draft: ProjectDraft = field(default_factory=ProjectDraft)

    @property
    def source_path(self) -> Path | None:
        return Path(self.source.path) if self.source.path else None

    def anchor_index(self) -> dict[str, Anchor]:
        return {anchor.id: anchor for anchor in self.anchors}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "app_version": self.app_version,
            "title": self.title,
            "source": self.source.to_dict(),
            "settings": self.settings.to_dict(),
            "original_text": self.original_text,
            "cleaned_text": self.cleaned_text,
            "anchors": [anchor.to_dict() for anchor in self.anchors],
            "entries": [entry.to_dict() for entry in self.entries],
            "workflow_notes": self.workflow_notes,
            "future_waveform": self.future_waveform,
            "draft": self.draft.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SubtitleProject:
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            app_version=str(payload.get("app_version", "v260820")),
            title=str(payload.get("title", "未命名项目")),
            source=SourceAudio.from_dict(payload.get("source", {})),
            settings=ProjectSettings.from_dict(payload.get("settings", {})),
            original_text=str(payload.get("original_text", "")),
            cleaned_text=str(payload.get("cleaned_text", "")),
            anchors=[Anchor.from_dict(item) for item in payload.get("anchors", [])],
            entries=[SubtitleEntry.from_dict(item) for item in payload.get("entries", [])],
            workflow_notes=list(payload.get("workflow_notes", [])),
            future_waveform=dict(payload.get("future_waveform", {"manual_overrides": []})),
            draft=ProjectDraft.from_dict(dict(payload.get("draft", {}))),
        )
