"""Explicit local-model download, source selection and CUDA/CPU loading."""

from __future__ import annotations

import ctypes
import importlib.util
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from platformdirs import user_data_path

from .constants import APP_ID
from .models import ProcessingDevice

ProgressCallback = Callable[[str, int | None], None]


@dataclass(frozen=True, slots=True)
class ModelPreset:
    id: str
    label: str
    description: str
    whisper_model: str
    repository_id: str
    approximate_download_mb: int


@dataclass(frozen=True, slots=True)
class ModelSource:
    id: str
    label: str
    endpoint: str
    description: str


@dataclass(frozen=True, slots=True)
class SourceProbe:
    ok: bool
    message: str
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class GpuDiagnostic:
    available: bool
    summary: str
    details: str
    compute_type: str | None = None


class ModelNotDownloadedError(RuntimeError):
    """Raised when the user has not explicitly downloaded the selected model."""


class ModelDownloadCancelled(RuntimeError):
    """Raised when the user requests a safe stop during a model transfer."""


class ModelDownloadError(RuntimeError):
    """A copyable, multi-line diagnostic for one direct model-file request."""


MODEL_PRESETS: tuple[ModelPreset, ...] = (
    ModelPreset(
        "fast",
        "快速",
        "用于草稿转写与短片测试。",
        "base",
        "Systran/faster-whisper-base",
        150,
    ),
    ModelPreset(
        "small",
        "平衡（默认）",
        "适合常规中英文影视对白字幕。",
        "small",
        "Systran/faster-whisper-small",
        470,
    ),
    ModelPreset(
        "accurate",
        "高精度",
        "用于最终校对前的高质量识别。",
        "medium",
        "Systran/faster-whisper-medium",
        1500,
    ),
)

MODEL_SOURCES: tuple[ModelSource, ...] = (
    ModelSource(
        "official",
        "官方源（适合可直连 / VPN）",
        "https://huggingface.co",
        "Hugging Face 官方模型仓库。",
    ),
    ModelSource(
        "cn_mirror",
        "国内镜像源",
        "https://hf-mirror.com",
        "适合部分中国境内网络环境；可先测试连接。",
    ),
    ModelSource("custom", "自定义地址", "", "用于企业镜像、代理网关或您可信的模型源。"),
)

_REQUIRED_MODEL_FILES = (
    "config.json",
    "model.bin",
    "tokenizer.json",
)
_COMPLETION_MARKER = ".lyrasub-model-complete.json"
_PARTIAL_SUFFIX = ".lyrasub-partial"

# `os.add_dll_directory()` handles must remain alive for as long as the process
# needs the directories. This is particularly important in PyInstaller builds,
# where PATH alone is not a reliable search source for native extension DLLs.
_DLL_DIRECTORY_HANDLES: list[object] = []
_DLL_DIRECTORY_PATHS: set[str] = set()


