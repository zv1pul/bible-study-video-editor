@echo off
REM Windows launcher: sets up a virtual environment on first run,
REM then starts the app in your browser. Double-click this file.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" during the install.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo First run: creating the virtual environment ^(a few minutes^)...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\pip.exe install -r requirements-local.txt
)

echo Starting the Bible Study Video Editor...
.venv\Scripts\python.exe -m streamlit run app.py
pause
