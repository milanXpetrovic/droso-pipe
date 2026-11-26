# Starts the droso-pipe worker automatically on this PC: at logon, hidden (no window), restarted a minute after
# it stops (run-worker.ps1), logging to C:\w\worker.log.
# Run once, in a NORMAL PowerShell window (not "Run as administrator"): the worker must use the same NAS
# connection as you (net use \\TRUENAS\videos /user:worker), which an elevated window does not see.
#
#   powershell -ExecutionPolicy Bypass -File C:\droso-pipe\worker\install-task.ps1
#
# Running it again replaces the task and restarts the worker (e.g. after changing worker.toml).
# Remove:  Unregister-ScheduledTask -TaskName droso-worker -Confirm:$false

$ErrorActionPreference = "Stop"
$config = Join-Path $PSScriptRoot "worker.toml"
if (-not (Test-Path $config)) { throw "No $config yet: copy worker.example.toml to worker.toml and fill in the token." }
New-Item -ItemType Directory -Force C:\w | Out-Null
$null = (Get-Command python).Source          # fails here, not later in the hidden window, if python is missing

$user = "$env:USERDOMAIN\$env:USERNAME"
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSScriptRoot\run-worker.ps1`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName "droso-worker" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "droso-worker"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -like "*droso_worker.py*" |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 3
}
Register-ScheduledTask -TaskName "droso-worker" -Force -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "droso-pipe worker: cuts out arenas and tracks for the NAS. See worker\README.md" | Out-Null
Start-ScheduledTask -TaskName "droso-worker"
Start-Sleep -Seconds 8
Get-ScheduledTask -TaskName "droso-worker" | Select-Object TaskName, State | Format-Table
Get-Content C:\w\worker.log -Tail 2
Write-Host "Worker started (no window). Log: C:\w\worker.log  (Get-Content C:\w\worker.log -Wait)"
