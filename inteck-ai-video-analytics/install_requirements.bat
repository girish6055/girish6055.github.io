@echo off
title INTECK - install dependencies
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
python setup_models.py
echo.
echo Dependencies installed. Run run_from_source.bat to start.
pause
