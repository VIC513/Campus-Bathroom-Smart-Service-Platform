#!/bin/bash
# 浴室智洗 BathSight - 一键启动（macOS / Linux）
# 用法：在项目根目录执行 ./start.sh

set -e
cd "$(dirname "$0")"

echo "=========================================="
echo "  浴室智洗 BathSight - 一键启动"
echo "=========================================="

# 可选：启动后端（需要 Redis + Python 依赖）
# 取消下面注释即可自动启动后端（uvicorn main:app --port 8000）
# (redis-server &) ; pip install -q fastapi 'uvicorn[standard]' redis && uvicorn main:app --port 8000 &

# 首次运行自动安装前端依赖
if [ ! -d "frontend/node_modules" ]; then
    echo "[1/2] 首次运行，正在安装前端依赖..."
    (cd frontend && npm install)
fi

echo "[2/2] 正在启动前端开发服务器..."
cd frontend && npm run dev -- --host
