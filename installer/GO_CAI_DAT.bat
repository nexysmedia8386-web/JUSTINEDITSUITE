@echo off
chcp 65001 >nul
title Go cai dat Justin EDIT Suite
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
pause
