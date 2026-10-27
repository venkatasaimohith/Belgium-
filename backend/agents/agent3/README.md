# Agent 3 — Best front-facing Street View panorama

Agent 3 has one live acquisition strategy:

1. Geocode the full Agent 1 address.
2. Resolve its CadGIS building façade and nearby road geometry.
3. Probe the address and road around the façade.
4. Rank panoramas by apparent façade width × frontality.
5. Select one panorama and aim at the building centre.
6. Download `640x640` images at FOV `90` and FOV `110`.

Up to 10 addresses are processed concurrently. Mailbox detection is not run in Agent 3.

## Outputs

| File | Description |
|---|---|
| `agent3_images.json` | Selected panorama and both image records |
| `images/<site_id>/*_fov90.jpg` | Primary detection image |
| `images/<site_id>/*_fov110.jpg` | Wider fallback image |
| `images/<site_id>/*_meta.json` | Ranking and acquisition metadata |

```powershell
python agent3_image.py `
  --site-json ..\output\agent2_site.json `
  --agent1-mapping-json ..\output\agent1_mapping.json `
  --out-dir ..\output `
  --api-key $env:GOOGLE_MAPS_API_KEY `
  --workers 10
```
