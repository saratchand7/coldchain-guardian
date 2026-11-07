@echo off
setlocal
cd /d "%~dp0"
title ColdChain Guardian Launcher

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python was not found. Install Python or create the project virtual environment first.
    pause
    exit /b 1
  )
  set "PYTHON_EXE=python"
)

echo Starting ColdChain Guardian in a separate server window...
start "ColdChain Guardian Server - KEEP OPEN" /D "%~dp0" "%PYTHON_EXE%" -m flask --app app run --host=127.0.0.1 --port=5000 --no-debugger --no-reload
if errorlevel 1 (
  echo The server process could not be started.
  pause
  exit /b 1
)

echo Waiting for http://127.0.0.1:5000 ...
for /L %%I in (1,1,30) do (
  curl.exe --silent --fail --max-time 1 http://127.0.0.1:5000/ >nul 2>&1
  if not errorlevel 1 goto ready
  timeout /t 1 /nobreak >nul
)

echo The server did not respond within 30 seconds.
echo Review the separate "ColdChain Guardian Server" window for the exact error.
pause
exit /b 1

:ready
echo Server is ready. Opening the dashboard...
start "" "http://127.0.0.1:5000/"
exit /b 0
