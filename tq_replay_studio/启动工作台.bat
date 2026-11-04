@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto install_first
".venv\Scripts\python.exe" run.py
if errorlevel 1 pause
exit /b
:install_first
echo 请先运行本目录的安装依赖.bat，创建独立运行环境。
pause
exit /b 1
