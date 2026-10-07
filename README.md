# MiniMax H3 Local

面向 Windows 的 MiniMax H3 本地视频生成适配，基于已有的 Wan2GP 推理环境。

**输入提示词 → H3 生成 → 临时预览 → 点击保存才保留。**

这是轻量适配仓库，不包含 Wan2GP 主程序、模型权重、Python 环境、生成视频或用户任务记录。不是 MiniMax 官方客户端。

## 功能

- 中文界面，整段提示词直接提交 H3，不调用分镜助手或提示词改写模型。
- 自定义时长，例如 30、60、120 秒；长视频使用 H3 原生滑动窗口续接。
- 三档画质，也可单独修改分辨率、采样步数和随机种子。
- 临时预览、停止生成、手动保存与下载。
- 未保存的任务自动清理；多标签页和刷新缓冲保护。
- 当前结果绑定本次任务，不自动用历史视频代替失败或消失的结果。
- RTX 5070 12GB 的 Profile 5 分阶段加载配置，以及现有 Heretic 编码器的 INT8 词嵌入兼容补丁。

| 画质 | 分辨率 | 步数 |
| --- | --- | --- |
| 快速预览 | 640×384 | 8 |
| 日常画质 | 640×384 | 12 |
| 精细画质（默认） | 832×480 | 20 |

## 安装到已有 Wan2GP

需要先有可正常运行 H3 的 Windows Wan2GP 安装，且虚拟环境为 `env_venv\Scripts\python.exe`。首次部署 Wan2GP 请按其项目的安装说明配置 CUDA、PyTorch、注意力后端和模型组件；本项目不会替你重装这些大体积依赖。

已验证的 Wan2GP 提交及设备见 [upstream.json](upstream.json)。其他版本的兼容性尚未验证；安装器遇到不认识的编码器源码会停止，不会强行替换。

```powershell
git clone https://github.com/Byeolyyy/minimax-h3-local.git
cd minimax-h3-local

# 把下面的路径换成你的 Wan2GP 目录。先关闭其 H3 后台。
python install.py --wan2gp "D:\AI\Wan2GP"
```

安装器会：

1. 检查目标目录、虚拟环境和上游编码器格式。
2. 将需要变更的已有文件备份到目标的 `deployment\backups` 下。
3. 复制界面、服务与启停脚本，添加或更新 `h3_local_heretic` 模型定义。
4. 关闭提示词增强、额外插件、模型预加载和长视频中间片段的保留。
5. 应用兼容补丁（已应用时跳过）。

它不会下载权重、运行推理或修改已有任务和输出。若目标环境缺少轻量 Web 依赖，另行执行：

```powershell
& "D:\AI\Wan2GP\env_venv\Scripts\python.exe" -m pip install -r requirements-ui.txt
```

模型组件的固定版本、路径、大小和大文件 SHA-256 见 [model-manifest.json](deployment/model-manifest.json)。仅在确实需要下载这些组件时运行安装后的 `deployment\download_h3.py`；总模型体积约 31 GB，不包含环境。权重需遵守各自发布方条款。

## 使用

在 **Wan2GP 目录** 双击 `Start-H3.bat`，访问 <http://127.0.0.1:7860>。填写提示词、秒数和画质后点击生成。用完运行 `Stop-H3.bat`。

H3 当前最低输入时长约 4.5 秒，24 fps，帧数按 `17n + 5` 取整，因此 120 秒会换算为约 119.92 秒。界面显示预计实际时长。长视频不是无限制保证：耗时、内存和磁盘需求会增加，画面和角色可能逐渐变化。

H3 使用的 Qwen3-VL-32B 是必需文本编码器；本适配不加载额外 Qwen3-1.7B 分镜模型。

### 保存与清理

- 完成后是临时预览；点击 **保存视频** 才永久保留在 `studio\jobs\任务ID\output`。
- 保存后可再下载到其他位置。
- 未保存内容在下一次生成、点击删除预览、关闭最后一个页面或退出程序时清理。
- 关闭页面有约 15 秒缓冲；连接异常失联约 3 分钟后清理。关闭所有页面也会取消未完成任务。
- 程序异常结束的临时任务在下次启动时清理。
- 更新前没有明确 `saved: false` 标记的历史文件不会自动删除。

### 结果与错误

当前任务会显示实际提交提示词、时长、分辨率和步数。旧作品仅在点击生成记录后显示，并标为历史任务。任务失败或已被清理时会明确提示，不回退播放旧例子。

## 后续开发与更新

在这个仓库中修改代码，提交 Git；停止 H3 后重新运行安装命令，将修改部署到 Wan2GP。当前使用目录和开发仓库分开，避免把模型、缓存和私人输入意外提交。

| 文件 | 用途 |
| --- | --- |
| `deployment/studio.html` | 界面、参数、当前任务选择及保存操作 |
| `deployment/studio_server.py` | 参数校验、H3 子进程、任务 API、页面存活检测 |
| `deployment/temporary_outputs.py` | 临时输出清理与单实例文件锁 |
| `deployment/launch_h3.py` | 本机代理绕过与 Wan2GP CLI 入口 |
| `deployment/patch_heretic.py` | 编码器兼容补丁 |
| `install.py` | 适配安装、配置合并及备份 |

## 测试（不生成视频）

```powershell
python -m pip install -r requirements-dev.txt
python deployment/test_direct.py
python test_install.py
node --check deployment/check_job_selection.js
```

若要同时验证上游的滑动窗口算法，设置 `H3_WAN2GP_ROOT` 为本机 Wan2GP 路径后运行接口测试。仓库不携带上游算法源码。

已启动安装后的本地界面且装有 Microsoft Edge 时，可运行 `python deployment/check_direct_ui.py` 检查浏览器布局和提交竞态。浏览器测试使用模拟提交，不触发视频生成。

GitHub Actions 在 Windows 上执行轻量回归与安装测试，不下载权重、不运行 GPU 推理。已有短视频生成曾在上述本机硬件验证；长片端到端画质没有保证。

## 来源与许可

本适配基于 [Wan2GP](https://github.com/deepbeepmeep/Wan2GP) 的 H3 集成和 CLI。兼容补丁涉及上游代码，保留 [WanGP Community License 2.0](LICENSE.txt) 及来源说明。它不是 MIT/Apache 许可的完整模型发行版，也不授予模型权重的额外使用权。其他组件各自遵循发布方许可。

- [MiniMax H3 GGUF](https://huggingface.co/unsloth/MiniMax-H3-GGUF)
- [H3 组件](https://huggingface.co/DeepBeepMeep/MiniMax-H3)
- [Heretic NVFP4 编码器](https://huggingface.co/sakamakismile/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4)
