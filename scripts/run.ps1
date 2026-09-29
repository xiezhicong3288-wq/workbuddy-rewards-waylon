[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArgs
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$scriptPath = Join-Path $PSScriptRoot 'main.py'
$candidates = New-Object System.Collections.Generic.List[object]

function Add-Candidate {
    param([string]$File, [string[]]$Prefix = @())
    if ([string]::IsNullOrWhiteSpace($File)) { return }
    # The Microsoft Store aliases in WindowsApps are 0-byte stubs: running one opens the
    # Store instead of Python, so they must never be allowed to win candidate selection.
    if ($File -like '*\WindowsApps\*') { return }
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { return }
    $candidates.Add([pscustomobject]@{ File = $File; Prefix = $Prefix })
}

if ($env:WORKBUDDY_REWARDS_PYTHON) {
    Add-Candidate -File $env:WORKBUDDY_REWARDS_PYTHON
}

foreach ($name in @('python', 'python3')) {
    $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
        Select-Object -First 1
    if ($cmd) { Add-Candidate -File $cmd.Source }
}

$py = Get-Command 'py' -CommandType Application -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
    Select-Object -First 1
if ($py) { Add-Candidate -File $py.Source -Prefix @('-3') }

foreach ($root in @(
    (Join-Path $env:USERPROFILE '.workbuddy\binaries\python\versions'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)) {
    if (Test-Path -LiteralPath $root -PathType Leaf) {
        Add-Candidate -File $root
        continue
    }
    if (Test-Path -LiteralPath $root -PathType Container) {
        Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Add-Candidate -File (Join-Path $_.FullName 'python.exe') }
    }
}

foreach ($candidate in $candidates) {
    $usable = $false
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 1
    try {
        & $candidate.File @($candidate.Prefix) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>$null | Out-Null
        $usable = ($global:LASTEXITCODE -eq 0)
    }
    catch {
        $usable = $false
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if (-not $usable) { continue }

    & $candidate.File @($candidate.Prefix) $scriptPath @CommandArgs
    exit $global:LASTEXITCODE
}

throw 'Python 3.10+ was not found. Install Python or set WORKBUDDY_REWARDS_PYTHON to a working interpreter.'
