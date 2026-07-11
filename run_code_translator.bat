@echo off
setlocal
cd /d "%~dp0"

if defined CODE_TRANS_PYTHON (
  set "PYTHON_EXE=%CODE_TRANS_PYTHON%"
) else if exist "D:\anaconda3\envs\code_trans\python.exe" (
  set "PYTHON_EXE=D:\anaconda3\envs\code_trans\python.exe"
) else (
  set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" code_translator_app.py
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
