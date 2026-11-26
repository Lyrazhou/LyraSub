# 《弦字幕 Plus》（LyraSub Plus）v261127A 源码包

本归档保存《弦字幕 Plus》（LyraSub Plus）v261127A 的可维护源码、测试、Windows 安装脚本、构建工具和操作文档。基础版在任何对比或引用中统一写作 **《弦字幕（LyraSub）》**。

## v261127A 更新

修复 Windows 安装界面、卸载项和快捷方式中文名称显示乱码。NSIS 脚本现在以 UTF-8 BOM 编码保存，工作流会检查 BOM；静默安装后还会验证开始菜单快捷方式、桌面快捷方式和卸载注册表显示名均为正确中文。

## 继承的功能与修复

v261127A 基于 v261126A，其中包含 `HH:MM:SS.mmm` 毫秒时间码编辑及相邻字幕 ±1ms 快捷操作和越界保护；识别模块导入前注册冻结应用 DLL 搜索目录，并在冻结目录和静默安装目录分别执行识别运行时自检。项目模型与工程用户数据保持原有安装兼容性。

## 本包包含

| 内容 | 说明 |
|---|---|
| `src/` | 应用核心源码，包括主界面、工程、音视频识别、LLM、翻译、SRT 与分段合并逻辑。 |
| `tests/` | 单元测试和 Qt 离屏界面回归测试。 |
| `.github/workflows/windows-installer.yml` | Windows x64 完整安装程序构建、静默安装、识别组件和中文快捷方式检查工作流。 |
| `installer/` | NSIS 完整安装程序脚本。 |
| `tools/` | Windows 应用目录构建与补丁工具。 |
| `docs/` | 完整操作说明、快速指南、版本对比、宣传文案和源码归档说明。 |
| `run_lyrasub.py`、`pyproject.toml` | 启动入口和项目质量配置。 |

## 排除内容

编译 EXE、`dist/`、`build-win/`、`build-spec/`、Wine 前缀、Python 缓存、测试缓存、本地识别模型和真实 API 密钥不进入源码 ZIP。识别模型由用户在软件启动后按需下载。

## 质量验证

```bash
ruff check src tests
ruff format --check src tests
QT_QPA_PLATFORM=offscreen pytest -q
```

当前修改的 Linux 离屏环境测试与静态检查须通过。Windows 安装程序由 Windows x64 runner 构建，并验证 NSIS 源码 UTF-8 BOM、冻结应用目录及静默安装目录的识别依赖导入、快捷方式中文文件名、卸载注册表显示名、PE 文件、安装后 manifest 和应用启动。任一失败都阻止发布。

## Windows x64 构建

GitHub Actions 可手动运行 `Windows x64 installer`，或在 `main` 更新后自动运行。构建与安装验证全部成功后，将 v261127A 安装包和源码归档附加到 GitHub Release。

若在 Windows x64 环境自行构建，先运行 `tools/build_windows_app.sh` 所实现的等效 PyInstaller 收集步骤，再从 `installer/` 使用 NSIS 编译 `setup.nsi`。安装路径与用户数据路径保持原有兼容性，安装器不主动清除用户模型目录。

> 本工具由天琴工作室（Lyra Studio）出品 · 海舟（Lyrazhou）制作
> Email: fantasyland@163.com
