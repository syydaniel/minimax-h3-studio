# 工程记录

记录这个项目是怎么一步步做出来的：为什么这么选、踩过哪些坑、每个结论的依据。

## 1. 选型：为什么用 h3.c

MiniMax-H3 于 2026 年 7 月发布，是一个 33B 参数的有声视频生成模型，开放权重。可选的推理方案：

| 方案 | 平台 | 结论 |
|---|---|---|
| [SGLang Diffusion](https://github.com/sgl-project/sglang) | NVIDIA / AMD / 昇腾 | 官方推荐，最快（8×H200 上 5 秒视频约 40 秒），但不支持 Mac |
| [vLLM-Omni](https://github.com/vllm-project/vllm-omni) | NVIDIA | 同上 |
| [vPipe](https://github.com/tgo-app-dev/vpipe) | Apple Silicon | 能跑，但它的 int8 GEMM 和 SageAttention 加速只在 M5 上生效；设计目标是 16 GB 小内存，靠权重流式加载换内存，大内存机器上反而吃亏 |
| [h3.c](https://github.com/antirez/h3.c) | Apple Silicon | 原生 Metal 实现，明确支持 M3 Max，全常驻，任务类型齐全（文生视频、首尾帧、参考）。选定 |

## 2. 权重下载

- 模型仓库总计 **498 GB**，同时包含 SGLang 布局（`FL2VA/`、`Ref2VA/`）和 diffusers 布局（`transformer/`、`vae/` 等）。
  h3.c 只读前者
- 默认的 Xet 传输后端在作者网络下只有 **0.15 MB/s**（估算要 11 天），设置 `HF_HUB_DISABLE_XET=1` 走经典 CDN 后提到
  **5 到 8 MB/s**，约 42 倍
- `hf download --include "A" "B"` 里，第二个模式会被当成位置参数（文件名），请求字面路径 `B/%2A` 而 404
- 中途下 Ref2VA 时，`Ref2VA/transformer/model.safetensors.index.json` 会先落地。
  h3.c 启动时只检查这个文件是否存在，存在就认定 Ref2VA 已装，于是**所有任务**都报 "no safetensors files"
- 一开始以为 Ref2VA 只需要 transformer（依据是 `h3.c:424-447` 的启动检查），这是错的。
  实际生成时（`h3.c:921-930`）会读整个 Ref2VA 分区。按 HF 清单的 sha256 比对，Ref2VA 的 text_encoder、video_vae、
  audio_vae、processor、tokenizer 与 FL2VA 完全相同，于是用硬链接复用，只有 transformer 需要单独下载

## 3. 马赛克（平坦区域的网格）排查

**现象**：768p 成片的天空、雾气里有一格一格的细网格。

**排查步骤**：

1. 看编码参数：成片是 libx264、CRF 18，理论上接近无损
2. 周期分析：在避开主体的天空区域统计相邻像素跳变，横向在 8 和 16 像素边界上有明显峰值，纵向很弱
3. 查资料：h3.c 的 PR #1（https://github.com/antirez/h3.c/pull/1）报告了同类 16 像素网格，
   原因是 VAE 解码分块被自动选成 320 像素，而官方配置是 256；ComfyUI 那边也报告过 H3 的 VAE 解码实现存在网格问题
4. 复核 h3.c 的分块选择算法：所有官方 768p 画布都会被选成 304 或 320 像素，没有一个用 256。
   h3 日志里也打印了 `video VAE tiles 5x3 at 320 pixels`
5. 对照实验：同提示词、同 seed、1344×768、39 帧，用 `--frames-dir` 导出**编码前的原始帧**，
   一组用自动分块（320），一组设 `H3_VAE_TILE_PIXELS=256`

**结果**（`tools/grid.py`，16 像素边界跳变比，1.0 为无网格）：

| | 320 分块 | 256 分块 |
|---|---|---|
| 全画面 横向 | 1.326 | 1.235 |
| 全画面 纵向 | 1.135 | 1.106 |
| 原始帧 vs H.264 PSNR | 45.2 dB | 45.5 dB |

**结论**：

- 不是视频编码的问题，原始帧里就有
- 改用官方 256 分块后网格明显减轻，肉眼可见，但没有完全消失，剩余部分来自 h3.c 的 VAE 解码实现
- 两次运行之间 DiT 输出并非逐字节一致（第一组运行时 GPU 上还有另一个任务），所以两个因素没法完全拆开

本仓库的 h3.c 已应用 PR #1，界面也会显式传 `H3_VAE_TILE_PIXELS=256`。

## 4. 官方参数的出处

- **画布**：diffusers 的 MiniMax-H3 管线 `resolve_canvas_size`：短边 768，面积上限 768×1344，两边就近取整到 32 的倍数
  （32 = VAE 空间压缩 16 × DiT patch 2）。取整后面积可能略超上限（比如 2.39:1 会算出 1568×672），
  而 h3.c 会拒绝，所以本项目在超限时把长边按 32 递减
- **时长**：模型卡写的是 4 到 15 秒，SGLang 接口也限定 4 到 15 秒；帧数须为 5 + 17k，即 107 到 362 帧
- **h3.c 自己的下限**：每次生成至少一个 22 帧的解码块；参考视频的音轨要求至少 2 秒，
  而参考视频的帧数会被向下对齐到 5 + 17k（`h3_ffmpeg.c`），所以视频本身至少 2.34 秒
- **提示词格式**：模型仓库的 `docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`（三段式）与 `..._ref_en.md`（参考模式六段式）。
  h3.c 不会自动加首帧指令行，这一行要写在提示词里，Claude 优化会按规范加上

## 5. 性能参考（Apple M3 Max，128 GB）

| 形状 | 单次 DiT 前向 |
|---|---|
| 256×256 × 22 帧 | 约 2 秒 |
| 1344×768 × 39 帧 | 约 89 秒 |
| 1344×768 × 243 帧 | 约 18 分钟（复用加 token 缩减时的平均值） |

- 注意力开销随帧数超线性增长，按帧数线性外推会低估约一倍
- `--profile` 显示 M3 上大部分时间花在 CPU 编码 Metal 命令上，而不是 GPU 计算
- 参考素材会拼进序列，参考生视频明显更慢

## 6. Web 界面里值得注意的实现

- 进度来自 h3 的 stderr（格式 `\r%-25s %4d/%-4d`）和 `h3 profile:` 行
- h3 也用 `h3:` 前缀打印普通信息（分块方案、复用步数），所以只有进程非零退出时，最后一条 `h3:` 消息才算错误
- 界面发起的任务会等外部 h3 进程结束再开始，避免两个进程同时占 GPU
- 预览环境里在 `~/Desktop` 作为工作目录启动 Python，会卡在 `open()` 上（macOS 隐私保护），所以启动脚本先切换目录
- GPU 利用率来自 `ioreg` 的 AGXAccelerator 统计，不需要 sudo
