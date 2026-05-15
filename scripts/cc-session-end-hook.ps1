# Claude Code SessionEnd hook (Windows PowerShell)
$transcript = $env:CLAUDE_CODE_TRANSCRIPT_PATH
if (-not $transcript -and $args.Count -gt 0) { $transcript = $args[0] }
if (-not $transcript) { exit 0 }

$memoryd = (Get-Command memoryd -ErrorAction SilentlyContinue).Source
if (-not $memoryd) { $memoryd = "memoryd" }

try {
    & $memoryd capture --client claude-code --transcript $transcript 2>&1 | Out-Null
} catch {
    # best-effort; never block CC
}

# Fork sync export --auto. memoryd internally honors [sync] enabled +
# auto_export_on_session_end gates, so this silently no-ops when not configured.
try {
    Start-Process -FilePath $memoryd -ArgumentList "sync","export","--auto" `
        -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
} catch {
    # best-effort
}
exit 0
