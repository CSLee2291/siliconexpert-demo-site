# SiliconExpert MCP server - Windows installer.
# Creates .venv next to this script, installs deps, and runs a self-test.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "=== SiliconExpert MCP server install ===" -ForegroundColor Cyan
Write-Host "Working dir: $here"

# ---------------------------------------------------------------------------
# 1. Find a real Python >= 3.10.
#
# Gotcha: Windows ships a "python.exe" at
# C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\python.exe that is NOT
# actually Python - it's a Store redirector. It silently produces no output
# and silently fails every command. We detect that and refuse to continue.
# ---------------------------------------------------------------------------

function Test-RealPython([string]$exe) {
    # A real Python prints its version; the Store stub prints nothing.
    $out = & $exe -c "import sys; print('PY%d.%d' % sys.version_info[:2])" 2>$null
    if (-not $out -or $out -notmatch '^PY(\d+)\.(\d+)$') { return $null }
    return [pscustomobject]@{
        Path    = $exe
        Version = "$($Matches[1]).$($Matches[2])"
        Major   = [int]$Matches[1]
        Minor   = [int]$Matches[2]
    }
}

# Prefer `py -3` (the Python Launcher) because it sidesteps the Store alias.
$candidates = @()
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $candidates += ,@($pyLauncher.Source, "-3")
}
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd -and $pythonCmd.Source -notlike "*WindowsApps*") {
    $candidates += ,@($pythonCmd.Source)
}

$python = $null
foreach ($c in $candidates) {
    $probe = & $c[0] $c[1..($c.Length-1)] -c "import sys; print('PY%d.%d' % sys.version_info[:2])" 2>$null
    if ($probe -match '^PY(\d+)\.(\d+)$') {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
            $python = $c
            Write-Host "Found Python $major.$minor via: $($c -join ' ')"
            break
        } else {
            Write-Host "Skipping Python $major.$minor (need 3.10+): $($c -join ' ')" -ForegroundColor Yellow
        }
    }
}

if (-not $python) {
    Write-Host ""
    Write-Host "ERROR: No working Python 3.10+ found on this machine." -ForegroundColor Red
    Write-Host ""
    if ($pythonCmd -and $pythonCmd.Source -like "*WindowsApps*") {
        Write-Host "The 'python' on your PATH is the Windows Store stub at:"
        Write-Host "  $($pythonCmd.Source)"
        Write-Host "It's a placeholder, not a real Python install."
        Write-Host ""
    }
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/"
    Write-Host "During install, CHECK the 'Add python.exe to PATH' box."
    Write-Host "Then re-run .\install.ps1"
    Write-Host ""
    Write-Host "Tip: you can also 'winget install Python.Python.3.12' from an"
    Write-Host "admin PowerShell, then open a fresh terminal and re-run." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Create .venv.
# ---------------------------------------------------------------------------

if (Test-Path ".venv") {
    Write-Host ".venv already exists - skipping create." -ForegroundColor Yellow
} else {
    Write-Host "Creating .venv..."
    & $python[0] $python[1..($python.Length-1)] -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create .venv (python -m venv exit $LASTEXITCODE)."
        exit 1
    }
}

$venvPy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Error "Venv created but $venvPy is missing. Delete .venv and re-run."
    exit 1
}

# ---------------------------------------------------------------------------
# 3. Install deps.
# ---------------------------------------------------------------------------

Write-Host "Installing requirements into .venv..."
& $venvPy -m pip install --upgrade pip 2>&1 | Out-Null
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install failed (exit $LASTEXITCODE)."
    exit 1
}

# ---------------------------------------------------------------------------
# 4. Self-test.
# ---------------------------------------------------------------------------

Write-Host "Running self-test..."
$testOutput = & $venvPy -c "import asyncio, sys; sys.path.insert(0, '.'); import server; tools = asyncio.run(server.mcp.list_tools()); print('TOOLS:', len(tools))"
if ($testOutput -match "TOOLS: 11") {
    Write-Host "OK - 11 tools registered." -ForegroundColor Green
} else {
    Write-Error "Self-test failed: $testOutput"
    exit 1
}

# ---------------------------------------------------------------------------
# 5. Print the Claude Desktop config snippet with this machine's paths baked in.
# ---------------------------------------------------------------------------

$venvPathEscaped   = $venvPy -replace '\\', '\\'
$serverPathEscaped = (Join-Path $here "server.py") -replace '\\', '\\'
Write-Host ""
Write-Host "=== Claude Desktop config snippet ===" -ForegroundColor Cyan
Write-Host "Paste this into %APPDATA%\Claude\claude_desktop_config.json"
Write-Host "(merge with any existing mcpServers block):"
Write-Host ""
Write-Host @"
{
  "mcpServers": {
    "siliconexpert-demo": {
      "command": "$venvPathEscaped",
      "args": [
        "$serverPathEscaped"
      ],
      "env": {
        "SE_API_BASE": "https://siliconexpert-demo-cslee.azurewebsites.net"
      }
    }
  }
}
"@ -ForegroundColor White
Write-Host ""
Write-Host "Fully quit Claude Desktop (tray icon -> Quit), re-open, and look for" -ForegroundColor Green
Write-Host "11 tools in the hammer icon." -ForegroundColor Green
