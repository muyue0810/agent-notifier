@echo off
REM Agent Notifier CLI - Windows 启动脚本
REM 用法: tools\box_cli.bat done Codex "answer ready"
REM 或:   tools\box_cli.bat  (进入交互模式)
REM
REM 前提: Windows 已装 Python 和 pyserial
REM   pip install pyserial
REM
REM 设备无需通过 WSL/usbipd，Windows 直接访问 COM 口

setlocal
set SCRIPT_DIR=%~dp0
python "%SCRIPT_DIR%box_cli.py" %*
endlocal
