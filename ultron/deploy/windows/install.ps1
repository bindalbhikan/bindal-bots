# Windows: install the agent in a virtualenv and run it as a scheduled task that starts at logon
# and restarts when it stops.
#   cd <the folder with agent.toml and .env>
#   powershell -ExecutionPolicy Bypass -File ultron\deploy\windows\install.ps1
$ErrorActionPreference = "Stop"
$Dir = (Get-Location).Path
if (-not (Test-Path "$Dir\agent.toml")) { throw "run this in the folder that holds agent.toml" }
if (-not (Test-Path "$Dir\.env")) { Write-Host "note: no .env here yet - put the secrets in $Dir\.env (see ultron\README.md)" }

$py = (Get-Command py -ErrorAction SilentlyContinue) ? "py" : "python"
& $py -m venv .venv
& "$Dir\.venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& "$Dir\.venv\Scripts\pip.exe" install "./ultron[full]"
& "$Dir\.venv\Scripts\ultron.exe" check

$action = New-ScheduledTaskAction -Execute "$Dir\ultron\deploy\windows\run_agent.bat" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "Ultron" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "Ultron"
Write-Host "running. log: $Dir\output\ultron.log   stop: Stop-ScheduledTask Ultron"