class ModelManager:
    """Owns user-writable caches and never writes models into the app directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (user_data_path(APP_ID, appauthor=False) / "models")
        self.whisper_root = self.root / "whisper"
        self.alignment_root = self.root / "alignment"
        self.whisper_root.mkdir(parents=True, exist_ok=True)
        self.alignment_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _runtime_dll_directories() -> list[Path]:
        """Return trusted CUDA/cuDNN directories for an installed Windows runtime."""
        if os.name != "nt":
            return []

        directories: list[Path] = []
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        cuda_root = program_files / "NVIDIA GPU Computing Toolkit" / "CUDA"
        if cuda_root.is_dir():
            directories.extend(
                path / "bin"
                for path in sorted(cuda_root.glob("v12*"), reverse=True)
                if (path / "bin").is_dir()
            )
        cuda_path = os.environ.get("CUDA_PATH", "").strip()
        if cuda_path:
            directories.extend([Path(cuda_path), Path(cuda_path) / "bin"])

        cudnn_root = program_files / "NVIDIA" / "CUDNN"
        if cudnn_root.is_dir():
            directories.extend(
                path / "bin"
                for path in sorted(cudnn_root.glob("v9*"), reverse=True)
                if (path / "bin").is_dir()
            )
        for variable in ("LYRASUB_CUDNN_BIN", "CUDNN_PATH", "CUDNN_HOME", "CUDNN_ROOT"):
            configured = os.environ.get(variable, "").strip()
            if configured:
                directories.extend([Path(configured), Path(configured) / "bin"])

        # The CTranslate2 wheel used by LyraSub may contain cuDNN 9 loader DLLs.
        # Register its package folder explicitly because PyInstaller's _internal
        # directory is not always searched by native dependencies.
        spec = importlib.util.find_spec("ctranslate2")
        if spec and spec.submodule_search_locations:
            directories.extend(Path(location) for location in spec.submodule_search_locations)

        unique: list[Path] = []
        seen: set[str] = set()
        for directory in directories:
            try:
                resolved = directory.resolve()
            except OSError:
                resolved = directory
            key = str(resolved).casefold()
            if directory.is_dir() and key not in seen:
                seen.add(key)
                unique.append(directory)
        return unique

    @staticmethod
    def _register_runtime_dll_directories() -> tuple[list[Path], list[str]]:
        """Add trusted native-runtime folders to the frozen process DLL search path."""
        if os.name != "nt" or not hasattr(os, "add_dll_directory"):
            return [], []
        registered: list[Path] = []
        errors: list[str] = []
        for directory in ModelManager._runtime_dll_directories():
            key = str(directory.resolve()).casefold()
            try:
                if key not in _DLL_DIRECTORY_PATHS:
                    _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
                    _DLL_DIRECTORY_PATHS.add(key)
                registered.append(directory)
            except OSError as exc:
                errors.append(f"{directory}：{type(exc).__name__}: {exc}")
        try:
            # Make AddDllDirectory locations available to native extensions that
            # call LoadLibrary directly, not only to ctypes.WinDLL calls.
            load_library_search_default_dirs = 0x00001000
            if not ctypes.windll.kernel32.SetDefaultDllDirectories(  # type: ignore[attr-defined]
                load_library_search_default_dirs
            ):
                errors.append("SetDefaultDllDirectories 返回失败。")
        except (AttributeError, OSError) as exc:
            errors.append(f"SetDefaultDllDirectories 异常：{type(exc).__name__}: {exc}")
        return registered, errors

    @staticmethod
    def _loadable_dll(name: str, known_directories: list[Path]) -> tuple[bool, str]:
        candidates = [
            directory / name for directory in known_directories if (directory / name).is_file()
        ]
        try:
            if os.name == "nt":
                ctypes.WinDLL(name)
            else:
                ctypes.CDLL(name)
            source = f" · 文件候选：{candidates[0]}" if candidates else ""
            return True, f"可加载{source}"
        except OSError as exc:
            location = (
                "；已发现文件但其依赖或版本无法加载：" + ", ".join(map(str, candidates))
                if candidates
                else "；已注册目录中未发现同名文件"
            )
            return False, f"{type(exc).__name__}: {exc}{location}"

    @staticmethod
    def _nvidia_smi_report() -> str:
        executable = shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe")
        if not executable:
            return "nvidia-smi：未在 PATH 中找到。"
        try:
            result = subprocess.run(
                [executable, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
        except OSError as exc:
            return f"nvidia-smi：无法执行（{exc}）。"
        output = (result.stdout or result.stderr).strip()
        return f"nvidia-smi：{output or f'退出码 {result.returncode}'}"

    @staticmethod
    def _preferred_cuda_compute_type(supported: set[str]) -> str | None:
        """Select a safe CTranslate2 compute type for the detected CUDA device."""
        # Pascal GPUs such as GTX 1060 report INT8/FP32 but deliberately exclude
        # efficient FP16 because they do not have Tensor Cores. Do not override
        # CTranslate2's capability check with CT2_CUDA_ALLOW_FP16.
        for compute_type in ("float16", "int8", "float32"):
            if compute_type in supported:
                return compute_type
        return None

    def diagnose_gpu(self) -> GpuDiagnostic:
        """Return a copyable Windows GPU runtime report without throwing on CPU-only systems."""
        lines = ["《弦字幕》GPU 自检", self._nvidia_smi_report()]
        directories, registration_errors = self._register_runtime_dll_directories()
        if directories:
            lines.append("已显式注册 DLL 目录：" + "；".join(map(str, directories)))
        if registration_errors:
            lines.append("DLL 目录注册异常：" + "；".join(registration_errors))
        dll_results: list[bool] = []
        for dll in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"):
            loadable, detail = self._loadable_dll(dll, directories)
            dll_results.append(loadable)
            lines.append(f"{dll}：{'可加载' if loadable else '缺失或不可加载'} · {detail}")

        count = 0
        supported_types: set[str] = set()
        compute_type: str | None = None
        try:
            import ctranslate2  # type: ignore[import-not-found]

            count = ctranslate2.get_cuda_device_count()
            lines.append(f"CTranslate2 CUDA 设备数：{count}")
            if count > 0:
                supported_types = set(ctranslate2.get_supported_compute_types("cuda"))
                compute_type = self._preferred_cuda_compute_type(supported_types)
                lines.append(
                    "CTranslate2 CUDA 支持计算精度："
                    + (", ".join(sorted(supported_types)) or "未返回")
                )
                lines.append(f"LyraSub 推荐 GPU 计算精度：{compute_type or '无兼容选项'}")
        except Exception as exc:
            lines.append(f"CTranslate2 CUDA 检测异常：{type(exc).__name__}: {exc}")
        runtime_complete = all(dll_results)
        available = count > 0 and runtime_complete and compute_type is not None
        lines.append(f"应用进程 PATH：{os.environ.get('PATH', '')}")
        if available:
            detail = "；Pascal/GTX 10 系会自动使用 INT8" if compute_type == "int8" else ""
            summary = (
                f"检测到 {count} 个可用 CUDA 设备，将使用 {compute_type} GPU 计算精度。{detail}"
            )
        elif not runtime_complete:
            summary = "未检测到完整 CUDA 运行库。请检查 CUDA 12/cuDNN 9 和 NVIDIA 驱动。"
        elif count > 0:
            summary = "CUDA 设备可见，但 CTranslate2 未返回兼容计算精度；请复制完整诊断信息。"
        else:
            summary = "CUDA 运行库可加载，但 CTranslate2 未找到可用设备；请检查 NVIDIA 驱动或 GPU 可见性。"
        return GpuDiagnostic(available, summary, "\n".join(lines), compute_type)

    def cuda_available(self) -> bool:
        return self.diagnose_gpu().available

    def resolve_device(self, requested: ProcessingDevice) -> tuple[str, str, str | None]:
        """Return faster-whisper device, compute type and an explanatory fallback note."""
        if requested == ProcessingDevice.CPU:
            return "cpu", "int8", None
        diagnostic = self.diagnose_gpu()
        if requested == ProcessingDevice.NVIDIA_GPU:
            if not diagnostic.available or not diagnostic.compute_type:
                raise RuntimeError(
                    "未检测到可用的 NVIDIA CUDA 环境，不能按您的设置使用 GPU。\n"
                    f"{diagnostic.summary}\n\n{diagnostic.details}"
                )
            return "cuda", diagnostic.compute_type, None
        if diagnostic.available and diagnostic.compute_type:
            return "cuda", diagnostic.compute_type, None
        return "cpu", "int8", f"{diagnostic.summary} 已自动切换为 CPU 模式。"

    def preset(self, preset_id: str) -> ModelPreset:
        for preset in MODEL_PRESETS:
            if preset.id == preset_id or preset.whisper_model == preset_id:
                return preset
        raise ValueError(f"未知模型档位：{preset_id}")

    @staticmethod
    def sanitize_endpoint(endpoint: str) -> str:
        cleaned = endpoint.strip().rstrip("/")
        if not cleaned.startswith("https://"):
            raise ValueError("模型源地址必须以 https:// 开头。")
        return cleaned

    @staticmethod
    def sanitize_proxy(proxy_url: str) -> str:
        cleaned = proxy_url.strip()
        if not cleaned:
            return ""
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError(
                "代理地址须为空，或以 http://、https:// 开头，例如 http://127.0.0.1:7890。"
            )
        return cleaned

    @staticmethod
    def _proxies(proxy_url: str) -> dict[str, str] | None:
        return {"http": proxy_url, "https": proxy_url} if proxy_url else None

    @staticmethod
    def _safe_url(url: str) -> str:
        """Remove signed query text while retaining the final host/path for diagnostics."""
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    def model_directory(self, preset_id: str) -> Path:
        return self.whisper_root / self.preset(preset_id).id

    def is_model_downloaded(self, preset_id: str) -> bool:
        directory = self.model_directory(preset_id)
        if not directory.is_dir() or any(directory.rglob(f"*{_PARTIAL_SUFFIX}")):
            return False
        # Legacy complete folders from earlier builds have no marker but still remain usable.
        return all(
            (directory / name).is_file() and (directory / name).stat().st_size > 0
            for name in _REQUIRED_MODEL_FILES
        )

    def downloaded_size_bytes(self, preset_id: str) -> int:
        directory = self.model_directory(preset_id)
        if not directory.exists():
            return 0
        return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())

    def probe_source(
        self,
        preset_id: str,
        endpoint: str,
        proxy_url: str = "",
        timeout_seconds: int = 15,
    ) -> SourceProbe:
        """Test direct repository metadata access only; no model file is downloaded."""
        preset = self.preset(preset_id)
        source = self.sanitize_endpoint(endpoint)
        proxy = self.sanitize_proxy(proxy_url)
        try:
            response = requests.get(
                f"{source}/api/models/{preset.repository_id}",
                timeout=(10, timeout_seconds),
                headers={"User-Agent": "LyraSub/1.0 source-probe"},
                proxies=self._proxies(proxy),
            )
        except requests.RequestException as exc:
            return SourceProbe(False, self._probe_error(source, proxy, exc))
        if response.status_code == 200:
            proxy_note = "已使用代理。" if proxy else "未使用应用内代理。"
            return SourceProbe(True, f"连接正常：已找到 {preset.repository_id}。{proxy_note}", 200)
        if response.status_code in {401, 403}:
            return SourceProbe(
                False,
                f"模型源返回 HTTP {response.status_code}，需要认证或拒绝访问。\n来源：{source}",
                response.status_code,
            )
        if response.status_code == 404:
            return SourceProbe(
                False,
                f"模型源无法找到模型仓库。\n来源：{source}\n仓库：{preset.repository_id}",
                404,
            )
        return SourceProbe(
            False, f"模型源返回 HTTP {response.status_code}。\n来源：{source}", response.status_code
        )

    @staticmethod
    def _probe_error(source: str, proxy: str, exc: Exception) -> str:
        return (
            "模型源连接失败\n"
            f"来源：{source}\n"
            f"代理：{proxy or '未设置'}\n"
            f"异常类型：{type(exc).__name__}\n"
            f"异常详情：{exc}"
        )

    def _repository_files(
        self,
        preset: ModelPreset,
        source: str,
        proxy_url: str,
        cancel_event: Event | None,
    ) -> list[str]:
        if cancel_event and cancel_event.is_set():
            raise ModelDownloadCancelled(
                "[DOWNLOAD_CANCELLED] 已停止下载；未完成的临时文件已清理。"
            )
        metadata_url = f"{source}/api/models/{preset.repository_id}"
        try:
            response = requests.get(
                metadata_url,
                timeout=(10, 30),
                headers={"User-Agent": "LyraSub/1.0 direct-model-downloader"},
                proxies=self._proxies(proxy_url),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ModelDownloadError(
                "模型下载失败：无法取得仓库文件清单\n"
                f"来源：{source}\n"
                f"请求 URL：{metadata_url}\n"
                f"代理：{proxy_url or '未设置'}\n"
                f"异常类型：{type(exc).__name__}\n"
                f"异常详情：{exc}"
            ) from exc
        names = [str(item.get("rfilename", "")) for item in payload.get("siblings", [])]
        selected = [
            name
            for name in names
            if name in {"config.json", "model.bin", "tokenizer.json", "preprocessor_config.json"}
            or name.startswith("vocabulary.")
        ]
        missing = [name for name in _REQUIRED_MODEL_FILES if name not in selected]
        if missing:
            raise ModelDownloadError(
                "模型下载失败：仓库清单不完整\n"
                f"来源：{source}\n仓库：{preset.repository_id}\n"
                f"缺少文件：{', '.join(missing)}\n"
                f"获得清单：{', '.join(names) or '空'}"
            )
        return selected

    def _download_file(
        self,
        source: str,
        preset: ModelPreset,
        filename: str,
        destination: Path,
        index: int,
        total_files: int,
        proxy_url: str,
        status: ProgressCallback | None,
        cancel_event: Event | None,
    ) -> None:
        source_url = f"{source}/{preset.repository_id}/resolve/main/{quote(filename)}"
        partial = destination.with_name(f"{destination.name}{_PARTIAL_SUFFIX}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial.unlink(missing_ok=True)
        try:
            response = requests.get(
                source_url,
                stream=True,
                allow_redirects=True,
                timeout=(15, 60),
                headers={"User-Agent": "LyraSub/1.0 direct-model-downloader"},
                proxies=self._proxies(proxy_url),
            )
            response.raise_for_status()
            total_bytes = int(response.headers.get("content-length", "0") or 0)
            final_url = self._safe_url(response.url)
            if status:
                status(
                    f"正在下载第 {index}/{total_files} 个文件：{filename}\n"
                    f"请求：{source_url}\n最终地址：{final_url}",
                    0,
                )
            received = 0
            with partial.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 512):
                    if cancel_event and cancel_event.is_set():
                        raise ModelDownloadCancelled(
                            "[DOWNLOAD_CANCELLED] 已停止下载；未完成的临时文件已清理。"
                        )
                    if not chunk:
                        continue
                    output.write(chunk)
                    received += len(chunk)
                    if status and total_bytes:
                        percent = min(99, int(received * 100 / total_bytes))
                        status(
                            f"正在下载第 {index}/{total_files} 个文件：{filename} · "
                            f"{received / 1_000_000:.1f}/{total_bytes / 1_000_000:.1f} MB",
                            percent,
                        )
            partial.replace(destination)
            if status:
                status(f"已完成第 {index}/{total_files} 个文件：{filename}", 100)
        except ModelDownloadCancelled:
            partial.unlink(missing_ok=True)
            raise
        except requests.RequestException as exc:
            partial.unlink(missing_ok=True)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            final_url = ""
            if getattr(exc, "response", None) is not None:
                final_url = self._safe_url(exc.response.url)
            raise ModelDownloadError(
                "模型下载失败：文件请求未成功\n"
                f"来源：{source}\n"
                f"请求文件：{filename}\n"
                f"请求 URL：{source_url}\n"
                f"最终地址：{final_url or '请求未获得重定向结果'}\n"
                f"HTTP 状态：{status_code or '无响应'}\n"
                f"代理：{proxy_url or '未设置'}\n"
                f"异常类型：{type(exc).__name__}\n"
                f"异常详情：{exc}"
            ) from exc
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise ModelDownloadError(
                "模型下载失败：无法写入本地文件\n"
                f"请求文件：{filename}\n"
                f"目标路径：{destination}\n"
                f"临时路径：{partial}\n"
                f"异常类型：{type(exc).__name__}\n"
                f"异常详情：{exc}"
            ) from exc
        finally:
            response.close() if "response" in locals() else None

    def download_model(
        self,
        preset_id: str,
        endpoint: str,
        proxy_url: str = "",
        status: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> Path:
        """Directly download model files, bypassing Hub metadata validation in frozen Windows builds."""
        preset = self.preset(preset_id)
        source = self.sanitize_endpoint(endpoint)
        proxy = self.sanitize_proxy(proxy_url)
        destination = self.model_directory(preset_id)
        if self.is_model_downloaded(preset_id):
            if status:
                status(f"“{preset.label}”模型已存在，将直接复用本地文件。", 100)
            return destination
        if status:
            status(
                f"正在读取“{preset.label}”模型文件清单（约 {preset.approximate_download_mb} MB）。",
                0,
            )
        files = self._repository_files(preset, source, proxy, cancel_event)
        try:
            for index, filename in enumerate(files, start=1):
                target = destination / filename
                self._download_file(
                    source,
                    preset,
                    filename,
                    target,
                    index,
                    len(files),
                    proxy,
                    status,
                    cancel_event,
                )
        except ModelDownloadCancelled:
            for partial in destination.rglob(f"*{_PARTIAL_SUFFIX}") if destination.exists() else []:
                partial.unlink(missing_ok=True)
            raise
        if not self.is_model_downloaded(preset_id):
            raise ModelDownloadError(
                "模型下载未完整结束。\n"
                f"模型目录：{destination}\n"
                "请检查磁盘空间与网络后重试；未完成文件不会被当作可用模型。"
            )
        (destination / _COMPLETION_MARKER).write_text("complete\n", encoding="utf-8")
        if status:
            status(f"“{preset.label}”模型下载完成，已保存到 {destination}。", 100)
        return destination

    def ensure_whisper_model(
        self,
        preset_id: str,
        requested_device: ProcessingDevice,
        status: Callable[[str], None] | None = None,
    ) -> tuple[object, str, str, str | None]:
        """Load only an explicitly downloaded local model; never initiate hidden network I/O."""
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("本机缺少识别运行组件，请先安装最新 LyraSub-patch.exe。") from exc

        preset = self.preset(preset_id)
        if not self.is_model_downloaded(preset_id):
            raise ModelNotDownloadedError(
                f"“{preset.label}”模型尚未下载。请先在本页选择模型来源并点击“下载选中模型”。"
            )
        device, compute_type, fallback = self.resolve_device(requested_device)
        if status:
            status(f"正在载入已下载的“{preset.label}”模型。")
        model_directory = self.model_directory(preset_id)
        try:
            model = WhisperModel(str(model_directory), device=device, compute_type=compute_type)
        except Exception as exc:
            if device == "cuda" and requested_device == ProcessingDevice.AUTO:
                if status:
                    status("GPU 初始化失败，正在自动切换到 CPU。")
                model = WhisperModel(str(model_directory), device="cpu", compute_type="int8")
                return model, "cpu", "int8", "GPU 初始化失败，已自动切换为 CPU 模式。"
            diagnostic = self.diagnose_gpu()
            raise RuntimeError(
                "无法载入已下载模型。\n"
                f"原始异常：{type(exc).__name__}: {exc}\n\n"
                f"{diagnostic.summary}\n\n{diagnostic.details}"
            ) from exc
        return model, device, compute_type, fallback

    def model_root(self) -> Path:
        return self.root
