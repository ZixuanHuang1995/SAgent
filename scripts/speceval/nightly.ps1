# SpecEval 排程（研究計劃 C-16）：每晚跑 tiny-4，週末跑 full-12，結果目錄用日期命名。
#
# 跑之前檢查 SAgent 有沒有 AI 工作在跑或排隊（兩邊會互搶 CPU，結果和時間都不準），有的話這次不跑。
# run_suite.py 執行期間會讓電腦保持喚醒；排程時段也要避開會把電腦關機、重開的時間。
# 任務卡（speceval/tasks）要先用 build_tasks.py 產生、人審過（reviewed_by_human: true）再排程。
#
# 用 Windows 工作排程器註冊（在 PowerShell 執行一次；路徑換成自己的專案位置）：
#   schtasks /Create /TN "SAgent SpecEval tiny" /SC DAILY /ST 01:00 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\AI\playground\SAgent\scripts\speceval\nightly.ps1 -Suite tiny"
#   schtasks /Create /TN "SAgent SpecEval full" /SC WEEKLY /D SAT /ST 01:00 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\AI\playground\SAgent\scripts\speceval\nightly.ps1 -Suite full"
# 取消：schtasks /Delete /TN "SAgent SpecEval tiny" /F
#
# 手動試跑（假模型，幾秒鐘）：.\scripts\speceval\nightly.ps1 -Suite tiny -Fake

param(
    [ValidateSet("tiny", "full")]
    [string] $Suite = "tiny",
    [string] $Model = "qwen3.6:35b-a3b",
    [string] $SAgentUrl = "http://127.0.0.1:8765",
    [string] $Ollama = "http://127.0.0.1:11434",
    [string] $Tasks = "speceval/tasks",
    [string] $OutDir = "",
    [switch] $Fake
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $root
try {
    try {
        $projects = Invoke-RestMethod -Uri "$SAgentUrl/api/projects" -TimeoutSec 10
        $busy = 0
        foreach ($p in @($projects)) { $busy += [int]$p.running_runs + [int]$p.queued_runs }
        if ($busy -gt 0) {
            Write-Host "SAgent 有 $busy 個 AI 工作在跑或排隊，這次不跑"
            exit 0
        }
    } catch {
        Write-Host "連不到 SAgent（$SAgentUrl），當成沒有其他 AI 工作，照樣跑"
    }

    if (-not (Test-Path (Join-Path $Tasks "*.yaml"))) {
        throw "找不到任務卡：$Tasks。先用 scripts/speceval/build_tasks.py 產生，人審過再排程"
    }

    if (-not $OutDir) { $OutDir = "speceval-out/nightly-$(Get-Date -Format 'yyyyMMdd')-$Suite" }
    New-Item -ItemType Directory -Force $OutDir | Out-Null
    $runArgs = @("scripts/speceval/run_suite.py", "--tasks", $Tasks, "--suite", $Suite, "--model", $Model,
        "--ollama", $Ollama, "--out-dir", $OutDir)
    if ($Fake) { $runArgs += "--fake" }

    # 沒有 uv 時用 backend\.venv（見 AGENTS.md 的環境注意事項）
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $exe = "uv"
        $runArgs = @("run", "--project", "backend", "python") + $runArgs
    } else {
        $exe = Join-Path $root "backend\.venv\Scripts\python.exe"
    }
    Write-Host "開始 SpecEval $Suite：$OutDir"
    $env:PYTHONIOENCODING = "utf-8"
    & $exe @runArgs 2>&1 | Tee-Object -FilePath (Join-Path $OutDir "run.log")
    if ($LASTEXITCODE -ne 0) { throw "run_suite.py 結束代碼 $LASTEXITCODE，紀錄在 $OutDir\run.log" }
    Write-Host "完成。比較兩次：python scripts/analyze_runs.py --speceval <前一次的目錄> --speceval $OutDir"
} finally {
    Pop-Location
}
