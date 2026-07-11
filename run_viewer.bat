@echo off
setlocal
cd /d "%~dp0"

set "JSON_FILE=%~1"

if "%JSON_FILE%"=="" (
  if exist "backend_unit.json" (
    set "JSON_FILE=backend_unit.json"
  ) else if exist "outputs\backend_unit.json" (
    set "JSON_FILE=outputs\backend_unit.json"
  ) else (
    echo JSON file not found.
    echo.
    echo Place backend_unit.json in the repository root or outputs folder,
    echo or pass the JSON path explicitly:
    echo   run_viewer.bat D:\path\to\backend_unit.json
    pause
    exit /b 1
  )
)

if not exist "%JSON_FILE%" (
  echo JSON file not found: %JSON_FILE%
  pause
  exit /b 1
)

D:\anaconda3\envs\code_trans\python.exe code_unit_viewer.py "%JSON_FILE%"
endlocal
