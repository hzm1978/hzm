@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv-studio\Scripts\python.exe" goto managed
if exist ".venv\Scripts\python.exe" goto local
where py >nul 2>nul
if not errorlevel 1 goto launcher
python bootstrap.py --install %*
goto finished
:managed
".venv-studio\Scripts\python.exe" bootstrap.py --install %*
goto finished
:local
".venv\Scripts\python.exe" bootstrap.py --install %*
goto finished
:launcher
py -3 bootstrap.py --install %*
:finished
set "TQ_STUDIO_EXIT=%ERRORLEVEL%"
if "%TQ_STUDIO_EXIT%"=="0" goto success
echo 未完成。请查看上方具体原因，安装日志为本目录 setup.log。
pause
exit /b %TQ_STUDIO_EXIT%
:success
pause
exit /b 0
