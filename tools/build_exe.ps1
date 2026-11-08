# 打包大肥鱼桌面版：python -m PyInstaller + 把 .env / 旧记忆放到 exe 旁边
# 用法：powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $root

# 优先用项目里的 .venv；PyInstaller 和运行依赖都得装在里面
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = 'python' }

Write-Host ''
Write-Host '  ~~~ 正在打包 DaFeiYu.exe ~~~' -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean DaFeiYu.spec | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 失败了，看上面的输出' }

$out = Join-Path $root 'dist\DaFeiYu'
$exe = Join-Path $out 'DaFeiYu.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "没找到 $exe" }

# exe 旁边放 .env：第一次运行会搬到 %APPDATA%\大肥鱼，之后就用那份
if (Test-Path -LiteralPath '.env') {
    Copy-Item -LiteralPath '.env' -Destination (Join-Path $out '.env') -Force
    Write-Host '  已带上 .env（首次运行会搬到 %APPDATA%\大肥鱼）' -ForegroundColor Green
}

# 旧记忆也带一份，第一次运行一起搬过去
$db = Join-Path $root 'data\fish.db'
if (Test-Path -LiteralPath $db) {
    $dst = Join-Path $out 'data'
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    foreach ($suffix in '', '-wal', '-shm') {
        $src = "$db$suffix"
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $dst "fish.db$suffix") -Force
        }
    }
    Write-Host '  已带上 data/fish.db（首次运行会搬过去）' -ForegroundColor Green
}

$size = (Get-ChildItem -LiteralPath $out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ''
Write-Host ("  好了：{0}" -f $exe) -ForegroundColor Green
Write-Host ("  整个文件夹 {0:N1} MB，双击 DaFeiYu.exe 就能用" -f $size) -ForegroundColor Green
Write-Host ''
