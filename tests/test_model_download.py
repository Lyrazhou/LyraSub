from pathlib import Path

import pytest

from lyrasub.model_manager import (
    MODEL_PRESETS,
    ModelDownloadCancelled,
    ModelManager,
    ModelNotDownloadedError,
)
from lyrasub.models import ProcessingDevice


class FakeResponse:
    def __init__(
        self, url: str, payload: dict[str, object] | None = None, content: bytes = b""
    ) -> None:
        self.url = url
        self._payload = payload
        self._content = content
        self.status_code = 200
        self.headers = {"content-length": str(len(content))} if content else {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        assert self._payload is not None
        return self._payload

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return [self._content] if self._content else []

    def close(self) -> None:
        return None


def test_all_three_presets_expose_download_metadata() -> None:
    assert [preset.id for preset in MODEL_PRESETS] == ["fast", "small", "accurate"]
    assert all(
        preset.repository_id.startswith("Systran/faster-whisper-") for preset in MODEL_PRESETS
    )
    assert all(preset.approximate_download_mb > 0 for preset in MODEL_PRESETS)


def test_download_model_uses_direct_files_and_local_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ModelManager(root=tmp_path / "models")
    requests_seen: list[str] = []
    siblings = [
        {"rfilename": "config.json"},
        {"rfilename": "model.bin"},
        {"rfilename": "tokenizer.json"},
        {"rfilename": "vocabulary.txt"},
    ]

    def fake_get(url: str, **_: object) -> FakeResponse:
        requests_seen.append(url)
        if "/api/models/" in url:
            return FakeResponse(url, payload={"siblings": siblings})
        return FakeResponse(url, content=f"data-{Path(url).name}".encode())

    import lyrasub.model_manager as model_manager_module

    monkeypatch.setattr(model_manager_module.requests, "get", fake_get)
    destination = manager.download_model("fast", "https://mirror.example.com")

    assert destination == manager.model_directory("fast")
    assert manager.is_model_downloaded("fast")
    assert requests_seen[0] == "https://mirror.example.com/api/models/Systran/faster-whisper-base"
    assert (
        "https://mirror.example.com/Systran/faster-whisper-base/resolve/main/model.bin"
        in requests_seen
    )


def test_cancelled_download_does_not_leave_a_usable_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ModelManager(root=tmp_path / "models")
    siblings = [
        {"rfilename": "config.json"},
        {"rfilename": "model.bin"},
        {"rfilename": "tokenizer.json"},
    ]

    def fake_get(url: str, **_: object) -> FakeResponse:
        if "/api/models/" in url:
            return FakeResponse(url, payload={"siblings": siblings})
        return FakeResponse(url, content=b"model")

    from threading import Event

    import lyrasub.model_manager as model_manager_module

    cancelled = Event()
    cancelled.set()
    monkeypatch.setattr(model_manager_module.requests, "get", fake_get)
    with pytest.raises(ModelDownloadCancelled):
        manager.download_model("fast", "https://mirror.example.com", cancel_event=cancelled)
    assert not manager.is_model_downloaded("fast")


def test_local_loading_never_starts_hidden_download(tmp_path: Path) -> None:
    manager = ModelManager(root=tmp_path / "models")
    with pytest.raises(ModelNotDownloadedError):
        manager.ensure_whisper_model("small", ProcessingDevice.CPU)


def test_recognition_import_failure_preserves_native_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    manager = ModelManager(root=tmp_path / "models")
    registration_calls: list[bool] = []
    monkeypatch.setattr(
        ModelManager,
        "_register_runtime_dll_directories",
        staticmethod(lambda: (registration_calls.append(True) or [], [])),
    )
    original_import = builtins.__import__

    def fail_faster_whisper(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("DLL load failed while importing _ext")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_faster_whisper)
    with pytest.raises(RuntimeError, match="DLL load failed while importing _ext"):
        manager.ensure_whisper_model("small", ProcessingDevice.CPU)
    assert registration_calls == [True]


def test_frozen_bundle_root_is_added_to_python_import_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    bundle_root = tmp_path / "_internal"
    bundle_root.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert ModelManager._prepare_frozen_import_paths() == [bundle_root]
    assert sys.path[0] == str(bundle_root)


def test_custom_source_requires_https(tmp_path: Path) -> None:
    manager = ModelManager(root=tmp_path / "models")
    with pytest.raises(ValueError, match="https"):
        manager.sanitize_endpoint("http://untrusted.example")


def test_preferred_cuda_compute_type_handles_pascal_and_modern_gpus() -> None:
    assert ModelManager._preferred_cuda_compute_type({"int8", "int8_float32", "float32"}) == "int8"
    assert ModelManager._preferred_cuda_compute_type({"int8", "float16", "float32"}) == "float16"
    assert ModelManager._preferred_cuda_compute_type({"float32"}) == "float32"
    assert ModelManager._preferred_cuda_compute_type({"int8_float16"}) is None
