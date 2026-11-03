@echo off
cd /d "%~dp0"
set "FUTURES_PYTHON=python"
if exist "%~dp0..\.venv\Scripts\python.exe" set "FUTURES_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "FUTURES_PYTHON=%~dp0.venv\Scripts\python.exe"
"%FUTURES_PYTHON%" run.py
if errorlevel 1 pause
