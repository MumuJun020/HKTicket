#!/usr/bin/env bash
# 一键启动（macOS / Linux）
#
#     ./start.sh
#
# 第一次跑会自动建虚拟环境并装依赖，之后直接启动。
# 不需要先 cd 到项目目录，双击也能跑。

set -e
cd "$(dirname "$0")"

PY=venv/bin/python

if [ ! -x "$PY" ]; then
  echo "首次运行，正在创建虚拟环境..."
  python3 -m venv venv
  echo "正在安装依赖（大约一两分钟）..."
  venv/bin/pip install -q --upgrade pip
  venv/bin/pip install -q -r requirements.txt
  echo "环境准备完成。"
  echo
fi

# 依赖装没装全也检查一下：只建了 venv 但装依赖时中断过的话，
# 直接启动会抛 ImportError，报错很难看懂
if ! "$PY" -c "import flask, playwright, openpyxl" 2>/dev/null; then
  echo "依赖不完整，正在补装..."
  venv/bin/pip install -q -r requirements.txt
fi

exec "$PY" run.py
