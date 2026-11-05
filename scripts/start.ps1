# 啟動 SAgent。第一次執行會自動安裝套件、打包前端。
# 用法：
#   .\scripts\start.ps1                 # 一般啟動，開 http://127.0.0.1:8765
#   .\scripts\start.ps1 --fake-llm      # 示範模式，不接模型
#   .\scripts\start.ps1 --port 9000     # 換埠號
# 有 uv 就用 uv；沒有 uv 時改用 backend\.venv 裡的 Python。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    # 這台電腦自己的設定（例如 SAGENT_JAVA）寫在專案根目錄的 local.env，不進版控。
    # 一行一個「名稱=值」，# 開頭是註解；已經設定的環境變數不會被蓋掉。
    if (Test-Path "local.env") {
        foreach ($line in Get-Content "local.env" -Encoding UTF8) {
            $line = $line.Trim()
            if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
            $name, $value = $line.Split("=", 2)
            $name = $name.Trim()
            if (-not [Environment]::GetEnvironmentVariable($name)) {
                [Environment]::SetEnvironmentVariable($name, $value.Trim())
            }
        }
    }
    if (-not (Test-Path "frontend/dist/index.html")) {
        Write-Host "第一次啟動：安裝前端套件並打包…"
        Push-Location frontend
        npm install
        npm run build
        Pop-Location
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv run --project backend sagent serve @args
    }
    elseif (Test-Path "backend/.venv/Scripts/sagent.exe") {
        & "backend/.venv/Scripts/sagent.exe" serve @args
    }
    else {
        Write-Host "找不到 uv，也沒有 backend\.venv。請安裝 uv，或用 Python 3.11 以上建立 .venv："
        Write-Host "  python -m venv backend\.venv"
        Write-Host "  backend\.venv\Scripts\python.exe -m pip install -e backend pytest ruff"
        exit 1
    }
}
finally {
    Pop-Location
}
