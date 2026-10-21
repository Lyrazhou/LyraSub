import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from lyrasub.models import LanguageMode, ProcessingDevice
from lyrasub.transcription import SUPPORTED_MEDIA_SUFFIXES, LocalTranscriber


class StubModel:
    def __init__(self) -> None:
        self.source_path: str | None = None

    def transcribe(self, source_path: str, **_kwargs):
        self.source_path = source_path
        segment = SimpleNamespace(
            start=0.0,
            end=1.0,
            text=" Hello",
            words=[SimpleNamespace(word=" Hello", start=0.0, end=1.0, probability=0.99)],
        )
        return iter([segment]), SimpleNamespace(language="en", language_probability=0.99)


class StubModels:
    def __init__(self, model: StubModel) -> None:
        self.model = model

    def ensure_whisper_model(self, _model_id, _device, _notify):
        return self.model, "cpu", "int8", None


def test_wav_is_a_supported_media_suffix() -> None:
    assert ".wav" in SUPPORTED_MEDIA_SUFFIXES
    assert {".mp3", ".mp4", ".wav"} <= SUPPORTED_MEDIA_SUFFIXES


def test_faster_whisper_audio_decoder_accepts_metadata_errors(tmp_path: Path) -> None:
    from faster_whisper.audio import decode_audio

    wav = tmp_path / "decoder-probe.wav"
    with wave.open(str(wav), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 1600)

    samples = decode_audio(str(wav), sampling_rate=16_000)

    assert len(samples) == 1600


def test_local_transcriber_passes_wav_path_to_asr(tmp_path: Path) -> None:
    wav = tmp_path / "dialogue.wav"
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    model = StubModel()
    transcriber = LocalTranscriber(StubModels(model))

    result = transcriber.transcribe(
        wav,
        model_id="stub-model",
        device=ProcessingDevice.CPU,
        language_mode=LanguageMode.AUTO,
    )

    assert model.source_path == str(wav)
    assert result.text == "Hello"
    assert result.language == "en"
    assert result.anchors


def test_local_transcriber_rejects_unsupported_extension(tmp_path: Path) -> None:
    audio = tmp_path / "dialogue.flac"
    audio.write_bytes(b"not-a-supported-media-file")

    with pytest.raises(ValueError, match="MP3、WAV.*MP4"):
        LocalTranscriber(StubModels(StubModel())).transcribe(
            audio,
            model_id="stub-model",
            device=ProcessingDevice.CPU,
            language_mode=LanguageMode.AUTO,
        )
