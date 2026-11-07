@echo off
cd /d "%~dp0"
title ColdChain Guardian Server

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)

echo Starting ColdChain Guardian at http://127.0.0.1:5000
echo Keep this window open while using the application.
"%PYTHON_EXE%" -c "from app import app; app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)"

echo.
echo The server stopped. Review any error shown above.
pause
