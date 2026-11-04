@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto failed
".venv\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --console --name TqReplayStudio --collect-all tqsdk --hidden-import tq_workbench.sdk_worker --hidden-import tq_workbench.history_worker --hidden-import tq_workbench.trade_worker run.py
if errorlevel 1 goto failed
echo 生成于 dist\TqReplayStudio，分发时须保留整个文件夹。
pause
exit /b 0
:failed
echo 请先安装依赖，再检查上方构建错误。本构建脚本需在Windows本机运行。
pause
exit /b 1
