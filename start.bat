@echo off
chcp 65001 >nul
rem 一键启动（Windows）
rem
rem     双击本文件即可
rem
rem 第一次跑会自动建虚拟环境并装依赖，之后直接启动。

cd /d "%~dp0"

set PY=venv\Scripts\python.exe

if not exist "%PY%" (
    echo 首次运行，正在创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo 创建失败。请先安装 Python 3.10 或更高版本，
        echo 安装时记得勾选 "Add Python to PATH"。
        echo 下载地址：https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo 正在安装依赖（大约一两分钟）...
    venv\Scripts\pip.exe install -q --upgrade pip
    venv\Scripts\pip.exe install -q -r requirements.txt
    echo 环境准备完成。
    echo.
)

rem 依赖装没装全也检查一下：只建了 venv 但装依赖时中断过的话，
rem 直接启动会抛 ImportError，报错很难看懂
"%PY%" -c "import flask, playwright, openpyxl" 2>nul
if errorlevel 1 (
    echo 依赖不完整，正在补装...
    venv\Scripts\pip.exe install -q -r requirements.txt
)

"%PY%" run.py
pause
