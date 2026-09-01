@echo off
title Stop Skybrush Server
echo Stopping Skybrush Server...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*skybrushd*' }); if ($p.Count -eq 0) { Write-Host 'No running Skybrush Server was found.' } else { foreach ($x in $p) { try { Stop-Process -Id $x.ProcessId -Force -ErrorAction Stop; Write-Host ('Stopped process ' + $x.ProcessId) } catch { } }; Write-Host 'Skybrush Server stopped.' }"
echo.
pause
