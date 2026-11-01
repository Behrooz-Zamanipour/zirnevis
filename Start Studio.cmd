@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Studio setup is required. Run Install Studio.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" launch.py
if errorlevel 1 (
  echo.
  echo The studio could not start. Check the message above.
  pause
)
