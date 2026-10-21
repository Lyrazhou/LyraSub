#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINEPREFIX="${WINEPREFIX:-$HOME/.wine-lyrasub}"
WIN_PYTHON='C:\LyraPython312\python.exe'
WIN_ROOT="Z:${ROOT//\//\\}"
WIN_DIST="${WIN_ROOT}\\dist"
WIN_BUILD="${WIN_ROOT}\\build-win"
WIN_SPEC="${WIN_ROOT}\\build-spec"

export WINEPREFIX
export WINEDEBUG=-all
wine "$WIN_PYTHON" -c "import av,wave,tempfile,pathlib; from faster_whisper.audio import decode_audio; assert av.__version__.split('.')[0] == '18', f'Unsupported PyAV version: {av.__version__}'; p=pathlib.Path(tempfile.gettempdir())/'lyrasub_build_probe.wav'; w=wave.open(str(p),'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\\0\\0'*1600); w.close(); samples=decode_audio(str(p),sampling_rate=16000); assert len(samples)==1600, len(samples); print('Build environment faster-whisper/PyAV verification passed:', av.__version__, len(samples))"
rm -rf "$ROOT/dist" "$ROOT/build-win" "$ROOT/build-spec"
mkdir -p "$ROOT/dist"

wine "$WIN_PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name LyraSub \
  --paths "${WIN_ROOT}\\src" \
  --distpath "$WIN_DIST" \
  --workpath "$WIN_BUILD" \
  --specpath "$WIN_SPEC" \
  --collect-all opencc \
  --exclude-module faster_whisper \
  --exclude-module ctranslate2 \
  --exclude-module onnxruntime \
  --exclude-module av \
  --exclude-module tokenizers \
  --exclude-module huggingface_hub \
  --exclude-module numpy \
  "${WIN_ROOT}\\run_lyrasub.py"

# Keep the patch small by retaining the original manual ASR package layout, while
# adding the native runtime DLL folders that the first baseline omitted. Without
# numpy.libs / av.libs, Windows cannot load the package extension modules.
RUNTIME_SITE="$WINEPREFIX/drive_c/LyraPython312/Lib/site-packages"
RUNTIME_TARGET="$ROOT/dist/LyraSub/_internal"
for package in \
  faster_whisper ctranslate2 onnxruntime av tokenizers huggingface_hub \
  hf_xet filelock fsspec tqdm httpx httpcore anyio h11 click \
  numpy yaml typing_extensions platformdirs packaging opencc numpy.libs av.libs; do
  if [[ -e "$RUNTIME_SITE/$package" ]]; then
    cp -a "$RUNTIME_SITE/$package" "$RUNTIME_TARGET/"
  fi
done

# Fail the build if OpenCC is missing from the frozen analysis or from the
# runtime package tree. Validate the actual copied bundle, not only site-packages.
if grep -q "missing module named opencc" "$ROOT/build-win/LyraSub/warn-LyraSub.txt"; then
  echo "ERROR: PyInstaller reports the OpenCC module as missing." >&2
  exit 1
fi
wine "$WIN_PYTHON" -c "import sys; sys.path.insert(0, r'${WIN_DIST}\\LyraSub\\_internal'); from opencc import OpenCC; result = OpenCC('t2s').convert('繁體中文測試'); assert result == '繁体中文测试', result; print('Bundled OpenCC verification passed:', result)"
wine "$WIN_PYTHON" -c "import sys; sys.path.insert(0, r'${WIN_DIST}\\LyraSub\\_internal'); import wave,tempfile,pathlib,av; from faster_whisper.audio import decode_audio; p=pathlib.Path(tempfile.gettempdir())/'lyrasub_build_probe.wav'; w=wave.open(str(p),'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\\0\\0'*1600); w.close(); samples=decode_audio(str(p),sampling_rate=16000); assert len(samples)==1600, len(samples); print('Bundled faster-whisper/PyAV decode verification passed:', av.__version__, len(samples))"

cat > "$ROOT/dist/LyraSub/release-manifest.json" <<'JSON'
{
  "product": "LyraSub",
  "display_name": "弦字幕 Plus（LyraSub Plus）",
  "version": "v261015A",
  "build_revision": 3,
  "baseline": false,
  "release_note": "LyraSub Plus：新增 WAV 音频导入与本地识别支持；修复 OpenCC 打包遗漏及 faster-whisper/PyAV 音频解码依赖冲突（Build 3）。",
  "update_channel": "stable",
  "models_location": "%LOCALAPPDATA%\\LyraSub\\models"
}
JSON

echo "Windows application directory built at: $ROOT/dist/LyraSub"
