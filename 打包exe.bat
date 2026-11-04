@echo off
setlocal
cd /d "%~dp0"
title Build PDF Page Size Tool

set "PY="
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
  py -3 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  echo.
  echo [ERROR] Python 3.8+ not found.
  echo         Install from https://www.python.org/downloads/
  echo         and check "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

echo [1/2] Installing build dependencies ...
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto failed

echo.
echo [2/2] Building exe ...
%PY% build_exe.py %*
if errorlevel 1 goto failed

echo.
echo [OK] Done. Output: dist folder
echo.
pause
exit /b 0

:failed
echo.
echo [FAILED] Build failed. Read the messages above.
echo.
pause
exit /b 1
