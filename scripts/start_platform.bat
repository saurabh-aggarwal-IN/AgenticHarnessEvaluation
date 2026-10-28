@echo off
setlocal
cd /d "%~dp0"
title Clinical Coding Agent Evals Platform - Launcher
echo Launching Clinical Coding Agent Evals Platform...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_platform.ps1" %*
endlocal
