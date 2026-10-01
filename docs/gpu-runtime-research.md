# 《弦字幕》GPU 运行时调研结论

## 结论

GTX 1060 是 Pascal 架构显卡，计算能力为 6.1；因此它并不因硬件类型本身而必然无法用于 CUDA 推理。当前《弦字幕》使用 faster-whisper 与 CTranslate2 的 Windows GPU 后端，GPU 初始化还依赖 Windows 驱动、CUDA 运行库、cuBLAS、cuDNN 与 Visual C++ 运行时。若这些组件缺失、版本与 CTranslate2 wheel 不匹配，或 NVIDIA 驱动过旧，`ctranslate2.get_cuda_device_count()` 和模型载入会失败，即使设备管理器可以正常显示 GTX 1060。

## 识别后端约束

| 项目 | 结论 | 对应用的处理 |
|---|---|---|
| CTranslate2 Windows wheel | 支持 GPU 执行，官方安装说明要求 CUDA 12.x | 自检应明确报告 CUDA DLL 是否可加载。 |
| faster-whisper 当前 CTranslate2 | 官方 README 指出新版本使用 CUDA 12 与 cuDNN 9 | 诊断应检查 `cublas64_12.dll`、`cublasLt64_12.dll`、`cudnn64_9.dll`。 |
| Windows 运行时 | CTranslate2 文档要求 Visual C++ runtime | 诊断显示运行库加载失败的系统错误。 |
| GPU 计算类型 | faster-whisper 支持 `float16` 与 `int8_float16` 的 CUDA 路径 | 应优先 `float16`，载入失败时保留底层异常并给出 CPU 回退。 |

## 修复策略

应用应新增可复制的“GPU 自检”输出，包括：NVIDIA 设备/驱动（来自 `nvidia-smi`）、CTranslate2 检测到的 CUDA 设备数、关键 DLL 加载结果、当前应用目录与 PATH、推荐运行模式及原始异常。GPU 初始化失败时，界面不得只显示笼统“初始化失败”，而应提示用户复制诊断或选择 CPU 临时工作。

## 参考资料

[1] [CTranslate2 安装文档](https://opennmt.net/CTranslate2/installation.html)

[2] [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)

[3] [NVIDIA CUDA GPU 计算能力说明](https://developer.nvidia.com/cuda-gpus)
