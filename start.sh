#!/bin/bash
# 启动 H3 Studio: http://127.0.0.1:${PORT:-7870}（默认只监听本机）
cd "$(dirname "$0")"
[ -x webui/.venv/bin/python ] || { echo "请先运行 ./install.sh"; exit 1; }
[ -x third_party/h3.c/h3 ] || [ -n "${H3_BIN:-}" ] || { echo "h3 还没编译，请先运行 ./install.sh"; exit 1; }
PORT="${PORT:-7870}"
export PORT
( sleep 1.5; open "http://127.0.0.1:$PORT" ) &
# 从 webui 目录启动：macOS 在受保护目录下作为工作目录时，可能挂起 Python 的文件访问
cd webui && exec ./.venv/bin/python server.py
