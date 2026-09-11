#!/bin/bash
# 下载 MiniMax-H3 权重到 models/MiniMax-H3（或 $H3_MODEL_DIR）。可随时 Ctrl-C，重跑自动续传。
#
# 只下载 h3.c 实际读取的部分，不下载仓库里另一套 diffusers 布局（约 288 GB）：
#   FL2VA/*               144 GB   文生视频、首帧、首尾帧
#   Ref2VA/transformer     66 GB   参考生视频
# Ref2VA 其余子目录（text_encoder、video_vae、audio_vae 等）与 FL2VA 逐字节相同，
# 脚本按 HF 清单的 sha256 核对后用硬链接复用，不重复下载、不多占磁盘。
set -uo pipefail
cd "$(dirname "$0")/.."
REPO=$PWD
DEST="${H3_MODEL_DIR:-$REPO/models/MiniMax-H3}"
HF="$REPO/webui/.venv/bin/hf"
PY="$REPO/webui/.venv/bin/python"
[ -x "$HF" ] || { echo "请先运行 ./install.sh"; exit 1; }
# Xet 传输后端在部分网络下极慢（实测 0.15 MB/s，经典 CDN 5 到 8 MB/s）
export HF_HUB_DISABLE_XET=1

mkdir -p "$DEST"
FREE_GB=$(df -g "$DEST" | awk 'NR==2{print $4}')
echo "目标目录: $DEST   可用空间: ${FREE_GB} GB（需要约 215 GB）"
[ "$FREE_GB" -ge 215 ] || [ -f "$DEST/FL2VA/transformer/config.json" ] || { echo "磁盘空间不足"; exit 1; }

# hf download 的 --include 每次只接受一个模式，多给的会被当成文件名
fetch() {
  until "$HF" download MiniMaxAI/MiniMax-H3 --local-dir "$DEST" --max-workers 16 --include "$1"; do
    echo "[$(date +%T)] 中断，10 秒后续传..." >&2; sleep 10
  done
}

echo; echo "== 1/4 FL2VA（144 GB）=="
fetch "FL2VA/*"

echo; echo "== 2/4 Ref2VA transformer（66 GB）=="
# 不能直接下进模型目录：index.json 会先于权重落地，h3.c 见到它就认为 Ref2VA
# 已安装，所有任务（包括文生视频）都会报 "no safetensors files"。先下到暂存区。
if [ ! -f "$DEST/Ref2VA/transformer/model.safetensors.index.json" ]; then
  STAGE="$DEST/.ref2va-staging"
  until "$HF" download MiniMaxAI/MiniMax-H3 --local-dir "$STAGE" --max-workers 16 --include "Ref2VA/transformer/*"; do
    echo "[$(date +%T)] 中断，10 秒后续传..." >&2; sleep 10
  done
  mkdir -p "$DEST/Ref2VA" && mv "$STAGE/Ref2VA/transformer" "$DEST/Ref2VA/transformer" && rm -rf "$STAGE"
fi
fetch "Ref2VA/tokenizer/*"
fetch "Ref2VA/model_index.json"

echo; echo "== 3/4 Ref2VA 其余子目录：与 FL2VA 相同的用硬链接 =="
"$PY" - "$DEST" <<'PY'
import json, os, sys, urllib.request
dest = sys.argv[1]
api = json.load(urllib.request.urlopen("https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3?blobs=true", timeout=60))
sha = {s["rfilename"]: ((s.get("lfs") or {}).get("sha256") or s.get("blobId")) for s in api["siblings"]}
linked = need = 0
for name, h in sha.items():
    if not name.startswith("Ref2VA/") or name.startswith(("Ref2VA/transformer/", "Ref2VA/tokenizer/")):
        continue
    target = os.path.join(dest, name)
    if os.path.exists(target):
        continue
    twin = "FL2VA/" + name[len("Ref2VA/"):]
    src = os.path.join(dest, twin)
    if sha.get(twin) == h and os.path.exists(src):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.link(src, target); linked += 1
    else:
        need += 1
print(f"硬链接 {linked} 个文件，需另行下载 {need} 个")
PY
# 与 FL2VA 不同或 FL2VA 里没有的 Ref2VA 文件（目前为 0 个，官方若更新会走这里）
for f in $("$PY" - "$DEST" <<'PY'
import json, os, sys, urllib.request
dest = sys.argv[1]
api = json.load(urllib.request.urlopen("https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3?blobs=true", timeout=60))
for s in api["siblings"]:
    n = s["rfilename"]
    if n.startswith("Ref2VA/") and not os.path.exists(os.path.join(dest, n)):
        print(n)
PY
); do fetch "$f"; done

echo; echo "== 4/4 按 HF 清单核对 =="
"$PY" - "$DEST" <<'PY'
import json, os, sys, urllib.request
dest = sys.argv[1]
api = json.load(urllib.request.urlopen("https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3?blobs=true", timeout=60))
bad = 0
for part in ("FL2VA/", "Ref2VA/"):
    files = [s for s in api["siblings"] if s["rfilename"].startswith(part)]
    ok = 0
    for s in files:
        p = os.path.join(dest, s["rfilename"])
        if os.path.exists(p) and (not s.get("size") or os.path.getsize(p) == s["size"]):
            ok += 1
        else:
            bad += 1; print("  缺失或不完整:", s["rfilename"])
    print(f"{part:8s} {ok}/{len(files)} 个文件完整")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && echo "权重就绪。下一步: ./scripts/smoke-test.sh 或 ./start.sh"
