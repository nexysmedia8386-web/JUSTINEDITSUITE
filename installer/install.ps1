# -*- coding: utf-8 -*-
# Justin EDIT Suite — Windows installer. Self-healing: whatever is missing is downloaded and installed (per user,
# no admin rights): Python 3.12, Python packages, ffmpeg, CUDA libraries, AI pack (when the PC has a GPU), models,
# Ollama + vision model (NVIDIA >= 8 GB VRAM). Safe to run again at any time: it only fills the gaps.
param(
    [ValidateSet('auto', 'yes', 'no')][string]$AI = 'auto',
    [ValidateSet('auto', 'yes', 'no')][string]$Ollama = 'auto',
    [switch]$NoModels
)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Host.UI.RawUI.WindowTitle = 'Justin EDIT Suite - Cai dat'

function Step($n, $t) { Write-Host "`n[$n] $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!] $t" -ForegroundColor Yellow }
function Info($t) { Write-Host "  $t" -ForegroundColor Gray }

$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Version = (Get-Content (Join-Path $Here 'VERSION.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $Version) { $Version = '2.1' }
Clear-Host
Write-Host '==========================================================' -ForegroundColor Green
Write-Host "   JUSTIN EDIT SUITE $Version - CAI DAT TU DONG" -ForegroundColor Green
Write-Host '   Thieu gi tu tai nay - chay lai bao nhieu lan cung duoc' -ForegroundColor Gray
Write-Host '==========================================================' -ForegroundColor Green

$Root = Join-Path $env:LOCALAPPDATA 'JustinEditSuite'
$EngineDst = Join-Path $Root 'engine'
$CepDir = Join-Path $env:APPDATA 'Adobe\CEP\extensions'
$ExtDst = Join-Path $CepDir 'com.justin.editsuite'

# ---------------------------------------------------------------- 1. Premiere: allow unsigned CEP panels
Step '1/5' 'Bat che do cho phep panel trong Premiere Pro...'
foreach ($v in 9..25) {
    $k = "HKCU:\Software\Adobe\CSXS.$v"
    try { if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }; Set-ItemProperty -Path $k -Name 'PlayerDebugMode' -Value '1' -Force } catch {}
}
Ok 'PlayerDebugMode = 1 (CSXS 9-25)'

# ---------------------------------------------------------------- 2. Python >= 3.10 (install 3.12 when missing)
Step '2/5' 'Tim / cai Python...'
function Test-Py($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    if ($exe -like '*\WindowsApps\*') { return $false }          # Microsoft Store alias, not a real Python
    $v = & $exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
    if (-not $v) { return $false }
    $p = $v.Trim().Split('.')
    return ([int]$p[0] -eq 3 -and [int]$p[1] -ge 10)
}
function Find-Py {
    $c = @()
    foreach ($n in 'python', 'python3') { $g = Get-Command $n -ErrorAction SilentlyContinue -All; if ($g) { $c += $g.Source } }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { foreach ($ver in '-3.12', '-3.11', '-3.13', '-3.10') { $e = & py $ver -c "import sys;print(sys.executable)" 2>$null; if ($e) { $c += $e.Trim() } } }
    foreach ($ver in '312', '311', '313', '310') {
        $c += Join-Path $env:LOCALAPPDATA "Programs\Python\Python$ver\python.exe"
        $c += "C:\Program Files\Python$ver\python.exe"
    }
    foreach ($e in $c) { if (Test-Py $e) { return $e } }
    return $null
}
$Py = Find-Py
if (-not $Py) {
    Warn 'Chua co Python 3.10+ -> dang cai Python 3.12 (cho rieng tai khoan nay)...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
        $Py = Find-Py
    }
    if (-not $Py) {
        $inst = Join-Path $env:TEMP 'python-3.12.10-amd64.exe'
        Info 'Tai Python tu python.org (~27 MB)...'
        try {
            Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $inst -UseBasicParsing
            Start-Process -FilePath $inst -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0' -Wait
        } catch { Warn "Tai Python loi: $($_.Exception.Message)" }
        $Py = Find-Py
    }
}
if (-not $Py) {
    Write-Host "`n  [X] Khong cai duoc Python tu dong. Cai tay Python 3.12 tai https://www.python.org/downloads/ (tich 'Add python.exe to PATH') roi chay lai file cai dat." -ForegroundColor Red
    exit 1
}
Ok "Python: $Py"

# ---------------------------------------------------------------- 3. Panel + engine files
Step '3/5' 'Chep panel va engine...'
New-Item -ItemType Directory -Force -Path $CepDir, $Root | Out-Null
foreach ($p in 'extension\com.justin.editsuite', 'engine') { if (-not (Test-Path (Join-Path $Here $p))) { Write-Host "  [X] Thieu thu muc $p trong goi cai dat" -ForegroundColor Red; exit 1 } }
if (Test-Path $ExtDst) { Remove-Item -LiteralPath $ExtDst -Recurse -Force }
Copy-Item -Path (Join-Path $Here 'extension\com.justin.editsuite') -Destination $ExtDst -Recurse -Force
# engine: replace code, keep nothing stale (caches live in each source folder's _nexys_work, not here)
if (Test-Path $EngineDst) { Remove-Item -LiteralPath $EngineDst -Recurse -Force }
Copy-Item -Path (Join-Path $Here 'engine') -Destination $EngineDst -Recurse -Force
Ok "Panel  -> $ExtDst"
Ok "Engine -> $EngineDst"

# ---------------------------------------------------------------- 4. Everything else: nxcore.doctor installs what is missing
Step '4/5' 'Kiem tra va tu cai cac thanh phan con thieu (co the mat vai phut lan dau)...'
$dargs = @('-m', 'nxcore.doctor', '--fix', '--ai', $AI, '--ollama', $Ollama, '--settings', $EngineDst)
if (-not $NoModels) { $dargs += '--models' }
Push-Location $EngineDst
& $Py @dargs 2>&1 | ForEach-Object {
    $l = "$_"
    if ($l.StartsWith('DOCTOR ')) { $script:DoctorJson = $l.Substring(7) }
    elseif ($l -match '^\s*\u2717') { Write-Host $l -ForegroundColor Red }
    elseif ($l -match '^\s*\u2193') { Write-Host $l -ForegroundColor Yellow }
    else { Write-Host $l }
}
$rc = $LASTEXITCODE
Pop-Location

# ---------------------------------------------------------------- 5. Done
Step '5/5' 'Ket qua'
if ($rc -eq 0) {
    Ok 'Da du thanh phan. Mo Premiere Pro -> Window -> Extensions -> Justin EDIT Suite.'
    Info 'Neu Premiere dang mo: tat han roi mo lai de nap panel moi.'
} else {
    Warn 'Con thanh phan chua cai duoc (xem dong mau do o tren). Kiem tra mang roi chay lai file cai dat - no chi cai phan con thieu.'
}
Info "Log he thong: chay lai bat cu luc nao, hoac trong panel: Cai dat -> Kiem tra he thong -> Tu sua."
exit $rc
