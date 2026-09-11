#!/bin/bash
# H3 Studio 安装：检查环境、编译 h3.c、建 Python 环境、拉取官方提示词规范。
# 可重复运行。模型权重另用 scripts/download-weights.sh 下载。
set -euo pipefail
cd "$(dirname "$0")"
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\n[错误] %s\n' "$*" >&2; exit 1; }

say "检查环境"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || die "h3.c 只支持 Apple Silicon Mac（M 系列芯片）"
xcode-select -p >/dev/null 2>&1 || die "缺少 Xcode Command Line Tools，请先运行: xcode-select --install"
command -v brew >/dev/null || die "缺少 Homebrew，安装方法见 https://brew.sh"
command -v ffmpeg >/dev/null || { say "安装 ffmpeg"; brew install ffmpeg; }
command -v uv >/dev/null || { say "安装 uv"; brew install uv; }
MEM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
echo "芯片: $(sysctl -n machdep.cpu.brand_string)  内存: ${MEM_GB} GB"
[ "$MEM_GB" -ge 64 ] || echo "[提醒] 内存低于 64 GB。DiT 常驻约需 37 到 45 GiB，768p 长片段可能不够，见 docs/DEPLOY.md"
case "$PWD" in
  "$HOME/Desktop"*|"$HOME/Documents"*|*"Mobile Documents"*)
    echo "[提醒] 仓库放在桌面/文稿/iCloud 目录下。这些目录受 macOS 隐私保护或会同步，建议移到 ~/h3-studio 这类普通目录" ;;
esac

say "编译 h3.c（third_party/h3.c，已含 PR #1 VAE 分块修复）"
make -C third_party/h3.c -j"$(sysctl -n hw.ncpu)" h3 >/dev/null
./third_party/h3.c/h3 --help >/dev/null && echo "h3 编译完成"

say "创建 Python 环境 webui/.venv"
[ -x webui/.venv/bin/python ] || uv venv --python 3.13 webui/.venv
uv pip install --python webui/.venv/bin/python -r webui/requirements.txt

say "拉取 MiniMax 官方提示词规范（Claude 优化功能要用）"
mkdir -p webui/guides
for n in base ref; do
  curl -fsSL "https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/main/docs/VIDEO_PROMPT_WRITING_GUIDE_${n}_en.md" \
    -o "webui/guides/${n}.md"
done
echo "已保存到 webui/guides/"

say "安装完成"
cat <<EOF
下一步:
  1. 下载模型权重（约 210 GB，可断点续传）:  ./scripts/download-weights.sh
  2. 可选，冒烟测试（约 1 分钟）:             ./scripts/smoke-test.sh
  3. 启动界面:                               ./start.sh
EOF
