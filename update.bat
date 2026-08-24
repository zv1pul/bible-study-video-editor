@echo off
REM Fetch the latest version and refresh the packages. Safe to run any time —
REM it never touches your API key or anything in assets\.
cd /d "%~dp0"

if not exist ".git" (
  echo This copy was not installed from the repository, so it cannot update
  echo itself. Download the latest version and replace this folder.
  pause
  exit /b 1
)

echo Checking for a newer version...
git pull --ff-only
if exist ".venv" .venv\Scripts\pip.exe install -q -r requirements-local.txt

echo.
echo Up to date. Launch the app with run.bat as usual.
pause
