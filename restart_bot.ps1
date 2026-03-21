# NCAA Trading Bot - Kill old instance and restart with updated code
# Also prevents Windows sleep mode

Write-Host "=== NCAA Bot Restart ===" -ForegroundColor Cyan

# 1. Kill any existing bot processes
Write-Host "Stopping old bot processes..." -ForegroundColor Yellow
$killed = 0
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like 'python*' -and $_.CommandLine -like '*bot.py*'
} | ForEach-Object {
    Write-Host "  Killing PID $($_.ProcessId): $($_.CommandLine)" -ForegroundColor Red
    Stop-Process -Id $_.ProcessId -Force
    $killed++
}
if ($killed -eq 0) { Write-Host "  No existing bot found." }

Start-Sleep -Seconds 2

# 2. Prevent sleep mode (AC + battery)
Write-Host "Disabling sleep/hibernate..." -ForegroundColor Yellow
powercfg -change -standby-timeout-ac 0
powercfg -change -hibernate-timeout-ac 0
powercfg -change -standby-timeout-dc 0
powercfg -change -hibernate-timeout-dc 0
Write-Host "  Sleep/hibernate disabled (AC + battery)."

# 3. Start bot in background
Write-Host "Starting updated bot..." -ForegroundColor Green
Set-Location $PSScriptRoot
Start-Process -FilePath "pythonw" -ArgumentList "bot.py" -WindowStyle Hidden
Start-Sleep -Seconds 2

# 4. Verify it's running
$proc = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like 'python*' -and $_.CommandLine -like '*bot.py*'
}
if ($proc) {
    Write-Host "Bot started! PID: $($proc.ProcessId)" -ForegroundColor Green
    Write-Host "Monitor via: bot.log, trades.csv" -ForegroundColor Cyan
} else {
    Write-Host "WARNING: Bot may not have started. Check for errors:" -ForegroundColor Red
    Write-Host "  Run 'python bot.py' in foreground to see errors" -ForegroundColor Red
}

Write-Host "`n=== Done ===" -ForegroundColor Cyan
