# Claude Code SessionStart hook for memoryd (Windows PowerShell).
#
# CC pipes our stdout into additionalContext, so the model has a
# "who the user is" briefing before the first turn. Failures must be
# silent — emit empty stdout and log to a file under the data root.

$ErrorActionPreference = "Continue"

$dataRoot = $env:MEMORYD_DATA_ROOT
if (-not $dataRoot) {
    $dataRoot = Join-Path $HOME ".local\share\memoryd"
}
$logDir = Join-Path $dataRoot "logs"
$logFile = Join-Path $logDir "cc-session-start.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-LogLine([string]$msg) {
    try {
        $stamp = (Get-Date).ToUniversalTime().ToString("o")
        Add-Content -Path $logFile -Value "[$stamp] $msg"
    } catch {
        # never let logging break the hook
    }
}

$memoryd = $env:MEMORYD_BIN
if (-not $memoryd) {
    $cmd = Get-Command memoryd -ErrorAction SilentlyContinue
    if ($cmd) { $memoryd = $cmd.Source }
}
if (-not $memoryd) {
    Write-LogLine "memoryd binary not found; skipping inject"
    Write-Output ""
    exit 0
}

$projectDir = $env:CLAUDE_PROJECT_DIR
if (-not $projectDir) { $projectDir = $HOME }

try {
    Push-Location $projectDir -ErrorAction SilentlyContinue

    $args = @(
        "inject",
        "--scope=auto",
        "--max-chars=1500",
        "--top-entities=8",
        "--recent=5"
    )

    # Run with a 5s timeout (Start-Process gives us Wait-Job semantics).
    $job = Start-Job -ScriptBlock {
        param($bin, $argList)
        & $bin @argList
    } -ArgumentList $memoryd, $args

    if (Wait-Job $job -Timeout 5) {
        $out = Receive-Job $job
        if ($null -ne $out) { Write-Output $out }
    } else {
        Stop-Job $job -ErrorAction SilentlyContinue
        Write-LogLine "inject timed out (>5s); emitting empty"
        Write-Output ""
    }
    Remove-Job $job -Force -ErrorAction SilentlyContinue
} catch {
    Write-LogLine "inject raised: $($_.Exception.Message); emitting empty"
    Write-Output ""
} finally {
    Pop-Location -ErrorAction SilentlyContinue
}

exit 0
