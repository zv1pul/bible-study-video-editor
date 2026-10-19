@echo off
rem Starts Bible Study Video Editor. Everything it needs is in this folder.
set "BSVE_HOME=%LOCALAPPDATA%\BibleStudyVideoEditor"
if not exist "%BSVE_HOME%" mkdir "%BSVE_HOME%"
start "" "%~dp0python\pythonw.exe" "%~dp0launcher.py"
