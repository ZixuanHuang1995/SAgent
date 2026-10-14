# 啟動 SAgent。第一次執行會自動安裝套件、打包前端。
# 用法：
#   .\scripts\start.ps1                 # 一般啟動，開 http://127.0.0.1:8765
#   .\scripts\start.ps1 --fake-llm      # 示範模式，不接模型
#   .\scripts\start.ps1 --port 9000     # 換埠號

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not (Test-Path "frontend/dist/index.html")) {
        Write-Host "第一次啟動：安裝前端套件並打包…"
        Push-Location frontend
        npm install
        npm run build
        Pop-Location
    }
    uv run --project backend sagent serve @args
}
finally {
    Pop-Location
}
