@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3.12 -m venv .venv
if not errorlevel 1 goto install
py -3 -m venv .venv
if not errorlevel 1 goto install
python -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -c "import sys;sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,14) else 1)"
if errorlevel 1 goto version_error
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo 安装完成。请运行启动工作台.bat。
pause
exit /b 0
:version_error
echo 需要 Python 3.11至3.13，推荐64位3.12。请安装后重新创建本软件的.venv。
pause
exit /b 1
:failed
echo 安装未完成，请检查Python及网络。错误信息见上方。
pause
exit /b 1
