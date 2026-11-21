# 《弦字幕 Plus》（LyraSub Plus）v261121A 源码包

本归档保存《弦字幕 Plus》（LyraSub Plus）v261121A 的可维护源码、测试、Windows 安装脚本、构建工具和操作文档。基础版在任何对比或引用中统一写作 **《弦字幕（LyraSub）》**。

## v261121A 更新

校对导出中的开始/结束时间支持 `HH:MM:SS.mmm`（时:分:秒.毫秒）输入与显示。新增“上条结束时间”和“下条开始时间”快捷设置，分别设为相邻字幕结束时间 +1ms、开始时间 −1ms；不存在相邻条目时禁用对应按钮。快捷值若突破时间围栏或产生无效时长，会提示并拒绝修改，不会自动更改当前行另一端。工程内部仍以毫秒整数存储时间。

## 本包包含

| 内容 | 说明 |
|---|---|
| `src/` | 应用核心源码，包括主界面、工程、音视频识别、LLM、翻译、SRT 与分段合并逻辑。 |
| `tests/` | 单元测试和 Qt 离屏界面回归测试。 |
| `.github/workflows/windows-installer.yml` | Windows x64 完整安装程序构建、静默安装及启动检查工作流。仅上传 14 天有效的 Actions artifact，不创建 GitHub Release。 |
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

当前修改的 Linux 离屏环境测试与静态检查须通过。Windows 安装程序需由 Windows x64 runner 构建，并执行 PE 文件、静默安装、安装后 manifest 和启动冒烟验证。该环境不支持本地 Windows x64 打包时，不得复用旧版本安装器冒充 v261121A。

## Windows x64 构建

GitHub Actions 可手动运行 `Windows x64 installer`，或在 `main` 更新后自动运行。完成后从对应 workflow run 下载 `v261121A-windows-x64-installer` 临时 artifact。流程不创建 Release，也不把安装包发布至 GitHub Release；临时 artifact 保留 14 天。

若在 Windows x64 环境自行构建，先运行 `tools/build_windows_app.sh` 所实现的等效 PyInstaller 收集步骤，再从 `installer/` 使用 NSIS 编译 `setup.nsi`。安装路径与用户数据路径保持原有兼容性，安装器不主动清除用户模型目录。

> 本工具由天琴工作室（Lyra Studio）出品 · 海舟（Lyrazhou）制作
> Email: fantasyland@163.com
