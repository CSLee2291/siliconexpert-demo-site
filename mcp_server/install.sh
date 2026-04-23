#!/usr/bin/env bash
# SiliconExpert MCP server — macOS / Linux installer.
# Creates .venv next to this script, installs deps, and runs a self-test.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

echo "=== SiliconExpert MCP server install ==="
echo "Working dir: $here"

# 1. Locate a Python >= 3.10.
if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 not found on PATH. Install Python 3.10+ first." >&2
  exit 1
fi
py_version="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
echo "Found python3 $py_version at $(command -v python3)"

# 2. Create .venv if missing.
if [ -d ".venv" ]; then
  echo ".venv already exists — skipping create."
else
  echo "Creating .venv..."
  python3 -m venv .venv
fi

venv_py="$here/.venv/bin/python"

# 3. Install deps.
echo "Installing requirements into .venv..."
"$venv_py" -m pip install --upgrade pip >/dev/null
"$venv_py" -m pip install -r requirements.txt

# 4. Self-test.
echo "Running self-test..."
count="$("$venv_py" -c "
import asyncio, sys
sys.path.insert(0, '.')
import server
tools = asyncio.run(server.mcp.list_tools())
print(len(tools))
")"
if [ "$count" = "11" ]; then
  echo "OK — 11 tools registered."
else
  echo "Self-test failed: got $count tools (expected 11)" >&2
  exit 1
fi

# 5. Print the config snippet.
echo
echo "=== Claude Desktop config snippet ==="
echo "Paste this into ~/Library/Application Support/Claude/claude_desktop_config.json:"
echo
cat <<EOF
{
  "mcpServers": {
    "siliconexpert-demo": {
      "command": "$venv_py",
      "args": [
        "$here/server.py"
      ],
      "env": {
        "SE_API_BASE": "https://siliconexpert-demo-cslee.azurewebsites.net"
      }
    }
  }
}
EOF
echo
echo "Restart Claude Desktop and look for 11 tools in the hammer / tools icon."
