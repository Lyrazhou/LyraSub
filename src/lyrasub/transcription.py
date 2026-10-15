"""Local audio/video transcription with word/character anchor creation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from uuid import uuid4

from .anchor_mapping import is_audible, is_cjk
from .model_manager import ModelManager
from .models import Anchor, AnchorState, LanguageMode, ProcessingDevice
from .tasks import TaskCancelledError

ProgressCallback = Callable[[str, int | None], None]
SUPPORTED_MEDIA_SUFFIXES = frozenset({".mp3", ".mp4", ".wav"})


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    anchors: list[Anchor]
    language: str
    language_probability: float | None
    duration_ms: int | None
    execution_device: str
    fallback_note: str | None


def _milliseconds(seconds: float | None) -> int | None:
    return None if seconds is None else max(0, round(seconds * 1000))


def _source_duration_ms(path: Path) -> int | None:
    """Read container duration without decoding all audio; unavailable metadata is tolerated."""
    try:
        import av  # type: ignore[import-not-found]

        with av.open(str(path)) as container:
            if container.duration is not None:
                return max(0, round(float(container.duration) / float(av.time_base) * 1000))
    except Exception:
        return None
    return None


def _format_elapsed(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _split_timed_unit(
    text: str,
    start_ms: int | None,
    end_ms: int | None,
    source_segment_id: str,
    probability: float | None,
) -> list[Anchor]:
    """Create character anchors for CJK tokens and word anchors elsewhere.

    A CJK token sometimes arrives as multiple characters from an ASR backend. Its
    duration is divided among audible characters; punctuation stays visible but has
    no timing so it cannot become an artificial subtitle start time.
    """
    compact = text.strip()
    if not compact:
        return []
    use_characters = any(is_cjk(character) for character in compact)
    units = list(compact) if use_characters else [compact]
    audible_indexes = [index for index, unit in enumerate(units) if is_audible(unit)]
    anchors: list[Anchor] = []
    span = (
        max(0, (end_ms or 0) - (start_ms or 0))
        if start_ms is not None and end_ms is not None
        else 0
    )
    audible_order = {index: order for order, index in enumerate(audible_indexes)}
    for index, unit in enumerate(units):
        if index not in audible_order or start_ms is None or end_ms is None:
            anchors.append(
                Anchor(
                    text=unit,
                    state=AnchorState.INHERITED,
                    source_segment_id=source_segment_id,
                    confidence=probability,
                )
            )
            continue
        order = audible_order[index]
        count = len(audible_indexes)
        unit_start = start_ms + round(span * order / count)
        unit_end = start_ms + round(span * (order + 1) / count)
        anchors.append(
            Anchor(
                text=unit,
                start_ms=unit_start,
                end_ms=max(unit_start, unit_end),
                state=AnchorState.INHERITED,
                source_segment_id=source_segment_id,
                confidence=probability,
            )
        )
    return anchors


def _join_source_text(anchors: list[Anchor]) -> str:
    output = ""
    for anchor in anchors:
        if not output:
            output = anchor.text
        elif (
            output[-1].isascii()
            and output[-1].isalnum()
            and anchor.text[0].isascii()
            and anchor.text[0].isalnum()
        ):
            output += f" {anchor.text}"
        else:
            output += anchor.text
    return output.strip()


class LocalTranscriber:
    """Runs local faster-whisper in a background worker controlled by the UI."""

    def __init__(self, models: ModelManager | None = None) -> None:
        self.models = models or ModelManager()

    def transcribe(
        self,
        audio_path: str | Path,
        model_id: str,
        device: ProcessingDevice,
        language_mode: LanguageMode,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> TranscriptionResult:
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f"找不到音频文件：{path}")
        if path.suffix.lower() not in SUPPORTED_MEDIA_SUFFIXES:
            raise ValueError("仅支持导入 MP3、WAV 音频或带音轨的 MP4 视频文件。")

        def ensure_not_cancelled() -> None:
            if cancel_event and cancel_event.is_set():
                raise TaskCancelledError("识别已停止；不会写入未完成的转写结果。")

        def notify(message: str, value: int | None = None) -> None:
            if progress:
                progress(message, value)

        notify("正在读取媒体音轨时长……", 0)
        source_duration_ms = _source_duration_ms(path)
        ensure_not_cancelled()
        notify("正在载入本地识别模型……")
        model, execution_device, _compute_type, fallback_note = self.models.ensure_whisper_model(
            model_id, device, notify
        )
        language = None if language_mode == LanguageMode.AUTO else language_mode.value
        ensure_not_cancelled()
        if source_duration_ms:
            notify(
                f"正在识别音频并生成词级时间锚点：00:00 / {_format_elapsed(source_duration_ms)}", 0
            )
        else:
            notify("正在识别音频并生成词级时间锚点。")
        try:
            segments, info = model.transcribe(
                str(path),
                language=language,
                beam_size=5,
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            anchors: list[Anchor] = []
            last_end = 0
            for ordinal, segment in enumerate(segments, start=1):
                ensure_not_cancelled()
                segment_id = f"seg_{ordinal}_{uuid4().hex[:8]}"
                words = getattr(segment, "words", None) or []
                if words:
                    for word in words:
                        created = _split_timed_unit(
                            str(getattr(word, "word", "")),
                            _milliseconds(getattr(word, "start", None)),
                            _milliseconds(getattr(word, "end", None)),
                            segment_id,
                            getattr(word, "probability", None),
                        )
                        anchors.extend(created)
                else:
                    anchors.extend(
                        _split_timed_unit(
                            str(getattr(segment, "text", "")),
                            _milliseconds(getattr(segment, "start", None)),
                            _milliseconds(getattr(segment, "end", None)),
                            segment_id,
                            None,
                        )
                    )
                last_end = max(last_end, _milliseconds(getattr(segment, "end", None)) or 0)
                if source_duration_ms:
                    percent = min(98, round(last_end * 100 / source_duration_ms))
                    notify(
                        f"正在识别：{_format_elapsed(last_end)} / {_format_elapsed(source_duration_ms)} "
                        f"· 已生成 {ordinal} 个语音片段",
                        percent,
                    )
                else:
                    notify(f"已处理 {ordinal} 个语音片段。")
        except Exception as exc:
            raise RuntimeError(f"本地识别未能完成：{exc}") from exc

        ensure_not_cancelled()
        notify("正在整理词级时间锚点与识别文本……", 99)
        detected_language = str(getattr(info, "language", language or "auto"))
        probability = getattr(info, "language_probability", None)
        notify("识别完成，已保留可编辑时间锚点。", 100)
        return TranscriptionResult(
            text=_join_source_text(anchors),
            anchors=anchors,
            language=detected_language,
            language_probability=float(probability) if probability is not None else None,
            duration_ms=source_duration_ms or last_end or None,
            execution_device=execution_device,
            fallback_note=fallback_note,
        )
