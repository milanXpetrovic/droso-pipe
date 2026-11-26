# Keeps the droso-pipe worker running on a Windows PC: started hidden (no window to close by mistake) by the
# droso-worker task (install-task.ps1), restarts the worker a minute after it stops. Log: C:\w\worker.log
New-Item -ItemType Directory -Force C:\w | Out-Null
$env:DW_PYTHON = (Get-Command python).Source
$env:DW_WORKER = Join-Path $PSScriptRoot "droso_worker.py"
$env:DW_CONFIG = Join-Path $PSScriptRoot "worker.toml"
while ($true) {
    # cmd keeps the log plain text (PowerShell 5.1 would write UTF-16); --% passes the line to cmd as it is
    cmd /c --% ""%DW_PYTHON%" -u "%DW_WORKER%" --config "%DW_CONFIG%" >> C:\w\worker.log 2>&1"
    Add-Content -Path C:\w\worker.log -Encoding ascii -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') worker stopped, restarting in 60 s"
    Start-Sleep -Seconds 60
}
