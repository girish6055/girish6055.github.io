@echo off
setlocal enabledelayedexpansion
title INTECK AI Video Analytics - Windows build
cd /d "%~dp0"

echo ============================================================
echo  INTECK AI Video Analytics - Windows EXE build
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.11 or 3.12 ^(64-bit^) from python.org
    echo         and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

python -c "import sys; print('Python', sys.version); sys.exit(0 if sys.version_info[:2] >= (3,9) and sys.version_info[0]==3 else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.11 or 3.12 64-bit is required.
    pause
    exit /b 1
)

python -c "import struct,sys; sys.exit(0 if struct.calcsize('P')*8==64 else 1)"
if errorlevel 1 (
    echo [ERROR] A 64-bit Python is required ^(PyTorch has no 32-bit Windows build^).
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/6] Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 ( echo [ERROR] venv creation failed. & pause & exit /b 1 )
) else (
    echo [1/6] Reusing existing .venv
)

call ".venv\Scripts\activate.bat"

echo [2/6] Upgrading pip ...
python -m pip install --upgrade pip setuptools wheel

echo [3/6] Installing dependencies ^(PyTorch download is ~2 GB on first run^) ...
pip install -r requirements-build.txt
if errorlevel 1 ( echo [ERROR] Dependency install failed. & pause & exit /b 1 )

echo [4/6] Preparing YOLO11 weights ...
python setup_models.py
if errorlevel 1 ( echo [WARN] Weights were not downloaded; copy a .pt file into models\ manually. )

echo [5/6] Running self-tests ...
python -m unittest discover -s tests -t . -q
if errorlevel 1 ( echo [WARN] Self-tests reported a failure; continuing with the build. )

echo [6/6] Compiling the executable with PyInstaller ...
pyinstaller INTECK_AI_Analytics.spec --noconfirm
if errorlevel 1 ( echo [ERROR] PyInstaller build failed. & pause & exit /b 1 )

set "OUT=dist\INTECK_AI_Analytics"
echo Copying runtime files into %OUT% ...
if not exist "%OUT%\config"     mkdir "%OUT%\config"
if not exist "%OUT%\models"     mkdir "%OUT%\models"
if not exist "%OUT%\logs"       mkdir "%OUT%\logs"
if not exist "%OUT%\snapshots"  mkdir "%OUT%\snapshots"
if not exist "%OUT%\recordings" mkdir "%OUT%\recordings"
copy /y "config\config.json" "%OUT%\config\config.json" >nul
copy /y "config\config.example.json" "%OUT%\config\config.example.json" >nul 2>&1
copy /y "models\*.pt" "%OUT%\models\" >nul 2>&1
copy /y "README.md" "%OUT%\README.md" >nul 2>&1
copy /y "docs\OPERATIONS.md" "%OUT%\OPERATIONS.md" >nul 2>&1

echo.
echo ============================================================
echo  BUILD COMPLETE
echo.
echo  Executable : %CD%\%OUT%\INTECK_AI_Analytics.exe
echo  Config     : %CD%\%OUT%\config\config.json
echo.
echo  Edit config\config.json next to the .exe to set RTSP URLs
echo  and zones, then double-click the .exe. The dashboard opens
echo  at http://127.0.0.1:8080/
echo ============================================================
pause
