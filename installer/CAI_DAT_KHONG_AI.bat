@echo off
chcp 65001 >nul
title Cai dat Justin EDIT Suite (ban nhe, khong goi AI)
echo Ban nhe: khong cai PyTorch / goi AI / Ollama (tiet kiem ~10 GB). Cai them sau trong panel: Cai dat - Kiem tra he thong.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -AI no -Ollama no
echo.
pause
