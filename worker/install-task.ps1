# Starts the droso-pipe worker automatically on this PC: at logon, restarted if it stops, logging to C:\w\worker.log.
# Run once, in a NORMAL PowerShell window (not "Run as administrator"): the worker must use the same NAS
# connection as you (net use \\TRUENAS\videos /user:worker), which an elevated window does not see.
#
#   powershell -ExecutionPolicy Bypass -File C:\droso-pipe\worker\install-task.ps1
#
# Remove again:  Unregister-ScheduledTask -TaskName droso-worker -Confirm:$false

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$config = Join-Path $PSScriptRoot "worker.toml"
if (-not (Test-Path $config)) { throw "No $config yet: copy worker.example.toml to worker.toml and fill in the token." }
New-Item -ItemType Directory -Force C:\w | Out-Null

$python = (Get-Command python).Source
$run = "/c `"`"$python`" -u `"$repo\worker\droso_worker.py`" --config `"$config`" >> C:\w\worker.log 2>&1`""
$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $run -WorkingDirectory "$repo\worker"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "droso-worker" -Force -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "droso-pipe worker: cuts out arenas and tracks for the NAS. See worker\README.md" | Out-Null
Start-ScheduledTask -TaskName "droso-worker"
Start-Sleep -Seconds 5
Get-ScheduledTask -TaskName "droso-worker" | Select-Object TaskName, State | Format-Table
Write-Host "Worker started. Log: C:\w\worker.log  (Get-Content C:\w\worker.log -Wait)"
