# Mailbox Detection Studio

Belgium address → mailbox pipeline with a web UI.

## Layout

```
Mail Box-ADV/
├── start_app.bat          ← double-click to run everything
├── frontend/              ← React UI
├── backend/
│   ├── server.py          ← FastAPI (API + serves frontend)
│   ├── pipeline/runner.py ← Orchestrates Agents 1–7
│   ├── agents/agent1…6/   ← all pipeline agents
│   ├── jobs/
│   └── requirements.txt
└── check 2 and 3 new/     BeSt / CadGIS register caches
```

## Quick start

1. Install and start Docker Desktop.
2. Double-click **`start_app.bat`**. It starts PostgreSQL and Redis automatically.
3. Browser opens on this PC at **http://localhost:8002** by default
4. The console also prints a **Network URL** like `http://192.168.x.x:8002` — open that from any other device on the same Wi‑Fi/LAN
5. Upload a `.gpkg` / `.kml` / `.kmz`
6. Click **Start Validation Pipeline**

## API cache

All external API responses (GRB WFS, Google Street View metadata/images,
BeSt, and CadGIS) use a shared cache. Requests check Redis first, then
PostgreSQL. A cached response is reused only while it is less than 30 days old;
otherwise the live API is called and both cache layers are refreshed.

PostgreSQL and Redis data are stored in named Docker volumes, so the cache
survives container restarts. If Docker is temporarily unavailable, the pipeline
continues with live API requests and reconnects to the cache later.

The mailbox PostgreSQL container publishes port `15435` by default; Redis uses
`16379`. If Docker reports that a port is already allocated, leave unrelated
containers running and choose an unused `POSTGRES_PORT` in `.env`. Update the
port in `DATABASE_URL` to the same value, then run
`docker compose up -d postgres redis` and restart the backend. Do not delete
database volumes to resolve a host-port conflict.

### Access from other systems

The server binds to `0.0.0.0` (all interfaces). URLs are detected dynamically:

| Who | URL |
|---|---|
| This PC | `http://localhost:8002` by default |
| Other PC / phone (same network) | `http://<your-LAN-IP>:8002` by default (printed at startup) |

Check live URLs anytime: `http://localhost:8002/api/access-info`

If another device cannot connect, allow the selected TCP port for Python in Windows Firewall. Set `PORT=` in the root `.env`; if it is busy, the launcher automatically selects the next free port.

## Pipeline (UI stages)

| Stage | Agent |
|---|---|
| 1. Input + Dedup | Agent 1 |
| 2. Parcel | Agent 2 |
| 3. Street View Images | Agent 3 |
| 4. Image Enhancement | Agent 4 |
| 5. Detect Mailboxes | Agent 5 |
| 6. Reject Neighbour Mailboxes | Agent 6 |
| 7. Floor Count | Agent 7 |
| Export Results | JSON / GPKG |

Detection uses only the supplied `rfdetr_large_mailbox_bundle/weights/last_ema.pth`
with `RFDETRLarge` (`rfdetr==1.10.1`), at confidence 0.4 by default. Every
returned mailbox is passed unchanged to Agent 6. Agent 6 combines target-house
and driveway localisation with parcel-frontage geometry. Clear target boxes are
kept, clear neighbour boxes are rejected, and unresolved boxes are sent to
manual review. Register cross-verification is not part of the active pipeline.

The records table loads every API page before applying local search and
pagination. Selecting 50 rows displays all 50 addresses, not just the first
25 returned by the API default. Pending GeoPackage previews include all input
addresses.

The launcher uses the project `.venv` and checks RF-DETR as well as the image
enhancement dependencies. Restart it after backend code changes; an already
running process retains its imported pipeline code.

### NVIDIA GPU enhancement

Install matching CUDA PyTorch and torchvision builds into the project environment:

```powershell
.\.venv\Scripts\python.exe -m pip --use-feature=truststore install -r backend\requirements-cuda.txt
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The CUDA 12.8 builds require a compatible NVIDIA driver. Set
`MAILBOX_ENHANCEMENT_DEVICE=cuda` in `.env` and restart the backend to require
GPU enhancement (an unavailable GPU raises an explicit error). `auto` selects
CUDA when available, otherwise CPU; `cpu` explicitly selects CPU. Per-job
`enhancement_device` options override this setting. Logs and enhancement-stage
results report the selected device. CPU-only PyTorch cannot use an NVIDIA GPU.

## Config (`.env`)

The root `.env` is the single configuration file used by both the backend and
Docker Compose. Copy `.env.example` to `.env` when setting up a new machine.

| Key | Meaning |
|---|---|
| `GOOGLE_MAPS_API_KEY` | Live Street View (without it Agent 3 uses dry-run placeholders) |
| `MAILBOX_MAX_SITES` | Optional debug cap (default `0` = process all sites) |
| `MAILBOX_ENHANCEMENT_DEVICE` | Real-ESRGAN device: `auto`, `cuda` (required GPU), or `cpu` |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Local vision-model endpoint and model name |
| `BEST_API_BASE_URL` | BeSt address API endpoint |
| `BEST_API_TIMEOUT_SEC`, `CADGIS_TIMEOUT_SEC` | Register API timeouts |
| `PREFER_LIVE_CADGIS` | Prefer live CadGIS over the result-file cache |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | PostgreSQL container credentials |
| `POSTGRES_PORT`, `REDIS_PORT` | Host ports published by Docker Compose |
| `DATABASE_URL` | PostgreSQL connection for durable API responses |
| `REDIS_URL` | Redis connection for fast cache reads |
| `API_CACHE_TTL_SECONDS` | Cache lifetime (default `2592000`, or 30 days) |
| `PORT`, `BIND_HOST`, `PUBLIC_HOST` | Backend listener and optional public hostname |

## Manual start

```bash
py -3.12 -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe backend\server.py
```

Open http://localhost:8002 (or the port printed by the server)
