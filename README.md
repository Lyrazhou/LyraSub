# 《弦字幕 Plus》（LyraSub Plus）v261015A 源码包

本压缩包保存《弦字幕 Plus》（LyraSub Plus）v261015A 的完整可维护源码、测试、Windows 安装脚本、构建工具和归档文档。基础版在任何对比或引用中统一写作 **《弦字幕（LyraSub）》**。

## 本包包含

| 内容 | 说明 |
|---|---|
| `src/` | 应用核心源码，包括主界面、工程、MP3/WAV/MP4 识别、LLM、翻译、SRT 与分段合并逻辑。 |
| `tests/` | 单元测试和 Qt 离屏界面回归测试。 |
| `installer/` | NSIS 完整安装程序脚本。 |
| `tools/` | Windows 应用目录构建与补丁工具。 |
| `docs/` | 完整操作说明、快速指南、版本对比、宣传文案、WAV 升级说明及归档说明。 |
| `run_lyrasub.py`、`pyproject.toml` | 启动入口和项目质量配置。 |

## 本包明确不包含

已编译 EXE、`dist/`、`build-win/`、`build-spec/`、旧发布文件、Wine 前缀、Python 缓存、测试缓存、本地识别模型和任何真实 API 密钥均不属于源码归档。完整安装程序请从 v261015A GitHub Release 下载 `LyraSub-Plus-v261015A-setup.exe`。识别模型由用户在软件启动后按需下载。

## 质量验证

v261015A Build 3 修复 Windows 安装包遗漏 OpenCC 及 PyAV 与 faster-whisper 音频解码 API 不兼容的问题，并加入真实解码回归测试；同时包含 WAV 音频导入、工程保存与本地识别。

```bash
ruff format src tests
ruff check src tests --fix
pytest
QT_QPA_PLATFORM=offscreen pytest tests/test_ui_smoke.py
```

## Windows 构建

请在配有 Windows Python 3.12、PyInstaller、NSIS，或兼容 Wine 构建环境的机器上，从项目根目录执行：

```bash
WINEPREFIX=/home/ubuntu/.wine-lyrasub bash tools/build_windows_app.sh
cd installer
makensis setup.nsi
```

构建后，请检查 `dist/LyraSub/release-manifest.json`，确认 `version` 为 `v261015A`，并在新环境执行安装验证后再对外发布。

> 本工具由天琴工作室（Lyra Studio）出品 · 海舟（Lyrazhou）制作  
> Email: fantasyland@163.com
