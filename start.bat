@echo off
chcp 65001 >nul
title 浴室智洗 BathSight - 一键启动
cd /d %~dp0

echo ==========================================
echo   浴室智洗 BathSight - 一键启动
echo ==========================================

REM 可选：启动后端（需要已安装 Redis + Python 依赖）
REM 取消下面两行的注释即可自动启动后端（uvicorn main:app --port 8000）
REM start "后端API" cmd /k "pip install -q fastapi uvicorn[standard] redis && uvicorn main:app --port 8000"

REM 首次运行自动安装前端依赖
if not exist "frontend\node_modules" (
    echo [1/2] 首次运行，正在安装前端依赖...
    cd frontend
    call npm install
    cd ..
)

echo [2/2] 正在启动前端开发服务器...
cd frontend
call npm run dev -- --host

pause
