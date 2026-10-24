@echo off
REM Runs the agent from the folder that holds agent.toml (the scheduled task starts it there),
REM restarting it 10 seconds after it stops. Log: output\ultron.log
if not exist output mkdir output
set PYTHONUNBUFFERED=1
:loop
".venv\Scripts\ultron.exe" serve >> output\ultron.log 2>&1
timeout /t 10 /nobreak > nul
goto loop
