# 《弦字幕 Plus》（LyraSub Plus）v260822A 源码包

本压缩包保存《弦字幕 Plus》（LyraSub Plus）v260822A 的完整可维护源码、测试、Windows 安装脚本、构建工具和归档文档。基础版在任何对比或引用中统一写作 **《弦字幕（LyraSub）》**。

## 本包包含

| 内容 | 说明 |
|---|---|
| `src/` | 应用核心源码，包括主界面、工程、识别、LLM、翻译、SRT 与分段合并逻辑。 |
| `tests/` | 单元测试和 Qt 离屏界面回归测试。 |
| `installer/` | NSIS 完整安装程序脚本。 |
| `tools/` | Windows 应用目录构建与补丁工具。 |
| `docs/` | 完整操作说明、快速指南、版本对比、宣传文案及归档说明。 |
| `run_lyrasub.py`、`pyproject.toml` | 启动入口和项目质量配置。 |

## 本包明确不包含

已编译 EXE、`dist/`、`build-win/`、`build-spec/`、旧发布文件、Wine 前缀、Python 缓存、测试缓存、本地识别模型和任何真实 API 密钥均不属于源码归档。完整安装程序请使用与本包一同交付的 `LyraSub-Plus-setup.exe`。识别模型由用户在软件启动后按需下载。

## 质量验证

v260822A 归档前已通过：73 项单元测试、27 项离屏界面测试；完整安装程序已在全新的隔离 Wine 前缀中静默安装，并确认已安装 `LyraSub.exe` 与 `release-manifest.json`，其中版本为 `v260822A`、构建修订号为 `2`。

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

构建后，请检查 `dist/LyraSub/release-manifest.json`，确认 `version` 为 `v260822A`，并在新环境执行安装验证后再对外发布。

> 本工具由天琴工作室（Lyra Studio）出品 · 海舟（Lyrazhou）制作  
> Email: fantasyland@163.com
