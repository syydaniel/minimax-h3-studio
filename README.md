# H3 Studio

在 Apple Silicon Mac 上本地运行 [MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3)（有声视频生成模型）的一站式工具包：
推理引擎 [h3.c](https://github.com/antirez/h3.c) 的源码、一键安装和权重下载脚本，以及一个本地 Web 界面。

> 只支持 Apple Silicon（M 系列）Mac。h3.c 只有 Metal 后端。
> 作者实测环境：Apple M3 Max、128 GB 统一内存、macOS 27。

## 能做什么

- **四种生成模式**：文生视频、首帧生视频、首尾帧生视频、参考生视频（图片、视频、音频混合参考）
- **只用官方参数**：画幅只提供官方 768p 画布，时长只允许官方的 4 到 15 秒
- **Claude 一键优化提示词**：中文随手写想法，Claude 按 MiniMax 官方提示词规范改写成英文
- **实时进度与监控**：每个阶段的进度和耗时、剩余时间、日志，GPU、CPU、内存曲线
- **作品画廊**：预览、复用参数、下载、在访达中显示

## 快速开始

```bash
git clone https://github.com/syydaniel/minimax-h3-studio.git ~/h3-studio
cd ~/h3-studio
./install.sh                    # 检查环境、编译 h3.c、建 Python 环境
./scripts/download-weights.sh   # 下载模型权重，约 210 GB，可断点续传
./scripts/smoke-test.sh         # 可选：约 1 分钟的冒烟测试
./start.sh                      # 打开 http://127.0.0.1:7870
```

需要先装好 Xcode Command Line Tools 和 Homebrew。完整步骤、每个依赖的官方链接、硬件要求和常见问题见
**[docs/DEPLOY.md](docs/DEPLOY.md)**。

## 硬件与耗时参考

| 项目 | 要求 |
|---|---|
| 芯片 | Apple Silicon（M1 及以后）；M5 有额外的 Metal 4 加速路径 |
| 内存 | 建议 64 GB 以上。DiT 常驻约 37 到 45 GiB，另需系统和解码余量 |
| 磁盘 | 约 215 GB 可用空间（权重 210 GB） |
| 系统 | macOS 26 或更新（作者在 macOS 27 上验证） |

M3 Max 128 GB 上的实测（768p 为 1344×768）：

| 任务 | 耗时 |
|---|---|
| 256×256 冒烟测试，0.9 秒，4 步 | 约 35 秒 |
| 1344×768，0.9 秒，4 步 | 约 4 分钟 |
| 1344×768，10 秒，20 步，复用 2 + token 缩减 | 约 3.5 小时 |
| 参考生视频 768×768，4.5 秒，4 步，4 个参考素材 | 约 55 分钟 |

生成很慢是这类 33B 视频模型在笔记本上的常态，界面会显示预计剩余时间。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `install.sh` / `start.sh` | 安装、启动 |
| `scripts/download-weights.sh` | 下载并校验权重 |
| `scripts/smoke-test.sh` | 冒烟测试 |
| `webui/` | Web 界面（FastAPI 后端 + 原生 JS 前端） |
| `third_party/h3.c/` | h3.c 源码（MIT），已含 PR #1 修复，见 `UPSTREAM.md` |
| `tools/grid.py` | 画面网格伪影检测脚本 |
| `docs/DEPLOY.md` | 部署方案 |
| `docs/NOTES.md` | 工程记录：选型、踩坑、排查过程 |

## 许可

- 本仓库自己的代码：见 [LICENSE](LICENSE)
- h3.c：MIT，版权归 Salvatore Sanfilippo
- MiniMax-H3 权重：不随仓库分发，使用者需自行接受 [MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)
- 详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

## 内容限制

界面会拒绝把未成年人或儿童形象性化的提示词，既不生成，也不送去 Claude 优化。

## 致谢

- [antirez/h3.c](https://github.com/antirez/h3.c)：MiniMax-H3 的 Apple Silicon 原生推理引擎
- [MiniMax](https://huggingface.co/MiniMaxAI)：开放 MiniMax-H3 权重与提示词规范
- [Hugging Face diffusers](https://github.com/huggingface/diffusers)：官方画布算法参考
