# -*- coding: utf-8 -*-
# Removes the panel, the engine and the portable ffmpeg. Keeps: settings / chats (%APPDATA%\JustinEditSuite),
# Python packages, downloaded models, and every source folder's _nexys_work analysis cache.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$targets = @(
    (Join-Path $env:APPDATA 'Adobe\CEP\extensions\com.justin.editsuite'),
    (Join-Path $env:LOCALAPPDATA 'JustinEditSuite')
)
foreach ($p in $targets) {
    if ($p -and (Test-Path $p)) { Remove-Item -LiteralPath $p -Recurse -Force; Write-Host "  [OK] Da xoa $p" -ForegroundColor Green }
}
Write-Host "`nDa go Justin EDIT Suite. Cai dat ca nhan (%APPDATA%\JustinEditSuite) va cache phan tich trong cac thu muc source van duoc giu." -ForegroundColor Green
