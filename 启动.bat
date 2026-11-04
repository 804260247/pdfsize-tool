@echo off
setlocal
cd /d "%~dp0"
title PDF Page Size Tool

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

%PY% -c "import pymupdf" >nul 2>nul
if errorlevel 1 (
  echo [INFO] First run: installing pymupdf ...
  %PY% -m pip install pymupdf
  if errorlevel 1 goto failed
)

%PY% "pdfsize_gui.py" --check >nul 2>nul
if errorlevel 1 (
  echo [WARN] Self-check failed, starting with console window to show details:
  %PY% "pdfsize_gui.py"
  echo.
  pause
  exit /b 1
)

where pythonw >nul 2>nul
if errorlevel 1 (
  %PY% "pdfsize_gui.py"
  exit /b 0
)

start "" pythonw "pdfsize_gui.py"
exit /b 0

:failed
echo.
echo [FAILED] Cannot start. See messages above.
echo.
pause
exit /b 1
