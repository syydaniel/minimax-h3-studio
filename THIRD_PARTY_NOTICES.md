# 第三方组件与许可

本仓库自己的代码（`webui/`、`scripts/`、`tools/`、安装与启动脚本、文档）按根目录 `LICENSE` 发布。
以下组件不属于本仓库作者，各自遵循原许可。

## h3.c（随仓库分发）

- 位置：`third_party/h3.c/`
- 上游：https://github.com/antirez/h3.c ，作者 Salvatore Sanfilippo（antirez）
- 许可：MIT，原文见 [`third_party/h3.c/LICENSE`](third_party/h3.c/LICENSE)
- h3.c 内含的第三方代码（改编自 ccv 的 Metal FlashAttention，BSD-3-Clause）见
  [`third_party/h3.c/THIRD_PARTY_NOTICES.md`](third_party/h3.c/THIRD_PARTY_NOTICES.md)
- 版本与本地改动见 [`third_party/h3.c/UPSTREAM.md`](third_party/h3.c/UPSTREAM.md)

## MiniMax-H3 模型权重（不随仓库分发）

- 模型页：https://huggingface.co/MiniMaxAI/MiniMax-H3
- 许可：MiniMax H3 Community License，原文 https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE
- 许可问答：https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/QA-about-License.md
- 权重约 210 GB，由 `scripts/download-weights.sh` 从 Hugging Face 直接下载到使用者本机。
  使用前请自行阅读并接受上述许可，商用尤其要看清条款。

## MiniMax-H3 官方提示词规范（不随仓库分发）

- 来源：模型仓库的 `docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md` 与 `docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`
- 由 `install.sh` 下载到 `webui/guides/`，供 Claude 优化功能作为系统提示使用

## vPipe（不随仓库分发，可选）

- 上游：https://github.com/tgo-app-dev/vpipe
- 许可：Apache-2.0
- 本仓库只提供编译与接入说明（[`docs/VPIPE.md`](docs/VPIPE.md)），不分发其源码或二进制。
  其中记录了一处 macOS 27 上必需的本地改动（三个 .metal 文件里 `bfloat16` 名字有歧义），
  该改动施加在使用者自己的 vPipe 检出上。

## 步数蒸馏 LoRA（不随仓库分发，可选）

只有按 [`docs/TURBO.md`](docs/TURBO.md) 自行构建 Turbo 权重时才需要下载。
两者都是 MiniMax-H3 的衍生物，除各自许可外仍受 MiniMax H3 Community License 约束。

- FL2VA：https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora ，Apache-2.0
- Ref2VA：https://huggingface.co/lightx2v/Minimax-h3-Turbo ，Apache-2.0

## LoRA 折叠脚本（随仓库分发）

- 位置：`tools/fold_turbo_lora.py`
- 上游：https://github.com/antirez/h3.c/pull/14 ，作者 Guy Zyskind（`guyz`）
- 许可：MIT（随 h3.c，版权归 Salvatore Sanfilippo）
- 本仓库内为逐字节未修改的副本；该 PR 截至收录时尚未合并进 h3.c 上游

## 画布算法

`webui/server.py` 的 `official_canvas()` 与 `webui/static/create.js` 的 `resolveCanvas()`
移植自 Hugging Face diffusers 的 MiniMax-H3 管线（Apache-2.0）：
https://github.com/huggingface/diffusers/tree/main/src/diffusers/modular_pipelines/minimax_h3
（`modular_pipeline.py` 中的 `resolve_canvas_size`）

## Python 依赖

见 `webui/requirements.txt`，安装时从 PyPI 获取，各自遵循其许可。
