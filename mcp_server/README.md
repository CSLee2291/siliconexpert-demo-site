# SiliconExpert Demo — MCP Server

Exposes the [SiliconExpert Demo Web App](https://siliconexpert-demo-cslee.azurewebsites.net/) as an MCP (Model Context Protocol) server so LLMs can answer questions about Advantech parts and their SiliconExpert data.

## What it does

Each `/api/*` slice of the live backend is wrapped as a focused MCP tool. A chat client (Claude Desktop, Claude Code, Cursor, etc.) can call these tools to pull just the data needed to answer a specific question.

| Tool | Backs |
|---|---|
| `search_advantech_part` | `/api/search` |
| `get_overview` | `/api/detail` → `part` + `parametric` |
| `get_lifecycle_and_risk` | `/api/detail` → `lifecycle` |
| `get_pricing_and_stock` | `/api/detail` → `commercial` |
| `get_compliance` | `/api/detail` → `regulatory` + `chemicals` |
| `get_documents` | `/api/detail` → `documents` |
| `get_packaging_data` | `/api/detail` → `packaging` |
| `get_countries_of_origin` | `/api/detail` → `countries` |
| `get_cross_reference` | `/api/xref` |
| `get_pcn_history` | `/api/pcn` |
| `get_full_part_info` | `/api/detail` (everything except raw SE blob) |

The first tool call for a part number fetches `/api/detail` once and caches it in-process. Subsequent slice-tool calls on the same PN are served from cache — no extra round-trips to Azure.

## Install

```bash
cd mcp_server
pip install -r requirements.txt
```

Requires Python 3.10+. Tested on 3.12.

## Configuration

The server reads these environment variables:

| Name | Default | Purpose |
|---|---|---|
| `SE_API_BASE` | `https://siliconexpert-demo-cslee.azurewebsites.net` | Target Web App. Override to point at a local Flask (`http://127.0.0.1:8000`) or a different Azure deployment. |
| `SE_API_TIMEOUT` | `60` | Per-request timeout in seconds. |
| `SE_MCP_LOG_LEVEL` | `INFO` | Python logging level. Logs go to stderr. |

## Wiring it into Claude Desktop

Edit `claude_desktop_config.json`:

* Windows: `%APPDATA%\Claude\claude_desktop_config.json`
* macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Add an `mcpServers` entry:

```json
{
  "mcpServers": {
    "siliconexpert-demo": {
      "command": "python",
      "args": [
        "C:\\Users\\cs.lee.ADVANTECH\\Documents\\ClaudeCodeProjects\\SiliconExpertDemoSite\\mcp_server\\server.py"
      ],
      "env": {
        "SE_API_BASE": "https://siliconexpert-demo-cslee.azurewebsites.net"
      }
    }
  }
}
```

Restart Claude Desktop. You should see the hammer / tools icon in the prompt bar — clicking it lists all 11 tools.

## Wiring it into Claude Code

From any directory:

```bash
claude mcp add siliconexpert-demo --scope user -- python /absolute/path/to/mcp_server/server.py
```

Or add it per-project to `.mcp.json`:

```json
{
  "mcpServers": {
    "siliconexpert-demo": {
      "command": "python",
      "args": ["./mcp_server/server.py"]
    }
  }
}
```

## Example prompts

Once connected, you can ask natural-language questions. The LLM picks the right tool(s) on its own.

* *"What is Advantech part 1410025327-27A0?"* → `search_advantech_part` → `get_overview`
* *"Is 1410025327-27A0 near end-of-life?"* → `get_lifecycle_and_risk`
* *"What's the pricing and stock for 1410025601-02?"* → `get_pricing_and_stock`
* *"Is 1100000041 RoHS and REACH compliant?"* → `get_compliance`
* *"What are cross-references for 1100000041?"* → `get_cross_reference`
* *"Any PCNs on 10000158?"* → `get_pcn_history`
* *"Give me a full summary of 1410025327-27A0"* → `get_full_part_info`
* *"Where is 1410025601-02 manufactured?"* → `get_countries_of_origin`

## Local-first development

Point the server at a local Flask instance for fast iteration:

```powershell
$env:SE_API_BASE="http://127.0.0.1:8000"
python server.py
```

Then start the Flask backend separately (`python backend/flask_app.py` from the repo root).

## Testing tools without an MCP client

The MCP SDK ships a dev inspector:

```bash
mcp dev mcp_server/server.py
```

Opens a browser UI listing all tools; you can invoke each with arbitrary arguments and see the raw response. Useful for verifying a tool before plugging it into an LLM.

## Known limitations

* **No auth on the backend.** The Azure Web App currently has no API key. If auth is added later, the MCP server will need to forward a token — add an `SE_API_KEY` env var and an `Authorization` header in `_get()`.
* **In-process cache only.** If you run multiple MCP server instances (e.g. Claude Desktop + Claude Code simultaneously), each has its own cache. Moving to a shared cache (Redis / SQLite) isn't worth it at this scale.
* **No pagination for `/api/xref`.** Some parts have 400+ crosses; the full list is returned in one response. Claude handles it but the token cost is non-trivial — if this becomes a problem, add a `limit` / `offset` pair to the xref tool.
