# Agent 2 — Parcel (CadGIS lookup + frontage)

## What it does

Reads the site list produced by Agent 1 and, for each site, resolves the
cadastral parcel, finds the building footprint, computes the street-facing
frontage edge and produces flags.

## Files

| File | Purpose |
|---|---|
| `agent2_parcel.py` | Main script |
| `test_agent2.py` | Unit tests |
| `requirements.txt` | Python dependencies |

## Outputs

| File | Layers / Contents |
|---|---|
| `agent2_site.json` | Array of per-site dicts — parcel_id, parcel & building WKT, frontage endpoints, frontage length, flags, status |
| `agent2_result.gpkg` | `sites`, `parcels`, `frontage_edges`, `buildings`, `roads` |

## Quick start

```bash
# Install dependencies (once)
pip install -r requirements.txt

# With a local parcel GPKG
python agent2_parcel.py \
    --sites-gpkg ../output/agent1_sites.gpkg \
    --mapping-json ../output/agent1_mapping.json \
    --parcel-gpkg /path/to/cadgis_parcels.gpkg \
    --out-dir ./output

# With live Flemish WFS (Flanders only — needs internet)
python agent2_parcel.py \
    --sites-gpkg ../output/agent1_sites.gpkg \
    --fetch-wfs \
    --out-dir ./output

# Run unit tests
python -m pytest test_agent2.py -v
```

## Key parameters

| Flag | Default | Description |
|---|---|---|
| `--sites-gpkg` | *(required)* | Agent 1 output GPKG |
| `--mapping-json` | — | Agent 1 mapping JSON (optional for Agent 2) |
| `--parcel-gpkg` | — | Local parcel GPKG |
| `--parcel-layer` | auto | Layer name within parcel GPKG |
| `--building-gpkg` | — | Building footprints GPKG |
| `--road-gpkg` | — | Road network GPKG |
| `--fetch-wfs` | — | Use live Flemish CadGIS WFS |
| `--out-dir` | `.` | Output directory |
| `--fallback-dist` | `15` | Nearest-parcel fallback distance (m) |
| `--wide-frontage` | `12` | Threshold for WIDE_FRONTAGE flag (m) |

## Flags produced

| Flag | Condition |
|---|---|
| `MULTI_ADDRESS` | `member_count > 1` (multiple input addresses share this site) |
| `NEAREST_PARCEL` | No parcel contained the site point; nearest used instead |
| `NO_PARCEL` | No parcel found within fallback distance |
| `WIDE_FRONTAGE` | Frontage edge exceeds wide-frontage threshold |
| `FRONTAGE_ERROR` | Error during frontage computation |

## Note on left/right corners

The frontage endpoints `point_a` and `point_b` are in EPSG:31370 but their
left/right assignment relative to the camera is resolved in Agent 3 once the
Street View panorama position is known.
