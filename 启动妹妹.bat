@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "FISH_HOME=%~dp0sister"
set FISH_NO_BROWSER=1
title 大肥鱼的妹妹 · 鱼丸
echo.
echo    ~~~ 鱼丸 正在入水 ~~~
echo.
echo    她自己的面板：http://127.0.0.1:8766
echo    这个窗口别关，关了妹妹就睡了
echo.
rem 优先用项目里的 .venv（依赖都装在里面），没有才退回系统的 python
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
rem 先把 sister/.env 补出来（端口、NapCat 地址都从主配置推）
"%PY%" -m app.sister ensure >nul 2>&1
"%PY%" -m app
if errorlevel 1 pause
