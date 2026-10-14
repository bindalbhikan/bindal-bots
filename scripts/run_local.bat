@echo off
REM Runs one bot once on this PC with the project .env:  scripts\run_local.bat ajio-tat --xlsx
cd /d "%~dp0.."
if not exist output mkdir output
python run.py %* >> output\local_run.log 2>&1
