# SiliconExpert MCP server — Windows installer.
# Creates .venv next to this script, installs deps, and runs a self-test.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "=== SiliconExpert MCP server install ===" -ForegroundColor Cyan
Write-Host "Working dir: $here"

# 1. Locate a Python >= 3.10.
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Error "Python not found on PATH. Install Python 3.10+ from https://www.python.org/downloads/"
    exit 1
}
$pyVersion = & python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
Write-Host "Found python $pyVersion at $($py.Source)"

# 2. Create .venv if missing.
if (Test-Path ".venv") {
    Write-Host ".venv already exists — skipping create." -ForegroundColor Yellow
} else {
    Write-Host "Creating .venv..."
    python -m venv .venv
}

# 3. Install deps.
$venvPy = Join-Path $here ".venv\Scripts\python.exe"
Write-Host "Installing requirements into .venv..."
& $venvPy -m pip install --upgrade pip 2>&1 | Out-Null
& $venvPy -m pip install -r requirements.txt

# 4. Self-test.
Write-Host "Running self-test..."
$testOutput = & $venvPy -c "import asyncio, sys; sys.path.insert(0, '.'); import server; tools = asyncio.run(server.mcp.list_tools()); print('TOOLS:', len(tools))"
if ($testOutput -match "TOOLS: 11") {
    Write-Host "OK — 11 tools registered." -ForegroundColor Green
} else {
    Write-Error "Self-test failed: $testOutput"
    exit 1
}

# 5. Print the config snippet.
$venvPath = $venvPy -replace '\\', '\\'
$serverPath = (Join-Path $here "server.py") -replace '\\', '\\'
Write-Host ""
Write-Host "=== Claude Desktop config snippet ===" -ForegroundColor Cyan
Write-Host "Paste this into %APPDATA%\Claude\claude_desktop_config.json:"
Write-Host ""
Write-Host @"
{
  "mcpServers": {
    "siliconexpert-demo": {
      "command": "$venvPath",
      "args": [
        "$serverPath"
      ],
      "env": {
        "SE_API_BASE": "https://siliconexpert-demo-cslee.azurewebsites.net"
      }
    }
  }
}
"@ -ForegroundColor White
Write-Host ""
Write-Host "Restart Claude Desktop and look for 11 tools in the hammer / tools icon." -ForegroundColor Green
