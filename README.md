# SiliconExpert Demo Site

Component-search UI for Advantech, wired to the SiliconExpert ProductAPI.
Two interchangeable backends (Flask or Next.js) expose the same `/api/*`
contract and serve the same single-page frontend.

![Home → Detail → Bulk Paste workflow](docs/screenshot-placeholder.png)

---

## Prerequisites

You will need the following installed locally:

| Dependency | Version | Notes |
|---|---|---|
| **Python** | 3.10+ (tested on 3.12) | For the Flask backend |
| **Node.js** | 18.18+ (LTS 22 recommended) | For the Next.js backend |
| **npm** | bundled with Node | `pnpm` / `yarn` also work |
| **git** | any recent | for cloning |

You also need:

* **SiliconExpert API credentials** — a login + API key with access to
  `authenticateUser`, `partsearch`, `partDetail`, `listPartSearch`, `xref`,
  `pcn`, `manufacturers`, `supplierProfile`, `parametric/*`, and
  `userStatus`. (Get these from <https://www.siliconexpert.com>.)
* **Optional**: Denodo REST Web-Service access to
  `iv_plm_allparts_latest`. The app still works without it — it's only used
  as a fallback when a PN is missing from the local Excel mapping.

## Required files (not committed to the repo)

| Path | Purpose |
|---|---|
| `.env` | Your environment config. Copy from `.env.template`, fill in secrets. |
| `acl_pn_comID/V_SE_MPN_LIST*.xlsx` | Advantech PN ↔ MPN ↔ Manufacturer ↔ SE_ComID Excel. 10k-row mapping. Ask the project owner for the latest copy. |

Both are listed in `.gitignore`. The application will fail on startup if
`.env` is missing or the Excel path is invalid.

## First-run setup

```bash
# 1. Clone and change into the repo
git clone <this-repo>
cd SiliconExpertDemoSite

# 2. Create your env file and fill in the two required secrets
cp .env.template .env
# ✏️  edit .env   →   SILICONEXPERT_LOGIN, SILICONEXPERT_API_KEY

# 3. Put the Excel mapping in place (obtain the file separately)
cp /path/to/V_SE_MPN_LIST20260128.xlsx acl_pn_comID/

# 4a. Flask backend (option A — recommended for a quick smoke test)
pip install -r backend/requirements.txt
python backend/flask_app.py                  # serves http://127.0.0.1:8000

# 4b. Next.js backend (option B — same API contract, Node runtime)
npm install
npm run dev                                   # serves http://127.0.0.1:3001
```

Both servers read the **same** `.env` and serve the **same** static
frontend from `public/index.html`. Pick whichever fits your environment.

## Project layout

```
public/index.html          # React frontend (self-contained, via Babel standalone)
backend/                   # Python / Flask backend
  flask_app.py             #   HTTP routes
  service.py               #   normalization layer over /partDetail, /xref, /pcn, …
  siliconexpert.py         #   thin SE client with per-request session
  excel_lookup.py          #   Excel loader (PN → SE_ComID)
  denodo_client.py         #   optional fallback for PNs not in Excel
  recent_store.py          #   SQLite-backed recent-search history
lib/ + app/                # Next.js (App Router) mirror of the Python backend
mcp_server/                # MCP server exposing the Azure Web App to LLMs
  server.py                #   11 tools (overview, lifecycle, pricing, …)
  requirements.txt         #   mcp + httpx
  README.md                #   setup + Claude Desktop / Claude Code config
acl_pn_comID/              # user-provided Excel mapping (gitignored)
.env                       # local secrets + config (gitignored)
.env.template              # template to copy from
requirements.txt           # root-level deps (+ gunicorn) for Azure Oryx build
runtime.txt                # pins Python 3.12 for Azure Oryx
startup.sh                 # gunicorn launcher; referenced by Azure Startup Command
.gitattributes             # pins *.sh to LF so Azure Linux can execute startup.sh
```

## `/api/*` contract (both backends)

| Route | Description |
|---|---|
| `GET /` | Serves `public/index.html` |
| `GET /api/health` | Health + Excel stats |
| `GET /api/search?q=` | PN / MPN lookup with SE fallback |
| `GET /api/bulk` · `POST /api/bulk` | Bulk lookup, 50-PN cap |
| `GET /api/detail?pn=` · `?comId=` | Full part detail bundle |
| `GET /api/xref?pn=` · `?comId=` | Cross-reference table |
| `GET /api/pcn?pn=` · `?comId=` | Product Change Notifications |
| `GET /api/taxonomy` | Full SE taxonomy tree (cached) |
| `GET /api/features?plName=` | Parametric feature filters per product line |
| `GET /api/browse?plName=` | Parts within a product line |
| `GET /api/mfr?q=` | Fuzzy manufacturer search |
| `GET /api/supplier?name=` | Manufacturer profile |
| `GET /api/user-status` | Account quota |
| `GET /api/recent` · `DELETE /api/recent` | Recent-search history |

## Lookup flow

1. Input is normalized (trim + upper-case).
2. **Excel by PN** — exact match against `V_SE_MPN_LIST*.xlsx`.
3. If PN matched but `SE_ComID` is blank → `POST /partsearch` with
   `MPN + Manufacturer` to resolve a ComID on the fly.
4. If no PN match → try **Excel by MPN**.
5. If still nothing → **Denodo `iv_plm_allparts_latest`** (if configured
   and reachable; app continues without it otherwise).
6. On detail → `POST /partDetail` with `getLifeCycleData=1` — pulls the
   full bundle (lifecycle, pricing, inventory, parametric, compliance,
   chemical, packaging, PCN counts, …).

## Denodo is optional

Leave the `DENODO_*` block in `.env` blank if you don't have access. The
app continues to serve:

* All Excel-matched PNs (with live SE data)
* Single-part search by MPN (via `/partsearch`)
* Bulk paste (up to 50 PNs at a time)
* Every detail tab (SE endpoints are independent of Denodo)

When Denodo is configured but unreachable, the Results page shows an
amber banner explaining that only the local-mapping fallback is
temporarily offline; SiliconExpert queries continue to work.

## Testing

Manual / UI walk-through with an Advantech PN that has rich SE data:

```
1410025327-27A0
```

Expected:

* Result card renders `Processor Supervisor Adj 1 Active Low/Open Drain
  5-Pin SC-70 T/R` by Texas Instruments, $0.299 unit, 7.2y to EOL.
* Opening the detail page populates all 8 tabs (Overview · Lifecycle &
  Risk · Cross Reference · Pricing & Stock · Compliance · Documents ·
  Packaging Data · Countries Of Origin) with live SE data.

For other representative parts:

* `1100000041` — Walsin ceramic cap (exercises the `/xref` 400+ crosses
  path and rich parametric data).
* `10000158` — Sporton ferrite bead (exercises the empty `ParametricData`
  fallback to `/getPlFeatures`).
* `1410025601-02` — Nexperia analog switch (exercises the full
  pricing / inventory / 45-tier price-break path).

## Deployment — Azure Web App (Linux, Python 3.12)

The Flask backend runs on Azure App Service Linux without code changes.
**Deploy via the VS Code Azure App Service extension** — do *not* use
GitHub Actions (see "Known-bad path" below).

### One-time setup (Azure portal)

1. Create a **Web App** — Linux, Python 3.12 runtime.
2. **Configuration → General settings → Startup Command**:
   ```
   Bash startup.sh
   ```
3. **Configuration → Application settings** — add:

   | Name | Value |
   |---|---|
   | `SILICONEXPERT_LOGIN` | your SE login |
   | `SILICONEXPERT_API_KEY` | your SE API key |
   | `EXCEL_PATH` | `/home/site/wwwroot/acl_pn_comID/V_SE_MPN_LIST20260128.xlsx` |
   | `RECENT_DB_PATH` | `/home/recent_searches.db` (persists across restarts) |
   | `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` (Oryx runs pip install) |
   | `DENODO_*` | *leave blank unless the Denodo host is reachable from Azure* |

### Deploy from VS Code

1. Install the **Azure App Service** extension.
2. Sign in to your Azure tenant.
3. Right-click the Web App → **Deploy to Web App…** → pick the repo root.
4. The extension zips the working directory as-is and uploads; Kudu +
   Oryx run `pip install -r requirements.txt` on the server.

### Upload the Excel mapping (once)

The xlsx is gitignored, so VS Code deploy won't include it unless you
untrack it. Easiest is a one-off static upload:

```powershell
az webapp deploy `
  --resource-group <your-rg> --name <your-app> `
  --type static `
  --src-path ".\acl_pn_comID\V_SE_MPN_LIST20260128.xlsx" `
  --target-path "/home/site/wwwroot/acl_pn_comID/V_SE_MPN_LIST20260128.xlsx"
```

`--type static` uploads one file without triggering Oryx or restarting
the app. **Do not use `--type zip` for data files** — it re-triggers a
build.

### Smoke tests

```bash
curl https://<your-app>.azurewebsites.net/api/health
curl "https://<your-app>.azurewebsites.net/api/search?q=1410025327-27A0"
```

## MCP server (LLM integration)

`mcp_server/` wraps the live Azure Web App as a Model Context Protocol server
so any MCP-aware client (Claude Desktop, Claude Code, Cursor, etc.) can answer
questions about Advantech parts directly. Eleven focused tools cover every
detail tab plus cross-reference and PCN history. The first tool call for a
given PN fetches the full bundle; subsequent slice-tool calls on the same PN
are served from an in-process cache.

```bash
cd mcp_server
pip install -r requirements.txt
python server.py       # stdio transport
```

Point it at a different backend with `SE_API_BASE`. See `mcp_server/README.md`
for full setup, Claude Desktop / Claude Code config, and example prompts.

### Known-bad path: Azure-generated GitHub Actions workflow

The workflow Azure's Deployment Center auto-creates (`.github/workflows/
main_<app>.yml`) uses `actions/upload-artifact@v4` +
`azure/webapps-deploy@v3`. That combo compresses the whole app into
`output.tar.zst` inside `/home/site/wwwroot/`, so `startup.sh` never
lands at the path the Startup Command expects. Container exits 127
immediately on every cold start. If the portal re-creates the workflow,
delete it — VS Code deploy is the supported path.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend boots but `/api/health` errors about the Excel file | `EXCEL_PATH` wrong or file missing | Put the xlsx at the configured path |
| "Authentication Succeeded" then 401 on subsequent calls | Cookie-session reuse regression | Already handled — per-request sessions are mandatory |
| Denodo banner on every search | Denodo env set but network unreachable | Clear the `DENODO_*` values in `.env` or fix the network path |
| Next.js won't start — "address already in use :3001" | Stale dev server | Kill the PID holding 3001 or change `-p` in `package.json` |
| Empty result card | SE returned no `ResultDto` | Check the reason banner; typical causes are expired API key or an unmapped ComID |
| Azure container exits 127 on cold start | `startup.sh` not at `/home/site/wwwroot/` (Oryx compressed it into `output.tar.zst`) | Redeploy via VS Code; do not use Azure's auto-generated GitHub Actions workflow |
| Azure `FileNotFoundError: Excel file not found` | xlsx missing on Azure or `EXCEL_PATH` wrong | Re-upload the xlsx via `az webapp deploy --type static` and confirm `EXCEL_PATH` is an absolute path |
| Azure Oryx "Couldn't detect platform 'python'" during deploy | Deployment target was a data-only zip (no `requirements.txt` inside) | Use `--type static` for data files, or include the whole repo in the zip |
