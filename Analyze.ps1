param(
    [switch]$IncludeClose,
    [switch]$Rescan,
    [string]$Player,
    [string]$GameRoot,
    [string]$DataDir,
    [string]$Python,
    [string[]]$InputPath
)
$ErrorActionPreference = 'Stop'
$taskArguments = @((Join-Path $PSScriptRoot 'analysis/analyze.py'))
if ($IncludeClose) { $taskArguments += '--include-close' }
if ($Rescan) { $taskArguments += '--rescan' }
if ($Player) { $taskArguments += @('--player', $Player) }
if ($GameRoot) { $taskArguments += @('--game-root', $GameRoot) }
if ($DataDir) { $taskArguments += @('--data-dir', $DataDir) }
foreach ($taskInput in $InputPath) { $taskArguments += @('--input', $taskInput) }

# Optional local override is ignored by Git; no machine-specific runtime in source.
if (-not $Python) { $Python = $env:FOREVERSTATE_PYTHON }
$taskConfig = Join-Path $PSScriptRoot '.python-path'
if (-not $Python -and (Test-Path -LiteralPath $taskConfig)) {
    $Python = (Get-Content -LiteralPath $taskConfig -Raw).Trim()
}
$taskPython = $Python
$taskPrefix = @()
if (-not $taskPython) {
    foreach ($taskName in @('py', 'python3', 'python')) {
        $taskCommand = Get-Command $taskName -CommandType Application -ErrorAction SilentlyContinue
        if (-not $taskCommand) { continue }
        $taskCandidatePrefix = @()
        if ($taskName -eq 'py') { $taskCandidatePrefix = @('-3') }
        # Probe versions: a command may be old Python or an unconfigured Store alias.
        try {
            & $taskCommand.Source @taskCandidatePrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            $taskWorks = $LASTEXITCODE -eq 0
        } catch { $taskWorks = $false }
        if ($taskWorks) {
            $taskPython = $taskCommand.Source
            $taskPrefix = $taskCandidatePrefix
            break
        }
    }
}
if (-not $taskPython) {
    throw 'Install Python 3.10+ or pass -Python with its executable path. No analysis packages are required.'
}
& $taskPython @taskPrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required.' }
& $taskPython @taskPrefix @taskArguments
if ($LASTEXITCODE -ne 0) { throw "Forever analysis failed (exit $LASTEXITCODE). Existing archives were retained." }
