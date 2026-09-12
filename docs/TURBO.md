# Turbo 加速（可选）

Turbo 是把「步数蒸馏 LoRA」提前折进权重，让 20 步的活 5 到 8 步干完。
h3.c 没有 LoRA 运行时，也不需要：LoRA 就是 `W' = W + scale * (B @ A)`，
可以离线烘进 checkpoint，推理时零额外开销。

装不装都行。不装，界面里 Turbo 一档显示「未安装」且点不动，其余功能不受影响。

## 需要多少磁盘

Turbo 是一份独立的模型目录，但用 APFS 克隆（`cp -cR`）建立，只有真正被改写的
文件才占新空间。实测：被折叠的分片里 97.7% 的块与原件不同，其余（文本编码器、
两个 VAE、分词器）与原权重逐字节相同、共享磁盘块。

| | 表观大小 | 真正新增 |
|---|---:|---:|
| FL2VA/transformer | 62 GB | 约 61 GB |
| Ref2VA/transformer | 62 GB | 约 61 GB |
| 其余（text_encoder、video_vae、audio_vae 等） | 144 GB | 约 0 |
| 合计 | 268 GB | **约 121 GB** |

只做文生视频、首帧、首尾帧的话，可以只折 FL2VA，新增约 61 GB。

## 用到的两个 LoRA

| 用途 | 来源 | 许可 | 步数 |
|---|---|---|---|
| FL2VA（文生视频、首帧、首尾帧） | [larryvrh/MiniMax-H3-Turbo-Lora](https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora) 的 `minimax_h3_turbo_v4_step600_ema.safetensors` | Apache-2.0 | 5 到 8（作者建议 4 起，4 步在快动作上有拖影，5 是实测下限） |
| Ref2VA（参考生视频） | [lightx2v/Minimax-h3-Turbo](https://huggingface.co/lightx2v/Minimax-h3-Turbo) 的 `minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors` | Apache-2.0 | 8 |

两个 LoRA 都是 MiniMax-H3 的衍生物，除各自的 Apache-2.0 外，仍受
[MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) 约束。

## 参考生视频的 Turbo 有额外前提

lightx2v 的 768p Ref2VA 适配器是按 video sigma shift = 6 训练的，而上游 h3.c 把
这个值写死为 12，没有开关。所以：

- 本仓库自带的 `third_party/h3.c` **不支持**参考生视频的 Turbo。
- 界面会用 `strings` 探测 h3 二进制里有没有 `H3_VIDEO_SHIFT`，没有就把参考模式的
  Turbo 标成「未安装」，而不是设一个不起作用的环境变量后照跑。
- 想用的话，需要自己给 h3.c 打补丁，让
  `h3_video_sigma_shift()` 读取 `H3_VIDEO_SHIFT`（默认 12），
  重新 `make` 之后界面会自动放开。

文生视频、首帧、首尾帧的 Turbo 没有这个限制，开箱即用。

## 构建步骤

以下假设仓库在 `~/h3-studio`，原始权重已经下载到 `models/MiniMax-H3`。
折叠过程只读原权重、另写新目录，不会改动原始权重。

```bash
cd ~/h3-studio
mkdir -p models/loras && cd models/loras
HF_HUB_DISABLE_XET=1 uv run --with huggingface_hub hf download \
  larryvrh/MiniMax-H3-Turbo-Lora minimax_h3_turbo_v4_step600_ema.safetensors --local-dir .
```

用 APFS 克隆建出 Turbo 目录（瞬间完成，先不占额外空间）：

```bash
cd ~/h3-studio/models
cp -cR MiniMax-H3 MiniMax-H3-turbo
```

把 FL2VA 的 LoRA 折进去（覆盖克隆出来的 transformer）：

```bash
cd ~/h3-studio
python3 tools/fold_turbo_lora.py \
  --checkpoint models/MiniMax-H3/FL2VA/transformer \
  --lora models/loras/minimax_h3_turbo_v4_step600_ema.safetensors \
  --out models/MiniMax-H3-turbo/FL2VA/transformer
```

只做上面这步，文生视频、首帧、首尾帧的 Turbo 就可用了。

参考生视频还要多两步（并且需要上一节说的引擎补丁）。lightx2v 的 LoRA 是
diffusers/PEFT 的 key 空间，先转成 h3.c 的原始 key 空间再折：

```bash
cd ~/h3-studio/models/loras
HF_HUB_DISABLE_XET=1 uv run --with huggingface_hub hf download \
  lightx2v/Minimax-h3-Turbo minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors --local-dir .

cd ~/h3-studio
python3 tools/convert_lightx2v_lora.py \
  models/loras/minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors \
  models/loras/ref2v_8step_768p_h3raw.safetensors --check

python3 tools/fold_turbo_lora.py \
  --checkpoint models/MiniMax-H3/Ref2VA/transformer \
  --lora models/loras/ref2v_8step_768p_h3raw.safetensors \
  --out models/MiniMax-H3-turbo/Ref2VA/transformer
```

建好之后重启界面即可，模型一档会出现「Turbo 加速」。
目录位置可以用 `H3_TURBO_MODEL_DIR` 改到别处。

## 使用限制（界面会强制）

- 步数：文生视频/首帧/首尾帧 5 到 8 步，参考生视频固定 8 步。
- 不能与 `reuse`、`core-reuse`、token 缩减同用。蒸馏后的采样计划没有这些手段
  要利用的冗余，同时开会明显掉质量，所以服务端直接拒绝而不是悄悄忽略。
- 分辨率、时长的官方限制与原版完全一致。

## 实测（Apple M3 Max，128 GB）

| 任务 | 原版 | Turbo |
|---|---:|---:|
| 864×480（480p 16:9），4.5 秒 | 未测 | 5 步约 13 分钟（含加载） |
| 参考生视频 1024×768，10 秒，4 个参考素材 | 20 步约 8.7 小时 | 8 步约 2.7 小时（3.2 倍） |

质量上要诚实：参考生视频那组，Turbo 版的锐度（取 12 帧算 Laplacian 方差均值）
是 3.04，原版 20 步是 4.50，低约 32%，画面内容也有偏移（同一 seed 下主体转向不同）。
音频响度基本一致（-24.7 对 -24.0 LUFS）。
所以 Turbo 适合出草稿、试镜头、赶时间，终稿建议还是用原版步数。

## 出处与许可

- `tools/fold_turbo_lora.py` 来自 [antirez/h3.c PR #14](https://github.com/antirez/h3.c/pull/14)
  「Distilled 5-6 step sampling via folded Turbo LoRA, plus three fixes」，作者 Guy Zyskind（`guyz`），
  MIT（随 h3.c）。本仓库内为逐字节未修改的副本；该 PR 截至收录时尚未合并进上游。
- `tools/convert_lightx2v_lora.py` 是本仓库自己的代码，按根目录 `LICENSE` 发布。
  其中的 key 空间对应关系参考了 Hugging Face diffusers 的
  `convert_minimax_h3_to_diffusers.py`（Apache-2.0），并与本地权重逐字节校验过。
