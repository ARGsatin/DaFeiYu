@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title 大肥鱼
rem 优先用项目里的 .venv（依赖都装在里面），没有才退回系统的 python
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo.
echo    ~~~ 大肥鱼 正在入水 ~~~
echo.
"%PY%" -m app
if errorlevel 1 pause
