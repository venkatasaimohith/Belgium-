# Agent 5 — Cascaded mailbox detection

Agent 5 runs RF-DETR on Agent 4's enhanced images in this order:

1. Detect on FOV 90.
2. If a mailbox is found, return it and do not run FOV 110.
3. If FOV 90 is empty, detect on FOV 110.
4. A count found only at FOV 110 is retained and marked for manual ownership review.

Agent 5 does not reject neighbour mailboxes. Ownership rejection remains exclusively in Agent 6.

Agent 5A keeps only `mailbox` detections strictly above `0.70`. Results at or
above `0.75` are accepted directly; results in `(0.70, 0.75)` are accepted only when
Agent 5B's local Florence-2 verification score is strictly above `0.80`.
All other detections are rejected before the FOV cascade decision.

Agent 5B uses only the supplied local
`florence-community/Florence-2-large-ft` Transformers implementation. RF-DETR
and Florence are loaded in separate GPU waves so they do not occupy VRAM at
the same time. Florence's native grounding output has no calibrated
probability (`confidence: null`); the pipeline therefore records the derived
evidence value explicitly as `florence_verification_score`.

Outputs:

- `agent5a_candidates.json` — RF-DETR candidates sent to Florence.
- `agent5b_florence.json` — complete Florence grounding/caption audit.
- `agent5_detect.json` — accepted detections consumed by Agent 6.
- `agent5_florence_debug/<SITE_ID>/` — accepted/rejected annotated JPEGs and
  matching JSON evidence for every Florence-reviewed candidate. Red is
  rejected, green is accepted, and blue shows raw Florence grounding.

Florence grounds mailbox phrases on the complete enhanced image for each
RF-DETR candidate in the `(0.70, 0.75)` range. Only Florence grounding that
overlaps that RF-DETR box is scored; unrelated grounding elsewhere is ignored
and cannot add an independent mailbox. A verification score strictly above
`0.80` accepts the existing RF-DETR candidate.

`agent5_cross_verify.py` is inactive legacy code and is not used by the pipeline.
