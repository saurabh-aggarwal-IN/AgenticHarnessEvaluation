@echo off
setlocal
cd /d "%~dp0"
title Clinical Coding Agent Evals Platform - Stopper
echo Stopping Clinical Coding Agent Evals Platform...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_platform.ps1" %*
endlocal
