@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 请先关闭期货复盘训练程序，再安装或更新所需组件。
set "FUTURES_PYTHON=python"
if exist "%~dp0..\.venv\Scripts\python.exe" set "FUTURES_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "FUTURES_PYTHON=%~dp0.venv\Scripts\python.exe"
"%FUTURES_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo 安装未完成，请检查 Python 和网络后重试。
    pause
    exit /b 1
)
echo 安装完成。请双击“启动程序.bat”，点击“免费实时”，首次在本机填写天勤账号；勾选“实时跟随”查看最新K线。
pause
