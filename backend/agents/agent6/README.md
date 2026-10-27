# Agent 6 — Geometry-First Neighbour Rejection

Agent 6 is the only stage that assigns mailbox ownership. It reads Agent 2
parcel data, Agent 4 camera/frontage geometry, and Agent 5 raw detections.

For every mailbox it uses the bottom-centre pixel, converts that column to a
pinhole-camera bearing, and casts three rays (left, centre, right) against the
target and adjacent CadGIS parcel frontages.

Decision policy:

1. Three target-frontage rays: `TARGET`. Cadastral target evidence cannot be
   vetoed by a narrower facade slice.
2. Three rays hitting the same adjacent parcel, safely away from its frontage
   endpoints and outside the padded target/architectural uncertainty zone:
   `NEIGHBOUR`.
3. One unresolved mailbox near the buffered target frontage/access envelope:
   `TARGET` (detached-house garden/driveway fallback).
4. A mailbox touching a padded parcel/facade boundary, conflicting GIS/image
   evidence, or missing adjacent geometry:
   `MANUAL_REVIEW`.
5. A target front door may promote one ambiguous mailbox but never reject one.

The target building footprint is clipped to its parcel, its camera-facing
facade and frontage are projected directly into the image, and those GIS
projections replace visual vertical-seam ownership lines. A locally available
Mask2Former checkpoint is used only to estimate the facade's vertical extent.
Set
`MAILBOX_AGENT6_ALLOW_MODEL_DOWNLOAD=1` to permit its one-time download.
Grounding DINO is used locally for the optional front-door fallback when its
checkpoint is available. Door candidates must be vertically oriented; square
or horizontal foreground detections such as cars are discarded.

Outputs:

- `agent6_reject.json`
- `agent6_review/<site_id>/geometry_0.json`
- `agent6_review/<site_id>/rejection_0.jpg`

Legacy `localisation_*.json` facade/driveway files are not read by this agent.
