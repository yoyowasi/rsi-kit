# Installs the RSI kit hooks for Claude Code: copies hooks/rsi*.py to <claude dir>/hooks
# and registers two hook groups (SessionStart, PreToolUse) in settings.json.
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1 [-ClaudeDir <dir>] [-Remove]
# -Remove only removes the settings.json registration (files and ~/.rsi-kit are left alone).
# settings.json is edited only when needed, after a .bak-<timestamp> copy.
param([string]$ClaudeDir = (Join-Path $HOME '.claude'), [switch]$Remove)
$ErrorActionPreference = 'Stop'

$python = $null
foreach ($candidate in 'py -3', 'python', 'python3') {
    $exe, $exeArgs = $candidate -split ' '
    $exeArgs = @($exeArgs | Where-Object { $_ })  # force an array: a scalar '-3' would be splatted char by char
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    & $exe @exeArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
    if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
}
if (-not $python) { throw 'Python 3.9+ not found as "py -3", "python" or "python3" (the Microsoft Store alias does not count). Install it and run again.' }
$exe, $exeArgs = $python -split ' '
$exeArgs = @($exeArgs | Where-Object { $_ })

$action = if ($Remove) { 'remove' } else { 'install' }
$setup = Join-Path $PSScriptRoot 'tools\setup.py'
if ($Remove) {
    & $exe @exeArgs $setup remove --claude-dir $ClaudeDir
} else {
    & $exe @exeArgs $setup install --python $python --claude-dir $ClaudeDir
}
if ($LASTEXITCODE -ne 0) { throw "tools\setup.py $action failed (exit $LASTEXITCODE)" }

if (-not $Remove) {
    $rsi = Join-Path $ClaudeDir 'hooks\rsi.py'
    Write-Host "Next: $python `"$rsi`" init   (then review ~/.rsi-kit/spec/rules.draft.json and rename it to rules.json)"
}
