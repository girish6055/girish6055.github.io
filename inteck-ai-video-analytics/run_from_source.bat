@echo off
title INTECK AI Video Analytics
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python run.py %*
pause
