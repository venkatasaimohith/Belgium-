# Agent 1 — Input + Deduplication

## What it does

Reads a Belgian address file (`.gpkg` or `.kml`), normalises text, groups
addresses that share the same physical dwelling (e.g. flat 1A and 1B at number
12), applies a spatial coherence check, assigns a `site_id` to each group, and
writes two output files for Agent 2.

## Files

| File | Purpose |
|---|---|
| `agent1_input_dedup.py` | Main script |
| `test_agent1.py` | Unit tests |
| `requirements.txt` | Python dependencies |

## Outputs

| File | Description |
|---|---|
| `agent1_sites.gpkg` | One row per site — `site_id`, postcode, normalised street, base house number, member count, representative point (EPSG:31370) |
| `agent1_mapping.json` | `site_id → [list of member address records]` with all original attributes |

## Quick start

```bash
# Install dependencies (once)
pip install -r requirements.txt

# Run on a real input
python agent1_input_dedup.py --input path/to/addresses.gpkg --out-dir ./output

# Inspect the input without writing anything
python agent1_input_dedup.py --input path/to/addresses.gpkg --inspect

# Run unit tests
python -m pytest test_agent1.py -v
```

## Key parameters

| Flag | Default | Description |
|---|---|---|
| `--input` | *(required)* | Path to `.gpkg` or `.kml` |
| `--out-dir` | `.` | Directory for output files |
| `--max-radius` | `30` | Max spread (m) within a group before it is split |
| `--inspect` | — | Print summary, no output written |

## Dedup logic

1. Normalise street name: lowercase, strip accents, collapse whitespace.
2. Parse house number into **base** / **letter** / **bus** components.
3. Dedup key = `postcode | normalised_street | base_number`.
4. Addresses with the same key are merged into one site whose representative
   point is the centroid of the group.
5. **Spatial coherence check**: if any member is >30 m from the centroid, the
   group is split (each member becomes its own site). This catches the same
   base number appearing on different buildings.

## Flags on sites

`member_count > 1` → `MULTI_ADDRESS` (handled by Agent 6 reconciliation).
