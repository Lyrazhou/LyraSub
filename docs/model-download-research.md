# 模型下载调研记录

**日期：** 2026-08-20  
**用途：** 《弦字幕》（LyraSub）三档本地识别模型的多下载源设计。

## 结论

faster-whisper 在加载模型名称（例如 `base`、`small`、`medium`）时，会从 Hugging Face Hub 获取相应的 CTranslate2 转换模型。其下载实现本质上调用 `huggingface_hub.snapshot_download`，并要求取得 `config.json`、`preprocessor_config.json`、`model.bin`、`tokenizer.json` 与词表文件。[1]

当前 `snapshot_download` API 接受显式 `endpoint` 参数，因而《弦字幕》可以在每次下载时向 API 传入用户选择的模型源，而不依赖启动前设置全局环境变量。官方文档还明确指出，Hub 的缓存、下载超时等均可配置；客户端在导入后读取环境变量，所以应用内直接传入端点与超时参数更可控。[2]

本次对模型仓库元数据接口的连通性检查显示，`https://huggingface.co/api/models/Systran/faster-whisper-small` 与 `https://hf-mirror.com/api/models/Systran/faster-whisper-small` 均返回 HTTP 200。软件将把前者标为“官方源（适合可直连/VPN）”，把后者标为“国内镜像”，但不把任何外部镜像保证为永久可用；每次下载前都应在应用内测试连接。

## 首版下载档位

| 显示档位 | faster-whisper 模型 | CTranslate2 仓库 | 界面预计下载量 |
|---|---|---|---:|
| 快速 | `base` | `Systran/faster-whisper-base` | 约 150 MB |
| 平衡（默认） | `small` | `Systran/faster-whisper-small` | 约 470 MB |
| 高精度 | `medium` | `Systran/faster-whisper-medium` | 约 1.5 GB |

以上是下载前用于决策的近似值；实际大小以当前仓库版本、缓存状态和镜像响应为准。三个档位均可由用户分别下载、缓存和复用；补丁不删除或强制重下模型。

## 实现要点

1. 下载向导必须列出三档模型，而不是在点击识别时隐式下载。
2. 模型源提供“官方源”“国内镜像”“自定义地址”三个选项，自定义地址须以 `https://` 开头。
3. “测试连接”请求仓库元数据，不启动大文件下载；下载失败时把网络、HTTP、磁盘与缓存错误分开提示。
4. 应用直接调用 `snapshot_download(..., endpoint=...)`，下载成功后再以本地模型目录构造 `WhisperModel`，避免第二次隐式联网。
5. 新建的下载状态、源设置和缓存目录不写入 `.lyrasub` 项目文件，只保存为当前用户的本地偏好。

## 参考资料

[1] [faster-whisper 官方仓库：模型下载与本地模型目录加载](https://github.com/SYSTRAN/faster-whisper)

[2] [Hugging Face Hub 官方文档：缓存、下载超时和环境配置](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables)

## Windows 运行时复现（v260820C 修复前）

在与发布版一致的 Windows Python 运行时中，`requests` 可以成功访问国内镜像与官方源的模型 API，并可直接读取 `config.json` 和 `model.bin` 的首个数据块；两种来源均返回 HTTP 200。然而同一运行时使用 `huggingface_hub.snapshot_download(..., endpoint=...)` 下载仅 `config.json` 时，抛出 `FileMetadataError`，随后被包装为 `LocalEntryNotFoundError`。错误信息声称远端资源“不在 huggingface.co”，即使应用已经显式传入镜像端点。

这证明此前失败并非模型仓库地址失效，也不是用户没有联网，而是该 Windows 打包环境中 Hugging Face Hub 客户端对于解析后的重定向/Xet 文件元数据的校验不兼容。后续修复将绕过这一步：先由应用请求仓库清单，再用 `requests` 逐个访问 `resolve/main/<文件名>`，允许 HTTP 重定向、显示具体文件 URL/状态，并把网络、代理、磁盘和本地缓存错误分别报告。

## 逐文件下载器验证（v260820C）

在同一 Windows 运行环境中，使用新的逐文件 `requests` 下载器从 `https://hf-mirror.com` 完整下载“快速”模型成功。实际写入的 `model.bin` 直连重定向到模型文件存储地址后可连续读取，最终本地模型目录大小为 147,882,951 字节，并通过完整性检查。该验证确认新实现已经绕过此前 `huggingface_hub.snapshot_download` 的元数据校验故障。
