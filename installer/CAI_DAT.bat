@echo off
chcp 65001 >nul
title Cai dat Justin EDIT Suite
echo Dang khoi chay trinh cai dat (tu tai thanh phan con thieu)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
