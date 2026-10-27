# Agent 7 — Floor Count

Agent 7 runs after mailbox rejection. It crops the target façade from the
enhanced FOV-90 image, detects windows, doors, garages and dormers with
Grounding DINO, groups windows into horizontal rows, and writes the estimated
count to `agent7_floor_count.json`.

Low-confidence, obstructed or geometrically uncertain façades are preserved as
manual-review results instead of being silently accepted.
