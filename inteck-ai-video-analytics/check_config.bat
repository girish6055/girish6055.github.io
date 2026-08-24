@echo off
title INTECK - configuration check
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python run.py --check-config
pause
