@echo off
setlocal
cd /d "%~dp0"

D:\anaconda3\envs\code_trans\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  exit /b 1
)

call run_viewer.bat "%~1"
endlocal
