# ESP32-S3-BOX 一键烧录（Windows 端，绕开 WSL USB 限制）
# 用法：
#   1. 设备 BOOT+RESET 进下载模式（变 1001/COM5）
#   2. 在 WSL 里运行：bash /home/muyue/esp/pc_status_display/tools/flash.sh
#   或在 Windows PowerShell 里运行这个 .ps1
# 脚本会自动：unbind（让 Windows 认设备）→ esptool 烧录 → hard_reset 重启

$ErrorActionPreference = "Continue"
$Python = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }
$BinDir = "C:\Windows\Temp"

Write-Host "=== ESP32-S3-BOX 烧录 ===" -ForegroundColor Cyan

# 1. unbind 设备（如果被 usbipd 管理）
Write-Host "[1/3] 解除 usbipd 绑定（让 Windows 认设备）..." -ForegroundColor Yellow
Start-Process -FilePath "usbipd" -ArgumentList "unbind","--busid","1-2" -Verb RunAs -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# 2. 找 ESP 的 COM 口
$ports = [System.IO.Ports.SerialPort]::GetPortNames()
$espPort = $null
foreach ($p in $ports) {
    if ($p -match "COM\d+") {
        $espPort = $p
        break
    }
}
# 优先找 COM5（ESP JTAG/serial 的默认编号）
if ($ports -contains "COM5") { $espPort = "COM5" }
if (-not $espPort) {
    Write-Host "未发现 COM 口！请确认设备已 BOOT+RESET 进下载模式，且已 unbind。" -ForegroundColor Red
    exit 1
}
Write-Host "[2/3] 使用端口: $espPort" -ForegroundColor Yellow

# 3. 烧录
Write-Host "[3/3] 烧录中..." -ForegroundColor Yellow
& $Python -m esptool --chip esp32s3 -p $espPort --before default_reset --after hard_reset `
    write_flash 0x0 (Join-Path $BinDir "bootloader.bin") `
                0x8000 (Join-Path $BinDir "partition-table.bin") `
                0x10000 (Join-Path $BinDir "pc_status_display.bin")

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n=== 烧录成功！按 RESET（不按 BOOT）正常启动 ===" -ForegroundColor Green
} else {
    Write-Host "`n=== 烧录失败（exit $LASTEXITCODE）===" -ForegroundColor Red
}
