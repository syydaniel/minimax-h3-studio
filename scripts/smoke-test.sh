#!/bin/bash
# 冒烟测试：256x256、22 帧、4 步，约 40 秒（首次运行多一些冷加载时间）。
# 只验证引擎和权重能端到端出片，不代表画质；正式生成请用界面里的官方画布。
set -euo pipefail
cd "$(dirname "$0")/.."
H3="${H3_BIN:-$PWD/third_party/h3.c/h3}"
MODEL="${H3_MODEL_DIR:-$PWD/models/MiniMax-H3}"
if pgrep -x h3 >/dev/null; then echo "已有 h3 进程在运行，等它结束再测"; exit 1; fi
mkdir -p outputs
"$H3" --info -d "$MODEL"
cd "$(dirname "$H3")"
time "$H3" --profile -d "$MODEL" \
  -p "integrated_multimodal_description: [Shot 1] Live-action, cinematic, a red fox walks through fresh snow in a pine forest. The camera tracks alongside at slow speed.

overall_soundscape: Soft wind and crunching snow.

non_diegetic_music: N/A" \
  --width 256 --height 256 --frames 22 --steps 4 --layers 50 --reuse 1 --seed 42 \
  -o "$OLDPWD/outputs/smoke.mp4" 2>&1 | grep -E "h3 profile|h3:"
ffprobe -v error -show_entries stream=codec_type,width,height,nb_frames -of csv=p=0 "$OLDPWD/outputs/smoke.mp4"
echo "通过：outputs/smoke.mp4"
