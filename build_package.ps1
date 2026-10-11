# -*- coding: utf-8 -*-
# Builds the installable package: dist\JustinEditSuite_v<ver>\ + .zip
#   engine     <- G:\JustinEditSuite\engine           (code only: no caches, no NX_* outputs)
#   extension  <- %APPDATA%\Adobe\CEP\extensions\com.justin.editsuite   (without its stale engine copy)
#   installer  <- G:\JustinEditSuite\installer\*      (CAI_DAT.bat, install.ps1, install_mac.sh, ...)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Ver = (Get-Content (Join-Path $Root 'installer\VERSION.txt') | Select-Object -First 1).Trim()
$Ext = Join-Path $env:APPDATA 'Adobe\CEP\extensions\com.justin.editsuite'
$Out = Join-Path $Root "dist\JustinEditSuite_v$Ver"
$Zip = "$Out.zip"

if (Test-Path $Out) { Remove-Item -LiteralPath $Out -Recurse -Force }
if (Test-Path $Zip) { Remove-Item -LiteralPath $Zip -Force }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Copy-Tree($src, $dst, $xd, $xf) {
    # robocopy: exit codes < 8 = success
    $a = @($src, $dst, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
    if ($xd) { $a += '/XD'; $a += $xd }
    if ($xf) { $a += '/XF'; $a += $xf }
    & robocopy @a | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $src -> $dst failed ($LASTEXITCODE)" }
}

Copy-Tree (Join-Path $Root 'engine') (Join-Path $Out 'engine') @('__pycache__', '_nexys_work', 'NX_*', '*.egg-info', '.pytest_cache') @('*.pyc', '*.log')
Copy-Tree $Ext (Join-Path $Out 'extension\com.justin.editsuite') @('engine', 'node_modules', '__pycache__', '.git') @('.debug', '*.log')
Copy-Item -Path (Join-Path $Root 'installer\*') -Destination $Out -Recurse -Force
Copy-Item -Path (Join-Path $Root 'engine\requirements-ai.txt') -Destination $Out -Force

# Windows launchers need CRLF, shell scripts LF
Get-ChildItem $Out -Filter *.bat | ForEach-Object {
    $t = [IO.File]::ReadAllText($_.FullName) -replace "`r?`n", "`r`n"
    [IO.File]::WriteAllText($_.FullName, $t, (New-Object Text.UTF8Encoding $false))
}
Get-ChildItem $Out -Include *.sh, *.command -Recurse | ForEach-Object {
    $t = [IO.File]::ReadAllText($_.FullName) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($_.FullName, $t, (New-Object Text.UTF8Encoding $false))
}

# sanity: the package must contain what install.ps1 expects
foreach ($p in 'engine\nxcut.py', 'engine\nxcore\doctor.py', 'extension\com.justin.editsuite\CSXS\manifest.xml', 'install.ps1', 'CAI_DAT.bat') {
    if (-not (Test-Path (Join-Path $Out $p))) { throw "gói thiếu $p" }
}
for ($try = 1; $try -le 5; $try++) {          # antivirus / indexer may hold a freshly copied file for a moment
    try { Compress-Archive -Path "$Out\*" -DestinationPath $Zip -CompressionLevel Optimal -Force; break }
    catch { if ($try -eq 5) { throw }; Write-Host "  zip: file đang bị khóa, thử lại ($try)..." -ForegroundColor Yellow; Start-Sleep -Seconds 3 }
}
$n = (Get-ChildItem $Out -Recurse -File).Count
Write-Host ("Đã đóng gói v{0}: {1} file · {2:N1} MB → {3}" -f $Ver, $n, ((Get-Item $Zip).Length / 1MB), $Zip) -ForegroundColor Green
