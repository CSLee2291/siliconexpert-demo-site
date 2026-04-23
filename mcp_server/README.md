# SiliconExpert Demo — MCP Server

Exposes the [SiliconExpert Demo Web App](https://siliconexpert-demo-cslee.azurewebsites.net/) as an MCP (Model Context Protocol) server so LLMs — Claude Desktop, Claude Code, Cursor, etc. — can answer questions about Advantech parts and their SiliconExpert data.

---

## Quick start (5 minutes)

1. **Unzip** this bundle somewhere on your machine. For the rest of this README, that path is called `<MCP_DIR>` — for example `C:\Tools\siliconexpert-mcp-server` on Windows or `~/tools/siliconexpert-mcp-server` on macOS.
2. **Install** — one command (see platform-specific scripts below).
3. **Configure your MCP client** — paste one JSON block into Claude Desktop's or Claude Code's config.
4. **Restart** the client and ask *"Is Advantech part 1410025327-27A0 near end-of-life?"*.

Prerequisites: **Python 3.10+** on PATH (tested on 3.12). No other system packages needed — HTTP calls go to the public Azure Web App at `https://siliconexpert-demo-cslee.azurewebsites.net`.

---

## What it does

Each `/api/*` slice of the live backend is wrapped as a focused MCP tool. The LLM picks the right tool for the question; the first call for a PN fetches the full detail bundle, subsequent slice calls on the same PN are served from an in-process cache.

| Tool | Backs | Use for questions like |
|---|---|---|
| `search_advantech_part` | `/api/search` | "Is this a real PN?" |
| `get_overview` | `/api/detail` → `part` + `parametric` | "What is X? Who makes it? Key specs?" |
| `get_lifecycle_and_risk` | `/api/detail` → `lifecycle` | "Is X end-of-life? YTEOL? Risk?" |
| `get_pricing_and_stock` | `/api/detail` → `commercial` | "Price? Stock? Lead time? Supply risk?" |
| `get_compliance` | `/api/detail` → `regulatory` + `chemicals` | "RoHS? REACH? Halogen? CAS list?" |
| `get_documents` | `/api/detail` → `documents` | "Datasheet? Image? Certifications?" |
| `get_packaging_data` | `/api/detail` → `packaging` | "Package? MSL? Reel size?" |
| `get_countries_of_origin` | `/api/detail` → `countries` | "Where is it made?" |
| `get_cross_reference` | `/api/xref` | "What are alternatives to X?" |
| `get_pcn_history` | `/api/pcn` | "Any recent PCNs on X?" |
| `get_full_part_info` | `/api/detail` (minus raw SE blob) | "Tell me everything about X." |

---

## Install

### Windows (PowerShell)

```powershell
cd <MCP_DIR>
.\install.ps1
```

### macOS / Linux

```bash
cd <MCP_DIR>
./install.sh
```

Either script:
1. Creates a `.venv/` inside `<MCP_DIR>`.
2. Installs `httpx` and `mcp` into the venv.
3. Runs a self-test that registers all 11 tools.

**Prefer to do it manually?** The two lines inside `install.ps1` / `install.sh` are all it takes — `python -m venv .venv` and `pip install -r requirements.txt`.

---

## Configure your MCP client

> **Important**: Always point `"command"` at the **venv's Python**, not plain `"python"`. Claude Desktop and Claude Code launch subprocesses with whatever `python` is first on PATH, which is usually a different interpreter than the one your install step used. Missing this is the #1 cause of "No module named httpx" in the log.

### Claude Desktop

Edit `claude_desktop_config.json`:
* **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
* **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Add (replacing `<MCP_DIR>` with your actual unzipped path — use **double backslashes** on Windows):

```json
{
  "mcpServers": {
    "siliconexpert-demo": {
      "command": "<MCP_DIR>\\.venv\\Scripts\\python.exe",
      "args": [
        "<MCP_DIR>\\server.py"
      ],
      "env": {
        "SE_API_BASE": "https://siliconexpert-demo-cslee.azurewebsites.net"
      }
    }
  }
}
```

macOS / Linux version (forward slashes, `bin/python`):

```json
{
  "mcpServers": {
    "siliconexpert-demo": {
      "command": "<MCP_DIR>/.venv/bin/python",
      "args": [
        "<MCP_DIR>/server.py"
      ],
      "env": {
        "SE_API_BASE": "https://siliconexpert-demo-cslee.azurewebsites.net"
      }
    }
  }
}
```

Fully quit Claude Desktop (tray icon → **Quit**, not just close the window) and re-open. The hammer / tools icon in the prompt bar should show **11 tools**.

### Claude Code

From any terminal:

```bash
# Windows
claude mcp add siliconexpert-demo --scope user -- "<MCP_DIR>\.venv\Scripts\python.exe" "<MCP_DIR>\server.py"

# macOS / Linux
claude mcp add siliconexpert-demo --scope user -- "<MCP_DIR>/.venv/bin/python" "<MCP_DIR>/server.py"
```

Or add it per-project to `.mcp.json`:

```json
{
  "mcpServers": {
    "siliconexpert-demo": {
      "command": "<MCP_DIR>/.venv/bin/python",
      "args": ["<MCP_DIR>/server.py"]
    }
  }
}
```

---

## Example prompts

Try these once connected:

* *"What is Advantech part `1410025327-27A0`?"* → picks `search_advantech_part` → `get_overview`
* *"Is `1410025327-27A0` near end-of-life?"* → `get_lifecycle_and_risk`
* *"What's the pricing and stock for `1410025601-02`?"* → `get_pricing_and_stock`
* *"Is `1100000041` RoHS and REACH compliant?"* → `get_compliance`
* *"What are cross-references for `1100000041`?"* → `get_cross_reference`
* *"Any PCNs on `10000158`?"* → `get_pcn_history`
* *"Give me a full summary of `1410025327-27A0`"* → `get_full_part_info`
* *"Where is `1410025601-02` manufactured?"* → `get_countries_of_origin`

---

## Environment variables

The server reads these (all optional):

| Name | Default | Purpose |
|---|---|---|
| `SE_API_BASE` | `https://siliconexpert-demo-cslee.azurewebsites.net` | Target Web App. Override to point at a local Flask (`http://127.0.0.1:8000`) or a different Azure deployment. |
| `SE_API_TIMEOUT` | `60` | Per-request HTTP timeout in seconds. |
| `SE_MCP_LOG_LEVEL` | `INFO` | Python logging level. Logs go to stderr. |

In Claude Desktop, set these in the `"env"` block of `mcpServers`. In Claude Code, either in `.mcp.json` or via `claude mcp add --env SE_API_BASE=...`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `install.ps1` prints "Found python at `...\WindowsApps\python.exe`" or fails with "cannot recognize ...venv\Scripts\python.exe" | That PATH is the **Windows Store stub**, not real Python. Install Python 3.12 from <https://www.python.org/downloads/> (check "Add python.exe to PATH") or run `winget install Python.Python.3.12`, then open a fresh terminal and re-run the installer. |
| Log: `No module named httpx` | `"command"` is pointing at the wrong Python. Run `<MCP_DIR>/.venv/Scripts/python.exe -c "import httpx, mcp; print('ok')"` — if it errors, rebuild the venv (delete `.venv/` and re-run the install script). |
| Log: `Server transport closed unexpectedly` | Almost always a crash in the Python process. Scroll up in the log for the traceback. |
| No hammer / tools icon in Claude Desktop | The config JSON is malformed or the file isn't at the expected path. Paste the config into a JSON linter. |
| Tools show but all calls return `{ "error": "..." }` | The Azure Web App is unreachable from this machine. Test with `curl https://siliconexpert-demo-cslee.azurewebsites.net/api/health` — if that fails, check the network / proxy. |
| Tool call hangs for > 60s | Increase `SE_API_TIMEOUT` (e.g. set to `120`). SE's xref endpoint is occasionally slow. |

---

## Testing tools without an MCP client

The MCP SDK ships a dev inspector:

```bash
<MCP_DIR>/.venv/bin/mcp dev <MCP_DIR>/server.py
```

Opens a browser UI listing all tools; you can invoke each with arbitrary arguments and see the raw response. Useful for verifying a tool before plugging it into an LLM.

---

## Known limitations

* **No auth on the backend.** The Azure Web App currently has no API key. If auth is added later, the MCP server will need to forward a token — add an `SE_API_KEY` env var and an `Authorization` header in `_get()`.
* **In-process cache only.** If you run multiple MCP server instances (e.g. Claude Desktop + Claude Code simultaneously), each has its own cache.
* **No pagination for `/api/xref`.** Some parts have 400+ crosses; the full list is returned in one response. The LLM handles it but tokens aren't free.

---

## Files in this bundle

```
<MCP_DIR>/
├── README.md          ← you are here
├── server.py          ← the MCP server (11 tools)
├── requirements.txt   ← pinned deps (httpx, mcp)
├── install.ps1        ← Windows one-command installer
└── install.sh         ← macOS / Linux one-command installer
```
